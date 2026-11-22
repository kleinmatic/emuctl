# Changelog

## Unreleased

- `emuctl list --usage` adds CPU% and memory columns. `list --json` and
  `status --json` now carry CPU time, memory, tasks, disk I/O and disk image
  sizes for each guest (docs/monitoring.md). The unit file turns on systemd's
  CPU, memory, task and I/O accounting.
- `emuctl stop --now` skips the guest's shutdown, or ends one under way, and
  stops the emulator at once. The supervisor treats SIGUSR2 as this request.
- Linux with systemd is the supported platform, Debian 13 the reference;
  the core keeps running on FreeBSD (docs/platforms.md).
- `tools/devhost`: a throwaway host with four fake guests for trying emuctl
  without an emulator or root.

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
