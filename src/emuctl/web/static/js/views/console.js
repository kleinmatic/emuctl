// SPDX-License-Identifier: GPL-2.0-only
// The serial console: xterm.js on a WebSocket to the guest's console broker.
// It opens viewing; keys go to the guest only after "Take keyboard".
// The screen is 80 x 24, as the guests' consoles are, in a terminal's case
// whose lamps say what the page is doing: ON LINE when connected, LOCAL when
// not, KBD LOCKED while your keys are not sent.
import { Terminal } from "../../vendor/xterm/xterm.mjs";
import { get, guestPath } from "../api.js";
import { h, overlay } from "../dom.js";
import { slowSend } from "../keys.js";
import { COLS, PHOSPHORS, ROWS, fontFor, storePhosphor, storedPhosphor, xtermTheme } from "../phosphor.js";

const KEYS = [["Ctrl-P", "\x10"], ["Ctrl-E", "\x05"], ["Ctrl-C", "\x03"], ["Ctrl-]", "\x1d"]];

export function consoleView(name, store) {
  const enc = new TextEncoder();
  let look = storedPhosphor();
  const term = new Terminal({ cols: COLS, rows: ROWS, scrollback: 5000, convertEol: false,
    cursorBlink: true, disableStdin: true, letterSpacing: 0, theme: xtermTheme(look),
    fontFamily: fontFor(look, 9999, 9999).family });
  const screen = h("div", { class: "terminal", "aria-label": `Console of ${name}` });
  const frame = h("div", { class: "terminal-frame" }, screen);
  const lamp = (label) => h("span", { class: "lamp", text: label });
  const lamps = { online: lamp("ON LINE"), local: lamp("LOCAL"), locked: lamp("KBD LOCKED") };
  const vt = h("div", { class: "vt", dataset: { look } },
    h("div", { class: "vt-case" },
      h("div", { class: "vt-bezel" }, frame),
      h("div", { class: "vt-strip" }, h("span", { class: "vt-badge", text: name }),
        h("span", { class: "lamps", "aria-hidden": "true" }, lamps.online, lamps.local, lamps.locked))));
  const box = h("div", { class: "vt-box" }, vt);
  function setLamps(online, typingNow) {
    lamps.online.classList.toggle("on", online);
    lamps.local.classList.toggle("on", !online);
    lamps.locked.classList.toggle("on", online && !typingNow);
  }
  setLamps(false, false);
  const lookSel = h("select", { "aria-label": "Screen" },
    Object.entries(PHOSPHORS).map(([id, p]) => h("option", { value: id, text: p.label })));
  lookSel.value = look;
  lookSel.addEventListener("change", () => {
    look = lookSel.value;
    storePhosphor(look);
    vt.dataset.look = look;
    term.options.theme = xtermTheme(look);
    sizeScreen();
  });
  let veil = null;
  function cover(text) {
    if (veil) veil.remove();
    veil = overlay(text);
    frame.append(veil);
  }
  function uncover() { if (veil) { veil.remove(); veil = null; } }
  const bannerText = h("span");
  const takeBtn = h("button", { class: "btn warn", type: "button", text: "Take keyboard" });
  const banner = h("div", { class: "console-banner" }, bannerText, takeBtn);
  const keyBtns = KEYS.map(([label, bytes]) => h("button", { class: "btn mono", type: "button",
    text: label, disabled: true, onclick: () => { send(bytes); term.focus(); } }));
  const pasteBtn = h("button", { class: "btn", type: "button", text: "Paste slowly…", disabled: true,
    onclick: () => pasteDialog() });
  const reconnectBtn = h("button", { class: "btn hidden", type: "button", text: "Reconnect",
    onclick: () => connect() });
  const el = h("div", { class: "console" }, banner,
    h("div", { class: "toolbar" }, h("span", { class: "muted", text: "Send:" }), keyBtns, pasteBtn,
      h("span", { class: "spacer" }), h("label", { class: "inline" }, "Screen", lookSel), reconnectBtn,
      document.body.classList.contains("popout") ? null
        : h("button", { class: "btn", type: "button", text: "Pop out",
          onclick: () => import("./guest.js").then((m) => m.popOut(name, "console")) }),
      h("button", { class: "btn", type: "button", text: "Download log", onclick: download })),
    box);

  let ws = null;
  let typing = false;
  let closed = false;
  let pasting = null;

  // Returns false when the keys could not go: then a slow paste stops.
  function send(text) {
    if (!(ws && ws.readyState === WebSocket.OPEN && typing)) return false;
    const bytes = enc.encode(text);
    for (let i = 0; i < bytes.length; i += 1024) ws.send(bytes.subarray(i, i + 1024));
    return true;
  }

  function stopPaste() {
    if (pasting) { pasting.cancel(); pasting = null; }
  }

  function setStatus(s) {
    typing = s.typing;
    if (!typing) stopPaste();       // never finish a paste after the keyboard went away
    term.options.disableStdin = !typing;
    setLamps(Boolean(s.running), typing);
    for (const b of [...keyBtns, pasteBtn]) b.disabled = !typing;
    banner.classList.toggle("typing", typing);
    const others = s.viewers > 1 ? ` · ${s.viewers - 1} other viewer${s.viewers > 2 ? "s" : ""}` : "";
    if (!s.running) {
      bannerText.textContent = s.message || `${name} is not running.`;
      cover(`${name} is not running. This is the end of its last session.`);
      takeBtn.classList.add("hidden");
      reconnectBtn.classList.remove("hidden");
      return;
    }
    takeBtn.classList.remove("hidden");
    reconnectBtn.classList.add("hidden");
    uncover();
    if (typing) {
      bannerText.textContent = `Typing. Your keys go to ${name}${others}.`;
      takeBtn.textContent = "Release keyboard";
    } else {
      const who = s.typist ? ` ${s.typist} has the keyboard.` : "";
      bannerText.textContent = `Viewing. Your keys are not sent.${who}${others}`;
      takeBtn.textContent = s.typist ? `Take keyboard from ${s.typist}` : "Take keyboard";
    }
    if (s.message) bannerText.textContent += ` (${s.message})`;
  }

  takeBtn.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: typing ? "release" : "take" }));
      term.focus();
    }
  });

  term.onData((d) => send(d));

  function connect() {
    if (ws) ws.close();
    term.reset();
    bannerText.textContent = "Connecting…";
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}${guestPath(name, "/console")}`);
    ws.binaryType = "arraybuffer";
    const mine = ws;
    ws.onmessage = (ev) => {
      if (ws !== mine) return;
      if (typeof ev.data === "string") {
        try { setStatus(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
      } else {
        term.write(new Uint8Array(ev.data));
      }
    };
    ws.onclose = () => {
      if (closed || ws !== mine) return;
      stopPaste();
      typing = false;
      setLamps(false, false);
      term.options.disableStdin = true;
      for (const b of [...keyBtns, pasteBtn]) b.disabled = true;
      if (!bannerText.textContent.includes("not running")) {
        bannerText.textContent = "Disconnected.";
        cover("Disconnected. Press Reconnect.");
      }
      takeBtn.classList.add("hidden");
      reconnectBtn.classList.remove("hidden");
    };
  }

  async function download() {
    const r = await get(guestPath(name, "/log?source=console&lines=5000"));
    const a = h("a", { download: `${name}-console.log` });
    a.href = URL.createObjectURL(new Blob([r.text || ""], { type: "text/plain" }));
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  function pasteDialog() {
    const text = h("textarea", { rows: "8", "aria-label": "Text to paste" });
    const delay = h("input", { type: "number", min: "0", max: "1000", value: "20", "aria-label": "Delay" });
    const box = h("dialog", { class: "dialog wide" },
      h("h2", { text: "Paste slowly" }),
      h("p", { text: "Old consoles drop characters sent too fast. Each character is sent after a pause." }),
      text, h("label", { class: "inline" }, "Pause between characters (ms) ", delay),
      h("div", { class: "buttons" },
        h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => box.close() }),
        h("button", { class: "btn primary", type: "button", text: "Send", onclick: () => {
          const t = text.value.replace(/\r?\n/g, "\r");
          box.close();
          if (pasting) pasting.cancel();
          pasting = slowSend(t, Number(delay.value) || 0, send);
        } })));
    box.addEventListener("close", () => box.remove());
    document.body.append(box);
    box.showModal();
    text.focus();
  }

  // The largest font that shows the whole 80 x 24 screen in the room there
  // is; below the smallest size the screen scrolls sideways instead.
  function sizeScreen() {
    const pop = document.body.classList.contains("popout");
    const width = Math.max(200, box.clientWidth - 80);
    const height = Math.max(240, window.innerHeight - (pop ? 150 : 330));
    const f = fontFor(look, width, height);
    term.options.fontFamily = f.family;
    term.options.lineHeight = f.lineHeight;
    term.options.fontSize = f.size;
  }
  const onResize = () => sizeScreen();
  window.addEventListener("resize", onResize);
  requestAnimationFrame(async () => {
    // xterm.js measures the font when it opens, so it must have loaded.
    try { await document.fonts.load('20px "emuctl VT"'); } catch (e) { /* the fallback, then */ }
    if (closed) return;
    term.open(screen);
    sizeScreen();
    connect();
  });

  return {
    el,
    close() {
      closed = true;
      if (pasting) pasting.cancel();
      window.removeEventListener("resize", onResize);
      if (ws) ws.close();
      term.dispose();
    },
  };
}
