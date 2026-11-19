"use strict";

const {readFileSync} = require("node:fs");
const {execFileSync} = require("node:child_process");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const assert = require("node:assert/strict");

const io = readFileSync(path.join(__dirname, "rtl-project-io.js"), "utf8");
const {parseRepository, indexRtl, loadSelectedFiles, reviewedSimulation} = vm.runInNewContext(
  io + "\n({parseRepository, indexRtl, loadSelectedFiles, reviewedSimulation})", {URL});
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

test("loads a public GitHub archive and pins the source commit without any GitHub API requests", async () => {
  const root = path.dirname(__dirname);
  const directory = "RiscV-32bit/RiscV-32bit.srcs/sources_1/new";
  const file = directory + "/alu.v";
  const commit = execFileSync("git", ["rev-parse", "HEAD"], {cwd: root, encoding: "utf8"}).trim();
  const archive = execFileSync("git", ["archive", "--format=zip", "--prefix=design-HEAD/", commit, "--", file], {cwd: root});
  const requests = [];
  const {loadGitHub} = vm.runInNewContext(io + "\n({loadGitHub})", {
    URLSearchParams, TextDecoder, DecompressionStream, AbortSignal, Blob, fetch: async url => {
      requests.push(url);
      if (!url.startsWith("/api/github-archive?")) throw new Error("GitHub API rate limit exhausted");
      return new Response(archive);
    }
  });
  const result = await loadGitHub({owner: "example", repo: "design", ref: "", directory}, "", () => {});
  assert.deepEqual(requests, ["/api/github-archive?owner=example&repo=design&ref=HEAD"]);
  assert.equal(result.commit, commit);
  assert.deepEqual(Object.keys(result.sources), [file]);
  assert.match(result.sources[file], /module alu\b/);
});

test("passes reviewed RTL, include files, and a matching testbench to simulation", () => {
  const sources = {
    "rtl/chip.sv": "module chip; endmodule",
    "test/tb_chip.sv": "module tb_chip; chip dut(); endmodule",
    "include/pins.svh": "`define PINS 4"
  };
  const candidates = indexRtl(sources);
  const snapshot = reviewedSimulation({name: "New chip", sources, candidates},
    candidates.find(item => item.name === "chip"));
  assert.deepEqual(simple(snapshot), {name: "New chip", top: "chip", testbench: "tb_chip", sources});
});

test("keeps the reviewed variant and elaborated dependency order for parallel RTL trees", () => {
  const sources = {
    "older/chip.v": "module chip; endmodule",
    "older/tb_chip.v": "module tb_chip; endmodule",
    "rtl/chip.sv": "module chip; sub u(); endmodule",
    "rtl/tb_chip.sv": "module tb_chip; chip dut(); endmodule",
    "lib/sub.sv": "module sub; endmodule"
  };
  const candidates = indexRtl(sources);
  const selected = candidates.find(item => item.file === "rtl/chip.sv");
  const analysis = {top: "chip", hierarchy: {source: {file: selected.file}},
    files: ["lib/sub.sv", selected.file]};
  const snapshot = reviewedSimulation({name: "Project", sources, candidates}, selected, analysis);
  assert.deepEqual(Object.keys(snapshot.sources), ["lib/sub.sv", "rtl/chip.sv", "rtl/tb_chip.sv"]);
  assert.equal(snapshot.testbench, "tb_chip");
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
