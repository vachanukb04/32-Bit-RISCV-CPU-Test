"""Discover Verilog sources and export a hierarchy from the elaborated Slang AST."""

from collections import defaultdict
from pathlib import Path

import pyslang
from pyslang import ast, parsing, syntax

from rtl_process_view import process_view
from rtl_schematic import clock_routes, data_schematics
from rtl_structure import describe_structure, process_timing, signal_refs, targets


SOURCE_EXTENSIONS = {".v", ".sv"}
UNIT_KINDS = {"ModuleDeclaration", "InterfaceDeclaration", "ProgramDeclaration",
              "PackageDeclaration", "UdpDeclaration"}
SCOPE_TYPES = (ast.GenerateBlockSymbol, ast.GenerateBlockArraySymbol, ast.InstanceArraySymbol)
DIRECTIONS = {"In": "input", "Out": "output", "InOut": "inout", "Ref": "ref"}


class CompileError(ValueError):
    pass


def options_for(directory, root, top=None):
    options = pyslang.Bag()
    preprocessor = parsing.PreprocessorOptions()
    preprocessor.additionalIncludePaths = list(dict.fromkeys([directory, root]))
    options.preprocessorOptions = preprocessor
    if top is not None:
        settings = ast.CompilationOptions()
        settings.topModules = {top}
        options.compilationOptions = settings
    return options


def inventory(root, directory):
    files = [
        path for path in sorted(directory.rglob("*"))
        if path.suffix.lower() in SOURCE_EXTENSIONS and path.is_file()
        and not path.is_symlink() and ".git" not in path.relative_to(root).parts
        and path.resolve().is_relative_to(directory)
    ]
    if not files:
        raise CompileError("No .v or .sv files found in the selected source directory.")
    manager = pyslang.SourceManager()
    options = options_for(directory, root)
    units = defaultdict(set)
    dependencies = defaultdict(set)
    tops = []
    for file in files:
        tree = syntax.SyntaxTree.fromFile(str(file), manager, options)

        def collect(node):
            kind = node.kind.name
            if kind in UNIT_KINDS:
                name = (node.name if kind == "UdpDeclaration" else node.header.name).valueText
                if not name:
                    return
                units[name, kind == "PackageDeclaration"].add(file)
                if kind == "ModuleDeclaration":
                    tops.append({"name": name, "file": file.relative_to(root).as_posix(),
                                 "line": manager.getLineNumber(node.header.name.location)})
            elif isinstance(node, syntax.HierarchyInstantiationSyntax):
                dependencies[file].add((node.type.valueText, False))
            elif isinstance(node, syntax.PackageImportItemSyntax):
                dependencies[file].add((node.package.valueText, True))
            elif isinstance(node, syntax.ScopedNameSyntax):
                if isinstance(node.left, syntax.IdentifierNameSyntax):
                    dependencies[file].add((node.left.identifier.valueText, True))

        tree.root.visit(collect)

    if not tops:
        raise CompileError("No module declarations found. Check the source directory and preprocessing directives.")
    referenced = {name for refs in dependencies.values() for name, package in refs if not package}
    for item in tops:
        item["rootCandidate"] = item["name"] not in referenced
    tops.sort(key=lambda item: (not item["rootCandidate"], item["name"], item["file"], item["line"]))
    return files, units, dependencies, tops


def order_files(root, start, units, dependencies):
    ordered, visited, active = [], set(), set()

    def visit(path):
        if path in visited or path in active:
            return
        active.add(path)
        for name, package in sorted(dependencies[path]):
            choices = units.get((name, package), set())
            if not choices or path in choices:
                continue
            nearby = {other for other in choices if other.parent == path.parent}
            choices = nearby or choices
            if len(choices) != 1:
                locations = ", ".join(str(other.relative_to(root)) for other in sorted(choices))
                raise CompileError(
                    f"{path.relative_to(root)} references {name!r}, defined in {locations}. "
                    "Select a narrower source directory to resolve the ambiguity."
                )
            visit(next(iter(choices)))
        active.remove(path)
        visited.add(path)
        ordered.append(path)

    visit(start)
    return ordered


def location(root, manager, loc):
    loc = manager.getFullyExpandedLoc(loc)
    path = Path(manager.getFullPath(loc.buffer))
    name = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
    return {"file": name, "line": manager.getLineNumber(loc), "column": manager.getColumnNumber(loc)}


def source_text(manager, obj):
    span = obj.sourceRange
    start, end = span.start, span.end
    if start and end and start.buffer == end.buffer:
        return manager.getSourceText(start.buffer).encode("utf-8")[start.offset:end.offset].decode(
            "utf-8", errors="replace").strip()
    return str(getattr(obj, "syntax", "") or "").strip()


