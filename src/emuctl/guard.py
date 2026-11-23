# SPDX-License-Identifier: GPL-2.0-only
"""Refuse a start that would put two emulators on one disk image.

Four checks, run by the supervisor before it launches the emulator:
  0. the conf does not say `locked = <reason>`;
  1. no guest named in `exclusive_with` is running;
  2. no other process has one of this guest's disk images open (catches an
     emulator started by hand, outside emuctl);
  3. a lock per guest and per disk image, held for the supervisor's lifetime,
     so two starts cannot both pass checks 1 and 2.

Check 2 sees only what the calling user may inspect: on Linux, /proc/<pid>/fd
of its own processes unless run as root.
"""
import fcntl
import glob
import hashlib
import os
import platform
import subprocess

from .config import ConfigError, load_config
from .state import effective_state, read_state


class GuardError(Exception):
    pass


def holders(path):
    """[(pid, command)] for other processes that have `path` open."""
    me = os.getpid()
    found = []
    if platform.system() == "FreeBSD":
        try:
            out = subprocess.run(["fstat", path], capture_output=True, text=True,
                                 timeout=10).stdout.splitlines()[1:]
        except Exception:
            return found
        for line in out:
            cols = line.split()
            if len(cols) > 2 and cols[2].isdigit() and int(cols[2]) != me:
                found.append((int(cols[2]), cols[1]))
        return sorted(set(found))

    real = os.path.realpath(path)
    for fd in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            if os.path.realpath(os.readlink(fd)) != real:
                continue
        except OSError:
            continue
        pid = int(fd.split("/")[2])
        if pid == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            cmd = "?"
        found.append((pid, cmd))
    return sorted(set(found))


def _lock(path, what):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise GuardError(f"{what} is locked by another emuctl supervisor")
    return fd


def check(cfg, settings, locks=None):
    """Checks 0, 1 and 2. With `locks`, also take a lock per disk image and append
    it. Raises GuardError."""
    if cfg.locked:
        raise GuardError(f"{cfg.name} is locked: {cfg.locked} (remove the locked line from "
                         f"its conf to allow a start)")
    for peer in cfg.exclusive_with:
        try:
            load_config(settings.conf_path(peer))
        except (OSError, ConfigError):
            # A stale name must not quietly become "no interlock".
            raise GuardError(f"exclusive_with names '{peer}', which has no usable conf "
                             f"in {settings.guest_dir}; fix or remove the entry")
        state = effective_state(read_state(settings.run_dir, peer))
        if state not in ("down", "crashed"):
            raise GuardError(f"'{peer}' is {state}, and {cfg.name} is exclusive with it; "
                             f"stop it first: emuctl stop {peer}")
    for disk in cfg.disks:
        if not os.path.exists(disk):
            continue
        if locks is not None:
            key = hashlib.sha1(os.path.realpath(disk).encode()).hexdigest()[:16]
            locks.append(_lock(os.path.join(settings.run_dir, f"disk-{key}.lock"), disk))
        busy = holders(disk)
        if busy:
            lines = "".join(f"\n    pid {pid}: {cmd}" for pid, cmd in busy)
            raise GuardError(f"{disk} is already open by:{lines}\n"
                             f"  two emulators writing one disk image destroy it")


def check_and_lock(cfg, settings):
    """Every check, plus the locks; return the lock fds to keep open."""
    os.makedirs(settings.run_dir, exist_ok=True)
    locks = [_lock(os.path.join(settings.run_dir, f"{cfg.name}.lock"), f"guest {cfg.name}")]
    try:
        check(cfg, settings, locks)
    except Exception:
        release(locks)
        raise
    return locks


def release(locks):
    for fd in locks:
        try:
            os.close(fd)
        except OSError:
            pass
