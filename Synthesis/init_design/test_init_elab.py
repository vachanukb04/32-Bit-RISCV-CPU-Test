"""Acceptance and diagnostic tests for the init/analyze/elab CLI."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).with_name("init_elab.py")
REPO = SCRIPT.resolve().parents[2]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class InitElabTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / ".git").mkdir()
        self.filelist = self.root / "rtl.filelist"
        self.filelist.write_text("design.v\n", encoding="utf-8")
        self.out = self.root / "out"
        self.log = self.root / "logs" / "init_design.log"

    def invoke(self, *, filelist=None, top="top", options=()):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--filelist", str(filelist or self.filelist),
             "--top", top, "--out", str(self.out), "--log", str(self.log), *options],
            capture_output=True, text=True, check=False, cwd=REPO,
        )

    def write_rtl(self, source):
        (self.root / "design.v").write_text(source, encoding="utf-8")

    def assert_diagnostic(self, stage, message, result, instance=None):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        diag = read_json(self.out / "diagnostics.json")
        self.assertEqual(diag["status"], "failed")
        self.assertEqual(diag["stage"], stage)
        self.assertIn(message, diag["diagnostics"][-1]["message"])
        log = self.log.read_text(encoding="utf-8")
        self.assertIn(f"[ERROR] [{stage.upper()}]", log)
        self.assertIn(message, log)
        self.assertIn(f"Status: FAILED during {stage}", log)
        self.assertEqual(log.count("Starting init_design"), 1)
        self.assertGreater(diag["diagnostics"][-1]["source"]["line"], 0)
        if instance is not None:
            self.assertEqual(diag["diagnostics"][-1]["instance_path"], instance)
        self.assertFalse((self.out / "elaboration.json").exists())

    def test_original_riscv(self):
        filelist = REPO / "Synthesis/setup/riscv_core.filelist"
        result = self.invoke(filelist=filelist, top="riscv_core")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        design = read_json(self.out / "elaboration.json")
        self.assertEqual(len(design["definitions"]), 8)
        self.assertEqual(len(design["instances"]), 8)
        self.assertEqual(sum(len(i["bindings"]) for i in design["instances"]), 51)
        self.assertFalse(design["diagnostics"])
        self.assertEqual(len(list((self.out / "blocks").glob("*.rpt"))), 8)
        log = self.log.read_text(encoding="utf-8")
        for expected in (
            "[INFO] [INIT] Reading RTL file list",
            "Reading RTL source RiscV-32bit/RiscV-32bit.srcs/sources_1/new/alu.v",
            "[INFO] [ANALYZE] Begin RTL front-end compilation",
            "Analyzed alu: 8 ports",
            "Linking riscv_core.u_alu to alu",
            "Bound riscv_core.u_alu.alu_op to alu_op: input, 5 bits",
            "Preserved array riscv_core.u_dmem.mem: 32-bit elements [0:255]",
            "51 width-checked bindings, 0 open ports",
            "Wrote elaboration.rpt and 8 block reports",
            "Status: SUCCESS (0 diagnostics)",
        ):
            self.assertIn(expected, log)
        for module, signal, dims, width in (
            ("alu", "tmp", [], 33),
            ("reg_file", "regs", [[0, 31]], 32),
            ("data_mem", "mem", [[0, 255]], 32),
            ("inst_mem", "mem", [[0, 255]], 32),
        ):
            found = next(s for s in design["definitions"][module]["signals"] if s["name"] == signal)
            self.assertEqual((found["type"]["dimensions"], found["type"]["width"]), (dims, width))
        pc = next(i for i in design["definitions"]["pc"]["items"] if i["kind"] == "process")
        self.assertEqual([(e["edge"], e["expression"]["name"]) for e in pc["events"]],
                         [("posedge", "clk")])
        data_mem = next(i for i in design["definitions"]["data_mem"]["items"]
                        if i["kind"] == "process" and i["events"][0]["edge"] == "posedge")
        self.assertEqual(len(data_mem["events"]), 1)
        before = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        result = self.invoke(filelist=filelist, top="riscv_core")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, {p.relative_to(self.out): p.read_bytes()
                                 for p in self.out.rglob("*") if p.is_file()})
        self.assertEqual(self.log.read_text(encoding="utf-8"), log)

    def test_include_parameter_generate_and_macro(self):
        tick = chr(96)
        (self.root / "inc").mkdir()
        (self.root / "inc" / "width.vh").write_text(
            f"{tick}ifdef BUILD\n{tick}define DEFAULT_W 2\n{tick}else\n"
            f"{tick}define DEFAULT_W 1\n{tick}endif\n", encoding="utf-8")
        (self.root / "nested.f").write_text("+incdir+inc\n+define+BUILD\ndesign.v\n", encoding="utf-8")
        self.filelist.write_text("-f nested.f\n", encoding="utf-8")
        self.write_rtl(f'{tick}include "width.vh"\n'
                       f"module top #(parameter W = {tick}DEFAULT_W)(input wire [W-1:0] a,"
                       " output wire [W-1:0] y);\n"
                       "genvar i;\n"
                       "generate for (i=0; i<W; i=i+1) begin : lane\n"
                       "leaf #(.WIDTH(1)) u (.a(a[i]), .y(y[i]));\n"
                       "end endgenerate\n"
                       "endmodule\n"
                       "module leaf #(parameter WIDTH=2) (input wire [WIDTH-1:0] a,"
                       " output wire [WIDTH-1:0] y);\n"
                       "INV_X1 u_inv (.A(a), .Y(y));\n"
                       "endmodule\n")
        options = ("--interfaces", str(REPO / "Synthesis/setup/cell_interfaces.json"), "--parameter", "W=3")
        result = self.invoke(options=options)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        design = read_json(self.out / "elaboration.json")
        leaves = [i for i in design["instances"] if i["module"] == "leaf"]
        self.assertEqual([i["path"] for i in leaves], [f"top.lane[{i}].u" for i in range(3)])
        self.assertEqual(len({i["definition"] for i in leaves}), 1)
        self.assertEqual(len(design["definitions"]), 3)
        self.assertEqual(design["definitions"]["INV_X1"]["kind"], "macro")
        deps = read_json(self.out / "analysis.json")["dependencies_sha256"]
        self.assertIn("inc/width.vh", deps)

    def test_constant_context_sizing(self):
        self.write_rtl("module top;\n"
                       "localparam [7:0] MIXED = (4'shf + 8'sh01) + 8'h00;\n"
                       "localparam [7:0] SIGNED_SUM = 4'shf + 8'sh01;\n"
                       "localparam [7:0] SHIFT = $signed(4'h8) >>> 1;\n"
                       "endmodule\n")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        params = read_json(self.out / "elaboration.json")["definitions"]["top"]["parameters"]
        self.assertEqual({name: value["value"] for name, value in params.items()},
                         {"MIXED": 16, "SIGNED_SUM": 0, "SHIFT": 252})

    def test_diagnostics_and_failed_rerun(self):
        self.write_rtl("module top; endmodule\n")
        self.assertEqual(self.invoke().returncode, 0)
        for source, stage, msg, instance in (
            ("module top; module unexpected; endmodule", "analyze", "Unsupported module item", None),
            ("module top; endmodule module top; endmodule", "analyze", "Duplicate module", None),
            (chr(96) + 'include "missing.vh"\nmodule top; endmodule', "analyze", "Include file not found", None),
            ("module top; leaf u(); endmodule", "elab", "Unresolved module", "top.u"),
            ("module top; top u(); endmodule", "elab", "Recursive or excessively deep", "top.u"),
            ("module top(input wire a); leaf u(.bad(a)); endmodule "
             "module leaf(input wire a); endmodule", "elab", "Unknown port", "top.u"),
            ("module top(input wire a); leaf u(.a(a)); endmodule "
             "module leaf(input wire [1:0] a); endmodule", "elab", "Port width", "top.u"),
            ("module top; leaf u(.a(1'b0)); endmodule "
             "module leaf(output wire a); endmodule", "elab", "write target", "top.u"),
            ("module top(output wire y); assign y=missing; endmodule", "elab", "Undeclared identifier", "top"),
            ("module top #(parameter A=B, parameter B=A); endmodule", "elab", "Cyclic declaration", "top"),
        ):
            with self.subTest(source=source):
                self.write_rtl(source)
                self.assert_diagnostic(stage, msg, self.invoke(), instance)
        (self.root / "design.v").unlink()
        self.assert_diagnostic("init", "Cannot read", self.invoke())

    def test_open_port_reporting(self):
        self.write_rtl("module top; leaf u(.a(), .y()); endmodule\n"
                       "module leaf(input wire a, output wire y); assign y=a; endmodule\n")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = read_json(self.out / "diagnostics.json")
        self.assertEqual(len(report["diagnostics"]), 1)
        self.assertEqual(report["diagnostics"][0]["severity"], "warning")
        self.assertIn("Open input port", report["diagnostics"][0]["message"])
        self.assertIn("[WARNING] [ELAB]", self.log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
