// SPDX-License-Identifier: GPL-2.0-only
// The task panel along the bottom, the full list on the host's Tasks tab,
// and the dialog that shows one task's output.
import { get } from "../api.js";
import { clear, h } from "../dom.js";

export const RESULT_CLASS = {
  running: "res-running", ok: "res-ok", failed: "res-bad", refused: "res-warn",
  skipped: "res-muted", note: "res-muted",
};

export function time(t) {
  const d = new Date(t * 1000);
  return d.toTimeString().slice(0, 8);
}

function resultText(t) {
  if (t.result === "running") return "running…";
  const msg = t.message ? ` · ${t.message}` : "";
  return `${t.result}${msg}`;
}

export function taskRow(t) {
  return h("tr", { class: "task", tabindex: "0", dataset: { id: t.id },
    onclick: () => openTask(t.id), onkeydown: (e) => { if (e.key === "Enter") openTask(t.id); } },
  h("td", { class: "mono muted", text: time(t.started) }), h("td", { text: t.user }),
  h("td", {}, h("a", { href: `#/g/${encodeURIComponent(t.guest)}/summary`, text: t.guest,
    onclick: (e) => e.stopPropagation() })),
  h("td", { text: t.label }), h("td", { class: `wrap ${RESULT_CLASS[t.result] || ""}`, text: resultText(t) }));
}

export function taskTable(list) {
  if (!list.length) return h("div", { class: "empty", text: "Nothing done from this page yet." });
  return h("div", { class: "scroll" }, h("table", { class: "data tasklist" }, h("tbody", {}, list.map(taskRow))));
}

export async function openTask(id) {
  const box = h("dialog", { class: "dialog wide" });
  const body = h("div", { text: "Loading…" });
  box.append(h("h2", { text: `Task ${id}` }), body,
    h("div", { class: "buttons" }, h("button", { class: "btn", type: "button", text: "Close", onclick: () => box.close() })));
  box.addEventListener("close", () => box.remove());
  document.body.append(box);
  box.showModal();
  try {
    const t = await get(`/api/tasks/${id}`);
    const took = t.ended ? `${Math.max(0, Math.round(t.ended - t.started))} s` : "still running";
    clear(body).append(
      h("div", { class: "kv" },
        h("span", { class: "k", text: "Action" }), h("span", { text: `${t.label} ${t.guest}` }),
        h("span", { class: "k", text: "By" }), h("span", { text: t.user }),
        h("span", { class: "k", text: "Started" }), h("span", { text: new Date(t.started * 1000).toLocaleString() }),
        h("span", { class: "k", text: "Took" }), h("span", { text: took }),
        h("span", { class: "k", text: "Result" }), h("span", { class: RESULT_CLASS[t.result] || "", text: resultText(t) })),
      h("pre", { class: "log", text: t.output.length ? t.output.join("\n") : "(no output)" }));
  } catch (e) {
    body.textContent = e.message;
  }
}

// The panel: the last few tasks, kept up to date from the event stream.
export function taskPanel(store) {
  const list = h("div");
  const el = h("section", { class: "taskbar", "aria-label": "Tasks" },
    h("div", { class: "taskhead" }, h("h2", { text: "Tasks" }),
      h("a", { class: "muted", href: "#/host/tasks", text: "all tasks" })), list);
  const render = () => clear(list).append(taskTable(store.tasks.slice(0, 5)));
  store.taskListeners.add(render);
  render();
  return el;
}
