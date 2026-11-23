// SPDX-License-Identifier: GPL-2.0-only
// The host: summary cards, every guest, the bridges and the warnings.
import { get, poll } from "../api.js";
import { clear, h } from "../dom.js";
import { bytes, isUp, longUptime, pct, stateClass, uptime } from "../format.js";
import { taskTable } from "./tasks.js";

const NET_EVERY = 30000;      // the network report runs ip, bridge and ethtool; not every tick

export function stateBadge(row) {
  return h("span", {},
    h("span", { class: `st ${stateClass(row.state)}` },
      h("span", { class: "dot", "aria-hidden": "true" }), row.state),
    row.detail ? h("span", { class: "detail", text: row.detail }) : null,
    row.locked ? h("span", { class: "lock", title: `Starts are refused: ${row.locked}`, text: "locked" }) : null);
}

function stat(label, value, fraction, note) {
  const fill = h("div");
  fill.style.width = `${Math.max(0, Math.min(1, fraction || 0)) * 100}%`;
  return h("section", { class: "card stat" },
    h("div", { class: "label", text: label }), h("div", { class: "value", text: value }),
    h("div", { class: "meter" }, fill), h("div", { class: "note", text: note }));
}

function guestLinks(net) {
  // guest -> ["tap1 → br0"], from the host network report. A Map, since a
  // guest may be called "constructor".
  const out = new Map();
  const add = (g, text) => { if (!out.has(g)) out.set(g, []); out.get(g).push(text); };
  for (const br of (net && net.bridges) || []) {
    for (const m of br.members) for (const g of m.guests) add(g, `${m.name} → ${br.name}`);
  }
  for (const [g, rep] of Object.entries((net && net.guests) || {})) {
    for (const n of rep.nics) if (!n.bridge) add(g, n.host_if);
  }
  return out;
}

export function bridgeDiagram(net) {
  if (!net) return h("div", { class: "muted", text: "Loading…" });
  if (!net.bridges.length) return h("div", { class: "muted", text: "No bridges on this host." });
  return h("div", { class: "bridges" }, net.bridges.map((br) => {
    const lan = br.uplinks.length > 0;
    const label = [br.name, lan ? "LAN" : "host-only", ...br.addresses.filter((a) => !a.includes(":"))].join(" · ");
    return h("div", { class: "line" },
      h("span", { class: lan ? "br" : "br local", text: label }),
      h("span", { class: "muted", text: lan ? `uplink ${br.uplinks.join(", ")} ─┬─` : "no uplink ─┬─" }),
      br.members.filter((m) => !br.uplinks.includes(m.name)).map((m) =>
        h("span", { class: m.up ? "member" : "member down",
          text: `${m.name} ${m.guests.join(", ") || "-"}${m.up ? "" : " (down)"}` })));
  }));
}

export function warningList(warnings) {
  if (!warnings || !warnings.length) return h("div", { class: "muted", text: "None." });
  return h("ul", { class: "warnings" }, warnings.map((w) => h("li", {}, h("span", { text: w }))));
}

