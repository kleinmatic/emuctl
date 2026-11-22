# States

`emuctl list` shows one state per guest.

| State | Meaning |
|---|---|
| `down` | No emulator is running. |
| `starting` | The emulator has been launched; its console has not answered yet. |
| `booting` | The console is attached and `ready` has not matched. |
| `stuck` | Not ready, and the console's tail matches one of the conf's `stuck.<n>` patterns. The DETAIL column shows which. Goes back to `booting` if the console moves on. |
| `ready` | The `ready` pattern matched after all boot stages ran. |
| `degraded` | `ready`, but `guest_addr`:`guest_port` does not accept a connection. |
| `halted` | The guest was `ready` and then its console showed the `halted` pattern. The emulator is still running; stopping it loses nothing. |
| `stopping` | A stop was requested and is in progress. |
| `crashed` | The emulator exited by itself with a non-zero status, or never opened its console. DETAIL has the exit status. |
| `orphaned` | The emulator is running but its supervisor is not. Nothing is logging or serving its console. Stop the emulator by hand. |
| `error` | The guest's conf cannot be read. DETAIL has the reason. |

## How a state is decided

The supervisor writes a small JSON file per guest in `run_dir` each time the
state changes. `emuctl` does not trust that file alone, because a killed
supervisor leaves its last claim behind:

1. If the recorded emulator pid is gone, or now belongs to a different
   program, the guest is `down`.
2. If the emulator is alive and the supervisor's pid is gone, it is `orphaned`.
3. Otherwise the recorded state stands, and for `ready` the network check runs.

`degraded` is computed when you ask; it is never recorded. The check opens a
TCP connection and closes it. Nothing is sent to the guest.

An emulator that exits with status 0 without being asked is recorded as
`down`, not `crashed`: that is what SIMH does when its guest shuts down.

## Exit status of `emuctl status`

| Status | Meaning |
|---|---|
| 0 | `ready` |
| 3 | running, in any other state |
| 1 | `down`, `crashed` or `error` |

`emuctl list --json` and `emuctl status <name> --json` give the same
information for scripts.
