# Guest configuration

One file per guest, `<guest_dir>/<name>.conf`. Lines are `key = value`; `#`
starts a comment. In `ready`, `halted`, `command`, `stop_command` and the
`boot.`, `stop.` and `stuck.` families a `#` is part of the value, so put
comments on their own line there.

`emuctl check <name>` validates a conf against the host: paths, ports, peers.

## Required

| Key | Meaning |
|---|---|
| `name` | The guest's name. Must match the file name. Letters, digits, `.`, `_`, `-`. |
| `workdir` | Absolute path. The emulator runs here; `console.log` and `<name>.out` are written here. |
| `command` | The emulator command line, split like a shell would (quotes work; no variables or redirection). |
| `disks` | Comma-separated disk images the guest writes to, relative to `workdir` or absolute. The start guard watches these. Leave empty only with `diskless = yes`. |
| `serials` | Comma-separated TCP ports the emulator listens on for its serial lines. The first is the console. The rest are only held open. |
| `broker` | TCP port where emuctl re-serves the console. |
| `ready` | Regular expression that means the guest finished booting, usually its login prompt. |

## Boot

| Key | Default | Meaning |
|---|---|---|
| `boot.<n>` | none | `prompt \| command`. When the literal text `prompt` appears on the console, type `command` and Enter. Stages run in numeric order, once each. No stages means the guest boots by itself. |
| `boot_timeout` | 900 | Seconds after which a guest that is not `ready` is noted in the log. Nothing is stopped. |
| `console_timeout` | 180 | Seconds to wait for the emulator to open its console port before giving up. |
| `stuck.<n>` | none | Regular expression for console text that means the boot is waiting for a person. While it matches at the tail of the console the state is `stuck`. |

`ready`, `halted` and `stuck.<n>` are matched against the last 400 bytes of
console output, so text that scrolled by earlier cannot satisfy them.

## Stopping

See [stopping.md](stopping.md) for what each choice does to the guest.

| Key | Default | Meaning |
|---|---|---|
| `halted` | none | Regular expression for the console text a shut-down guest leaves, usually the firmware prompt. Gives the `halted` state, and tells a stop when the guest is down. |
| `stop.<n>` | none | `prompt \| command` stages typed at the console when a stop is requested, before the emulator is stopped. |
| `stop_wake` | yes | Send one Enter before the stop stages, so an idle console reprints its prompt. |
| `stop_command` | none | A program to run instead of `stop.<n>` stages. Use it when shutting down needs a credential. Not both. |
| `stop_timeout` | 300 | Seconds allowed for the in-guest shutdown before the emulator is stopped anyway. |
| `stop_mode` | `signal` | How the emulator itself is stopped: `signal` (SIGTERM), `monitor` (QEMU `quit`), or `freeze` (SIMH save and quit). |
| `monitor` | none | Path of QEMU's monitor socket. Required for `stop_mode = monitor`. |

A `stop_command` gets these in its environment, plus the guest's `env` and
whatever the service manager provides (for example a systemd
`EnvironmentFile`):

| Variable | Value |
|---|---|
| `EMUCTL_GUEST`, `EMUCTL_NODE` | the guest's `name` and `node` |
| `EMUCTL_WORKDIR` | `workdir` |
| `EMUCTL_BROKER_HOST`, `EMUCTL_BROKER_PORT` | where to reach the console |
| `EMUCTL_GUEST_ADDR` | `guest_addr`, or empty |

## Optional

| Key | Default | Meaning |
|---|---|---|
| `node` | `name` | The machine's own name for itself, when it differs. Shown in `list`. |
| `os`, `emulator` | empty | Free-text labels shown in `list` and `info`. |
| `env` | none | Space-separated `KEY=VALUE` pairs added to the emulator's environment. |
| `bind` | `127.0.0.1` | Address of the serial ports and the broker. See the README's security note before changing it. |
| `guest_addr`, `guest_port` | none, 23 | The guest's own network address and a TCP port that answers when it is healthy. A `ready` guest that does not accept a connection there is shown as `degraded`. |
| `exclusive_with` | none | Comma-separated guests that must not run at the same time as this one, for example two images of one machine identity. |
| `diskless` | no | `yes` allows an empty `disks`, for a guest that boots from the network. |

Unknown keys are ignored, and `emuctl check` lists them.

## Host settings

`/etc/emuctl/emuctl.conf` (`/usr/local/etc/emuctl/emuctl.conf` on FreeBSD), all
optional. Each can also be set by an `EMUCTL_<KEY>` environment variable, and
`EMUCTL_CONF` names a different file.

| Key | Default | Meaning |
|---|---|---|
| `guest_dir` | `/etc/emuctl/guests` | Where guest confs live. |
| `run_dir` | `/run/emuctl` | State files and locks. |
| `service` | `systemd` or `rcd` | The service manager `start`, `stop` and `log` use. |
| `unit` | `emuctl-guest@{name}.service` | systemd unit name for a guest. |
| `rc_script` | `emuctl` | FreeBSD rc.d script name. |
| `elevate` | `sudo` | Command put in front of service-manager calls when not root: `sudo`, `doas`, `mdo` (FreeBSD) or `none`. |
