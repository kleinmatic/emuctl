# 86Box

These notes come from five guests (MS-DOS 6.22, AT&T SVR4, UnixWare 2,
Windows NT 4 and Solaris 8 x86) on 86Box 6.0, built from upstream's own
`debian/` directory on Debian 13. Debian does not package 86Box.

## Console: a serial port on a pseudo-terminal, or none

86Box emulates a PC with a screen and keyboard. If the guest OS also has a
console or a login on COM1 (Solaris and UnixWare can), give 86Box's COM1 to
emuctl: set `console = pty` in the guest's conf, and in `86box.cfg`, with the
guest stopped:

```
[Ports (COM & LPT)]
serial1_device = serial_passthrough

[Serial Passthrough (COM) #1]
path = /run/emuctl/<guest>.tty
```

emuctl makes that pseudo-terminal before it starts 86Box and holds the other
end, so the guest gets a broker, `ready` and `halted` patterns, stages for a
clean shutdown, and the web page's Console tab. Do not use 86Box's "Virtual
Console" (`serial1_device = stdio`) instead: in its pseudo-terminal mode it
stops the machine at a dialog naming the new terminal on every start, and the
name changes each time.

A guest with nothing on its serial port (DOS, Windows NT) says
`console = none`: no `serials`, `broker`, `ready` or stages. It counts as
`ready` once 86Box has run for `ready_after` seconds; set `guest_addr` if the
guest has a network address that answers, so a hung guest shows as
`degraded`.

## A screen on a headless host

86Box is a Qt program and needs an X server. On a host with no screen, run
`Xvnc` and let 86Box draw on it; the VNC server is also how you, and the web
page's Display tab, see the guest. emuctl starts Xvnc as the guest's
`display_command` and waits for its socket (`display_wait`) before starting
86Box: Xvnc creates the socket file before it accepts connections, and an
86Box started in that gap dies with "could not connect to display".

If Xvnc uses TLS (`-SecurityTypes X509Vnc`), the conf needs
`display_auth = x509vnc` and `display_ca` naming Xvnc's certificate. Put a
copy where the web daemon's account can read it, such as
`/etc/emuctl/display/`: Xvnc's own copy usually sits in a directory only the
guests' account can enter.

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
- **Turn off "Resizable window"** (`vid_resize = 0` in `86box.cfg`, or
  View → Resizable window). With no window manager in Xvnc, a resizable
  window opens small in the corner and squeezes the guest into it. Fixed, the
  window is exactly the guest's screen and follows its video mode. Edit
  `86box.cfg` only while the guest is stopped: 86Box rewrites it on exit.
- **Size Xvnc for the guest's screen plus 86Box's menu, toolbar and status
  bar, 73 pixels in all (6.0, Qt).** A 1024 by 768 desktop needs
  `-geometry 1024x841`. For DOS, text is 720 by 400 and 640 by 480 graphics
  is common, so `-geometry 720x560` covers both.

## Example

[`examples/86box-dos.conf`](../../examples/86box-dos.conf).