export function hostView(tab, store) {
  let net = null;
  let netTimer = null;
  const body = h("div", { class: "content" });
  const host = store.host;
  const el = h("div", {},
    h("div", { class: "titlebar" }, h("h1", { text: `Host ${host.hostname}` }),
      h("span", { class: "muted", text: `${host.cpus} CPUs · ${bytes(host.mem_total_bytes)} · up ${longUptime(host.uptime)}` })),
    h("div", { class: "tabs", role: "tablist" },
      [["summary", "Summary", "#/"], ["network", "Network", "#/host/network"],
        ["energy", "Energy", "#/host/energy"], ["tasks", "Tasks", "#/host/tasks"]].map(([id, label, href]) =>
        h("a", { class: "tab", role: "tab", href, "aria-selected": String(tab === id), text: label }))),
    body);

  function summary() {
    const rows = store.rows;
    const up = rows.filter((r) => isUp(r.state)).length;
    const odd = rows.filter((r) => ["stuck", "degraded", "crashed", "orphaned"].includes(r.state));
    const mem = host.mem_total_bytes && host.mem_available_bytes !== null
      ? host.mem_total_bytes - host.mem_available_bytes : null;
    const guestMem = rows.reduce((a, r) => a + (r.mem_bytes || 0), 0);
    const cards = [
      stat("Load", host.load ? host.load[0].toFixed(2) : "—", host.load ? host.load[0] / host.cpus : 0,
        host.load ? `${host.load.join(" · ")} · ${host.cpus} CPUs` : ""),
      stat("Memory", `${bytes(mem)} / ${bytes(host.mem_total_bytes)}`, mem / host.mem_total_bytes,
        `guests use ${bytes(guestMem)}`),
      ...host.filesystems.map((fs) => stat("Guest storage", `${bytes(fs.free_bytes)} free`,
        1 - fs.free_bytes / fs.total_bytes, `${fs.mount} · ${bytes(fs.total_bytes)}`)),
      stat("Guests", `${up} of ${rows.length} up`, rows.length ? up / rows.length : 0,
        odd.length ? odd.map((r) => `${r.name} ${r.state}`).join(" · ") : "nothing needs attention"),
    ];
    const links = guestLinks(net);
    const table = h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["Guest", "OS", "Emulator", "State", "Uptime"].map((t) => h("th", { text: t })),
        h("th", { class: "num", text: "CPU" }), h("th", { class: "num", text: "Memory" }), h("th", { text: "Network" }))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", {}, h("a", { href: `#/g/${encodeURIComponent(r.name)}/summary`, text: r.name })),
        h("td", { text: r.os }), h("td", { class: "muted", text: r.emulator }),
        h("td", {}, stateBadge(r)), h("td", { class: "mono", text: uptime(r.uptime) }),
        h("td", { class: "num", text: pct(r.cpu_pct) }), h("td", { class: "num", text: bytes(r.mem_bytes) }),
        h("td", { class: "mono muted", text: (links.get(r.name) || []).join(", ") || "—" })))));
    return [
      h("div", { class: "stats" }, cards),
      h("section", { class: "card flush" }, h("h2", { text: "Guests" }),
        rows.length ? h("div", { class: "scroll" }, table) : h("div", { class: "empty", text: "No guests configured." })),
      h("div", { class: "row" },
        h("section", { class: "card wide" }, h("h2", { text: "Bridges" }), bridgeDiagram(net)),
        h("section", { class: "card narrow" },
          h("h2", { text: net ? `Warnings (${net.warnings.length})` : "Warnings" }), warningList(net && net.warnings))),
    ];
  }

  function network() {
    if (!net) return [h("div", { class: "muted", text: "Loading…" })];
    return [
      ...net.bridges.map((br) => h("section", { class: "card flush" },
        h("h2", {}, br.name, " ", h("span", { class: "muted",
          text: `${br.uplinks.length ? `LAN via ${br.uplinks.join(", ")}` : "host-only"} · ${br.addresses.join(", ") || "no address"}` })),
        h("div", { class: "scroll" }, h("table", { class: "data" },
          h("thead", {}, h("tr", {}, ["Member", "Kind", "Link", "Guests"].map((t) => h("th", { text: t })))),
          h("tbody", {}, br.members.map((m) => h("tr", {},
            h("td", { class: "mono", text: m.name }), h("td", { text: m.kind }),
            h("td", { text: m.up ? "up" : "down" }),
            h("td", {}, m.guests.length
              ? m.guests.map((g, i) => [i ? ", " : "", h("a", { href: `#/g/${encodeURIComponent(g)}/network`, text: g })])
              : (br.uplinks.includes(m.name) ? "uplink" : "—"))))))))),
      net.bridges.length ? null : h("div", { class: "card muted", text: "No bridges on this host." }),
      h("section", { class: "card" }, h("h2", { text: `Warnings (${net.warnings.length})` }), warningList(net.warnings)),
    ];
  }

  let power = null;
  function energy() {
    if (!power) return [h("div", { class: "muted", text: "Loading…" })];
    const rows = [...power.guests].sort((a, b) => (b.cpu_hours_24h - a.cpu_hours_24h) || a.name.localeCompare(b.name));
    const now = rows.reduce((a, r) => a + (r.cpu_pct || 0), 0);
    const today = rows.reduce((a, r) => a + r.cpu_hours_24h, 0);
    const up = rows.filter((r) => isUp(r.state));
    const hours = (m) => (m ? `${(m / 60).toFixed(1)} h` : "—");
    return [
      h("div", { class: "stats" },
        stat("Guests use now", `${(now / 100).toFixed(2)} CPUs`, now / 100 / power.cpus,
          `${pct(now / power.cpus)} of ${power.cpus} host CPUs`),
        stat("Over the last day", `${today.toFixed(1)} CPU-hours`, today / (24 * power.cpus),
          `${pct((today / 24 / power.cpus) * 100)} of the host's CPU, on average`),
        stat("Up now", `${up.length} of ${rows.length}`, rows.length ? up.length / rows.length : 0,
          up.length ? up.map((r) => r.name).join(" · ") : "nothing is running")),
      h("section", { class: "card flush" }, h("h2", {}, "Guests ",
        h("span", { class: "muted", text: "CPU is per cent of one host CPU; averages count only the time a guest was up" })),
      h("div", { class: "scroll" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, h("th", { text: "Guest" }), h("th", { text: "State" }), h("th", { text: "Up for" }),
          ["CPU now", "Hour average", "Day average", "Up in the last day", "CPU-hours, last day"].map((t) => h("th", { class: "num", text: t })))),
        h("tbody", {}, rows.map((r) => h("tr", {},
          h("td", {}, h("a", { href: `#/g/${encodeURIComponent(r.name)}/summary`, text: r.name })),
          h("td", {}, stateBadge(r)), h("td", { text: isUp(r.state) ? longUptime(r.uptime) : "—" }),
          h("td", { class: "num", text: pct(r.cpu_pct) }), h("td", { class: "num", text: pct(r.cpu_1h) }),
          h("td", { class: "num", text: pct(r.cpu_24h) }), h("td", { class: "num", text: hours(r.up_minutes_24h) }),
          h("td", { class: "num", text: r.cpu_hours_24h ? r.cpu_hours_24h.toFixed(2) : "—" }))))))),
    ];
  }

  async function loadEnergy() {
    try { power = await poll("/api/energy"); } catch (e) { /* keep the last */ }
    if (tab === "energy") render();
  }

  function tasks() {
    return [h("section", { class: "card flush" }, h("h2", {}, "Tasks ",
      h("span", { class: "muted", text: "the last 200; every one is also in the daemon's task log" })),
    taskTable(store.tasks))];
  }

  function render() {
    const views = { network, tasks, energy };
    clear(body).append(...(views[tab] || summary)());
  }

  async function loadNet() {
    try { net = await poll("/api/host/network"); } catch (e) { net = { bridges: [], guests: {}, warnings: [`could not read the network: ${e.message}`] }; }
    render();
  }

  async function refreshHost() {
    try { Object.assign(host, await poll("/api/host")); } catch (e) { /* keep the last */ }
  }

  const onRows = async () => {
    if (tab === "summary") { await refreshHost(); render(); }
    if (tab === "energy") loadEnergy();
  };
  const onTasks = () => { if (tab === "tasks") render(); };
  store.listeners.add(onRows);
  store.taskListeners.add(onTasks);
  render();
  if (tab === "energy") loadEnergy();
  else if (tab !== "tasks") {
    loadNet();
    netTimer = setInterval(loadNet, NET_EVERY);
  }
  return { el, close() { store.listeners.delete(onRows); store.taskListeners.delete(onTasks); clearInterval(netTimer); } };
}
