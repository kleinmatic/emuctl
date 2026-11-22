// SPDX-License-Identifier: GPL-2.0-only
// Light, dark, or the system's choice. Remembered in this browser only.

const KEY = "emuctl-theme";

function stored() {
  try { return localStorage.getItem(KEY); } catch (e) { return null; }
}

export function applyTheme(theme = stored()) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

export function currentTheme() {
  const t = document.documentElement.dataset.theme;
  if (t) return t;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  try { localStorage.setItem(KEY, next); } catch (e) { /* this browser only, then */ }
  applyTheme(next);
  return next;
}
