# Changelog

## Unreleased

- `locked = <reason>` in a guest's conf refuses every start, from
  `emuctl start`, the web page or the service manager, with that reason.
  `emuctl check`, `emuctl info` and the web page show it.
- The web page's guest summary lists what `emuctl check` finds, and a
  Notes tab shows the guest's `notes` file.
- The graphs can show the last day as well as the last hour. The day, one
  point a minute, is saved to `history_file` (in the daemon's state
  directory by default) and survives a restart.
- An Energy tab on the host shows what is up, for how long, and what CPU
  each guest has used now, over the hour and over the day.
- Guests with a display get a thumbnail of their screen on the summary, at
  most a minute old.
- Read-only API tokens for scripts and monitoring (`emuctl web token add
  <name>`), which read guest status and numbers but not logs or notes, and
  `/metrics` in Prometheus' format.
- The Console tab can look like a terminal: an 80 x 24 screen in amber,
  green or white phosphor, in a font after DEC's VT320 that has the
  line-drawing characters old systems draw forms with, with lamps showing
  ON LINE, LOCAL and KBD LOCKED. Or "modern": an ordinary terminal. Each
  user picks per guest; `console_look` in `web.conf` sets the default.
- A phone layout: the guest list folds behind a "Guests" button.
- The guest you are on stands out in the guest list, and state dots are lit
  like front-panel LEDs (green ready, amber busy, orange stuck, red crashed);
  a guest that is off is an unlit ring.

## 0.4.0 (2026-11-22)

- The web page has a Display tab for guests whose emulator has a VNC
  server (`display` in the conf). The daemon logs in with the password in
  `display_password_file` and shows the screen with noVNC; the browser never
  sees the password.
- A stopped guest's Console tab shows its last output under a grey overlay
  that says it is not running.
- "Pop out" on the Console and Display tabs opens that screen alone in its
  own window.

## 0.3.0 (2026-11-22)

### Breaking

- `emuctl list` shows GUEST, STATE, UPTIME and DETAIL and fits in 80 columns.
  The NODE, OS and EMULATOR columns are gone by default; `--node`, `--os` and
  `--emulator` add them back, and `--usage` adds CPU% and MEM. A script that
  reads the table should use `--json`, which is unchanged.

### New

- A web interface, `emuctl web serve` (docs/web.md). It needs Python
  3.11 and aiohttp. It shows the host, every guest with its state, CPU,
  memory, disk and network use over the last hour, the guest's hardware,
  network and logs, and the host's bridges and warnings, updated live. It
  serves HTTPS on port 8600 with a self-signed certificate made on first
  start, has its own users (`emuctl web passwd`), and runs as its own
  account. `sudo ./install.sh web` sets it up.
- The web page can start guests and stop them cleanly, by force or at once,
  asking before anything that is a power cut. Each action is a task with
  its output, recorded with the user's name in a task log. Start is
  disabled, with the reason, when the start guard would refuse.
- The web page has each guest's serial console (xterm.js), shared with
  `emuctl console`. It opens watching, with the recent console log as
  scrollback; "Take keyboard" lets you type. It can send Ctrl-P, Ctrl-E,
  Ctrl-C and Ctrl-], paste slowly for consoles that drop fast input, and
  download the log.

## 0.2.0 (2026-11-22)

- `emuctl start` no longer reports `crashed` straight away when the previous
  run had crashed; it waits for the new run's own state.
- `emuctl list --usage` shows CPU% and memory in place of the node, OS and
  emulator labels, and fits in 80 columns. `list --json` and
  `status --json` now carry CPU time, memory, tasks, disk I/O and disk image
  sizes for each guest (docs/monitoring.md). The unit file turns on systemd's
  CPU, memory, task and I/O accounting.
- `emuctl stop --now` skips the guest's shutdown, or ends one under way, and
  stops the emulator at once. The supervisor treats SIGUSR2 as this request.
- A plain `emuctl stop` on a guest that is already stopping mentions `--now`.
  On systemd it still waits for the stop to finish; under rc.d it now does
  nothing, where it used to send a second SIGTERM that cut the shutdown short.
- New guest conf keys describe the machine: `kind`, `config`, `nics`,
  `display`, `display_auth`, `display_password_file`, `notes` and
  `hw.<label>` (docs/config.md). `emuctl info` shows them and `emuctl check`
  reports named files that are missing.
- `emuctl info <guest> --hardware` shows the emulated machine (CPU, memory,
  disks, network cards and their host interfaces) read from the SIMH, es40,
  gxemul or QEMU configuration.
- `emuctl net` shows the host's bridges, their members and the guests behind
  them; `emuctl net <guest>` shows a guest's interfaces, whether its MAC has
  been seen on its bridge, and its traffic. `emuctl check` now warns about
  missing interfaces, two guests sharing an interface or MAC, a ready guest
  whose MAC was never seen, and checksum offload on a veth
  (docs/networking.md). `list --json` carries each guest's network bytes.
- Linux with systemd is the supported platform, Debian 13 the reference;
  the core keeps running on FreeBSD (docs/platforms.md).
- `tools/devhost`: a throwaway host with four fake guests for trying emuctl
  without an emulator or root.

### Breaking

- `kind`, `display` and `display_auth` used to be ignored as unknown keys.
  They now have meanings, and a conf that sets one to a value emuctl does not
  accept fails to load (`emuctl check` says why). Rename or remove such a key.
- `nics` interface names must look like Linux interface names (letters,
  digits, `_`, `.`, `-`; up to 15 characters, not starting with `.` or `-`).
- Under rc.d, `emuctl stop` on a guest that is already stopping returns at
  once instead of cutting its shutdown short. Use `--now` for that.
- `emuctl info <guest> --json` without `--hardware` is an error; use
  `emuctl status <guest> --json`.

## 0.1.0 (2026-11-22)

First public version.

- One `emuctl` command: `list`, `info`, `status`, `start`, `stop`, `console`,
  `log`, `check`, `supervise`.
- Supervisor: console hold, boot stages, console log, multi-client broker,
  states (`booting`, `stuck`, `ready`, `halted`, `crashed`, `orphaned`),
  reattach after the emulator drops its console client.
- Clean stop: `stop.<n>` stages typed at the console, or a `stop_command`.
  `${NAME}` in a stage is filled in from the environment, so a password can
  stay out of the conf. `halt_grace` waits after the halted prompt. A
  `stop_command` that exits 0 with no `halted` pattern counts as a clean halt.
- Patterns ignore a line cut in half at the edge of the 400-byte window.
- Start guard: `exclusive_with`, open-disk scan, per-guest and per-disk locks.
  `emuctl start` runs the checks itself and reports a refusal.
- systemd template unit and FreeBSD rc.d script; `install.sh` for installing without pip.
  `elevate = mdo` for FreeBSD hosts without sudo.
- `emuctl --version` shows the git commit when installed from a checkout with
  `install.sh`. Releases are made by tag; see docs/releasing.md.
