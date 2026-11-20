from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rtl_sim_cli import run_project


class SimulationTests(unittest.TestCase):
    def project(self, source, **options):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.sv"
            path.write_text(source)
            return run_project(path.parent, [path.name], "tb", **options)

    def test_mux_four_state_resolution_multiple_drivers_and_vcd(self):
        response = self.project("""
`timescale 1ns/1ps
module tb;
  logic select, drive;
  logic [3:0] a, b, y;
  wire bus;
  assign bus = drive ? 1'b0 : 1'bz;
  assign bus = select ? 1'b1 : 1'bz;
  always_comb y = select ? a : b;
  initial begin
    $dumpfile("mux.vcd"); $dumpvars(0, tb);
    a = 4'b1101; b = 4'b1001; select = 1'bx; drive = 0;
    #1; assert(y === 4'b1x01) else $fatal(1, "bad mux %b", y);
    select = 1; drive = 1;
    #1; assert(bus === 1'bx && y === a) else $fatal(1, "bad resolution");
    select = 0; drive = 0;
    #1; assert(bus === 1'bz && y === b) else $fatal(1, "bad z");
    $display("four state passed"); $finish;
  end
endmodule
""")
        self.assertIn("result", response, response)
        result = response["result"]
        self.assertIn("four state passed", result["log"])
        self.assertEqual(result["wave"]["name"], "mux.vcd")
        self.assertIn("b1x01", result["wave"]["text"])
        self.assertEqual(result["ticks"], 3000)

    def test_nonblocking_order_and_memory_address_are_snapshotted(self):
        response = self.project("""
`timescale 1ns/1ps
module flop(input logic clk, d, output logic q);
  always @(posedge clk) q <= d;
endmodule
module tb;
  logic clk = 0, d = 0;
  wire q;
  flop ff(clk, d, q);
  logic [3:0] words [0:3];
  int index;
  initial begin
    for (int i=0; i<4; i++) words[i]=i[3:0];
    #1; d = 1; index = 2;
    words[index] <= 4'hb; index = 3;
    clk = 1;
    #0; assert(q === 1'bx && words[2] === 4'd2) else $fatal(1, "early NBA");
    #1; assert(q === 1'b1 && words[2] === 4'hb && words[3] === 4'd3)
      else $fatal(1, "bad NBA snapshot");
    $display("memory and edge passed"); $finish;
  end
endmodule
""")
        self.assertIn("result", response, response)
        self.assertIn("memory and edge passed", response["result"]["log"])

    def test_parameters_generate_scopes_and_indexed_slices(self):
        response = self.project("""
module bank #(parameter N = 4)(input wire [0:N-1] data,
    output wire [N-1:0] out);
  for (genvar j=0; j<N; j=j+1) begin : bits
    assign out[j] = ~data[j];
  end
endmodule
module tb;
  logic [0:5] v;
  wire [3:0] out;
  bank #(.N(4)) dut(v[1 +: 4], out);
  initial begin
    v = 6'b010100;
    #1; assert(out === 4'b1010) else $fatal(1, "bad generated bus %b", out);
    $finish;
  end
endmodule
""")
        self.assertIn("result", response, response)
        self.assertEqual(response["result"]["ticks"], 1000)

    def test_limits_and_diagnostics(self):
        forever = "module tb; bit clk; always #1 clk = ~clk; endmodule"
        response = self.project(forever, limit=2500)
        self.assertEqual(response["result"]["reason"], "Time limit reached")
        self.assertEqual(response["result"]["ticks"], 2500)
        malformed = self.project("module tb; logic x = ; endmodule")
        self.assertEqual(malformed["phase"], "Analyze")
        self.assertIn("test.sv:", malformed["log"])
        unsupported = self.project("module tb; logic c; always @(c) fork c = 0; join endmodule")
        self.assertEqual(unsupported["phase"], "Elaborate")
        self.assertIn("Parallel fork", unsupported["error"])
        failed = self.project("module tb; initial begin $display(\"before\"); $fatal(1, \"failure\"); end endmodule")
        self.assertEqual(failed["phase"], "Simulate")
        self.assertIn("failure", failed["error"])
        self.assertIn("before", failed["log"])

    def test_run_options_memory_file_and_explicit_wave_selection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "headers").mkdir()
            (root / "headers" / "expected.svh").write_text("`define EXPECTED 8'hc2\n")
            (root / "rom.hex").write_text("3f // first word\n@2 c2\n")
            (root / "test.sv").write_text("""
`include "expected.svh"
module tb;
  reg [7:0] rom [0:3];
  reg [7:0] out;
  initial begin
    $readmemh("rom.hex", rom);
    #1; out = rom[`WORD];
    #1; assert(out === `EXPECTED) else $fatal(1, "bad ROM %h", out);
    $finish;
  end
endmodule
""")
            response = run_project(root, ["test.sv"], "tb", includes=["headers"],
                                   defines=["WORD=2"], trace=["tb.out"])
        self.assertIn("result", response, response)
        wave = response["result"]["wave"]["text"]
        self.assertIn("out [7:0]", wave)
        self.assertIn("b11000010", wave)

    def test_bundled_riscv_runs_to_finish_and_writes_output(self):
        root = Path(__file__).resolve().parents[1]
        rtl = root / "RiscV-32bit/RiscV-32bit.srcs/sources_1/new"
        phases = []
        response = run_project(rtl, [path.name for path in sorted(rtl.glob("*.v"))],
                               "tb_riscv_core", report=phases.append)
        self.assertIn("result", response, response)
        result = response["result"]
        self.assertEqual(phases, ["Analyze", "Elaborate", "Simulate"])
        self.assertEqual((result["ticks"], result["timescale"]), (2020000, "1ps"))
        self.assertIn("Time: 55000 | IO Output changed to: 00005555", result["log"])
        self.assertEqual(result["wave"]["name"], "riscv_core.vcd")
        self.assertIn("dbg_pc [31:0]", result["wave"]["text"])


if __name__ == "__main__":
    unittest.main()
