// SPDX-License-Identifier: GPL-2.0-only
// The page: top bar, guest tree, and the view for the current address.
//   #/              the host
//   #/host/network  the host's bridges
//   #/g/<guest>/<tab>   one guest: summary, network, hardware, logs
import { get, poll, post, setCsrf } from "./api.js";
import { clear, h } from "./dom.js";
import { stateClass } from "./format.js";
import { applyTheme, currentTheme, toggleTheme } from "./theme.js";
import { hostView } from "./views/host.js";
import { guestView, popView } from "./views/guest.js";
import { taskPanel } from "./views/tasks.js";

applyTheme();

export const store = {
  session: null,
  host: null,
  rows: [],
  tasks: [],
  listeners: new Set(),
  taskListeners: new Set(),
  setTasks(list) {
    this.tasks = list;
    for (const fn of this.taskListeners) fn(list);
  },
  upsertTask(t) {
    const rest = this.tasks.filter((x) => x.id !== t.id);
    this.setTasks([t, ...rest].sort((a, b) => b.id - a.id).slice(0, 200));
  },
  setRows(rows) {
    this.rows = rows;
    for (const fn of this.listeners) fn(rows);
  },
  row(name) { return this.rows.find((r) => r.name === name) || null; },
};

const main = document.getElementById("view");
const tree = document.getElementById("tree");
const search = document.getElementById("search");
let view = null;

function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map((p) => {
    try { return decodeURIComponent(p); } catch (e) { return ""; }
  });
  if (parts[0] === "g" && parts[1]) return { kind: "guest", name: parts[1], tab: parts[2] || "summary" };
  if (parts[0] === "pop" && parts[1]) return { kind: "pop", name: parts[1], tab: parts[2] || "console" };
  return { kind: "host", tab: parts[0] === "host" && parts[1] ? parts[1] : "summary" };
}

export function guestHref(name, tab = "summary") {
  return `#/g/${encodeURIComponent(name)}/${tab}`;
}

function renderTree() {
  const r = route();
  const q = search.value.trim().toLowerCase();
  const items = [h("a", { class: `host${r.kind === "host" ? " current" : ""}`, href: "#/",
    text: `▾ ${store.host ? store.host.hostname : "host"}` })];
  for (const g of store.rows) {
    if (q && !`${g.name} ${g.os} ${g.node}`.toLowerCase().includes(q)) continue;
    items.push(h("a", { class: `guest${r.kind === "guest" && r.name === g.name ? " current" : ""}`,
      href: guestHref(g.name, r.kind === "guest" ? r.tab : "summary") },
    h("span", { class: `dot ${stateClass(g.state)}`, "aria-hidden": "true" }),
    h("span", { text: g.name }),
    h("span", { class: "state", text: g.locked ? `${g.state} · locked` : g.state })));
  }
  clear(tree).append(...items);
}

function show() {
  const r = route();
  if (view && view.close) view.close();
  // A popped-out console or display: that screen alone, nothing around it.
  document.body.classList.toggle("popout", r.kind === "pop");
  if (r.kind === "pop") {
    document.title = `${r.name} · ${r.tab}`;
    view = popView(r.name, r.tab, store);
  } else {
    document.title = `emuctl · ${store.host ? store.host.hostname : ""}`;
    view = r.kind === "guest" ? guestView(r.name, r.tab, store) : hostView(r.tab, store);
  }
  clear(main).append(view.el);
  renderTree();
}

let events = null;

function listen() {
  const es = new EventSource("/api/events");
  events = es;
  es.addEventListener("guests", (ev) => {
    store.setRows(JSON.parse(ev.data));
  });
  es.addEventListener("tasks", (ev) => store.setTasks(JSON.parse(ev.data)));
  es.addEventListener("task", (ev) => store.upsertTask(JSON.parse(ev.data)));
  es.onerror = async () => {
    // The stream ends on logout, an expired session or a daemon restart.
    // EventSource retries by itself; check whether we are still logged in.
    try { await poll("/api/session"); } catch (e) { es.close(); }
  };
}

async function boot() {
  store.session = await get("/api/session");
  setCsrf(store.session.csrf);
  document.getElementById("user").textContent = store.session.user;
  store.host = await get("/api/host");
  document.getElementById("where").textContent = `${store.host.hostname} · ${store.host.version}`;
  document.title = `emuctl · ${store.host.hostname}`;
  store.setRows(await get("/api/guests"));
  store.setTasks(await get("/api/tasks"));
  document.getElementById("tasks").append(taskPanel(store));
  store.listeners.add(renderTree);
  window.addEventListener("hashchange", show);
  search.addEventListener("input", renderTree);
  const theme = document.getElementById("theme");
  theme.textContent = currentTheme() === "dark" ? "Light" : "Dark";
  theme.addEventListener("click", () => {
    theme.textContent = toggleTheme() === "dark" ? "Light" : "Dark";
  });
  document.getElementById("logout").addEventListener("click", async () => {
    if (events) events.close();
    try { await post("/api/logout"); } finally { location.replace("/login"); }
  });
  show();
  listen();
}

boot().catch((e) => {
  clear(main).append(h("div", { class: "content" },
    h("div", { class: "error-banner", text: `Could not load the page: ${e.message}` })));
});
