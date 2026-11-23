// SPDX-License-Identifier: GPL-2.0-only
// The console's look: a phosphor (amber, green or white) in the VT font, or
// the plain look in IBM Plex Mono. Remembered in this browser only.

const KEY = "emuctl-phosphor";
export const COLS = 80;
export const ROWS = 24;

// fg: normal text; bright: bold and bright colours; dim: "bright black"; bg: the glass.
export const PHOSPHORS = {
  amber: { label: "Amber", fg: "#ffb000", bright: "#ffd27a", dim: "#a06e00", bg: "#140c02" },
  green: { label: "Green", fg: "#33ff66", bright: "#a6ffbb", dim: "#1f9c40", bg: "#04110a" },
  white: { label: "White", fg: "#d9dcd6", bright: "#ffffff", dim: "#8a8d88", bg: "#0e0f10" },
  plain: { label: "Plain", fg: "#d6dde6", bright: "#ffffff", dim: "#6b7280", bg: "#0b0d10" },
};

export function storedPhosphor() {
  let p = null;
  try { p = localStorage.getItem(KEY); } catch (e) { /* none, then */ }
  return Object.hasOwn(PHOSPHORS, p) ? p : "amber";
}

export function storePhosphor(p) {
  try { localStorage.setItem(KEY, p); } catch (e) { /* this page only, then */ }
}

// An xterm.js theme. A phosphor screen had one colour, so every ANSI colour
// becomes the phosphor, the bright ones its bright shade; the plain look
// keeps the colours.
export function xtermTheme(name) {
  const p = PHOSPHORS[name] || PHOSPHORS.amber;
  const base = { background: p.bg, foreground: p.fg, cursor: p.fg, cursorAccent: p.bg,
    selectionBackground: `${p.dim}88` };
  if (name === "plain") return base;
  const colours = ["red", "green", "yellow", "blue", "magenta", "cyan", "white"];
  const out = { ...base, black: p.bg, brightBlack: p.dim };
  for (const c of colours) {
    out[c] = p.fg;
    out[`bright${c[0].toUpperCase()}${c.slice(1)}`] = p.bright;
  }
  return out;
}

// The font and size for a screen of COLS x ROWS in `width` x `height`
// pixels. The VT font's cell is 0.4 x 1 of its size, so at multiples of 5
// every cell is a whole number of pixels. Never below 15: a narrow window
// scrolls sideways instead.
export function fontFor(name, width, height) {
  if (name === "plain") {
    const size = Math.max(11, Math.min(15, Math.floor(width / (COLS * 0.6)),
      Math.floor(height / (ROWS * 1.2))));
    return { family: '"IBM Plex Mono", monospace', size, lineHeight: 1.2 };
  }
  let size = 15;
  for (const s of [15, 20, 25, 30]) if (s * 0.4 * COLS <= width && s * ROWS <= height) size = s;
  return { family: '"emuctl VT", monospace', size, lineHeight: 1 };
}
