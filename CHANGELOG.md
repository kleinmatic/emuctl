# Changelog

## 0.1.0 (unreleased)

First public version.

- One `emuctl` command: `list`, `info`, `status`, `start`, `stop`, `console`,
  `log`, `check`, `supervise`.
- Supervisor: console hold, boot stages, console log, multi-client broker,
  states (`booting`, `stuck`, `ready`, `halted`, `crashed`, `orphaned`),
  reattach after the emulator drops its console client.
- Clean stop: `stop.<n>` stages typed at the console, or a `stop_command`.
- Start guard: `exclusive_with`, open-disk scan, per-guest and per-disk locks.
  `emuctl start` runs the checks itself and reports a refusal.
- systemd template unit and FreeBSD rc.d script; `install.sh` for installing without pip.
  `elevate = mdo` for FreeBSD hosts without sudo.
