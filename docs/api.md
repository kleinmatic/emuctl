# Web API

The page is built on this JSON API, and anything that can keep a cookie can
use it. Everything is under `/api` on the daemon's address. The fields of a
guest row are the same as in `emuctl list --json`.

Every route except `POST /api/login` needs the session cookie. Every POST
also needs the `X-CSRF-Token` header and an `Origin` header naming the
daemon's own address (see [web-security.md](web-security.md)). Errors come
back as `{"error": "..."}` with a 4xx status: 401 not logged in, 403 refused,
404 no such guest, 429 too many failed logins.

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

### `GET /api/guests/{guest}/usage?window=1h` (viewer)

The last hour, one point every 5 seconds, as columns:

```json
{"interval": 5, "t": [1795365600, 1795365605],
 "cpu_pct": [null, 7.4], "mem_bytes": [1180000000, 1180004352],
 "io_read": [null, 0.0], "io_write": [null, 2048.0],
 "nics": {"tap1": {"rx": [null, 1200.5], "tx": [null, 310.0]}}}
```
Rates are per second. `null` means no reading: the guest was down, had just
started, or a counter went backwards because it restarted. `window` accepts
only `1h` for now.

### `GET /api/guests/{guest}/network` (viewer)

The same as `emuctl net <guest> --json`: each host interface with its
bridge, link state, MAC and whether the bridge has seen it, traffic counters,
and warnings.

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

## Live updates

### `GET /api/events` (viewer)

Server-sent events. `guests` carries the full array of guest rows, once on
connect and then after every sample (every 5 seconds). A comment line every
15 seconds keeps the connection open. The stream ends on logout or when the
session expires.

```
event: guests
data: [{"name":"vax","state":"ready",...}]
```
