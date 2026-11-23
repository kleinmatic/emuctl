// SPDX-License-Identifier: GPL-2.0-only
// Talking to the daemon. Every POST carries the CSRF token from /api/session.

let csrf = "";

export function setCsrf(token) { csrf = token; }

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function call(method, path, body, background = false) {
  const opts = { method, credentials: "same-origin", headers: {} };
  // Timed refreshes say so, so they do not keep an idle session alive.
  if (background) opts.headers["X-Emuctl-Background"] = "1";
  if (method !== "GET") opts.headers["X-CSRF-Token"] = csrf;
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (r.status === 401) {
    location.replace("/login");
    throw new ApiError(401, "not logged in");
  }
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new ApiError(r.status, (data && data.error) || `HTTP ${r.status}`);
  return data;
}

// An image, as a blob: URL to put in an <img>. A timed refresh, so it does
// not count as the user being active (an <img src> could not say so).
export async function image(path) {
  const r = await fetch(path, { credentials: "same-origin", headers: { "X-Emuctl-Background": "1" } });
  if (r.status === 401) {
    location.replace("/login");
    throw new ApiError(401, "not logged in");
  }
  if (!r.ok) {
    let data = null;
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    throw new ApiError(r.status, (data && data.error) || `HTTP ${r.status}`);
  }
  return URL.createObjectURL(await r.blob());
}

export const get = (path) => call("GET", path);
export const poll = (path) => call("GET", path, undefined, true);
export const post = (path, body) => call("POST", path, body);
export const guestPath = (name, rest = "") => `/api/guests/${encodeURIComponent(name)}${rest}`;
