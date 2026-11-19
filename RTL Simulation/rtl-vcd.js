"use strict";

function readVcd(text) {
  const tokens = text.trim().split(/\s+/);
  const scope = [];
  const signals = new Map();
  let unit = "";
  let time = 0;
  let defined = false;
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (!defined) {
      const end = tokens.indexOf("$end", i + 1);
      if (end < 0) break;
      if (token === "$scope") scope.push(tokens[i + 2]);
      else if (token === "$upscope") scope.pop();
      else if (token === "$timescale") unit = tokens.slice(i + 1, end).join("");
      else if (token === "$var" && signals.size < 300) {
        const [type, size, id, name] = tokens.slice(i + 1, i + 5);
        const width = Number(size);
        if (type !== "real" && width > 0 && width <= 4096 && id && name && !signals.has(id)) {
          signals.set(id, {id, name: [...scope, name].join("."), width, values: []});
        }
      } else if (token === "$enddefinitions") defined = true;
      i = end;
      continue;
    }
    if (token.startsWith("#")) {
      const next = Number(token.slice(1));
      if (Number.isSafeInteger(next) && next >= time) time = next;
      continue;
    }
    const first = token[0]?.toLowerCase();
    const vector = first === "b" || first === "r";
    if (!vector && !["0", "1", "x", "z"].includes(first)) continue;
    const id = vector ? tokens[++i] : token.slice(1);
    const value = first === "r" ? "" : vector ? token.slice(1).toLowerCase() : first;
    const signal = signals.get(id);
    if (!signal || !/^[01xz]+$/.test(value)) continue;
    const padded = value.padStart(signal.width, /^[xz]/.test(value) ? value[0] : "0");
    const previous = signal.values.at(-1);
    if (previous?.time === time) previous.value = padded;
    else if (previous?.value !== padded) signal.values.push({time, value: padded});
  }
  return {unit: unit || "tick", time, signals: [...signals.values()].filter(signal => signal.values.length)};
}

function valueAt(signal, time) {
  let lo = 0;
  let hi = signal.values.length;
  while (lo < hi) {
    const middle = (lo + hi) >>> 1;
    if (signal.values[middle].time <= time) lo = middle + 1;
    else hi = middle;
  }
  return lo ? signal.values[lo - 1].value : "x".repeat(signal.width);
}
