"use strict";

const TILE = 12;
const snap = value => Math.round(value / TILE) * TILE;
const ceilTile = value => Math.ceil(value / TILE) * TILE;
const portSide = node => {
  const direction = node.port?.direction;
  if (direction === "output") return "right";
  if (direction === "input" || direction === "inout") return "left";
  return "";
};

const BOX_KINDS = ["instance", "bank", "process", "binding", "bitfields"];
const DECISION_ROW = 19;

function layoutSchematic(graph) {
  const {nodes, edges} = graph;
  const incoming = nodes.map(() => []);
  const outgoing = nodes.map(() => []);
  const readingOrder = new Map();
  const levels = new Map();

  for (const edge of edges) {
    incoming[edge.to.id].push(edge);
    outgoing[edge.from.id].push(edge);
  }

  // Keep feedback in the wiring graph while removing it from placement constraints.
  const active = new Set();
  const feedback = new Set();
  const order = [];
  function visit(node) {
    if (readingOrder.has(node)) return;
    readingOrder.set(node, readingOrder.size);
    active.add(node);
    for (const edge of outgoing[node.id]) {
      if (active.has(edge.to)) feedback.add(edge);
      else visit(edge.to);
    }
    active.delete(node);
    order.push(node);
  }
  const boundary = nodes.filter(node => portSide(node) === "left");
  for (const node of [...boundary, ...nodes]) visit(node);

  for (const node of order.reverse()) {
    const predecessors = incoming[node.id].filter(edge => !feedback.has(edge) && edge.from.kind !== "literal");
    const level = Math.max(0, ...predecessors.map(edge => levels.get(edge.from) + 1));
    levels.set(node, level);
  }
  const inputs = [];
  const outputs = [];
  const ranked = [];
  const lastLevel = Math.max(...levels.values());
  const step = Math.max(1, Math.ceil((lastLevel + 1) / 12));
  for (const node of nodes) {
    if (node.kind === "literal") {
      const destinations = outgoing[node.id].map(edge => levels.get(edge.to));
      if (destinations.length) levels.set(node, Math.max(0, Math.min(...destinations) - 1));
    }
    const side = portSide(node);
    if (side === "left") inputs.push(node);
    else if (side === "right") outputs.push(node);
    else (ranked[Math.floor(levels.get(node) / step)] ||= []).push(node);
  }
  const columns = [inputs, ...ranked, outputs].filter(column => column?.length);

  const row = new Map(nodes.map(node => [node, 0.5]));
  for (let pass = 0; pass < 6; pass++) {
    const forward = pass % 2 === 0;
    const sweep = forward ? columns : [...columns].reverse();
    for (const column of sweep) {
      const score = new Map(column.map(node => {
        const links = forward ? incoming[node.id] : outgoing[node.id];
        const neighbors = links.filter(edge => !feedback.has(edge)).map(edge => forward ? edge.from : edge.to);
        const average = neighbors.length ? neighbors.reduce((sum, peer) => sum + row.get(peer), 0) / neighbors.length
          : row.get(node);
        return [node, average];
      }));
      if (column !== inputs && column !== outputs) {
        column.sort((a, b) => score.get(a) - score.get(b) || readingOrder.get(a) - readingOrder.get(b));
      }
      column.forEach((node, index) => row.set(node, (index + .5) / column.length));
    }
  }

  for (const node of nodes) {
    if (BOX_KINDS.includes(node.kind)) {
      const longestIn = Math.max(0, ...[...node.inputs.values()].map(label => Math.min(24, label.length)));
      const longestOut = Math.max(0, ...[...node.outputs.values()].map(label => Math.min(18, label.length)));
      const rails = 42 + (longestIn + longestOut) * 6;
      let bodyWidth = 0;
      let bodyHeight = 0;
      if (node.rows?.length) {
        node.contentX = Math.max(18, longestIn * 6 + 14);
        bodyWidth = Math.max(...node.rows.map(row => row.depth * 13 + Math.min(row.text.length, 92) * 6.4 + 112));
        bodyHeight = 57 + DECISION_ROW * node.rows.length;
      } else if (node.fields && !node.compactFields) {
        bodyWidth = 104 + Math.max(...node.fields.map(field => Math.min(field.text.length, 58) * 6.5));
      }
      node.width = ceilTile(Math.max(132, rails + bodyWidth));
      node.height = ceilTile(Math.max(72, bodyHeight, 48 + Math.max(node.inputs.size, node.outputs.size) * 24));
    } else if (["net", "tap", "literal"].includes(node.kind)) {
      node.width = ceilTile(Math.max(48, Math.min(27, node.label.length) * 7 + 24,
        node.caption ? Math.min(27, node.caption.length) * 6 + 20 : 0));
      node.height = node.caption ? 48 : 24;
    } else {
      node.width = 72;
      const ports = node.kind === "expression" ? (node.inputs.size - 1) * 24 + 48 : 72;
      node.height = Math.max(72, ports);
    }
  }

  const gap = nodes.length > 45 ? 108 : 72;
  const isBoundary = column => column === inputs || column === outputs;
  const rowGap = column => isBoundary(column) ? 24 : 36;
  const packedHeight = column => column.reduce((total, node) => total + node.height + rowGap(column), 0);
  const bodyHeight = Math.max(0, ...columns.map(packedHeight));
  let x = inputs.length ? 36 : 60;
  for (const column of columns) {
    const width = Math.max(...column.map(node => node.width));
    let y = 60 + (isBoundary(column) ? 0 : snap((bodyHeight - packedHeight(column)) / 2));
    for (const node of column) {
      if (isBoundary(column)) node.width = width;
      node.x = x;
      node.y = y + node.height / 2;
      node.leftChannel = x - 24;
      node.rightChannel = x + width + 24;
      y += node.height + rowGap(column);
    }
    x += width + gap;
  }
  return {width: x - gap + (outputs.length ? 36 : 60), height: bodyHeight + 108};
}

