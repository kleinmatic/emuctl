// SPDX-License-Identifier: GPL-2.0-only
// The graphical console: noVNC on a WebSocket to the daemon, which has
// already logged in to the guest's VNC server. No password here.
import { guestPath } from "../api.js";
import { h, overlay } from "../dom.js";
import { isUp } from "../format.js";

// noVNC is loaded only when a display is opened, not with every page.
let RFB = null;

export function displayView(name, store) {
  const screen = h("div", { class: "display-screen", "aria-label": `Screen of ${name}` });
  const box = h("div", { class: "display-box" }, screen);
  const status = h("span", { class: "muted", text: "" });
  const scaleBox = h("input", { type: "checkbox", checked: true, "aria-label": "Scale to fit" });
  const reconnect = h("button", { class: "btn hidden", type: "button", text: "Reconnect",
    onclick: () => connect() });
  const cad = h("button", { class: "btn", type: "button", text: "Ctrl-Alt-Del", disabled: true,
    onclick: () => rfb && rfb.sendCtrlAltDel() });
  const full = h("button", { class: "btn", type: "button", text: "Full screen",
    onclick: () => box.requestFullscreen && box.requestFullscreen() });
  const el = h("div", { class: "display" },
    h("div", { class: "toolbar" }, h("label", { class: "inline" }, scaleBox, "Scale to fit"),
      cad, full, reconnect,
      document.body.classList.contains("popout") ? null
        : h("button", { class: "btn", type: "button", text: "Pop out",
          onclick: () => import("./guest.js").then((m) => m.popOut(name, "display")) }),
      h("span", { class: "spacer" }), status),
    box);
  let rfb = null;
  let veil = null;
  let closed = false;

  function cover(text) {
    if (veil) veil.remove();
    veil = overlay(text);
    box.append(veil);
  }

  function uncover() {
    if (veil) { veil.remove(); veil = null; }
  }

  async function connect() {
    if (closed) return;
    if (rfb) { rfb.disconnect(); rfb = null; }
    const row = store.row(name);
    if (row && !isUp(row.state)) {
      cover(`${name} is not running.`);
      reconnect.classList.remove("hidden");
      status.textContent = "";
      return;
    }
    uncover();
    reconnect.classList.add("hidden");
    status.textContent = "Connecting…";
    if (!RFB) RFB = (await import("../../vendor/novnc/core/rfb.js")).default;
    if (closed) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    rfb = new RFB(screen, `${proto}//${location.host}${guestPath(name, "/display")}`,
      { wsProtocols: ["binary"] });
    rfb.scaleViewport = scaleBox.checked;
    rfb.resizeSession = false;
    rfb.addEventListener("connect", () => {
      status.textContent = "Connected. Keyboard and mouse go to the guest while the screen has focus.";
      cad.disabled = false;
      rfb.focus();
    });
    rfb.addEventListener("desktopname", (e) => { status.title = e.detail.name; });
    const mine = rfb;
    rfb.addEventListener("disconnect", (e) => {
      if (closed || rfb !== mine) return;
      cad.disabled = true;
      reconnect.classList.remove("hidden");
      status.textContent = "";
      cover(e.detail.clean
        ? `The display closed.`
        : `Could not open ${name}'s display. It may not be running, or this login may have ` +
          `too many displays open (four at most); the daemon's log says which.`);
      rfb = null;
    });
  }

  scaleBox.addEventListener("change", () => { if (rfb) rfb.scaleViewport = scaleBox.checked; });
  const onRows = () => {
    const row = store.row(name);
    if (!rfb && row && isUp(row.state) && veil && !reconnect.classList.contains("hidden")) {
      status.textContent = `${name} is ${row.state}; press Reconnect when its screen is up.`;
    }
  };
  store.listeners.add(onRows);
  requestAnimationFrame(connect);
  return {
    el,
    close() {
      closed = true;
      store.listeners.delete(onRows);
      if (rfb) rfb.disconnect();
    },
  };
}
