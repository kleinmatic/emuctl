# emuctl

emuctl runs emulated computers as system services. It works with SIMH, es40,
QEMU, and other emulators that put their console on a TCP port.

```
$ emuctl list
GUEST    NODE     OS                STATE    UPTIME   DETAIL
pdp11    pdp11    2.11BSD           ready    3d04h    192.0.2.21 answers
vax      VAXA     OpenVMS VAX 7.3   stuck    0:02:10  boot is waiting at: enter date and time
alpha    alpha    Tru64 5.1B        halted   8:12:44  guest OS is down; safe to stop
aix      aix      AIX 5.1           down

$ emuctl start pdp11 --wait
$ emuctl console vax
$ emuctl stop alpha
```

## What it does

Each guest gets a small supervisor process. The supervisor starts the
emulator and connects to its console. Many emulators won't boot until
something is connected there.

If the guest needs a command at a firmware prompt, such as `boot dka0` at
`>>>`, the supervisor types it.

The console is saved to a log file and shared on a local port. Several people
can watch it and type at it at once. Ctrl-] leaves `emuctl console`.

`emuctl list` shows what each guest is doing: booting, ready, halted, or
stuck at a prompt that needs a person.

emuctl won't start a guest if another emulator already has its disk image
open. Two emulators writing to one disk image will corrupt it.

You can tell emuctl how to shut down the guest's operating system. It does
that before it stops the emulator. If you haven't told it how, stopping the
emulator is like pulling the power cord, so `emuctl stop` asks for `--force`.

emuctl doesn't install emulators, create disk images, or set up networking.

## Requirements

- Python 3.10 or newer. No other packages.
- Linux with systemd; Debian 13 is the reference system. The core also runs on
  FreeBSD. See [docs/platforms.md](docs/platforms.md).

## Install

```sh
git clone https://github.com/kleinmatic/emuctl
cd emuctl
sudo ./install.sh
```

This installs the `emuctl` command in `/usr/local/bin` and a service file for
your system. `pip install .` works too. Released versions are on the
[releases page](https://github.com/kleinmatic/emuctl/releases); `main` is the
development version.

To try it without a real emulator, see [docs/quickstart.md](docs/quickstart.md).

## Documentation

- [Quickstart](docs/quickstart.md): a first guest, using a fake emulator
- [Guest configuration](docs/config.md): every setting in a guest's file
- [States](docs/states.md): what `emuctl list` is telling you
- [Stopping](docs/stopping.md): what happens to the guest when you stop it
- [Monitoring](docs/monitoring.md): what the resource numbers mean
- Emulators: [SIMH](docs/emulators/simh.md), [es40](docs/emulators/es40.md),
  [QEMU](docs/emulators/qemu.md), [gxemul](docs/emulators/gxemul.md)
- [Networking](docs/networking.md)
- [Design](docs/design.md): why it works this way
- [Versions and releases](docs/releasing.md)
- [Supported platforms](docs/platforms.md)

## Other tools

[procServ](https://github.com/ralphlange/procServ) puts any program's
input and output on a telnet port, with logging.
[conserver](https://www.conserver.com/) shares and logs consoles for
machines that something else runs. If you only need one of those things,
they may suit you better.

## Security

Anyone who can connect to a guest's console port can type at the guest. The
port listens only on 127.0.0.1 unless you change it. Use SSH to reach it from
another machine.

## License

GPL version 2. See [LICENSE](LICENSE).
