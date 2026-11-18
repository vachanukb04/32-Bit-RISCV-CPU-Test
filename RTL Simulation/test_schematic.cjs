const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const scripts = ["schematic-graph.js", "schematic-layout.js"];
const api = runInNewContext(scripts.map(file => readFileSync(join(__dirname, file), "utf8")).join("\n") +
  "\n({buildSchematicGraph, layoutSchematic, createWireRouter});");

const location = line => ({file: "rom.v", line, column: 1});
const terminal = (label, net) => ({kind: "terminal", label, pins: [], nets: net ? [net] : []});
const output = (net, source) => ({kind: "output", label: net, nets: [net], pins: [{pin: "IN", source}]});
const render = scope => api.buildSchematicGraph(scope, new Map(scope.nets.map(net => [net, []])), net => net);

test("connects shared signals between elaborated assignments and memory on one graph", () => {
  const scope = {
    nets: ["addr", "valid", "mem", "inst", "spare"],
    ports: [{name: "addr", internal: "addr", direction: "input", bits: 32}],
    structures: [{kind: "memory", net: "mem", label: "mem", detail: "256 words × 32 bits",
      occurrences: [{source: location(2)}]}],
    children: [], logic: [], clockUses: [],
    schematics: [
      {source: location(3), nodes: [
        terminal("addr[1:0]", "addr"),
        terminal("2'b0"),
        {kind: "operator", label: "Equal", pins: [{pin: "A", source: 0}, {pin: "B", source: 1}]},
        output("valid", 2)
      ]},
      {source: location(4), nodes: [
        terminal("addr[9:2]", "addr"),
        {kind: "memory", label: "mem", nets: ["mem"], pins: [{pin: "ADDR", source: 0}]},
        terminal("valid", "valid"), terminal("32'b0"),
        {kind: "mux", label: "MUX", pins: [{pin: "0", source: 3}, {pin: "1", source: 1}, {pin: "S", source: 2}]},
        output("inst", 4)
      ]}
    ]
  };
  const graph = render(scope);
  const edge = (from, to, net) => graph.edges.some(item =>
    item.from.label === from && item.to.label === to && item.net === net);
  assert.equal(graph.anchors.size, 5);
  assert.equal(graph.anchors.get("mem").kind, "bank");
  assert.equal(graph.nodes.filter(node => node.label === "valid").length, 1);
  assert.ok(edge("addr", "addr[1:0]", "addr"));
  assert.ok(edge("addr", "addr[9:2]", "addr"));
  assert.ok(edge("Equal", "valid", "valid"));
  assert.ok(edge("valid", "MUX", "valid"));
  assert.ok(edge("mem", "MUX", "mem"));
  assert.ok(edge("MUX", "inst", "inst"));
});

test("keeps clock fanout and feedback when placing child instances on the same graph", () => {
  const port = (name, direction, binding) => ({
    name, direction, binding, bits: 1, refs: [binding], drives: direction === "output" ? [binding] : []
  });
  const s = {
    nets: ["clk", "data"],
    ports: [{name: "clk", internal: "clk", direction: "input", bits: 1}],
    structures: [], logic: [], schematics: [],
    children: [{name: "state", module: "ff", ports: [
      port("clk", "input", "clk"), port("d", "input", "data"), port("q", "output", "data")
    ]}],
    clockUses: [{pin: "Clock input", label: "state.clk", traceRefs: ["clk"]}]
  };
  const graph = render(s);
  const layout = api.layoutSchematic(graph);
  assert.equal(graph.edges.length, 3);
  const clock = graph.edges.find(edge => edge.net === "clk");
  assert.equal(clock.clock, true);
  assert.equal(clock.input, "clk");
  const state = clock.to;
  assert.ok(graph.edges.some(edge => edge.from === state && edge.output === "q" && edge.net === "data"));
  const router = api.createWireRouter(graph, layout);
  const start = {x: clock.from.rightChannel, y: clock.from.y};
  const end = {x: state.leftChannel, y: state.y};
  const path = router(start, end, 1);
  assert.equal(path[0].x, start.x);
  assert.equal(path.at(-1).x, end.x);
  assert.ok(path.every((point, i) => i === 0 ||
    Math.abs(point.x - path[i - 1].x) + Math.abs(point.y - path[i - 1].y) === 12));
});
