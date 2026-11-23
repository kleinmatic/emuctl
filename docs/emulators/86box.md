# 86Box

These notes come from five guests (MS-DOS 6.22, AT&T SVR4, UnixWare 2,
Windows NT 4 and Solaris 8 x86) on 86Box 6.0, built from upstream's own
`debian/` directory on Debian 13. Debian does not package 86Box.

## No serial console

86Box emulates a PC with a screen and keyboard. It has no serial console
emuctl can hold, so a guest says `console = none`: no `serials`, `broker`,
`ready` or stages. It counts as `ready` once 86Box has run for
`ready_after` seconds; set `guest_addr` if the guest has a network address
that answers, so a hung guest shows as `degraded`.

## A screen on a headless host

86Box is a Qt program and needs an X server. On a host with no screen, run
`Xvnc` and let 86Box draw on it; the VNC server is also how you, and the web
page's Display tab, see the guest. emuctl starts Xvnc as the guest's
`display_command` and waits for its socket (`display_wait`) before starting
86Box: Xvnc creates the socket file before it accepts connections, and an
86Box started in that gap dies with "could not connect to display".

Do not use Qt's own `vnc` platform plugin (`QT_QPA_PLATFORM=vnc`) instead.
It offers no authentication at all, and with 6.0 it crashed under real VNC
clients.

## The environment it needs

| Setting | Why |
|---|---|
| `QT_QPA_PLATFORM=xcb`, `DISPLAY=:N` | draw on the Xvnc server |
| `ALSOFT_DRIVERS=null`, `SDL_AUDIODRIVER=dummy` | a headless host has no sound card; without these 86Box dies partway through start-up, which looks like a VNC fault |
| `-N` | no "really quit?" dialog; nobody is there to click it |
| `-P <dir>` | the machine's directory, holding `86box.cfg` (emuctl reads it for the Hardware tab) |
| `-R <dir>` | the ROM set, which is not in the package |

## Stopping

86Box ignores SIGTERM and SIGINT, and quits on SIGHUP: set
`stop_signal = HUP`. That stops the emulator at once, which for the guest is
a power cut. DOS at a prompt does not mind; UnixWare, Solaris and Windows NT
do. For those, a `stop_command` that shuts the guest down over its network
(ssh, or telnet and a script) is the clean way; emuctl runs it first.

## Gotchas (86Box 6.0)

- **A moved or copied machine stops at a dialog.** 86Box records the host in
  `86box.cfg` and, for a machine with a network card, asks "This machine
  might have been moved or copied" on first start elsewhere. The emulator
  sits there, alive and idle, drawing nothing else. Answer it once on the
  Display tab; it remembers.
- **The process is called `qt_thread`,** not `86Box`, so `pkill -x 86Box`
  misses it. emuctl and systemd track it by pid and cgroup.
- **Size the display for the guest's modes plus 86Box's menu and status
  bars.** DOS text is 720 by 473 and 640 by 480 graphics about 640 by 553, so
  `-geometry 720x560` suits a DOS guest.

## Example

[`examples/86box-dos.conf`](../../examples/86box-dos.conf).
