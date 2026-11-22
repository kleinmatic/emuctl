# SPDX-License-Identifier: GPL-2.0-only
"""The supervisor's state file, and the rule for trusting it.

A recorded state is a claim, not a fact: a killed supervisor leaves its last
state behind. Every read is checked against live processes before it is
believed.
"""
import json
import os
import subprocess
import tempfile

STATES = ("down", "starting", "booting", "stuck", "ready", "halted",
          "stopping", "crashed", "orphaned")


def _path(rundir, name):
    return os.path.join(rundir, f"{name}.json")


def write_state(rundir, name, **fields):
    """Atomically replace the state file."""
    os.makedirs(rundir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=rundir, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(fields, fh)
        # mkstemp creates 0600. State is not secret (pids, ports, timestamps)
        # and `emuctl list` is usually run by a different user.
        os.chmod(tmp, 0o644)
        os.replace(tmp, _path(rundir, name))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_state(rundir, name):
    try:
        with open(_path(rundir, name)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def proc_alive(pid):
    """True if `pid` exists, including a process owned by another user."""
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True                     # exists, just not ours to signal
    except (OSError, TypeError, OverflowError, ValueError):
        return False


def proc_matches(pid, needle):
    """True if `pid` still looks like the emulator that was launched. Guards
    against a recycled pid."""
    if not needle:
        return True
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    return os.path.basename(needle.split()[0]) in out


def effective_state(state, alive=proc_alive, matches=proc_matches):
    """The state to report, from the recorded claim plus live process facts."""
    if not state:
        return "down"
    recorded = state.get("state", "down")
    if recorded == "crashed":
        return "crashed"
    pid = state.get("pid")
    if not pid or not alive(pid) or not matches(pid, state.get("cmd", "")):
        return "down"                   # emulator gone, or the pid was recycled
    sup = state.get("supervisor_pid")
    if sup and not alive(sup):
        return "orphaned"               # emulator alive, nothing owns it
    return recorded if recorded in STATES else "down"
