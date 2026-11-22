---
name: web-security-check
description: Review a diff to emuctl's web interface against its ten security rules (listen address and TLS, sessions, Origin and CSRF, guest-name checks, no shell, file access, login limit, CSP, task log, tests). Use before committing any change under src/emuctl/web or its static files, and at every release gate.
---

# Check a web change against the security rules

The web page can do anything root can do on the guests. This check is not
optional for changes under `src/emuctl/web/`.

1. Get the diff: `git diff main...HEAD -- src/emuctl/web tests systemd examples install.sh`
   (or the range being reviewed). Read every changed handler, middleware and
   static file.
2. For each rule in `AGENTS.md` ("The web interface's security rules"), find
   where the diff touches it and answer yes, no or not affected, with the
   file and line. Where each rule lives today:

   | # | Question | Where it is enforced |
   |---|---|---|
   | 1 | Can the daemon end up serving plain HTTP without `tls = off` being set explicitly? Does TLS-off still drop `Secure` and warn? | `web/main.py: cmd_serve`, `web/conf.py`, `web/tls.py`; the cookie in `app.py: login` |
   | 2 | Can any new route be reached without a session? Is every handler marked `@public` or `@needs(role)`? Are cookie flags intact? | `app.py: auth_middleware`, `is_public`, `auth.Sessions` |
   | 3 | Does every new state-changing request and WebSocket go through `origin_ok`, and every POST through `csrf_ok`? Did anything widen what `changes_something` treats as safe? | `app.py: auth_middleware` |
   | 4 | Does every guest name from a URL arrive as `{g}` and get used as `request["guest"]`? Is any guest name from a body or query string checked the same way? | `app.py: guest_middleware` |
   | 5 | Is any subprocess started with a shell, or with a string built from input? Does anything add sudo back (`settings.elevate` must stay `none`)? | everywhere; `main.py` forces `elevate = none` |
   | 6 | Can any route read a file not named in a guest's conf, follow a symlink out, or reach `/etc/emuctl/env/`? Are reads bounded and non-blocking? | `data.safe_path`, `data.tail_bytes`, `hardware.read_config` |
   | 7 | Is the login limit still applied to every way of logging in, charged before the password check? | `app.py: login`, `auth.LoginLimiter` |
   | 8 | Does the CSP still allow only the daemon's origin, with `unsafe-inline` for styles only (xterm.js) and nothing unsafe for scripts? Does new front-end code need an inline script or an outside URL? | `app.py: CSP`, `add_security_headers` |
   | 9 | Does every new action write to the task log with the user's name? | `web/tasks.py` |
   | 10 | Is there a test that tries to break each rule the diff touches? | `tests/test_web_*.py` |

3. Also check the front end (`static/js/`): no `innerHTML`, `outerHTML`,
   `insertAdjacentHTML` or `document.write` (guest names, console text and
   log lines are untrusted; use `h()` and `textContent`); no `style=`
   attributes or `setAttribute("style", ...)`; no secrets or passwords in
   anything sent to the browser; console and display WebSockets refuse input
   where the rules say so.
4. Check the test fixtures for real hostnames, MAC addresses, IP addresses or
   usernames.
5. Report each rule's answer. Anything answered "no" (or "yes" for 1, 5, 6)
   is a blocker: fix it, or stop and explain why it is not one.

At a phase gate, also run the `adversarial-review` skill (if available) on
the whole branch and include its findings.
