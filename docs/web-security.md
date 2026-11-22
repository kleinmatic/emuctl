# Web interface security

The web page can start and stop every guest, and (from 0.3) type at their
consoles. A console is often a root login with no password. Treat access to
the page as root access to every guest.

## What it can do

- Read: every guest's state, resource numbers, conf summary, console log,
  supervisor log, and the emulator config files the conf names (shown as
  written, so anything in them, a password included, is visible); the host's
  bridges and interfaces.
- Act: start and stop guests through systemd. The polkit rule allows
  `start`, `stop` and `kill` on `emuctl-guest@*.service` units only.
- It cannot edit confs, run commands on the host, or read `/etc/emuctl/env/`.

## Who can use it

Anyone with a user name and password in the users file. Every user can do
everything until roles arrive. Passwords are stored as scrypt hashes (cost
2^15) in a file only the daemon's account can read. There is no "remember me"
and no password reset by mail: the admin sets passwords with
`emuctl web passwd`.

## How it is protected

These are the rules every change to the web code follows (they are also in
`AGENTS.md`, for contributors), and what each means in practice.

1. **HTTPS by default.** The daemon listens on port 8600 on all addresses and
   serves HTTPS with a self-signed certificate made on first start, or one
   you name. Plain HTTP only with `tls = off`, which drops the cookie's
   `Secure` flag and logs a warning at every start.
2. **Sessions.** Logging in sets a random 256-bit token in a cookie marked
   `HttpOnly`, `Secure` (unless TLS is off) and `SameSite=Strict`. Sessions
   live in the daemon's memory and end after 12 hours without a request, on
   logout, when the user is removed, or when the daemon restarts.
3. **Requests from other sites are refused.** Every request that changes
   something, and every WebSocket, must carry an `Origin` header naming the
   page's own address. Every POST also carries a per-session CSRF token in a
   header.
4. **Guest names are checked** against the conf-name pattern and the list of
   real guests before anything uses them. Anything else is a 404.
5. **No shell.** Every program the daemon runs gets an argument list.
6. **File reads are limited** to the files a guest's conf names, each guest's
   `console.log`, and the emulator configs, after following symlinks.
   Nothing under `/etc/emuctl/env/` is ever read, even through a symlink.
7. **Login attempts are limited:** five failures per address, then a wait
   that doubles with each failure, up to five minutes. A success clears it.
8. **Content-Security-Policy** allows scripts, styles, fonts and connections
   from the daemon's own address only. The page loads nothing from the
   internet; its third-party files (fonts, xterm.js) are in the package.
9. **Every action is logged** with the user's name, in the task log (from
   0.3).

Also: the page builds everything from text, never HTML from the server, so a
guest name or console line cannot inject markup. Responses carry
`X-Frame-Options: DENY` and `X-Content-Type-Options: nosniff`, and
`Strict-Transport-Security` when TLS is on.

### Things to know

- **The page's own files are public.** HTML, JavaScript, CSS and fonts under
  `/static/` are served without a session: they are the same open-source
  files for everyone and hold no data. Everything under `/api/` needs a
  session.
- **The Origin check trusts the `Host` header** the browser sends. A page on
  another site cannot set either, so cross-site requests fail. It does not
  protect against someone who already has your session cookie.
- **Logins are limited per address.** Behind a reverse proxy every request
  comes from the proxy's address, so one person's failures would lock out
  everyone. emuctl does not read `X-Forwarded-For`; put the proxy's own rate
  limit in front instead.
- **The daemon's account is in `systemd-journal`** and in the guests' group;
  see [web.md](web.md) for what that exposes.

## Reporting a problem

Report security problems privately through GitHub's "Report a
vulnerability" on the repository's Security tab, not in a public issue.
