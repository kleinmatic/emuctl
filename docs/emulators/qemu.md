# QEMU

## Console and monitor

Put the guest's serial console on a telnet port and the monitor on a UNIX
socket:

```
-serial telnet:127.0.0.1:4451,server=on
-monitor unix:/srv/emu/aix/monitor.sock,server=on,wait=off
-display none
```

With `server=on` QEMU waits for a client on the serial port before it starts
the machine; that client is the supervisor. Older QEMU spells these `server`
and `server,nowait`.

## Conf

```ini
name      = aix
os        = AIX 5.1
emulator  = qemu-system-ppc
workdir   = /srv/emu/aix
command   = /usr/local/bin/qemu-system-ppc -M 40p -m 192 -serial telnet:127.0.0.1:4451,server=on -monitor unix:/srv/emu/aix/monitor.sock,server=on,wait=off -display none -hda /srv/emu/aix/disk.qcow2
disks     = disk.qcow2
serials   = 4451
broker    = 2351

ready     = login:
stop_mode = monitor
monitor   = /srv/emu/aix/monitor.sock
```

## Things worth knowing

- **`quit` is not a shutdown.** `stop_mode = monitor` lets QEMU close its disk
  images properly, which matters for qcow2, but the guest OS still loses power.
  Add `stop.<n>` stages or a `stop_command` to shut the OS down first.
- **Firmware that reads the keyboard.** emuctl sends a single Enter only to a
  console that has printed nothing at all since it attached, and never to a
  guest without boot stages. Open Firmware aborts its start-up on any key, so
  a firmware prompt that needs a boot command should be matched with text
  specific enough not to appear in ordinary boot output.
- **Disk locking.** QEMU takes its own lock on a disk image. emuctl's guard is
  in addition to that and also covers images other emulators would open
  without checking.
