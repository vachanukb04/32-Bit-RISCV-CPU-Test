#!/usr/bin/env python3
"""Bundle the Slang WASM Python extension with the cockpit's RTL analysis code."""

from argparse import ArgumentParser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


MODULES = (
    "compiler.py", "rtl_case_regions.py", "rtl_comb_logic.py", "rtl_process_view.py",
    "rtl_schematic.py", "rtl_structure.py",
    "rtl_sim_cli.py", "rtl_sim_engine.py", "rtl_sim_logic.py", "rtl_sim_model.py",
)


def package(binding, slang_checkout, output, notices=()):
    here = Path(__file__).resolve().parent
    files = {module: here / module for module in MODULES}
    files.update({
        "pyslang/pyslang.so": binding,
        "pyslang/__init__.py": slang_checkout / "pyslang/pyslang/__init__.py",
        "LICENSE-Slang.txt": slang_checkout / "LICENSE",
    })
    files.update({"licenses/" + name: Path(path) for name, path in notices})
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w") as archive:
        for name, source in files.items():
            archive.writestr(ZipInfo(name), source.read_bytes(),
                             compress_type=ZIP_DEFLATED, compresslevel=9)


if __name__ == "__main__":
    command = ArgumentParser(description=__doc__)
    command.add_argument("binding", type=Path, help="Emscripten pyslang extension for Pyodide 0.27.7")
    command.add_argument("slang_checkout", type=Path, help="Matching Slang checkout or source distribution")
    command.add_argument("output", type=Path)
    command.add_argument("--notice", action="append", nargs=2, default=[], metavar=("NAME", "PATH"))
    args = command.parse_args()
    package(args.binding, args.slang_checkout, args.output, args.notice)
