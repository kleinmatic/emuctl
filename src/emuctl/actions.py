# SPDX-License-Identifier: GPL-2.0-only
"""Whether a start or stop may go ahead, and what it will do.

The command line and the web page both decide through these two functions,
so they cannot disagree. Neither runs anything: each returns a Plan, and the
caller runs its steps.
"""
from dataclasses import dataclass, field

from . import guard, service
from .config import load_config
from .state import effective_state, read_state, supervisor_matches

STOP_MODES = ("clean", "force", "now")
RUNNING = ("ready", "booting", "stuck", "starting", "orphaned", "stopping")
# States in which stopping the emulator without a shutdown is a power cut for the guest.
CUT_STATES = ("ready", "degraded", "booting", "stuck")


@dataclass
class Plan:
    allowed: bool
    code: int = 0               # the CLI's exit status when not allowed; 0 = nothing to do
    message: str = ""           # why it is refused, or what to warn about
    power_cut: bool = False     # the guest OS will not be shut down first
    steps: list = field(default_factory=list)   # argv lists, run in order
    state: str = "down"


def plan_start(settings, name, cfg=None):
    cfg = cfg or load_config(settings.conf_path(name))
    state = effective_state(read_state(settings.run_dir, name))
    if state not in ("down", "crashed"):
        return Plan(False, 0, f"{name}: already {state}", state=state)
    try:
        guard.check(cfg, settings)
    except guard.GuardError as exc:
        return Plan(False, 2, f"REFUSING to start {name}: {exc}", state=state)
    return Plan(True, steps=[service.start_argv(settings, name)], state=state)


def _stop_now_signal(settings, name, raw, matches):
    """The command that asks the supervisor to skip the shutdown, or None.

    A supervisor from before emuctl 0.2 has no SIGUSR2 handler and would die
    of the signal, so it gets a SIGTERM instead: with the service manager's own
    SIGTERM that follows, that is a second stop request, which old supervisors
    also read as "skip the shutdown". Under rc.d the pid comes from the state
    file and is checked first, so a recycled pid is never signalled.
    """
    sig = "USR2" if raw.get("stop_now") else "TERM"
    pid = raw.get("supervisor_pid")
    if settings.service != "systemd" and not (pid and matches(pid, name)):
        return None
    return service.kill_argv(settings, name, sig, pid=pid)


def plan_stop(settings, name, mode="clean", cfg=None, matches=supervisor_matches):
    """mode: "clean" refuses a power cut, "force" accepts one, "now" asks for one.

    "now" skips the in-guest shutdown even when the conf has one, or ends one
    already under way. The emulator is still stopped by its stop_mode, so a
    SIMH guest with stop_mode = freeze is saved, not cut.
    """
    if mode not in STOP_MODES:
        raise ValueError(f"stop mode must be one of {STOP_MODES}, got {mode!r}")
    cfg = cfg or load_config(settings.conf_path(name))
    raw = read_state(settings.run_dir, name)
    state = effective_state(raw)
    running = state in RUNNING
    stop = service.stop_argv(settings, name)

    if mode == "now":
        # stop_mode = freeze saves the machine, but only once the console is up.
        cut = running and (cfg.stop_mode != "freeze" or state == "starting")
        msg = (f"{name}: stop now skips the guest's own shutdown; for the guest it is a "
               f"power cut" if cut else "")
        if state in ("down", "crashed"):
            return Plan(True, steps=[stop], state=state)
        kill = _stop_now_signal(settings, name, raw or {}, matches)
        if not kill or state == "orphaned":
            return Plan(False, 1, f"cannot reach {name}'s supervisor to skip the shutdown; "
                        f"nothing was stopped. `emuctl stop {name}` stops it normally.",
                        state=state)
        return Plan(True, message=msg, power_cut=cut, steps=[kill, stop], state=state)

    if state == "stopping":
        # A second plain stop would, under rc.d, send a second SIGTERM and cut
        # the shutdown short. That is what --now is for; say so instead.
        return Plan(False, 0, f"{name} is already stopping; `emuctl stop {name} --now` "
                    f"cuts the shutdown short", state=state)

    clean = cfg.stop_mode == "freeze" or (state == "ready" and cfg.clean_stop)
    cut = state in CUT_STATES and not clean
    if cut:
        msg = (f"{name} is {state} and this stop does not shut the guest OS down first; "
               f"for the guest it is a power cut.")
        if mode == "clean":
            return Plan(False, 1, msg, power_cut=True, state=state)
        return Plan(True, message=msg, power_cut=True, steps=[stop], state=state)
    return Plan(True, steps=[stop], state=state)
