// SPDX-License-Identifier: GPL-2.0-only
// One guest: summary with graphs, network, hardware, logs.
import { get, guestPath, poll } from "../api.js";
import { chart } from "../chart.js";
import { clear, h, kv } from "../dom.js";
import { DASH, bytes, isUp, last, longUptime, pct, rate } from "../format.js";
import { actionButtons } from "./actions.js";
import { consoleView } from "./console.js";
import { displayView } from "./display.js";
import { stateBadge, warningList } from "./host.js";

const TABS = [["summary", "Summary"], ["console", "Console"], ["display", "Display"],
  ["network", "Network"], ["hardware", "Hardware"], ["logs", "Logs"]];

function nicLine(n) {
  if (!n.exists) return `${n.host_if} · does not exist`;
  const bits = [n.host_if, n.kind, n.up ? "up" : "down"];
  if (n.via) bits.push(`via ${n.via}`);
  return bits.join(" · ");
}

function bridgeLine(n) {
  if (!n.bridge) return n.exists ? "not in any bridge" : DASH;
  return `${n.bridge} · ${n.bridge_uplink ? "LAN" : "host-only"}`;
}

function macLine(n) {
  if (!n.mac) return "not in the config";
  const seen = n.mac_seen === true ? "seen on the bridge" : n.mac_seen === false ? "not seen" : "not checked";
  return `${n.mac} · ${seen}`;
}

function hwDisks(disks) {
  return disks.map((d) => [d.unit, d.model, d.file, d.readonly ? "read-only" : null].filter(Boolean).join(" ")).join(" · ");
}

