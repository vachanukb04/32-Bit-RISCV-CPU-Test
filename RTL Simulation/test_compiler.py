"""Regression checks for source discovery and actual elaboration."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from compiler import CompileError, compile_design


class CompilerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, file, source):
        path = self.root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def test_source_dependency_header_parameter_and_generate_instances(self):
        self.write("design/params.svh", "`define WIDTH 2\n")
        self.write("design/top.sv", """
            `include "params.svh"
            module assembly(input wire [1:0] d, output wire [1:0] q);
                for (genvar i = 0; i < `WIDTH; i++) begin : lane
                    worker #(.W(1)) u(.d(d[i]), .q(q[i]));
                end
            endmodule
        """)
        self.write("lib/constants.sv", "package settings; parameter int W = 8; endpackage")
        self.write("lib/definitions.sv", """
            module worker #(parameter W = settings::W)
                (input wire [W-1:0] d, output wire [W-1:0] q);
                assign q = d;
            endmodule
        """)
        self.write("unrelated.v", "module unrelated; unknown u(); endmodule")
        result = compile_design(self.root, self.root, "assembly", "design/top.sv")
        self.assertEqual(result["files"], ["lib/constants.sv", "lib/definitions.sv", "design/top.sv"])
        self.assertEqual(result["headers"], ["design/params.svh"])
        instances = result["hierarchy"]["children"]
        self.assertEqual([instance["name"] for instance in instances], ["lane[0].u", "lane[1].u"])
        self.assertEqual(instances[0]["ports"][0]["bits"], 1)
        self.assertEqual(instances[0]["ports"][0]["refs"], ["assembly.d"])

    def test_duplicate_definition_resolves_next_to_top(self):
        self.write("first/top.v", "module top; worker u(); endmodule")
        self.write("first/worker.v", "module worker; endmodule")
        self.write("second/worker.v", "module worker; endmodule")
        result = compile_design(self.root, self.root, "top", "first/top.v")
        self.assertEqual(result["files"], ["first/worker.v", "first/top.v"])
        self.write("third/top.v", "module top; worker u(); endmodule")
        with self.assertRaisesRegex(CompileError, "narrower source directory"):
            compile_design(self.root, self.root, "top", "third/top.v")

    def test_inferred_structure_groups_and_conditional_bit_coverage(self):
        self.write("block.sv", """
            module block(input logic clk, en, input logic [31:0] d,
                         output logic [31:0] q, output logic [3:0] held,
                         output wire [31:0] muxed);
                logic [31:0] words [0:7];
                always_ff @(posedge clk) begin
                    q <= d;
                    if (en) words[0] <= d;
                end
                always @* begin
                    held[1:0] = d[1:0];
                    if (en) held[3:2] = d[3:2];
                end
                assign muxed = en ? (d & q) : words[0];
            endmodule
        """)
        x = compile_design(self.root, self.root, "block", "block.sv")
        tiles = {(tile["kind"], tile["label"]): tile for tile in x["hierarchy"]["structures"]}
        self.assertEqual(tiles["flop", "q"]["bits"], 32)
        self.assertIn("[0..31]", tiles["flop", "q"]["detail"])
        self.assertEqual(tiles["latch", "held"]["bits"], 2)
        self.assertIn("[2..3]", tiles["latch", "held"]["detail"])
        self.assertIn("8 words × 32 bits", tiles["memory", "words"]["detail"])
        self.assertIn(("mux", "Conditional mux"), tiles)
        self.assertIn(("gate", "AND"), tiles)

    def test_unknown_module_reports_compile_failure(self):
        self.write("top.v", "module top; unknown u(); endmodule")
        result = compile_design(self.root, self.root, "top", "top.v")
        self.assertIsNone(result["hierarchy"])
        self.assertIn("unknown module 'unknown'", result["diagnostics"])
        self.assertEqual(result["issues"][0]["source"], {"file": "top.v", "line": 1, "column": 13})
        self.assertEqual(result["sourceText"]["top.v"], "module top; unknown u(); endmodule")


if __name__ == "__main__":
    unittest.main()
