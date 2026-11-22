# Design

## One supervisor per guest

Each guest gets its own long-running supervisor process, started by the
service manager. It launches the emulator as a child, so the service manager
sees one unit per guest and a stop reaches the right process.

The supervisor is needed, rather than a unit file that runs the emulator
directly, because of the console. SIMH with a telnet console, es40, and QEMU
with `-serial ...,server=on` all wait for a client on the console port before
they run the machine. Something has to connect, and stay connected.

## The console has one client; the broker has many

Emulator consoles accept a single connection. If the supervisor holds it,
nobody else can look. So the supervisor copies everything it reads to a log
file and to every client of a second port, the broker, and forwards what
those clients type.

- Broker clients are written to outside the lock, with a short timeout. A
  client that stops reading is dropped. Console output is never held up
  waiting for a viewer, because some emulators stall the guest when their
  console cannot drain.
- The broker speaks enough telnet for a terminal client: it announces that it
  will echo and suppress go-ahead, strips negotiation from input, and turns
  CR-LF, CR-NUL and a bare LF into one CR. Without that last step Enter
  arrives twice, and the second one is read as an empty password.
- If the emulator closes the console connection while it keeps running, the
  supervisor reconnects and writes a note into the log where output was lost.

## Everything console-side runs on one thread

One loop reads the console and decides everything that follows from a read:
logging, forwarding, boot stages, state. A stop request only sets a flag. The
loop then runs the stop sequence itself and keeps reading while it waits, so
the broker stays usable by a `stop_command` that drives the console, and the
log covers the shutdown.

## Typing at a guest is dangerous

The supervisor types only what the conf lists, when its prompt appears. The
one exception is a single Enter sent to a console that has printed nothing at
all since the supervisor attached, to make a guest that was already sitting at
its prompt print it again. Any output cancels that for good, including across
long pauses in a boot, because firmware that is counting down or probing
devices can be derailed by one key.

## Patterns match the tail

`ready`, `halted` and `stuck` are matched against the last 400 bytes of
console output. A login prompt from an earlier boot in the scrollback must not
count as ready. After `ready` matches, the buffer is cleared, so `halted` can
only match text printed afterwards.

## State is a claim; processes are facts

The state file is written on transitions and is only as current as the
supervisor that wrote it. Readers check the recorded pids before believing
it: a missing or recycled emulator pid means `down`, and a live emulator with
a dead supervisor is `orphaned`. A process owned by another user counts as
alive; `kill(pid, 0)` failing with EPERM is not "gone".

## Two emulators on one image

Starting a second emulator against a disk image that one is already writing
destroys the image. Three checks run before launch:

1. `exclusive_with`, for guests that must not coexist for reasons emuctl
   cannot see (the same machine identity or MAC address in two images). A name
   with no conf behind it is an error, not a pass.
2. A scan for any process holding one of the guest's disk images open. This
   is what catches an emulator started by hand.
3. Lock files in `run_dir`, one per guest and one per disk image, held for the
   supervisor's lifetime, so two starts cannot both pass the first two checks.

The service manager's "one instance per name" rule covers none of these.

## Stopping is two separate things

Shutting down the guest OS and stopping the emulator are different acts, and
only the second is always possible. emuctl keeps them apart in the conf
(`stop.<n>` / `stop_command` for the first, `stop_mode` for the second) and in
what it reports, and the `emuctl stop` command will not do the second alone
on a running guest without `--force`. See [stopping.md](stopping.md).

## What is deliberately absent

- **No automatic restart.** A crashed emulator stays down. Restarting a
  machine whose disk may be dirty is a decision for a person.
- **No credentials.** The supervisor holds none. A shutdown that needs one
  goes through `stop_command` and the service's environment.
- **No network or disk management.** See [networking.md](networking.md).
