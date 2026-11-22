// SPDX-License-Identifier: GPL-2.0-only
// Pure formatting and chart maths. Tested with `node --test tests/js`.

export const DASH = "—";

export function bytes(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return DASH;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  if (i === 0) return `${Math.round(v)} B`;
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function rate(n) {
  return n === null || n === undefined ? DASH : `${bytes(n)}/s`;
}

export function pct(n) {
  return n === null || n === undefined ? DASH : `${n.toFixed(1)}%`;
}

// The same short form `emuctl list` uses: 2d04h, 1:12:40, 4:18.
export function uptime(s) {
  if (s === null || s === undefined) return DASH;
  s = Math.floor(s);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const two = (x) => String(x).padStart(2, "0");
  if (d) return `${d}d${two(h)}h`;
  return h ? `${h}:${two(m)}:${two(sec)}` : `${m}:${two(sec)}`;
}

export function longUptime(s) {
  if (s === null || s === undefined) return DASH;
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  return m ? `${m} min` : `${Math.floor(s)} s`;
}

export function clock(epochSeconds) {
  const d = new Date(epochSeconds * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export const LIVE = ["starting", "booting", "stuck", "ready", "degraded", "halted", "stopping",
  "orphaned"];

export function isUp(state) {
  return LIVE.includes(state);
}

export function stateClass(state) {
  return `st-${/^[a-z]+$/.test(state) ? state : "error"}`;
}

export function last(values) {
  for (let i = values.length - 1; i >= 0; i--) {
    if (values[i] !== null && values[i] !== undefined) return values[i];
  }
  return null;
}

export function maxOf(...lists) {
  let m = 0;
  for (const l of lists) for (const v of l) if (v !== null && v !== undefined && v > m) m = v;
  return m;
}

// A round number at or above `m`, for the top of a graph.
export function niceMax(m, floor = 1) {
  if (!(m > 0)) return floor;
  const p = 10 ** Math.floor(Math.log10(m));
  for (const s of [1, 2, 2.5, 5, 10]) if (s * p >= m) return Math.max(s * p, floor);
  return Math.max(10 * p, floor);
}

// SVG polyline point lists for `values` drawn into a w x h box, one list
// per unbroken run (a null is a gap: the guest was down or just restarted).
export function segments(values, max, w = 300, h = 80) {
  const out = [];
  let cur = [];
  const n = values.length;
  values.forEach((v, i) => {
    if (v === null || v === undefined) {
      if (cur.length) out.push(cur);
      cur = [];
      return;
    }
    const x = n > 1 ? (i * w) / (n - 1) : w;
    const y = h - (Math.min(v, max) / max) * (h - 4);
    cur.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  if (cur.length) out.push(cur);
  return out;
}