export function guestView(name, tab, store) {
  if (!TABS.some(([id]) => id === tab)) tab = "summary";
  const head = h("div", { class: "titlebar" });
  const body = h("div", { class: "content" });
  const tabs = TABS.map(([id, label]) =>
    h("a", { class: "tab", role: "tab", href: `#/g/${encodeURIComponent(name)}/${id}`,
      "aria-selected": String(tab === id), text: label, dataset: { tab: id } }));
  const el = h("div", {}, head, h("div", { class: "tabs", role: "tablist" }, tabs), body);
  let detail = null;
  let closed = false;
  let logState = { source: "console", lines: 200 };

  function renderHead() {
    const row = store.row(name) || detail || { name, state: "error", detail: "no such guest" };
    const sub = [row.os, row.emulator, row.node && row.node !== name ? `node ${row.node}` : null].filter(Boolean).join(" · ");
    clear(head).append(h("h1", { text: name }), stateBadge(row), h("span", { class: "muted", text: sub }),
      h("div", { class: "actions" }, actionButtons(name, detail, refreshDetail)));
    // The Display tab only for a guest whose conf names a display.
    const shown = tabs.find((t) => t.dataset.tab === "display");
    if (detail) shown.classList.toggle("hidden", !detail.display);
  }

  async function refreshDetail() {
    try { detail = await poll(guestPath(name)); } catch (e) { /* keep the last */ }
    if (!closed) renderHead();
  }

  // Network and hardware change rarely and cost more to read: once per visit.
  let once = null;
  async function summary(refresh = false) {
    const fetch = refresh ? poll : get;
    if (!once) {
      once = Promise.all([get(guestPath(name, "/network")).catch(() => null),
        get(guestPath(name, "/hardware")).catch(() => null)]);
    }
    const [[net, hw], d, u, logs] = await Promise.all([once,
      fetch(guestPath(name)), fetch(guestPath(name, "/usage")),
      fetch(guestPath(name, "/log?source=console&lines=12")).catch(() => null)]);
    detail = d;
    renderHead();
    if (d.conf_error) return [h("div", { class: "error-banner", text: `The conf cannot be read: ${d.conf_error}` })];
    const up = isUp(d.state);
    const disk = d.disk_bytes !== null
      ? `${bytes(d.disk_bytes)} used of ${bytes(d.disk_nominal_bytes)} · ${d.disks.length} image${d.disks.length === 1 ? "" : "s"}`
      : (d.disks.length ? "cannot read the images" : "diskless");
    const status = h("section", { class: "card" }, h("h2", { text: "Status" }), kv([
      ["State", `${d.state}${d.detail ? ` · ${d.detail}` : ""}`],
      ["Locked", d.locked ? `${d.locked} (starts are refused)` : null],
      ["Uptime", up ? longUptime(d.uptime) : DASH],
      ["Stop", d.stop_route],
      ["Exclusive with", d.exclusive_with.length ? d.exclusive_with.join(", ") : null],
      ["Disks", disk],
      ["Kind", d.kind],
      ["Can start", up ? null : (d.can_start ? "yes" : d.start_refused)],
    ]));
    const cards = [status];
    cards.push(chart({ title: "CPU", t: u.t, series: [{ label: "CPU", values: u.cpu_pct }],
      fmt: (v) => `${Math.round(v)}%`, big: up ? pct(last(u.cpu_pct)) : DASH, note: "of one CPU", floor: 10 }));
    cards.push(chart({ title: "Memory", t: u.t, series: [{ label: "Memory", values: u.mem_bytes }],
      fmt: bytes, big: up ? bytes(d.mem_bytes) : DASH,
      note: d.mem_peak_bytes ? `peak ${bytes(d.mem_peak_bytes)}` : "", floor: 1024 * 1024 }));
    cards.push(chart({ title: "Disk I/O", t: u.t, series: [{ label: "read", values: u.io_read }, { label: "write", values: u.io_write }],
      fmt: rate, big: up ? `${rate(last(u.io_read))} · ${rate(last(u.io_write))}` : DASH, floor: 1024 }));
    const ifnames = Object.keys(u.nics);
    if (ifnames.length) {
      const sum = (key) => u.t.map((_, i) => {
        let s = null;
        for (const n of ifnames) { const v = u.nics[n][key][i]; if (v !== null) s = (s || 0) + v; }
        return s;
      });
      const rx = sum("rx"), tx = sum("tx");
      cards.push(chart({ title: "Network", t: u.t, series: [{ label: "in", values: rx }, { label: "out", values: tx }],
        fmt: rate, big: `↓ ${rate(last(rx))} ↑ ${rate(last(tx))}`, floor: 1024 }));
    }
    if (net && net.nics.length) {
      const n = net.nics[0];
      cards.push(h("section", { class: "card" }, h("h2", { text: "Network" }), kv([
        ["Interface", h("span", { class: "mono", text: nicLine(n) })],
        ["Bridge", h("span", { class: "mono", text: bridgeLine(n) })],
        ["Guest MAC", h("span", { class: "mono", text: macLine(n) })],
        ["Errors", n.errors === null ? null : `${n.errors} errors · ${n.dropped} dropped`],
        ["More", net.nics.length > 1 ? `${net.nics.length - 1} more on the Network tab` : null],
      ]), net.warnings.length ? h("div", { class: "muted", text: `${net.warnings.length} warning(s) on the Network tab` }) : null));
    }
    if (hw && (hw.machine || hw.cpu || hw.memory || hw.disks.length)) {
      const src = hw.sources.map((p) => p.split("/").pop()).join(", ");
      cards.push(h("section", { class: "card" },
        h("h2", {}, "Hardware ", src ? h("span", { class: "muted", text: `from ${src}` }) : null), kv([
          ["Machine", hw.machine], ["CPU", hw.cpu], ["Memory", hw.memory],
          ["Disks", hw.disks.length ? hwDisks(hw.disks) : null],
          ["NIC", hw.nics.length ? hw.nics.map((n) => [n.model, n.host_if ? `on ${n.host_if}` : null].filter(Boolean).join(" ")).join(" · ") : null],
        ])));
    }
    if (logs && !logs.missing) {
      cards.push(h("section", { class: "card" }, h("h2", { text: "Last console lines" }),
        h("pre", { class: "text", text: logs.text || "(empty)" })));
    }
    return [h("div", { class: "grid" }, cards)];
  }

  async function network() {
    const net = await get(guestPath(name, "/network"));
    if (!net.nics.length) {
      return [h("div", { class: "card muted", text: "No host interfaces known. Set nics in the conf, or config so emuctl can read the emulator's own file." }),
        h("section", { class: "card" }, h("h2", { text: "Warnings" }), warningList(net.warnings))];
    }
    return [
      h("section", { class: "card flush" }, h("h2", { text: "Interfaces" }), h("div", { class: "scroll" },
        h("table", { class: "data" },
          h("thead", {}, h("tr", {}, ["Host interface", "Kind", "Link", "Bridge", "Guest MAC", "Seen", "Model"].map((t) => h("th", { text: t })),
            h("th", { class: "num", text: "In" }), h("th", { class: "num", text: "Out" }), h("th", { class: "num", text: "Errors" }), h("th", { class: "num", text: "Dropped" }))),
          h("tbody", {}, net.nics.map((n) => h("tr", {},
            h("td", { class: "mono", text: n.host_if }), h("td", { text: n.kind || DASH }),
            h("td", { text: n.exists ? (n.up ? "up" : "down") : "missing" }),
            h("td", { class: "mono", text: n.bridge ? `${n.bridge}${n.via ? ` via ${n.via}` : ""} (${n.bridge_uplink ? "LAN" : "host-only"})` : DASH }),
            h("td", { class: "mono", text: n.mac || DASH }),
            h("td", { text: n.mac_seen === true ? "yes" : n.mac_seen === false ? "no" : "not checked" }),
            h("td", { text: n.model || DASH }),
            h("td", { class: "num", text: bytes(n.rx_bytes) }), h("td", { class: "num", text: bytes(n.tx_bytes) }),
            h("td", { class: "num", text: n.errors ?? DASH }), h("td", { class: "num", text: n.dropped ?? DASH }))))))),
      h("section", { class: "card" }, h("h2", { text: `Warnings (${net.warnings.length})` }), warningList(net.warnings)),
      h("div", { class: "muted", text: "In and out are counted from the guest's side, since each interface was created." }),
    ];
  }

  async function hardware() {
    const hw = await get(guestPath(name, "/hardware"));
    const out = [h("section", { class: "card" }, h("h2", { text: "Machine" }), kv([
      ["Machine", hw.machine || DASH], ["CPU", hw.cpu || DASH], ["Memory", hw.memory || DASH],
      ["Read from", hw.sources.length ? hw.sources.join(", ") : "nothing: set config in the conf, or hw.* lines"],
    ]))];
    if (hw.disks.length) {
      out.push(h("section", { class: "card flush" }, h("h2", { text: "Storage" }), h("div", { class: "scroll" },
        h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["Unit", "Model", "Kind", "File", "Mode"].map((t) => h("th", { text: t })))),
          h("tbody", {}, hw.disks.map((d) => h("tr", {}, h("td", { class: "mono", text: d.unit || DASH }),
            h("td", { text: d.model || DASH }), h("td", { text: d.kind || DASH }),
            h("td", { class: "mono", text: d.file || DASH }), h("td", { text: d.readonly ? "read-only" : "read-write" }))))))));
    }
    if (hw.nics.length) {
      out.push(h("section", { class: "card flush" }, h("h2", { text: "Network adapters" }), h("div", { class: "scroll" },
        h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["Unit", "Model", "MAC", "Host interface"].map((t) => h("th", { text: t })))),
          h("tbody", {}, hw.nics.map((n) => h("tr", {}, h("td", { class: "mono", text: n.unit || DASH }),
            h("td", { text: n.model || DASH }), h("td", { class: "mono", text: n.mac || DASH }),
            h("td", { class: "mono", text: n.host_if || DASH }))))))));
    }
    if (hw.other.length) out.push(h("section", { class: "card" }, h("h2", { text: "Other" }), h("ul", {}, hw.other.map((o) => h("li", { text: o })))));
    if (hw.problems && hw.problems.length) out.push(h("section", { class: "card" }, h("h2", { text: "Could not read" }), warningList(hw.problems)));
    for (const r of hw.raw) {
      out.push(h("details", { class: "card" }, h("summary", { text: `${r.path} (as written)` }),
        h("pre", { class: "log", text: r.text })));
    }
    return out;
  }

  async function logs() {
    const pre = h("pre", { class: "log", text: "Loading…" });
    const source = h("select", { "aria-label": "Log" },
      h("option", { value: "console", text: "Console log" }), h("option", { value: "supervisor", text: "Supervisor log" }));
    const lines = h("select", { "aria-label": "Lines" }, [100, 200, 1000, 5000].map((n) => h("option", { value: n, text: `last ${n} lines` })));
    source.value = logState.source;
    lines.value = String(logState.lines);
    async function load() {
      logState = { source: source.value, lines: Number(lines.value) };
      try {
        const r = await get(guestPath(name, `/log?source=${logState.source}&lines=${logState.lines}`));
        pre.textContent = r.missing ? "There is no console log yet." : (r.text || "(empty)");
        pre.scrollTop = pre.scrollHeight;
      } catch (e) { pre.textContent = e.message; }
    }
    source.addEventListener("change", load);
    lines.addEventListener("change", load);
    load();
    return [h("div", { class: "toolbar" }, source, lines, h("button", { class: "btn", type: "button", onclick: load, text: "Refresh" })), pre];
  }

  let term = null;
  async function console_() {
    if (!term) term = consoleView(name, store);
    return [term.el];
  }

  let screen = null;
  async function display() {
    if (!screen) screen = displayView(name, store);
    return [screen.el];
  }

  const loaders = { summary, console: console_, display, network, hardware, logs };
  let busy = false;
  async function render(refresh = false) {
    if (busy) return;
    busy = true;
    try {
      const parts = await loaders[tab](refresh);
      if (!closed) clear(body).append(...parts);
    } catch (e) {
      if (!closed) clear(body).append(h("div", { class: "error-banner", text: e.status === 404 ? `No guest called ${name}.` : e.message }));
    } finally { busy = false; }
  }

  const onRows = () => { if (tab === "summary") render(true); else refreshDetail(); };
  store.listeners.add(onRows);
  renderHead();
  if (tab !== "summary") refreshDetail();
  render();
  return { el, close() { closed = true; store.listeners.delete(onRows); if (term) term.close(); if (screen) screen.close(); } };
}

// A console or display alone in its own window ("Pop out").
export function popView(name, tab, store) {
  if (!store.row(name) || !["console", "display"].includes(tab)) {
    return { el: h("div", { class: "content" }, h("div", { class: "error-banner",
      text: `No ${["console", "display"].includes(tab) ? tab : "screen"} for a guest called ${name}.` })),
    close() {} };
  }
  const inner = tab === "display" ? displayView(name, store) : consoleView(name, store);
  const head = h("div", { class: "pop-head" }, h("strong", { text: name }),
    h("span", { class: "muted", text: tab === "display" ? "display" : "console" }));
  return { el: h("div", { class: "content pop" }, head, inner.el), close: () => inner.close() };
}

export function popOut(name, tab) {
  const size = tab === "display" ? "width=1100,height=900" : "width=900,height=620";
  // A named window, so a second click brings the same one back rather than
  // opening another (each display holds one of the session's four).
  const w = window.open(`/#/pop/${encodeURIComponent(name)}/${tab}`, `emuctl-${name}-${tab}`, size);
  if (w) { w.opener = null; w.focus(); }
}