class RouteQueue {
  entries = [];

  add(entry) {
    const list = this.entries;
    let index = list.length;
    list.push(entry);
    while (index > 0) {
      const parent = (index - 1) >> 1;
      if (list[parent].priority <= entry.priority) break;
      list[index] = list[parent];
      index = parent;
    }
    list[index] = entry;
  }

  next() {
    const list = this.entries;
    const top = list[0];
    const last = list.pop();
    if (!list.length) return top;
    let index = 0;
    while (index * 2 + 1 < list.length) {
      let child = index * 2 + 1;
      if (child + 1 < list.length && list[child + 1].priority < list[child].priority) child++;
      if (list[child].priority >= last.priority) break;
      list[index] = list[child];
      index = child;
    }
    list[index] = last;
    return top;
  }
}

function createWireRouter(graph, layout) {
  const cols = Math.ceil(layout.width / TILE);
  const rows = Math.ceil(layout.height / TILE);
  const size = cols * rows;
  const blocked = new Uint8Array(size);
  const occupancy = new Int32Array(size * 2);
  const best = new Float64Array(size * 2);
  const seen = new Uint32Array(size * 2);
  const previous = new Int32Array(size * 2);
  let run = 0;

  for (const node of graph.nodes) {
    const left = Math.ceil((node.x - 4) / TILE);
    const right = Math.floor((node.x + node.width + 4) / TILE);
    const top = Math.ceil((node.y - node.height / 2 - 4) / TILE);
    const bottom = Math.floor((node.y + node.height / 2 + 4) / TILE);
    for (let y = top; y <= bottom; y++) blocked.fill(1, y * cols + left, y * cols + right + 1);
  }

  return function route(start, end, group) {
    const source = start.y / TILE * cols + start.x / TILE;
    const target = end.y / TILE * cols + end.x / TILE;
    const tx = target % cols;
    const ty = Math.floor(target / cols);
    const pending = new RouteQueue();
    run++;
    const seek = (cell, axis, cost, parent) => {
      const state = cell * 2 + axis;
      if (seen[state] === run && best[state] <= cost) return;
      seen[state] = run;
      best[state] = cost;
      previous[state] = parent;
      const distance = Math.abs(tx - cell % cols) + Math.abs(ty - Math.floor(cell / cols));
      pending.add({state, cost, priority: cost + .7 * distance});
    };
    seek(source, 0, 0, -1);
    while (pending.entries.length) {
      const {state, cost} = pending.next();
      if (cost !== best[state]) continue;
      const cell = state >> 1;
      if (cell === target) {
        const path = [];
        for (let step = state; step >= 0; step = previous[step]) {
          const at = step >> 1;
          path.push({x: at % cols * TILE, y: Math.floor(at / cols) * TILE});
          if (!occupancy[step]) occupancy[step] = group;
        }
        return path.reverse();
      }
      const x = cell % cols;
      for (const [next, axis, allowed] of [
        [cell + 1, 0, x < cols - 2], [cell - 1, 0, x > 1],
        [cell + cols, 1, cell < size - 2 * cols], [cell - cols, 1, cell >= 2 * cols]
      ]) {
        if (!allowed || blocked[next]) continue;
        const occupied = occupancy[next * 2 + axis];
        const crossing = occupancy[next * 2 + (1 - axis)];
        const shared = occupied === group;
        const crowded = occupied && !shared;
        const turn = axis !== (state & 1);
        const weight = (shared ? .7 : 1.05) + (turn ? 2.5 : 0) +
          (crowded ? 17 : 0) + (crossing && crossing !== group ? .8 : 0);
        seek(next, axis, cost + weight, state);
      }
    }
    const bottom = layout.height - 24;
    return [start, {x: start.x, y: bottom}, {x: end.x, y: bottom}, end];
  };
}
