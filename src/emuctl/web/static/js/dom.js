// SPDX-License-Identifier: GPL-2.0-only
// Building the page without innerHTML: everything from the server is text.

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const c of children) if (c) el.append(c);
  return el;
}

export function kv(pairs) {
  const box = h("div", { class: "kv" });
  for (const [k, v] of pairs) {
    if (v === null || v === undefined) continue;
    box.append(h("span", { class: "k", text: k }), h("span", { class: "v" }, v));
  }
  return box;
}

// A note laid over a screen: a stopped guest's last output, or a display.
export function overlay(text) {
  return h("div", { class: "screen-overlay" }, h("div", { class: "screen-note", text }));
}
