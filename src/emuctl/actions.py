# SPDX-License-Identifier: GPL-2.0-only
"""Whether a start or stop may go ahead, and what it will do.

The command line and the web page both decide through these two functions,
so they cannot disagree. Neither runs anything: each returns a Plan, and the
caller runs its steps.
"""
from dataclasses import dataclass, field

from . import guard, service
from .config import load_config
from .state import effective_state, read_state

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


def plan_stop(settings, name, mode="clean", cfg=None):
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
        cut = running and state != "halted" and cfg.stop_mode != "freeze"
        msg = (f"{name}: stop now skips the guest's own shutdown; for the guest it is a "
               f"power cut" if cut else "")
        if state in ("down", "crashed"):
            return Plan(True, message=msg, power_cut=cut, steps=[stop], state=state)
        kill = service.kill_argv(settings, name, "USR2", pid=(raw or {}).get("supervisor_pid"))
        return Plan(True, message=msg, power_cut=cut,
                    steps=([kill] if kill else []) + [stop], state=state)

    clean = cfg.stop_mode == "freeze" or (state == "ready" and cfg.clean_stop)
    cut = state in CUT_STATES and not clean
    if cut:
        msg = (f"{name} is {state} and this stop does not shut the guest OS down first; "
               f"for the guest it is a power cut.")
        if mode == "clean":
            return Plan(False, 1, msg, power_cut=True, state=state)
        return Plan(True, message=msg, power_cut=True, steps=[stop], state=state)
    return Plan(True, steps=[stop], state=state)
