# Web API

The page is built on this JSON API, and anything that can keep a cookie can
use it. Everything is under `/api` on the daemon's address. The fields of a
guest row are the same as in `emuctl list --json`.

Every route except `POST /api/login` needs the session cookie. Every POST
also needs the `X-CSRF-Token` header and an `Origin` header naming the
daemon's own address (see [web-security.md](web-security.md)). Errors come
back as `{"error": "..."}` with a 4xx status: 401 not logged in, 403 refused,
404 no such guest, 421 a host name the daemon does not know (see
[web.md](web.md)), 429 too many failed logins or open event streams.

A client that polls on a timer should send `X-Emuctl-Background: 1`; such
requests do not count as activity, so an unattended page still lets the
session time out.

"Role" is the least a user needs. Until roles arrive every user is an admin,
so every route is open to every logged-in user.

## Logging in

### `POST /api/login` (public)

```
POST /api/login
Origin: https://host:8600
Content-Type: application/json

{"user": "alice", "password": "..."}
```
```json
{"user": "alice", "role": "admin", "csrf": "Zf3...k1"}
```
Sets the `emuctl_session` cookie. Keep `csrf` for later POSTs. A wrong user
or password is a 401; after five failures from one address, 429 with
`Retry-After`.

### `POST /api/logout` (viewer)

Ends the session. `{"ok": true}`.

### `GET /api/session` (viewer)

```json
{"user": "alice", "role": "admin", "csrf": "Zf3...k1", "version": "0.3.0"}
```

## The host

### `GET /api/host` (viewer)

```json
{"hostname": "retrohost", "version": "0.3.0", "cpus": 8,
 "mem_total_bytes": 16777216000, "mem_available_bytes": 11000000000,
 "load": [0.52, 0.61, 0.70], "uptime": 3542400, "service": "systemd", "guests": 7,
 "filesystems": [{"mount": "/srv/emu", "guests": ["vax", "alpha"],
                  "total_bytes": 966367641600, "free_bytes": 442381631488}]}
```
`filesystems` lists each filesystem that holds a guest workdir, once.

### `GET /api/host/network` (viewer)

The same as `emuctl net --json`: `bridges` (name, addresses, uplinks, and
members with the guests behind each), `guests` (each guest's report, as
below) and `warnings`.

## Guests

### `GET /api/guests` (viewer)

An array of guest rows, the same as `emuctl list --json`, plus `cpu_pct`
over the last 5 seconds. Taken from the daemon's last sample, so at most 5
seconds old.

### `GET /api/guests/{guest}` (viewer)

One row plus what the page needs from the conf:

```json
{"name": "vax", "state": "down", "...": "...",
 "kind": "simh", "workdir": "/srv/emu/vax", "command": ["vax", "vax.ini"],
 "disks": ["/srv/emu/vax/rd0.dsk"], "stop_route": "types the stop sequence, then stops the emulator",
 "clean_stop": true, "exclusive_with": [], "console": {"bind": "127.0.0.1", "port": 2300},
 "display": null, "guest_addr": "192.0.2.10", "nics": [], "notes": false,
 "can_start": true, "start_refused": "", "boot_timeout": 300}
```
`can_start` and `start_refused` are the start guard's answer, as
`emuctl start` would give it. A guest whose conf does not parse has
`state: "error"` and `conf_error`.

### `GET /api/guests/{guest}/usage?window=1h|24h` (viewer)

`window=1h` (the default): the last hour, one point every 5 seconds, as
columns:

```json
{"interval": 5, "t": [1795365600, 1795365605],
 "cpu_pct": [null, 7.4], "mem_bytes": [1180000000, 1180004352],
 "io_read": [null, 0.0], "io_write": [null, 2048.0],
 "nics": {"tap1": {"rx": [null, 1200.5], "tx": [null, 310.0]}}}
```
Rates are per second. `null` means no reading: the guest was down, had just
started, or a counter went backwards because it restarted.

