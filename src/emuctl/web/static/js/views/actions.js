// SPDX-License-Identifier: GPL-2.0-only
// Start, Shutdown and Stop for one guest, with the confirmation the plan asks
// for before anything that is a power cut for the guest.
import { guestPath, post } from "../api.js";
import { h } from "../dom.js";
import { isUp } from "../format.js";

export function confirmDialog({ title, lines, confirm, danger = true }) {
  return new Promise((resolve) => {
    const box = h("dialog", { class: "dialog" });
    const done = (ok) => { resolve(ok); box.close(); };
    box.append(h("h2", { text: title }), ...lines.map((l) => h("p", { text: l })),
      h("div", { class: "buttons" },
        h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => done(false) }),
        h("button", { class: danger ? "btn danger" : "btn primary", type: "button", text: confirm,
          onclick: () => done(true) })));
    box.addEventListener("close", () => { resolve(false); box.remove(); });
    document.body.append(box);
    box.showModal();
  });
}

function button(label, cls, enabled, why, onclick) {
  return h("button", { class: `btn ${cls}`, type: "button", text: label, disabled: !enabled,
    title: enabled ? null : why, onclick });
}

// detail: GET /api/guests/{name}. onDone: called after an action is sent.
export function actionButtons(name, detail, onDone) {
  if (!detail || detail.conf_error) return [];
  const up = isUp(detail.state);
  const stop = detail.stop || {};
  const notRunning = `${name} is not running`;

  async function act(path, body, ask) {
    if (ask && !(await confirmDialog(ask))) return;
    try {
      await post(guestPath(name, path), body);
    } catch (e) {
      await confirmDialog({ title: "Not sent", lines: [e.message], confirm: "OK", danger: false });
    }
    onDone();
  }

  const clean = stop.clean || {};
  const cleanOk = up && clean.allowed && !clean.power_cut;
  const shutdownWhy = !up ? notRunning
    : clean.message || "this guest has no clean shutdown configured; use Stop";
  const cut = (mode) => ({
    title: mode === "now" ? `Stop ${name} now?` : `Stop ${name}?`,
    lines: [
      "This is a power cut for the guest.",
      mode === "now"
        ? "The guest's own shutdown is skipped, or cut short if one is under way, and the emulator stops at once."
        : "The emulator stops without the guest OS being shut down first.",
      ...(stop[mode] && stop[mode].message && !stop[mode].message.includes("power cut")
        ? [stop[mode].message] : []),
    ],
    confirm: mode === "now" ? "Stop now" : "Stop",
  });
  const stopping = detail.state === "stopping";
  const startWhy = up ? `${name} is already ${detail.state}` : detail.start_refused;

  const menu = h("div", { class: "menu hidden", role: "menu" },
    h("button", { class: "item", type: "button", role: "menuitem", disabled: !(up && !stopping && stop.force && stop.force.allowed),
      text: "Stop (force)", title: "stop without shutting the guest OS down",
      onclick: () => { menu.classList.add("hidden"); act("/stop", { mode: "force", power_cut: true }, cut("force")); } }),
    h("button", { class: "item", type: "button", role: "menuitem", disabled: !(up && stop.now && stop.now.allowed),
      text: "Stop now", title: "skip or cut short the guest's shutdown",
      onclick: () => { menu.classList.add("hidden"); act("/stop", { mode: "now", power_cut: true }, cut("now")); } }));
  const toggle = h("button", { class: "btn", type: "button", "aria-haspopup": "menu", text: "Stop ▾",
    disabled: !up, title: up ? null : notRunning,
    onclick: (e) => { e.stopPropagation(); menu.classList.toggle("hidden"); } });
  document.addEventListener("click", () => menu.classList.add("hidden"), { once: true });

  return [
    button("Start", "", detail.can_start && !up, startWhy, () => act("/start")),
    button("Shutdown", "primary", cleanOk && !stopping, stopping ? `${name} is already stopping` : shutdownWhy,
      () => act("/stop", { mode: "clean" })),
    h("span", { class: "dropdown" }, toggle, menu),
  ];
}