def describe_instance(root, manager, instance, parent_path=""):
    path = instance.hierarchicalPath
    ports = []
    for port in instance.body.portList:
        direction = DIRECTIONS.get(getattr(getattr(port, "direction", None), "name", ""), "interface")
        conn = instance.getPortConnection(port) if parent_path else None
        expr = getattr(conn, "expression", None)
        name = port.name
        dtype = getattr(port, "type", None)
        internal = getattr(port, "internalSymbol", None)
        output_refs = []
        if expr is not None and direction in {"output", "inout"}:
            output = expr.left if isinstance(expr, ast.AssignmentExpression) else expr
            output_refs = sorted({symbol.hierarchicalPath for symbol, _ in targets(output)})
        ports.append({
            "name": name, "direction": direction, "bits": dtype.bitWidth if dtype is not None else None,
            "binding": source_text(manager, expr) if expr is not None else "",
            "bindingSource": location(root, manager, expr.sourceRange.start)
            if expr is not None and expr.sourceRange.start else None,
            "refs": signal_refs(expr), "drives": output_refs,
            "internal": internal.hierarchicalPath if internal is not None else f"{path}.{name}",
        })

    children, local_members = [], []

    def walk(scope):
        for member in scope:
            if isinstance(member, ast.InstanceSymbol):
                children.append(describe_instance(root, manager, member, path))
            elif isinstance(member, SCOPE_TYPES) and not member.isUninstantiated:
                walk(member)
            else:
                local_members.append(member)

    walk(instance.body)
    structures, logic = describe_structure(
        local_members, lambda loc: location(root, manager, loc),
        lambda obj: source_text(manager, obj))
    locate = lambda loc: location(root, manager, loc)
    text = lambda obj: source_text(manager, obj)
    logic_by_location = {(entry["source"]["file"], entry["source"]["line"], entry["source"]["column"]): entry
                         for entry in logic if entry["source"]}
    for member in local_members:
        if isinstance(member, ast.ProceduralBlockSymbol):
            site = locate(member.location)
            entry = logic_by_location.get((site["file"], site["line"], site["column"]))
            if entry is None:
                continue
            timing, _ = process_timing(member)
            view = process_view(member, timing, locate, text)
            if view:
                entry["view"] = view
                if view["kind"] == "initialization":
                    entry["inputs"] = []
                    entry["outputs"] = sorted(view["outputs"])
    schematics = data_schematics(local_members, structures, locate, text)
    clocks = clock_routes(local_members, children, logic, locate, text)
    nets = sorted({member.hierarchicalPath for member in local_members
                   if isinstance(member, (ast.NetSymbol, ast.VariableSymbol))})
    return {
        "schematics": schematics, "clockUses": clocks, "nets": nets,
        "path": path, "name": path.removeprefix(parent_path + ".") if parent_path else instance.name,
        "module": instance.definition.name, "source": location(root, manager, instance.definition.location),
        "ports": ports, "children": children, "logic": logic, "structures": structures,
        "parameters": [{"name": p.name, "value": str(p.value)} for p in instance.body.parameters
                       if isinstance(p, ast.ParameterSymbol) and not p.isLocalParam],
    }


def compile_design(root, directory, top, source):
    _, units, dependencies, tops = inventory(root, directory)
    if not any(item["name"] == top and item["file"] == source for item in tops):
        raise CompileError("Select a module and source file from the scan results.")
    ordered = order_files(root, root / source, units, dependencies)
    manager = pyslang.SourceManager()
    options = options_for(directory, root, top)
    # Slang's single compilation unit processes preprocessor directives in file-list order.
    tree = syntax.SyntaxTree.fromFiles([str(path) for path in ordered], manager, options)
    compiler = ast.Compilation(options)
    compiler.addSyntaxTree(tree)
    instances = compiler.getRoot().topInstances
    diagnostics = compiler.getAllDiagnostics()
    messages = pyslang.DiagnosticEngine.reportAll(manager, diagnostics).replace(str(root) + "/", "")
    source_files = [path.relative_to(root).as_posix() for path in ordered]
    headers = set()
    for include in tree.getIncludeDirectives():
        if include.buffer:
            path = Path(manager.getFullPath(include.buffer.id))
            headers.add(path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path))
    include_dirs = list(dict.fromkeys([directory.relative_to(root).as_posix(), "."]))
    engine = pyslang.DiagnosticEngine(manager)
    issues = [{
        "severity": engine.getSeverity(diag.code, diag.location).name.lower(),
        "message": engine.formatMessage(diag),
        "source": location(root, manager, diag.location) if diag.location else None,
    } for diag in diagnostics]
    # Capture source as compiled. The viewer never interprets diagnostic paths as read requests.
    sources = {}
    for name in sorted({*source_files, *headers} | {
        issue["source"]["file"] for issue in issues if issue["source"]
    }):
        path = (root / name).resolve()
        if path.is_relative_to(root) and path.is_file():
            sources[name] = path.read_text(encoding="utf-8", errors="replace")
    result = {
        "issues": issues, "sourceText": sources,
        "top": top, "files": source_files, "headers": sorted(headers), "diagnostics": messages,
        "includeDirs": include_dirs,
        "filelist": "\n".join(f"./{path}" for path in source_files) + "\n",
        "hierarchy": None,
    }
    if not any(diag.isError() for diag in diagnostics):
        if len(instances) != 1:
            raise CompileError("Compilation did not produce exactly one selected top instance.")
        result["hierarchy"] = describe_instance(root, manager, instances[0])
    return result
