---
name: web-security-check
description: Review a diff to emuctl's web interface against its ten security rules (listen address, sessions, CSRF and Origin, guest-name validation, no shell, file access, login limits, CSP, task log, tests). Use before committing any change under src/emuctl/web or its static files, and at every release gate.
---

# Check a web change against the security rules

The web page can do anything root can do on the guests. This check is not
optional for changes under `src/emuctl/web/`.

1. Get the diff: `git diff main...HEAD -- src/emuctl/web tests` (or the range
   being reviewed). Read every changed handler, middleware and static file.
2. For each rule in `AGENTS.md` ("The web interface's security rules"), find
   where the diff touches it and answer yes, no or not affected, with the
   file and line:

   | # | Question |
   |---|---|
   | 1 | Can the daemon end up serving plain HTTP without `tls = off` being set explicitly? Does TLS-off still drop `Secure` and warn? |
   | 2 | Can any new route be reached without a session? Are cookie flags intact? |
   | 3 | Does every new state-changing request and WebSocket check Origin, and every POST the CSRF token? |
   | 4 | Does every guest name from a URL or body go through the shared lookup before use? |
   | 5 | Is any subprocess started with a shell, or with a string built from input? |
   | 6 | Can any route read a file not named in a guest's conf, follow a symlink out, or reach `/etc/emuctl/env/`? |
   | 7 | Is the login limit still applied to every way of logging in? |
   | 8 | Does the CSP still allow only the daemon's origin? Does new front-end code need `unsafe-inline` or an outside URL? |
   | 9 | Does every new action write to the task log with the user's name? |
   | 10 | Is there a test that tries to break each rule the diff touches? |

3. Also check the front end: no `innerHTML` with data from the API (guest
   names, console text, log lines are untrusted); no secrets or passwords in
   anything sent to the browser; console and display WebSockets refuse a
   viewer's input where the rules say so.
4. Check the test fixtures for real hostnames, MAC addresses, IP addresses or
   usernames.
5. Report each rule's answer. Anything answered "no" (or "yes" for 1, 5, 6)
   is a blocker: fix it, or stop and explain why it is not one.

At a phase gate, also run the `adversarial-review` skill (if available) on
the whole branch and include its findings.

> Written before the web package exists. When part 3a of the web work lands, update the
> questions to name the real middleware and lookup functions.
