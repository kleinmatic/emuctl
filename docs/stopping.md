# Stopping

An emulator is a computer with the power on. Stopping the emulator without
shutting down the operating system inside it is pulling the plug: the guest's
filesystem is left dirty and may need repair at the next boot.

emuctl has no way to shut a guest down unless you tell it one. What happens on
`emuctl stop` (or `systemctl stop`, or host shutdown) depends on the conf.

## 1. Shut the guest down first

These run only if the guest is `ready`. A guest that is still booting or is
`stuck` is not at a prompt the sequence expects, so emuctl skips straight to
stopping the emulator and says so in the log.

**Typed stages.** emuctl sends Enter, waits for each prompt, types its
command, then waits for the `halted` pattern. To log in first, name the
password as a variable and put its value in the service's environment file
(`/etc/emuctl/env/<name>.env` with the shipped systemd unit, readable only by
root):

```ini
halted = >>>
stop.1 = login:    | root
stop.2 = Password: | ${ROOT_PASSWORD}
stop.3 = #         | /etc/shutdown -h now
```

```sh
# /etc/emuctl/env/vax.env, mode 600
ROOT_PASSWORD='...'
```

This only works if the guest is at its `login:` prompt. If someone is logged
in on the console, the stages wait for a prompt that never comes, and the
emulator is stopped when `stop_timeout` runs out.

**A command.** When shutting down needs a password or a network login, point
`stop_command` at a script. It can log in over the network, or drive the
console through the broker (`EMUCTL_BROKER_HOST` / `EMUCTL_BROKER_PORT`), which
stays usable while it runs. Keep the credential out of the conf: put it in the
service's environment file (`/etc/emuctl/env/<name>.env` with the shipped
systemd unit) and read it from the environment.

If the command exits 0 and the conf has `halted`, emuctl waits for that pattern
before going on. With no `halted`, exit 0 is taken to mean the command has
seen the guest go down itself. Exit non-zero if it could not.

Some guests print their last line a little before they have finished
writing to disk. `halt_grace` is how many seconds emuctl waits after `halted`
matches before it stops the emulator.

Write `halted` so it cannot match text printed while shutdown is still
running. AIX, for example, prints `Wait for '....Halt completed....'` at the
start.

Either way `stop_timeout` (default 300 s) bounds the wait, and the emulator is
stopped when it expires. A second stop request (a second SIGTERM to the
supervisor) abandons the wait at once.

## 2. Stop the emulator

`stop_mode` chooses how.

| `stop_mode` | What it does | For the guest OS |
|---|---|---|
| `signal` (default) | SIGTERM to the emulator, SIGKILL after 60 s. | A power cut, unless step 1 ran or the guest was already `halted`. |
| `monitor` | Sends `quit` on QEMU's monitor socket and waits for QEMU to close it. | The same: `quit` is QEMU's power switch. It lets QEMU close its disk images properly. |
| `freeze` | SIMH only. Sends Ctrl-E, then `save <workdir>/frozen.state` and `quit` at the `sim>` prompt. | The machine is suspended, not shut down. Your SIMH ini must `restore` the file to resume it. |

## What `emuctl stop` refuses

If a guest is `ready`, `booting` or `stuck` and the stop would be a power cut,
`emuctl stop` prints why and does nothing. Repeat with `--force`, or shut the
guest down from inside first: once it shows `halted`, `emuctl stop` proceeds
without complaint.

This check is in the `emuctl` command only. `systemctl stop` and host shutdown
go straight to the supervisor, which then does what the conf allows.

## Timeouts in the service manager

The service manager must wait longer than the supervisor does. The shipped
systemd unit has `TimeoutStopSec=420`; the FreeBSD script has
`emuctl_stop_wait=420`. Raise them if any guest's `stop_timeout` is above 300.
