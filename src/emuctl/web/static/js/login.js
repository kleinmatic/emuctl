// SPDX-License-Identifier: GPL-2.0-only
import { applyTheme } from "./theme.js";

applyTheme();
const form = document.getElementById("login");
const msg = document.getElementById("msg");

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  msg.textContent = "";
  const body = { user: form.user.value, password: form.password.value };
  let r;
  try {
    r = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body), credentials: "same-origin",
    });
  } catch (e) {
    msg.textContent = "Cannot reach the server.";
    return;
  }
  if (r.ok) {
    location.replace("/");
    return;
  }
  let text = "Login failed.";
  try { text = (await r.json()).error || text; } catch (e) { /* keep the default */ }
  msg.textContent = text.charAt(0).toUpperCase() + text.slice(1) + ".";
  form.password.value = "";
  form.password.focus();
});
