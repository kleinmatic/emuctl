# SIMH

## Console

Put the simulated console on a telnet port in the simulator's ini file:

```
set console telnet=2001
```

SIMH then waits for a client on that port before it runs the machine, which is
the connection the supervisor makes. Do not also use `set console log=` to a
path the emuctl account cannot write.

Lines from a terminal multiplexer (`attach dz 2005` and the like) are guest
login ports, not the console. Leave them out of `serials`.

## Conf

```ini
name     = vax
os       = NetBSD/vax
emulator = open-simh 4.1
workdir  = /srv/emu/vax
command  = /usr/local/bin/vax vax.ini
disks    = ra0.dsk
serials  = 2001
broker   = 2301

ready    = login:
halted   = >>>
```

If the ini ends with a `boot` command the machine boots by itself and no
`boot.<n>` stage is needed. If it stops at the firmware prompt, add one:

```ini
boot.1   = >>> | boot dua0
```

## When the guest shuts down

What happens next depends on the ini. If the simulated CPU halts back to its
firmware, the console shows the firmware prompt and emuctl reports `halted`
(given a `halted` pattern). If the halt returns control to SIMH and the ini has
nothing left to run, SIMH reads end-of-file on its standard input and exits
with status 0; emuctl records that as `down`.

## Freeze

`stop_mode = freeze` suspends the machine instead of shutting it down. On stop,
emuctl sends Ctrl-E to reach the `sim>` prompt, then `save
<workdir>/frozen.state` and `quit`. Resuming is up to your ini: test for the
file, `restore` it and `continue`, otherwise `boot`. Not every simulator and
device combination restores cleanly; try it before relying on it.
