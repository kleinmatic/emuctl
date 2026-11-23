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
console output, so text that scrolled by earlier cannot satisfy them. If those
400 bytes begin partway through a line, that partial line is left out.

In the command half of a `boot.<n>` or `stop.<n>` stage, `${NAME}` is replaced
with the environment variable `NAME` from the supervisor's environment. Use it
for a password, so the password lives in a root-only file and not in the conf.
The log shows the stage as written, never the value. If a stop stage names a
variable that is not set, emuctl skips the whole stop sequence and says so. A
boot stage with an unset variable is logged and not sent.

The value is typed at the guest's console, and anything the guest echoes back
goes into `console.log` and to everyone watching the console. Use a variable
only at a prompt that does not echo, such as `Password:`.

## Stopping

See [stopping.md](stopping.md) for what each choice does to the guest.

| Key | Default | Meaning |
|---|---|---|
| `halted` | none | Regular expression for the console text a shut-down guest leaves, usually the firmware prompt. Gives the `halted` state, and tells a stop when the guest is down. |
| `stop.<n>` | none | `prompt \| command` stages typed at the console when a stop is requested, before the emulator is stopped. |
| `stop_wake` | yes | Send one Enter before the stop stages, so an idle console reprints its prompt. |
| `stop_command` | none | A program to run instead of `stop.<n>` stages. Use it when shutting down needs a credential. Not both. |
| `stop_timeout` | 300 | Seconds allowed for the in-guest shutdown before the emulator is stopped anyway. |
| `halt_grace` | 0 | Seconds to wait after `halted` appears during a stop, before stopping the emulator. For a guest whose last console line comes before it has finished writing to disk. |
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

## Describing the machine

These keys add nothing to how a guest runs. They tell `emuctl info`, the
monitoring commands and the web page what the guest is and where to look.

| Key | Default | Meaning |
|---|---|---|
| `kind` | guessed | `simh`, `es40`, `qemu`, `gxemul` or `other`. Guessed from the program name in `command`; set it when the guess is wrong, for example a SIMH binary with an unusual name. Decides how the emulator's config file is read for hardware details. |
| `config` | none | The emulator's own config files, comma-separated, relative to `workdir`. emuctl reads them for hardware details and never changes them. `emuctl check` reports one that is missing. QEMU takes its configuration on the command line, so QEMU guests need no `config`. |
| `nics` | found in `config` | Host network interfaces the guest uses (taps or veths), comma-separated. Only needed when emuctl cannot find them in the emulator's config. |
| `display` | none | A VNC server showing the guest's screen, as `vnc://host:port`. |
| `display_auth` | `vnc` if a password file is set, else `none` | How to log in to that VNC server: `none` or `vnc` (a VNC password). |
| `display_password_file` | none | A file holding the VNC password on its first line, relative to `workdir`. The web page's Display tab reads it to log in for you, so the `emuctl-web` account must be able to read it; nothing else should. It may not be under `/etc/emuctl/env/`, which the web daemon never reads. See [web.md](web.md#a-guests-screen). |
| `notes` | none | A text file about the guest, shown on the web page's Notes tab. |
| `locked` | none | A reason the guest must not be started, such as `locked = disk image being copied`. Not `yes` or `no`: `locked = no` is refused, since any reason locks. Every start is refused with that reason: `emuctl start`, the web page and the service manager alike. Stopping is not affected. Remove the line to allow starts again. |
| `hw.<label>` | none | A hardware line stated by hand, such as `hw.memory = 128 MB` or `hw.cpu = R3000, 25 MHz`. Shown as given, and used in place of anything read from `config` under the same label. |

`emuctl info <guest> --hardware` (add `--json` for scripts) shows what emuctl
read:

```
$ emuctl info vms-alpha --hardware
machine     AlphaServer ES40
cpu         1 × EV68CB, 800 MHz
memory      1 GB
disk        disk0.0: file, dka0.img
nic         pci0.4: DEC 21143, 02:00:00:00:00:03, on tap2
read from   /srv/emu/vms-alpha/es40.cfg
```

SIMH `.ini`, es40 `.cfg` and gxemul `machine.json` files are read from
`config`, or found in `command` when `config` is not set. QEMU guests are read
from `command`. A line emuctl does not understand is left out, never an
error. Where the result is wrong or thin, add `hw.` lines.

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
