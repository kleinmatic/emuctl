# Quickstart

This runs a first guest without an emulator, using the stand-in the test suite
uses (`tests/fake_emulator.py`). It prints a firmware prompt, boots when told
to, and halts on `shutdown`, which is enough to see every part of emuctl work.
Nothing here needs root. Run it from a clone of the repository.

If you only want to try emuctl, or are working on it, `tools/devhost` does
steps 1 to 3 for you with four ready-made fake guests:
`eval "$(tools/devhost up)"`, then `emuctl list`. The steps below show what it
sets up.

## 1. A private place for confs and state

```sh
export EMUCTL_GUEST_DIR=$HOME/emuctl-demo/guests
export EMUCTL_RUN_DIR=$HOME/emuctl-demo/run
mkdir -p $EMUCTL_GUEST_DIR $HOME/emuctl-demo/work
touch $HOME/emuctl-demo/work/disk.img
alias emuctl="PYTHONPATH=$PWD/src python3 -m emuctl.cli"
```

## 2. A guest

Save as `$EMUCTL_GUEST_DIR/demo.conf`, with your own home directory and clone
path in place of the two shown:

```ini
name     = demo
os       = fake firmware
workdir  = /home/you/emuctl-demo/work
command  = python3 /home/you/emuctl/tests/fake_emulator.py --serial 21900 --serial 21901
disks    = disk.img
serials  = 21900,21901
broker   = 2990

boot.1   = >>> | boot
ready    = login:
halted   = >>>
stop.1   = login: | shutdown
```

```sh
emuctl check demo
```

## 3. Run it

Normally the service manager runs the supervisor. For the demo, run it
yourself in a second terminal (with the same three exports):

```sh
emuctl supervise demo
```

In the first terminal:

```sh
emuctl list              # booting, then ready
emuctl info demo
emuctl console demo      # type something; Ctrl-] detaches
```

`console.log` in the workdir has everything the console printed, including
the `boot` that the supervisor typed.

## 4. Stop it

Press Ctrl-C in the supervisor's terminal. It types `shutdown` at the `login:`
prompt, waits for the `>>>` that means the guest has halted, and only then
stops the emulator. Remove the `stop.1` line and try again to see the
difference: the log says the stop was a power cut.

## 5. A real guest, as a service

1. Install: `sudo ./install.sh`.
2. Create the account the guests run as, and give it the guest's workdir:
   `sudo useradd --system --home-dir /srv/emu emuctl` (Linux) or
   `sudo pw useradd emuctl -d /srv/emu -s /usr/sbin/nologin` (FreeBSD).
   To use an existing account, set `User=` in a systemd drop-in or
   `emuctl_user` in `rc.conf`.
3. Write `/etc/emuctl/guests/<name>.conf` (`/usr/local/etc/emuctl/guests/` on
   FreeBSD). Start from the page for your emulator:
   [SIMH](emulators/simh.md), [es40](emulators/es40.md), [QEMU](emulators/qemu.md).
4. `emuctl check <name>`, then `emuctl start <name> --wait`.

Guests do not start at boot unless you enable them:
`systemctl enable emuctl-guest@<name>` on Linux, or `emuctl_enable=YES` and
`emuctl_guests="<name> ..."` in `rc.conf` on FreeBSD.
