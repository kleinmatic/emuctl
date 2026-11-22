// SPDX-License-Identifier: GPL-2.0-only
// Talking to the daemon. Every POST carries the CSRF token from /api/session.

let csrf = "";

export function setCsrf(token) { csrf = token; }

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function call(method, path, body) {
  const opts = { method, credentials: "same-origin", headers: {} };
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

export const get = (path) => call("GET", path);
export const post = (path, body) => call("POST", path, body);
export const guestPath = (name, rest = "") => `/api/guests/${encodeURIComponent(name)}${rest}`;
