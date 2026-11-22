---
name: dev-host
description: Start a throwaway emuctl host with fake guests (tools/devhost) to try a change by hand, with no root and no emulator. Use when asked to run, demo or manually test emuctl, or to check a change end to end beyond the unit tests.
---

# Try a change on a throwaway host

`tools/devhost` makes a directory with four fake guests, a launcher for this
checkout's `emuctl`, and stand-ins for `systemctl` and `journalctl`. Nothing
touches the real system: it needs no root and reads no `/etc/emuctl`.

## Start it

```sh
eval "$(tools/devhost up)"            # default directory: $TMPDIR/emuctl-devhost
eval "$(tools/devhost up /some/dir)"  # or choose one
```

The exports point emuctl at the directory and put its `bin/` first on `PATH`.
They last for that shell only. In another shell, `eval "$(tools/devhost env)"`
picks up the same host.

## The guests

| Guest | Behaviour | Good for testing |
|---|---|---|
| `vax` | waits at `>>>`, booted by a stage; clean stop types `shutdown` | boot stages, clean stop, `halted` |
| `alpha` | boots by itself; no clean stop configured | the power-cut refusal, `--force` |
| `stuck` | boots to a date prompt and waits | the `stuck` state |
| `twin` | like `vax`, `exclusive_with = vax` | the start guard |

All run `tests/fake_emulator.py`. Their confs are in `<dir>/guests/`, their
console logs in `<dir>/work/<guest>/console.log`, and each supervisor's output
(what `emuctl log` shows) in `<dir>/logs/<guest>.log`. Edit a conf to try a
new key; the next start reads it.

## Things to do

```sh
emuctl list
emuctl start vax --wait
emuctl console vax          # Ctrl-] detaches; type `boot`, `shutdown`, `drop`
emuctl start twin           # refused while vax runs
emuctl stop vax             # clean: types shutdown, waits for >>>
emuctl start alpha && emuctl stop alpha            # refused: power cut
emuctl stop alpha --force
emuctl log vax
```

The stand-in `systemctl` understands `start`, `stop`, `is-active`, `kill`
(with `-s` and `--kill-whom=main`) and `show -p` for `emuctl-guest@` units
only. `show` reports empty values, as a host without systemd's accounting
would. If a change needs another verb, add it to `tools/devhost` and to
`tests/test_devhost.py`.

## When finished

```sh
tools/devhost down          # stops every guest it started, deletes the directory
```

If a shell was closed with guests running, `down` still finds and stops them
through their pid files.

## Keep this current

When `tools/devhost` gains a guest, a verb or a new piece (the web daemon
will be added to it), update the tables above in the same commit.
