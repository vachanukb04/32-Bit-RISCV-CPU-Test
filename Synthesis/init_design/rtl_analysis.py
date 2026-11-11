"""Input handling and parsing for the supported Verilog RTL subset."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shlex


class FlowError(Exception):
    def __init__(self, message, source=None):
        super().__init__(message)
        self.source = source
        self.instance_path = None


@dataclass(frozen=True)
class Token:
    text: str
    file: str
    line: int
    column: int

    @property
    def source(self):
        return {"file": self.file, "line": self.line, "column": self.column}


IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
NUMBER = r"(?:[0-9][0-9_]*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|[0-9][0-9_]*)"
TICK = chr(96)
LEX = re.compile(
    rf"{NUMBER}|\$[A-Za-z_][A-Za-z0-9_$]*|{IDENT}|"
    r">>>|<<<|===|!==|==|!=|<=|>=|<<|>>|&&|\|\||~\^|\^~|~&|~\||"
    r"[()\[\]{}:;,.\#@?=+\-*/%&|^~!<>]"
)
COMMENT_OR_STRING = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"')
KEYWORDS = set("""
module endmodule input output inout wire reg logic integer signed unsigned
parameter localparam assign always always_comb always_ff always_latch initial
begin end if else case casez casex default endcase for generate endgenerate genvar
posedge negedge or and function endfunction task endtask while repeat forever
wait time real event tri wand wor supply0 supply1 defparam specify endspecify
force release automatic static typedef interface program package import endpackage
""".split())


def source_at(file, line=1, column=1):
    return {"file": file, "line": line, "column": column}


def display_path(path, root):
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def read_text(path, source):
    try:
        data = path.read_bytes()
        return data.decode("utf-8-sig"), hashlib.sha256(data).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise FlowError(f"Cannot read {path}: {exc}", source) from exc


def locate_root(filelist):
    for parent in filelist.parents:
        if (parent / ".git").exists():
            return parent
    raise FlowError("Cannot resolve ${REPO_ROOT}: file list is outside a Git checkout",
                    source_at(str(filelist)))


def read_inputs(filelist, top, interface_paths):
    """Expand source manifests without shell evaluation."""
    filelist = filelist.resolve()
    root = locate_root(filelist)
    if not re.fullmatch(IDENT, top) or top in KEYWORDS:
        raise FlowError(f"Invalid top module name: {top!r}")
    sources, incdirs, defines, manifests = {}, [], {}, {}
    active = set()

    def resolve(value, parent, at):
        prefix = "${REPO_ROOT}/"
        if value.startswith(prefix):
            return (root / value[len(prefix):]).resolve()
        if "${" in value:
            raise FlowError(f"Unsupported path variable in {value!r}", at)
        return (parent / value).resolve()

    def visit(path, at):
        if path in active:
            raise FlowError(f"Recursive file list: {display_path(path, root)}", at)
        active.add(path)
        body, digest = read_text(path, at)
        label = display_path(path, root)
        manifests[label] = digest
        for number, line in enumerate(body.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith(("#", "//")):
                continue
            at_line = source_at(label, number)
            try:
                fields = shlex.split(line)
            except ValueError as exc:
                raise FlowError(f"Malformed file list entry: {exc}", at_line) from exc
            directive = fields[0]
            if directive == "-f" and len(fields) == 2:
                visit(resolve(fields[1], path.parent, at_line), at_line)
            elif directive.startswith("+incdir+") and len(fields) == 1:
                directory = resolve(directive[8:], path.parent, at_line)
                if not directory.is_dir():
                    raise FlowError(f"Missing include directory: {directory}", at_line)
                if directory not in incdirs:
                    incdirs.append(directory)
            elif directive.startswith("+define+") and len(fields) == 1:
                name, sep, value = directive[8:].partition("=")
                if not re.fullmatch(IDENT, name):
                    raise FlowError(f"Invalid macro name: {name!r}", at_line)
                defines[name] = value if sep else "1"
            elif len(fields) == 1 and not directive.startswith(("-", "+")):
                source = resolve(directive, path.parent, at_line)
                if source in sources:
                    raise FlowError(f"Duplicate RTL source: {display_path(source, root)}", at_line)
                sources[source] = read_text(source, at_line)
            else:
                raise FlowError(f"Unsupported file list entry: {line}", at_line)
        active.remove(path)

    visit(filelist, source_at(display_path(filelist, root)))
    if not sources:
        raise FlowError("The file list contains no RTL sources", source_at(display_path(filelist, root)))

    interfaces = {}
    interface_files = {}
    for path in interface_paths:
        path = path.resolve()
        label = display_path(path, root)
        at = source_at(label)
        body, digest = read_text(path, at)
        try:
            cells = json.loads(body)["cells"]
        except (ValueError, KeyError, TypeError) as exc:
            raise FlowError("Expected a JSON cell catalog with a cells object", at) from exc
        if not isinstance(cells, dict):
            raise FlowError("The cell catalog cells field must be an object", at)
        for name, cell in cells.items():
            if name in interfaces or not re.fullmatch(IDENT, name):
                raise FlowError(f"Duplicate or invalid cell interface name: {name}", at)
            ports = cell.get("ports") if isinstance(cell, dict) else None
            if not isinstance(ports, list):
                raise FlowError(f"{name}: expected an ordered ports array", at)
            names = set()
            for port in ports:
                if not isinstance(port, dict):
                    raise FlowError(f"{name}: invalid cell interface port", at)
                pin, direction, width = (port.get(k) for k in ("name", "direction", "width"))
                if (not isinstance(pin, str) or not re.fullmatch(IDENT, pin) or pin in names
                        or direction not in ("input", "output", "inout")
                        or ("signed" in port and type(port["signed"]) is not bool)
                        or type(width) is not int or width < 1):
                    raise FlowError(f"{name}: invalid or duplicate port {pin!r}", at)
                names.add(pin)
            power_pins = cell.get("power_pins", [])
            if (not isinstance(power_pins, list)
                    or any(not isinstance(pin, str) or not re.fullmatch(IDENT, pin)
                           for pin in power_pins)
                    or len(set(power_pins)) != len(power_pins) or names.intersection(power_pins)):
                raise FlowError(f"{name}: invalid or duplicate power pins", at)
            interfaces[name] = {"ports": ports, "power_pins": power_pins, "source": at}
        interface_files[label] = digest

    manifest = {
        "top": top,
        "filelists": manifests,
        "sources": [{"file": display_path(p, root), "sha256": item[1]}
                    for p, item in sources.items()],
        "include_dirs": [display_path(p, root) for p in incdirs],
        "defines": defines,
        "cell_interface_files": interface_files,
    }
    return root, sources, incdirs, defines, interfaces, manifest


def tokenize_line(line, file, number):
    pos = 0
    while pos < len(line):
        if line[pos].isspace():
            pos += 1
            continue
        match = LEX.match(line, pos)
        if match is None:
            raise FlowError(f"Unsupported or invalid Verilog token near {line[pos:pos+20]!r}",
                            source_at(file, number, pos + 1))
        yield Token(match.group(), file, number, pos + 1)
        pos = match.end()


def preprocess(root, sources, include_dirs, defines):
    macros = dict(defines)
    deps, tokens, timescales = {}, [], []
    active_files = set()
    macro_reference = re.compile(TICK + rf"({IDENT})")

    def expand(text, at, stack=()):
        def replace(match):
            name = match[1]
            if name not in macros:
                raise FlowError(f"Undefined macro {TICK}{name}", at)
            if name in stack or len(stack) >= 64:
                raise FlowError(f"Recursive macro expansion for {name}", at)
            return expand(macros[name], at, (*stack, name))
        return macro_reference.sub(replace, text)

    def visit(path, data, at):
        if path in active_files:
            raise FlowError(f"Recursive RTL include: {display_path(path, root)}", at)
        active_files.add(path)
        text, digest = data
        file = display_path(path, root)
        deps[file] = digest
        # Retain newlines and columns while removing comments, including comments
        # before a directive. Quoted include filenames are left intact.
        text = COMMENT_OR_STRING.sub(lambda m: m[0] if m[0].startswith('"')
                                     else re.sub(r"[^\n]", " ", m[0]), text)
        frames = []
        enabled = True
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            at_line = source_at(file, number)
            match = re.match(TICK + rf"({IDENT})(.*)$", stripped)
            directive, arg = (match[1], match[2].strip()) if match else ("", "")
            if directive in ("ifdef", "ifndef", "elsif"):
                if not re.fullmatch(IDENT, arg):
                    raise FlowError(f"{TICK}{directive} requires a macro name", at_line)
            if directive in ("ifdef", "ifndef"):
                chosen = (arg in macros) == (directive == "ifdef")
                frames.append([enabled, chosen, False, at_line])
                enabled = enabled and chosen
            elif directive in ("elsif", "else", "endif"):
                if not frames:
                    raise FlowError(f"Unexpected {TICK}{directive}", at_line)
                if directive != "elsif" and arg:
                    raise FlowError(f"Unexpected text after {TICK}{directive}", at_line)
                parent, taken, saw_else, _ = frames[-1]
                if directive == "endif":
                    frames.pop()
                    enabled = parent
                else:
                    if saw_else:
                        raise FlowError(f"Unexpected {TICK}{directive} after else", at_line)
                    selected = not taken and (directive == "else" or arg in macros)
                    frames[-1][1] = taken or selected
                    frames[-1][2] = directive == "else"
                    enabled = parent and selected
            elif not enabled:
                continue
            elif directive == "include":
                requested = re.fullmatch(r'"([^"]+)"', arg)
                if not requested:
                    raise FlowError("Expected a quoted include path", at_line)
                candidates = [d / requested[1] for d in (path.parent, *include_dirs)]
                header = next((p.resolve() for p in candidates if p.is_file()), None)
                if header is None:
                    raise FlowError(f"Include file not found: {requested[1]}", at_line)
                visit(header, read_text(header, at_line), at_line)
            elif directive == "define":
                parts = re.fullmatch(rf"({IDENT})(?:\s+(.*))?", arg)
                if not parts or arg.endswith("\\"):
                    raise FlowError("Only single-line object-like macros are supported", at_line)
                macros[parts[1]] = parts[2] or ""
            elif directive == "undef":
                if not re.fullmatch(IDENT, arg):
                    raise FlowError("undef requires a macro name", at_line)
                macros.pop(arg, None)
            elif directive == "timescale":
                if not re.fullmatch(r"(?:1|10|100)\s*(?:s|ms|us|ns|ps|fs)\s*/\s*(?:1|10|100)\s*(?:s|ms|us|ns|ps|fs)", arg):
                    raise FlowError("Malformed timescale directive", at_line)
                timescales.append({"source": at_line, "value": arg})
            elif directive == "default_nettype":
                if arg not in ("none", "wire"):
                    raise FlowError(f"Unsupported default_nettype: {arg}", at_line)
            else:
                tokens.extend(tokenize_line(expand(line, at_line), file, number))
        if frames:
            raise FlowError("Unclosed preprocessor conditional", frames[-1][3])
        active_files.remove(path)

    for path, data in sources.items():
        visit(path, data, source_at(display_path(path, root)))
    file = display_path(next(reversed(sources)), root)
    tokens.append(Token("<eof>", file, len(next(reversed(sources.values()))[0].splitlines()) + 1, 1))
    return tokens, deps, timescales


BINARY = {op: precedence for precedence, ops in enumerate((
    "||", "&&", "|", "^ ^~ ~^", "&", "== != === !==", "< <= > >=",
    "<< >> <<< >>>", "+ -", "* / %",
), 1) for op in ops.split()}
UNARY = set("+ - ! ~ & ~& | ~| ^ ^~ ~^".split())


def node(kind, token, **fields):
    return {"kind": kind, "source": token.source, **fields}


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0

    @property
    def token(self):
        return self.tokens[self.index]

    def take(self):
        token = self.token
        if token.text != "<eof>":
            self.index += 1
        return token

    def accept(self, text):
        if self.token.text == text:
            return self.take()
        return None

    def expect(self, text):
        if self.token.text != text:
            self.error(f"Expected {text!r}, got {self.token.text!r}")
        return self.take()

    def error(self, message):
        raise FlowError(message, self.token.source)

    def identifier(self):
        token = self.token
        if not re.fullmatch(IDENT, token.text) or token.text in KEYWORDS:
            self.error(f"Expected identifier, got {token.text!r}")
        return self.take()

    def list_of(self, parse, close):
        result = []
        if self.token.text != close:
            result.append(parse())
            while self.accept(","):
                result.append(parse())
        self.expect(close)
        return result

    def bounds(self):
        self.expect("[")
        start = self.expr()
        self.expect(":")
        end = self.expr()
        self.expect("]")
        return [start, end]

    def expr(self, minimum=0):
        token = self.token
        if token.text in UNARY:
            self.take()
            left = node("unary", token, op=token.text, operand=self.expr(max(BINARY.values()) + 1))
        elif self.accept("("):
            left = self.expr()
            self.expect(")")
        elif self.accept("{"):
            first = self.expr()
            if self.accept("{"):
                parts = self.list_of(self.expr, "}")
                self.expect("}")
                left = node("replicate", token, count=first, parts=parts)
            else:
                parts = [first]
                while self.accept(","):
                    parts.append(self.expr())
                self.expect("}")
                left = node("concat", token, parts=parts)
        elif token.text.startswith("$"):
            self.take()
            if token.text not in ("$signed", "$unsigned", "$clog2"):
                raise FlowError(f"Unsupported system function {token.text}", token.source)
            self.expect("(")
            argument = self.expr()
            self.expect(")")
            left = node("call", token, name=token.text, argument=argument)
        elif re.fullmatch(NUMBER, token.text):
            self.take()
            left = node("literal", token, text=token.text)
        else:
            left = node("name", self.identifier(), name=token.text)
        left = self.selects(left)
        while (prec := BINARY.get(self.token.text, -1)) >= minimum:
            op = self.take()
            left = node("binary", op, op=op.text, left=left, right=self.expr(prec + 1))
        if minimum == 0 and (op := self.accept("?")):
            yes = self.expr()
            self.expect(":")
            left = node("ternary", op, condition=left, yes=yes, no=self.expr())
        return left

    def selects(self, value):
        while bracket := self.accept("["):
            index = self.expr()
            if self.accept(":"):
                value = node("slice", bracket, value=value, msb=index, lsb=self.expr())
            else:
                value = node("index", bracket, value=value, index=index)
            self.expect("]")
        return value

    def lvalue(self):
        token = self.token
        if self.accept("{"):
            return node("concat", token, parts=self.list_of(self.lvalue, "}"))
        return self.selects(node("name", self.identifier(), name=token.text))

    def assignment(self):
        lhs = self.lvalue()
        if self.token.text not in ("=", "<="):
            self.error(f"Expected assignment operator, got {self.token.text!r}")
        op = self.take()
        return node("assignment", op, op=op.text, lhs=lhs, rhs=self.expr())

    def statement(self):
        token = self.token
        if self.accept(";"):
            return node("empty", token)
        if self.accept("begin"):
            items = []
            while not self.accept("end"):
                items.append(self.statement())
            return node("block", token, statements=items)
        if self.accept("if"):
            self.expect("(")
            condition = self.expr()
            self.expect(")")
            yes = self.statement()
            no = self.statement() if self.accept("else") else None
            return node("if", token, condition=condition, yes=yes, no=no)
        if token.text in ("case", "casez", "casex"):
            self.take()
            self.expect("(")
            selector = self.expr()
            self.expect(")")
            arms = []
            seen_default = False
            while not self.accept("endcase"):
                start = self.token
                if self.accept("default"):
                    if seen_default:
                        raise FlowError("Duplicate default case item", start.source)
                    seen_default = True
                    self.accept(":")
                    labels = []
                else:
                    labels = self.list_of(self.expr, ":")
                arms.append(node("case_item", start, labels=labels, body=self.statement()))
            return node("case", token, match=token.text, selector=selector, arms=arms)
        if self.accept("for"):
            self.expect("(")
            init = self.assignment()
            self.expect(";")
            condition = self.expr()
            self.expect(";")
            step = self.assignment()
            self.expect(")")
            return node("for", token, init=init, condition=condition, step=step, body=self.statement())
        if not re.fullmatch(IDENT, token.text) and token.text != "{":
            self.error(f"Unsupported procedural statement starting with {token.text!r}")
        assignment = self.assignment()
        self.expect(";")
        return assignment

    def type_spec(self, default=None):
        storage = self.take().text if self.token.text in ("wire", "reg", "logic", "integer") else default
        sign = self.take().text if self.token.text in ("signed", "unsigned") else None
        packed = self.bounds() if self.token.text == "[" else None
        return {"storage": storage, "sign": sign,
                "signed": sign == "signed" or (sign is None and storage == "integer"),
                "packed": packed}

    def declarator(self, spec, *, direction=None, parameter=None):
        name = self.identifier()
        dims = []
        while self.token.text == "[":
            dims.append(self.bounds())
        value = self.expr() if self.accept("=") else None
        if parameter and value is None:
            raise FlowError(f"Parameter {name.text} requires a default expression", name.source)
        return node("declaration", name, name=name.text, direction=direction, parameter=parameter,
                    type_spec=spec, dimensions=dims, value=value)

    def header_ports(self):
        direction = None
        spec = None
        ports = []
        if not self.accept(")"):
            while True:
                if self.token.text in ("input", "output", "inout"):
                    direction = self.take().text
                    spec = self.type_spec("wire")
                if spec is None:
                    self.error("Only ANSI-style port declarations are supported")
                ports.append(self.declarator(spec, direction=direction))
                if not self.accept(","):
                    break
            self.expect(")")
        return ports

    def parameters(self, end, parameter=None):
        result = []
        spec = self.type_spec() if parameter else None
        while self.token.text != end:
            if self.token.text in ("parameter", "localparam"):
                parameter = self.take().text
                spec = self.type_spec()
            if parameter is None:
                self.error("Expected parameter declaration")
            result.append(self.declarator(spec, parameter=parameter))
            if not self.accept(","):
                break
        self.expect(end)
        return result

    def actuals(self, allow_open):
        def binding():
            token = self.token
            name = None
            if self.accept("."):
                name = self.identifier().text
                self.expect("(")
                value = None if allow_open and self.token.text == ")" else self.expr()
                self.expect(")")
            else:
                value = None if allow_open and self.token.text in (",", ")") else self.expr()
            return node("binding", token, name=name, value=value)
        return self.list_of(binding, ")")

    def generate_branch(self):
        token = self.expect("begin")
        self.expect(":")
        label = self.identifier()
        return node("generate_block", token, label=label.text, items=self.items_until("end", True))

    def item(self, generate=False):
        token = self.token
        if token.text in ("parameter", "localparam"):
            return self.parameters(";", self.take().text)
        if token.text in ("wire", "reg", "logic", "integer"):
            spec = self.type_spec()
            return self.list_of(lambda: self.declarator(spec), ";")
        if self.accept("genvar"):
            return [node("genvar", token, names=[n.text for n in self.list_of(self.identifier, ";")])]
        if self.accept("assign"):
            assignment = self.assignment()
            if assignment["op"] != "=":
                raise FlowError("A continuous assignment requires '='", assignment["source"])
            self.expect(";")
            return [node("continuous", token, assignment=assignment)]
        if token.text in ("always", "always_comb", "always_ff", "always_latch", "initial"):
            self.take()
            events = []
            if token.text in ("always", "always_ff"):
                self.expect("@")
                if self.accept("*"):
                    events = [node("event", token, edge="wildcard", expression=None)]
                else:
                    self.expect("(")
                    if self.accept("*"):
                        events = [node("event", token, edge="wildcard", expression=None)]
                    else:
                        while True:
                            event = self.token
                            edge = self.take().text if event.text in ("posedge", "negedge") else "level"
                            events.append(node("event", event, edge=edge, expression=self.expr()))
                            if not (self.accept("or") or self.accept(",")):
                                break
                    self.expect(")")
            elif token.text in ("always_comb", "always_latch"):
                events = [node("event", token, edge="wildcard", expression=None)]
            return [node("process", token, process=token.text, events=events, body=self.statement())]
        if self.accept("generate"):
            return self.items_until("endgenerate", True)
        if generate and self.accept("if"):
            self.expect("(")
            condition = self.expr()
            self.expect(")")
            yes = self.generate_branch()
            no = self.generate_branch() if self.accept("else") else None
            return [node("generate_if", token, condition=condition, yes=yes, no=no)]
        if generate and self.accept("for"):
            self.expect("(")
            inline = bool(self.accept("genvar"))
            init = self.assignment()
            self.expect(";")
            condition = self.expr()
            self.expect(";")
            step = self.assignment()
            self.expect(")")
            return [node("generate_for", token, inline=inline, init=init, condition=condition,
                         step=step, body=self.generate_branch())]
        if generate and token.text == "begin":
            return [self.generate_branch()]
        if not re.fullmatch(IDENT, token.text) or token.text in KEYWORDS:
            self.error(f"Unsupported module item starting with {token.text!r}")
        module = self.identifier()
        parameters = []
        if self.accept("#"):
            self.expect("(")
            parameters = self.actuals(False)
        name = self.identifier()
        self.expect("(")
        ports = self.actuals(True)
        self.expect(";")
        return [node("instance", name, module=module.text, name=name.text,
                     parameters=parameters, ports=ports)]

    def items_until(self, end, generate=False):
        items = []
        while not self.accept(end):
            if self.token.text == "<eof>":
                self.error(f"Expected {end!r} before end of file")
            items.extend(self.item(generate))
        return items

    def modules(self):
        result = {}
        while self.token.text != "<eof>":
            self.expect("module")
            name = self.identifier()
            if name.text in result:
                previous = result[name.text]["source"]
                raise FlowError(f"Duplicate module {name.text!r}; first defined at "
                                f"{previous['file']}:{previous['line']}", name.source)
            parameters = []
            if self.accept("#"):
                self.expect("(")
                parameters = self.parameters(")")
            ports = self.header_ports() if self.accept("(") else []
            self.expect(";")
            result[name.text] = node("module", name, name=name.text, parameters=parameters, ports=ports,
                                     items=self.items_until("endmodule"))
        return result
