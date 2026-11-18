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

const VALUE_STUB = 16;

function localValueLabel(text) {
  return text.length <= 24 ? text : text.slice(0, 14) + "…" + text.slice(-9);
}

const BOX_KINDS = ["instance", "bank", "process", "binding"];
const DECISION_ROW = 19;

function concatenationLabel(fields) {
  return "{" + fields.map(field => field.range).join(" ") + "}";
}

function layoutSchematic(graph) {
  const nodes = graph.nodes.filter(node => node.kind !== "literal");
  const incoming = graph.nodes.map(() => []);
  const outgoing = graph.nodes.map(() => []);
  const readingOrder = new Map();
  const levels = new Map();

  for (const edge of graph.edges) {
    if (edge.from.kind === "literal") {
      const inset = VALUE_STUB + 8 + Math.ceil(localValueLabel(edge.from.label).length * 7);
      edge.to.valueInset = Math.max(edge.to.valueInset || 0, inset);
    } else {
      incoming[edge.to.id].push(edge);
      outgoing[edge.from.id].push(edge);
    }
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
    const predecessors = incoming[node.id].filter(edge => !feedback.has(edge));
    const level = Math.max(0, ...predecessors.map(edge => levels.get(edge.from) + 1));
    levels.set(node, level);
  }
  const inputs = [];
  const outputs = [];
  const ranked = [];
  const lastLevel = Math.max(...levels.values());
  const step = Math.max(1, Math.ceil((lastLevel + 1) / 12));
  for (const node of nodes) {
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
      }
      node.width = ceilTile(Math.max(132, rails + bodyWidth));
      node.height = ceilTile(Math.max(72, bodyHeight, 48 + Math.max(node.inputs.size, node.outputs.size) * 24));
    } else if (node.kind === "bitfields") {
      node.width = ceilTile(Math.max(84, concatenationLabel(node.fields).length * 7 + 16));
      node.height = ceilTile(48 + node.fields.length * TILE);
    } else if (["net", "tap"].includes(node.kind)) {
      node.width = ceilTile(Math.max(48, Math.min(27, node.label.length) * 7 + 24,
        node.caption ? Math.min(27, node.caption.length) * 6 + 20 : 0));
      node.height = node.caption ? 48 : 24;
    } else {
      node.width = 72;
      const ports = node.kind === "expression" ? (node.inputs.size - 1) * 24 + 48 : 72;
      node.height = Math.max(72, ports);
    }
  }

  const gap = nodes.length > 45 ? 120 : 84;
  const isBoundary = column => column === inputs || column === outputs;
  const rowGap = column => isBoundary(column) ? 24 : 36;
  const packedHeight = (column, spacing = rowGap(column)) =>
    column.reduce((total, node) => total + node.height + spacing, 0);

  const root = {columns: new Map(), nodes: [], children: []};
  const bands = new Map((graph.regions || []).map(region =>
    [region.key, {region, columns: new Map(), nodes: [], children: []}]));
  for (const band of bands.values()) {
    const parent = bands.get(band.region.parent) || root;
    parent.children.push(band);
  }

  let x = inputs.length ? 36 : 60;
  for (const [index, column] of columns.entries()) {
    const width = Math.max(...column.map(node => node.width));
    const inset = Math.max(...column.map(node => node.valueInset || 0));
    if (inset) x += Math.max(0, ceilTile(inset + 36) - (index ? gap : x));
    for (const node of column) {
      if (isBoundary(column)) node.width = width;
      node.x = x;
      node.leftChannel = x - 24;
      node.rightChannel = x + width + 24;
      if (isBoundary(column)) continue;
      const band = bands.get(node.region?.key) || root;
      if (!band.columns.has(column)) band.columns.set(column, []);
      band.columns.get(column).push(node);
      band.nodes.push(node);
    }
    x += width + gap;
  }

  function measure(band, emptyLeft) {
    const rects = band.children.map(child => measure(child, emptyLeft + 12));
    band.selectors = band.region?.kind === "case" ? band.nodes.filter(node => node.kind === "mux") : [];
    const shared = new Set(band.selectors);
    const elements = band.nodes.filter(node => !shared.has(node));
    band.columns = new Map([...band.columns].map(([column, members]) =>
      [column, members.filter(node => !shared.has(node))]));
    const starts = elements.map(node => node.x - (node.valueInset || 0));
    const ends = elements.map(node => node.x + node.width);
    const occupied = rects.filter(rect => rect.hasContent);
    const contentLeft = Math.min(...starts, ...occupied.map(rect => rect.left));
    const contentRight = Math.max(...ends, ...occupied.map(rect => rect.right));
    const reference = Number.isFinite(contentLeft) ? contentLeft : emptyLeft;
    band.left = Math.max(24, reference - 24);
    const titleWidth = band.region ? Math.min(band.region.label.length, 51) * 8 + 28 : 0;
    band.right = Math.max(contentRight + 24, band.left + Math.max(158, titleWidth));
    // Empty arms align with the real circuitry in their enclosing case.
    for (const child of band.children) {
      if (!child.hasContent) {
        moveEmpty(child, band.left + 18 - child.left);
        band.right = Math.max(band.right, child.right + 24);
      }
    }
    if (band.selectors.length) {
      const inset = Math.max(0, ...band.selectors.map(node => node.valueInset || 0));
      const side = ceilTile(band.right + Math.max(84, inset + 36));
      let right = side;
      for (const node of band.selectors) {
        node.x = side;
        node.leftChannel = side - 24;
        node.rightChannel = side + node.width + 24;
        right = Math.max(right, side + node.width + 24);
      }
      band.right = right;
    }
    band.hasContent = band.nodes.length > 0 || occupied.length > 0;
    band.direct = Math.max(0, ...[...band.columns.values()].map(column => packedHeight(column, 24)));
    band.header = band.region ? 36 : 0;
    const empty = band.region && !band.direct && !band.children.length && !band.selectors.length ? 30 : 0;
    const stacked = band.direct + empty + band.children.reduce((height, child) => height + child.height + 12, 0);
    band.selectionHeight = packedHeight(band.selectors, 24);
    band.contentHeight = Math.max(stacked, band.selectionHeight);
    band.height = band.header + band.contentHeight + (band.region ? 24 : 0);
    return band;
  }

  function moveEmpty(band, dx) {
    band.left += dx;
    band.right += dx;
    for (const child of band.children) moveEmpty(child, dx);
  }

  const firstInside = columns.find(column => !isBoundary(column))?.[0].x || 96;
  measure(root, firstInside);
  const bodyHeight = Math.max(root.height, packedHeight(inputs), packedHeight(outputs));
  const regions = [];
  function placeBand(band, top) {
    if (band.region) {
      regions.push({...band.region, hasContent: band.hasContent, x: band.left, y: top, width: band.right - band.left, height: band.height});
    }
    let y = top + band.header;
    for (const members of band.columns.values()) {
      let cursor = y + snap((band.direct - packedHeight(members, 24)) / 2);
      for (const node of members) {
        node.y = cursor + node.height / 2;
        cursor += node.height + 24;
      }
    }
    y += band.direct;
    for (const child of band.children) {
      placeBand(child, y);
      y += child.height + 12;
    }
    // Keep the shared priority muxes beside the arms they select from.
    const preferred = new Map(band.selectors.map(node => {
      const positions = incoming[node.id].map(edge => edge.from.y).filter(Number.isFinite);
      const middle = positions.length ? positions.reduce((sum, at) => sum + at, 0) / positions.length : y;
      return [node, middle];
    }));
    band.selectors.sort((a, b) => preferred.get(a) - preferred.get(b));
    let next = top + band.header;
    let remaining = band.selectionHeight;
    for (const node of band.selectors) {
      const last = top + band.header + band.contentHeight - remaining;
      const at = Math.max(next, Math.min(last, snap(preferred.get(node) - node.height / 2)));
      node.y = at + node.height / 2;
      remaining -= node.height + 24;
      next = at + node.height + 24;
    }
  }
  placeBand(root, 60 + snap((bodyHeight - root.height) / 2));
  // Align the I/O rails to nearby circuitry without reordering module ports.
  for (const column of [inputs, outputs]) {
    let remaining = packedHeight(column);
    const isInput = column === inputs;
    const firstRegion = isInput && regions[0];
    let start = firstRegion ? Math.min(snap(firstRegion.y + 36), 60 + bodyHeight - remaining) : 60;
    for (const node of column) {
      const peers = (isInput ? outgoing : incoming)[node.id]
        .map(edge => isInput ? edge.to : edge.from).filter(peer => !portSide(peer));
      const positions = peers.map(peer => peer.y).sort((a, b) => a - b);
      const preferred = positions.length && !firstRegion
        ? snap(positions[Math.floor(positions.length / 2)] - node.height / 2) : start;
      const top = Math.max(start, Math.min(preferred, 60 + bodyHeight - remaining));
      node.y = top + node.height / 2;
      const occupied = node.height + rowGap(column);
      start = top + occupied;
      remaining -= occupied;
    }
  }
  const rightmost = Math.max(0, ...regions.map(region => region.x + region.width));
  if (outputs.length && outputs[0].x < rightmost + 36) {
    const offset = ceilTile(rightmost + 36 - outputs[0].x);
    for (const node of outputs) {
      node.x += offset;
      node.leftChannel += offset;
      node.rightChannel += offset;
    }
    x += offset;
  }
  const width = Math.max(x - gap + (outputs.length ? 36 : 60), rightmost + 24);
  return {width, height: bodyHeight + 108, regions};
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
    if (node.kind === "literal") continue;
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
          (crowded ? 42 : 0) + (crossing && crossing !== group ? 1.2 : 0);
        seek(next, axis, cost + weight, state);
      }
    }
    const bottom = layout.height - 24;
    return [start, {x: start.x, y: bottom}, {x: end.x, y: bottom}, end];
  };
}
