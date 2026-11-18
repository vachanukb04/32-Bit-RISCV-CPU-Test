"use strict";

const {readFileSync} = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const assert = require("node:assert/strict");

const io = readFileSync(path.join(__dirname, "rtl-project-io.js"), "utf8");
const {parseRepository, indexRtl, loadSelectedFiles} = vm.runInNewContext(
  io + "\n({parseRepository, indexRtl, loadSelectedFiles})", {URL});
const simple = value => JSON.parse(JSON.stringify(value));

test("accepts repository and tree URLs while keeping repo, ref, and source path distinct", () => {
  assert.deepEqual(simple(parseRepository("https://github.com/example/design.git/tree/main/rtl%20src", "", "")),
    {owner: "example", repo: "design", ref: "main", directory: "rtl src"});
  assert.deepEqual(simple(parseRepository("example/design", "feature/rework", "rtl/sub")),
    {owner: "example", repo: "design", ref: "feature/rework", directory: "rtl/sub"});
  assert.throws(() => parseRepository("https://github.com.example.com/a/b", "", ""));
  assert.throws(() => parseRepository("example/design", "main", "../rtl"));
  assert.throws(() => parseRepository("example/design", 'main"; quit', ""));
});

test("preserves folder-relative paths and ignores commented-out module declarations", async () => {
  const fixture = [
    {webkitRelativePath: "project/rtl/core.sv", size: 90,
      text: async () => "// module unused;\nmodule automatic core(input logic a); endmodule"},
    {webkitRelativePath: "project/include/width.svh", size: 20, text: async () => ""},
    {webkitRelativePath: "project/README.md", size: 14, text: async () => "# project"}
  ];
  const loaded = await loadSelectedFiles(fixture, true);
  assert.equal(loaded.name, "project");
  assert.deepEqual(Object.keys(loaded.sources), ["rtl/core.sv", "include/width.svh"]);
  assert.deepEqual(simple(indexRtl(loaded.sources)), [{name: "core", file: "rtl/core.sv"}]);
});

test("converts named buses, child-port bindings, constants, and a clocked cell to connected schematic data", async () => {
  const {elaborateNetlist} = await import("./rtl-netlist.js");
  const source = {"design.sv": "module top; endmodule\nmodule leaf; endmodule"};
  const netlist = {modules: {
    top: {
      attributes: {src: "design.sv:1.1-1.23"},
      ports: {a: {direction: "input", bits: [2, 3]}, clk: {direction: "input", bits: [5]},
        y: {direction: "output", bits: [6, 7]}, ready: {direction: "output", bits: ["1"]}},
      netnames: {v: {bits: [8, 9]}},
      cells: {u_leaf: {type: "leaf", connections: {d: [2, 3], o: [8, 9]}},
        reg: {type: "$dff", attributes: {src: "design.sv:1.4-1.23"}, parameters: {CLK_POLARITY: "1"},
          port_directions: {CLK: "input", D: "input", Q: "output"}, connections: {CLK: [5], D: [8, 9], Q: [6, 7]}}}
    },
    leaf: {
      attributes: {src: "design.sv:2.1-2.25"},
      ports: {d: {direction: "input", bits: [2, 3]}, o: {direction: "output", bits: [2, 3]}}
    }
  }};
  const {hierarchy} = elaborateNetlist(netlist, "top", "design.sv", source);
  const leaf = hierarchy.children[0];
  assert.equal(leaf.source.line, 2);
  assert.deepEqual(leaf.ports.find(port => port.name === "o").drives, ["top.v"]);
  assert.equal(hierarchy.clockUses[0].refs[0], "top.clk");
  assert.ok(hierarchy.schematics.some(sheet => sheet.nodes.some(node => node.label === "1'b1")));
  const flop = hierarchy.schematics.find(sheet => sheet.title === "reg").nodes;
  assert.ok(flop.some(node => node.kind === "flop" && node.edge === "PosEdge"));
  assert.deepEqual(flop.at(-1).nets, ["top.y"]);
});