`window=24h`: the last day, one point a minute (`"interval": 60`), each the
average of that minute's samples (memory: the largest). The day survives a
restart of the daemon; see `history_file` in [web.md](web.md#settings-etcemuctlwebconf).

### `GET /api/guests/{guest}/network` (viewer)

The same as `emuctl net <guest> --json`: each host interface with its
bridge, link state, MAC and whether the bridge has seen it, traffic counters,
and warnings.

### `GET /api/guests/{guest}/check` (viewer)

What `emuctl check <guest>` reports, run as the daemon's own account:
`{"errors": [...], "warnings": [...]}`, with the host network warnings that
name this guest added. A file the daemon may not read can show up as
missing.

### `GET /api/guests/{guest}/notes` (viewer)

The guest's `notes` file: `{"path", "text", "truncated"}`, at most the
first 256 KB. 404 if the conf names none, or the daemon cannot read it.
Files under `/etc/emuctl/env/` are never shown.

### `GET /api/guests/{guest}/hardware` (viewer)

The same as `emuctl info <guest> --hardware --json`, plus `raw`: each config
file read, as `{"path", "text"}`.

### `GET /api/guests/{guest}/log?source=console|supervisor&lines=N` (viewer)

```json
{"source": "console", "text": "...\nlogin: "}
```
`console` is the tail of the guest's `console.log` with escape sequences
removed; when there is none yet, `text` is empty and `missing` is true.
`supervisor` is the supervisor's journal, as `emuctl log` shows it. `lines`
is 1 to 5000, default 200.

## Actions

Every action is a task: it is recorded with the user's name, shown to every
open page, and appended to the daemon's task log. The answer comes at once,
with the task still running; follow it with `GET /api/tasks/{id}` or the
`task` event. A refused action is a finished task whose `result` is
`refused` and whose `message` says why, not an HTTP error. A guest has one
task at a time, except that a stop-now may cut short a stop under way.

### `POST /api/guests/{guest}/start` (operator)

Runs `emuctl start <guest>`.

```json
{"id": 7, "user": "alice", "guest": "vax", "action": "start", "label": "Start",
 "started": 1795365600.2, "ended": null, "result": "running", "message": "",
 "output": [], "rc": null}
```

### `POST /api/guests/{guest}/stop` (operator)

Body `{"mode": "clean"}`, `{"mode": "force"}` or `{"mode": "now"}`, which
run `emuctl stop`, `emuctl stop --force` and `emuctl stop --now`
([stopping.md](stopping.md)). When the stop would be a power cut for the
guest, the body must also say `"power_cut": true`, meaning the user was
asked and agreed, and `"run"`: the `run` field of `GET /api/guests/{guest}`
when they were asked. Otherwise, or if the guest has been restarted since,
the task is refused and nothing is done. The
page always asks before `force` and `now`. A `force` without
`power_cut: true` runs plain `emuctl stop`, which refuses a cut itself.

### `GET /api/tasks` (viewer)

The last 200 tasks, newest first, without their output. `result` is one of
`running`, `ok`, `failed`, `refused`, `skipped` (nothing to do, such as
starting a guest that is already up) and `note` (a record, such as taking a
console's keyboard).

### `GET /api/tasks/{id}` (viewer)

One task with `output`: what the `emuctl` command printed, up to 200 lines.

## The serial console

### `WS /api/guests/{guest}/console` (viewer to watch, operator to type)

A WebSocket to the guest's console broker. It needs the session cookie and an
`Origin` header naming the daemon. On connect the daemon sends the last 64 KB
of the guest's `console.log`, then live output.

- Binary frames, both ways: console bytes. The daemon removes telnet
  negotiation from what the guest sends and escapes `0xFF` in what the
  browser sends.
- Text frames from the browser: `{"type": "take"}` and
  `{"type": "release"}` take and give back the keyboard. Until a socket holds
  the keyboard, its binary frames are dropped. One socket per guest holds it;
  taking it moves it from whoever had it. Each take and release is a task
  (`note`).
- Text frames from the daemon:
  `{"type": "status", "typing": false, "typist": "bob", "viewers": 2,
  "running": true, "message": ""}`, whenever any of that changes.

The socket closes when the guest's console closes, on logout, or when the
session ends. A session may have eight consoles open.

## The display

### `WS /api/guests/{guest}/display` (operator)

A WebSocket carrying VNC (RFB) in binary frames, for noVNC or any client
that speaks VNC over a WebSocket. The daemon has already logged in to the
guest's VNC server; it offers the client RFB 3.8 with no password, then
passes bytes through both ways. The guest's conf must have `display`. The
socket closes with code 4000 and a reason when the display cannot be
reached, refuses the password, or the guest is not running; logout or an
ended session cuts it off. A session may have four displays open; a fifth is
refused with 429 before the upgrade. Only keys, the mouse and the clipboard
count as activity for the session, not the client's own update requests.
Opening one is a task (`note`).

## Live updates

### `GET /api/events` (viewer)

Server-sent events. `guests` carries the full array of guest rows, once on
connect and then after every sample (every 5 seconds). `tasks` carries the
task list once on connect; `task` carries one task (without output) each
time one starts or finishes. A session may have four streams open. A comment line every
15 seconds keeps the connection open. The stream ends on logout or when the
session expires.

```
event: guests
data: [{"name":"vax","state":"ready",...}]
```
