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

    def test_case_diagrams_show_data_gates_and_combine_nested_branch_conditions(self):
        self.write("mix.sv", """
            module mix(input logic reset, input logic [1:0] op,
                       input logic [3:0] a, b, mask, output logic [3:0] y);
                logic [3:0] partial;
                always @* begin
                    y = 0;
                    partial = 0;
                    if (!reset) begin
                        case (op)
                            2'b00, 2'b01: begin
                                partial = a & b;
                                y = partial | mask;
                            end
                            2'b10: y = a ^ b;
                            default: ;
                        endcase
                    end
                end
            endmodule
        """)
        scope = compile_design(self.root, self.root, "mix", "mix.sv")["hierarchy"]
        summary = scope["logic"][0]["view"]["summary"]
        y = next(output for output in summary["outputs"] if output["name"] == "y")
        full = y["circuit"]["nodes"]
        self.assertEqual({n["label"] for n in full if n["kind"] == "gate"},
                         {"AND", "OR", "Logical NOT", "XOR"})
        self.assertIn("mux", {n["kind"] for n in full})
        circuits = y["rules"][0]["circuits"]
        data = circuits["data"]["nodes"]
        out = data[-1]
        gate_or = data[out["pins"][0]["source"]]
        gate_and = data[gate_or["pins"][0]["source"]]
        self.assertEqual([(gate["label"], gate["bits"]) for gate in [gate_and, gate_or]], [("AND", 4), ("OR", 4)])
        self.assertEqual([data[pin["source"]]["label"] for pin in gate_and["pins"]], ["a", "b"])
        self.assertEqual(data[gate_or["pins"][1]["source"]]["label"], "mask")
        control = circuits["selection"]["nodes"]
        self.assertEqual(sum(node.get("symbol") == "===" for node in control), 2)
        self.assertEqual(control[control[-1]["pins"][0]["source"]]["label"], "AND")
        self.assertIn("OR", {node["label"] for node in control})
        self.assertTrue(any(node["label"] == "XOR" for node in y["rules"][1]["circuits"]["data"]["nodes"]))

    def test_case_expression_gates_connect_to_the_branch_comparator(self):
        self.write("dynamic.sv", """
            module dynamic_case(input logic key, a, b, c, output logic y);
                always @* begin
                    case (key)
                        (a & b): y = a | c;
                        c: y = ~b;
                        default: y = 0;
                    endcase
                end
            endmodule
        """)
        scope = compile_design(self.root, self.root, "dynamic_case", "dynamic.sv")["hierarchy"]
        output = scope["logic"][0]["view"]["summary"]["outputs"][0]
        self.assertEqual(set(output["controls"]), {f"dynamic_case.{name}" for name in ("key", "a", "b", "c")})
        first, second, _ = output["rules"]
        selection = first["circuits"]["selection"]["nodes"]
        compare = selection[selection[-1]["pins"][0]["source"]]
        gate = selection[compare["pins"][1]["source"]]
        self.assertEqual((compare["symbol"], gate["label"], gate["symbol"]), ("===", "AND", "&"))
        self.assertEqual([selection[pin["source"]]["label"] for pin in gate["pins"]], ["a", "b"])
        self.assertIn(("OR", "|"), [(node["label"], node.get("symbol"))
                                    for node in first["circuits"]["data"]["nodes"]])
        self.assertIn("NOT", {node["label"] for node in second["circuits"]["selection"]["nodes"]})

    def test_wildcard_case_shows_exclusion_of_an_earlier_overlapping_arm(self):
        self.write("priority.sv", """
            module priority_case(input logic [1:0] key, output logic y, z);
                always @* begin
                    z = 0;
                    casez (key)
                        2'b0?: y = 1;
                        2'b?0: y = 0;
                        default: y = 1;
                    endcase
                end
            endmodule
        """)
        scope = compile_design(self.root, self.root, "priority_case", "priority.sv")["hierarchy"]
        y = next(output for output in scope["logic"][0]["view"]["summary"]["outputs"] if output["name"] == "y")
        nodes = y["rules"][1]["circuits"]["selection"]["nodes"]
        label = [node["label"] for node in nodes]
        self.assertEqual(label.count("Casez match"), 2)
        self.assertIn("NOT", label)
        self.assertEqual(nodes[nodes[-1]["pins"][0]["source"]]["label"], "AND")

    def test_combinational_summary_tracks_inputs_and_the_latest_value_per_branch(self):
        self.write("dispatch.sv", """
            module dispatch(input logic [2:0] op, input logic submode, blocked,
                input logic [3:0] d, output logic [4:0] code, output logic y);
                logic [3:0] stage;
                always @* begin
                    code = 5'b00000;
                    stage = d;
                    y = stage[0];
                    if (!blocked) begin
                        case (op)
                            3'b010: code = submode ? 5'b10010 : 5'b10100;
                            3'b100: begin
                                stage = ~d;
                                y = stage[0];
                            end
                            default: ;
                        endcase
                    end
                end
            endmodule
        """)
        scope = compile_design(self.root, self.root, "dispatch", "dispatch.sv")["hierarchy"]
        process = next(item for item in scope["logic"] if item.get("view"))
        summary = process["view"]["summary"]
        self.assertEqual(summary["inputs"], ["dispatch.blocked", "dispatch.d", "dispatch.op", "dispatch.submode"])
        self.assertEqual(summary["dataInputs"], ["dispatch.d"])
        self.assertEqual(summary["conditions"], ["!blocked"])
        self.assertEqual(summary["selectors"], ["op"])
        outputs = {entry["name"]: entry for entry in summary["outputs"]}
        code = outputs["code"]
        self.assertEqual(code["range"], "[4:0]")
        self.assertEqual(code["inputs"], ["dispatch.blocked", "dispatch.op", "dispatch.submode"])
        self.assertTrue(any(row["when"] == ["!blocked is true", "case op matches 3'b010"]
                            and row["value"] == "submode ? 5'b10010 : 5'b10100" for row in code["rules"]))
        self.assertEqual(code["rules"][-1]["value"], "5'b00000")
        local_sources = {tuple(binding) for row in outputs["y"]["rules"] for binding in row["locals"]}
        self.assertEqual(local_sources, {("stage", "d"), ("stage", "~d")})

    def test_case_can_select_a_value_that_was_assigned_by_an_earlier_decision(self):
        self.write("select.sv", """
            module select(input logic take_b, input logic [1:0] a, b,
                          output logic q, spare);
                localparam logic [1:0] TRIGGER = 2'b01;
                logic [1:0] key;
                always @* begin
                    if (take_b) key = b;
                    else key = a;
                    spare = 1'b0;
                    case (key)
                        TRIGGER: q = 1'b1;
                        default: q = 1'b0;
                    endcase
                end
            endmodule
        """)
        scope = compile_design(self.root, self.root, "select", "select.sv")["hierarchy"]
        summary = next(item for item in scope["logic"] if item.get("view"))["view"]["summary"]
        q = next(output for output in summary["outputs"] if output["name"] == "q")
        self.assertEqual(q["inputs"], ["select.a", "select.b", "select.take_b"])
        self.assertEqual(q["rules"][0]["when"], ["case key matches TRIGGER (2'b01)"])

    def test_process_keeps_nested_decisions_and_parameterized_bit_fields(self):
        self.write("select.sv", """
            module select #(parameter EXT = 3)
                (input logic reset, mode, input logic [3:0] d,
                 output logic [EXT+3:0] q, output logic ready);
                logic local_mode;
                always @* begin
                    local_mode = mode;
                    ready = 0;
                    q = 0;
                    if (reset) ready = 1;
                    else case (local_mode)
                        0: casez (d[1:0])
                            2'b0?: q = {{EXT{d[3]}}, d[3:0]};
                            default: q = 0;
                        endcase
                        default: ready = 1;
                    endcase
                end
            endmodule
        """)
        model = compile_design(self.root, self.root, "select", "select.sv")["hierarchy"]
        view = next(logic["view"] for logic in model["logic"] if logic.get("view"))
        controls = [(row["kind"], row["text"], row["depth"]) for row in view["rows"]]
        self.assertIn(("if", "reset", 0), controls)
        self.assertIn(("else", "", 0), controls)
        self.assertIn(("case", "local_mode", 1), controls)
        self.assertIn(("case z", "d[1:0]", 3), controls)
        self.assertIn("select.local_mode", view["localReads"])
        shapes = (shape for sheet in model["schematics"] for shape in sheet["nodes"])
        bus = next(shape for shape in shapes if shape["kind"] == "bitfields")
        self.assertEqual(bus["bits"], 7)
        self.assertEqual(bus["fields"], [{"range": "[6:4]", "text": "EXT × d[3]"},
                                         {"range": "[3:0]", "text": "d[3:0]"}])

    def test_initialization_uses_elaborated_loop_range_and_overrides(self):
        self.write("memory.sv", """
            module memory #(parameter LIMIT = 9) (output logic [7:0] q);
                logic [7:0] bank [3:10];
                integer cursor;
                initial begin
                    for (cursor = 3; cursor < LIMIT; cursor++)
                        bank[cursor] = 8'hA4;
                    bank[4] = 8'h81;
                end
                assign q = bank[4];
            endmodule
        """)
        model = compile_design(self.root, self.root, "memory", "memory.sv")["hierarchy"]
        init = next(item for item in model["logic"] if item["kind"] == "Initial")
        view = init["view"]
        self.assertEqual(view["kind"], "initialization")
        self.assertEqual(view["indices"]["memory.cursor"],
                         {"name": "cursor", "start": 3, "last": 8, "step": 1, "final": 9})
        self.assertEqual([(row["kind"], row["text"]) for row in view["rows"]],
                         [("sweep", "cursor · 3 → 8 · step +1"),
                          ("fill", "bank[3..8] ← 8'hA4"), ("set", "bank[4] ← 8'h81")])
        self.assertEqual(init["inputs"], [])
        self.assertEqual(init["outputs"], ["memory.bank", "memory.cursor"])

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

    def test_clock_and_register_wiring_follow_elaboration(self):
        self.write("top.sv", """
            module arm(input c, rst, input [31:0] n, output logic [31:0] q);
              always_ff @(negedge c)
                if (rst) q <= 0;
                else q <= n;
            endmodule
            module top(input sys_clk, rst, input [31:0] n, output [31:0] q);
              arm stage(.c(sys_clk), .rst(rst), .n(n), .q(q));
            endmodule
        """)
        result = compile_design(self.root, self.root, "top", "top.sv")["hierarchy"]
        route = result["clockUses"][0]
        self.assertEqual((route["signal"], route["label"], route["edge"]), ("sys_clk", "stage.c", "NegEdge"))
        nodes = result["children"][0]["schematics"][0]["nodes"]
        flop = next(node for node in nodes if node["kind"] == "flop")
        self.assertEqual(flop["label"], "q[0..31]")
        data, clock = [nodes[pin["source"]] for pin in flop["pins"]]
        self.assertEqual(clock["label"], "c")
        self.assertEqual(data["kind"], "mux")
        zero, one, select = [nodes[pin["source"]] for pin in data["pins"]]
        self.assertEqual((zero["label"], select["label"]), ("n", "rst"))
        while one["kind"] == "operator":
            one = nodes[one["pins"][0]["source"]]
        self.assertEqual(one["label"], "0")

    def test_nested_address_comparators_connect_to_and(self):
        self.write("rom.sv", """
            module rom(input [31:0] address, output logic [31:0] data);
              logic [31:0] mem [0:255];
              wire valid;
              wire spare;
              assign valid = (address[31:10] == 22'd0) && (address[1:0] == 2'b00);
              initial mem[0] = 32'h13;
              always_comb data = valid ? mem[address[9:2]] : 32'h13;
            endmodule
        """)
        root = compile_design(self.root, self.root, "rom", "rom.sv")["hierarchy"]
        self.assertIn("rom.spare", root["nets"])
        drawings = root["schematics"]
        self.assertTrue(any(sheet.get("processKind") == "Initial" for sheet in drawings))
        address = next(sheet for sheet in drawings if sheet["title"] == "valid")["nodes"]
        self.assertEqual(address[-1]["nets"], ["rom.valid"])
        driven_by = address[address[-1]["pins"][0]["source"]]
        self.assertEqual(driven_by["label"], "Logical AND")
        for pin in driven_by["pins"]:
            comparator = address[pin["source"]]
            self.assertEqual(comparator["label"], "Equal")
            self.assertEqual(address[comparator["pins"][0]["source"]]["nets"], ["rom.address"])
        data = next(sheet for sheet in drawings if sheet["title"] == "data")["nodes"]
        self.assertTrue(any(shape["kind"] == "memory" and shape["nets"] == ["rom.mem"] for shape in data))

    def test_unknown_module_reports_compile_failure(self):
        self.write("top.v", "module top; unknown u(); endmodule")
        result = compile_design(self.root, self.root, "top", "top.v")
        self.assertIsNone(result["hierarchy"])
        self.assertIn("unknown module 'unknown'", result["diagnostics"])
        self.assertEqual(result["issues"][0]["source"], {"file": "top.v", "line": 1, "column": 13})
        self.assertEqual(result["sourceText"]["top.v"], "module top; unknown u(); endmodule")


if __name__ == "__main__":
    unittest.main()
