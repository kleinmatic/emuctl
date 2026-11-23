# SPDX-License-Identifier: GPL-2.0-only
"""Tasks: every action taken from the page, who took it, and what came of it.

A start or stop runs the `emuctl` command itself as a subprocess, so the page
does exactly what the command line does. actions.py decides first, so a
refusal is reported at once as a finished task with the reason. One task per
guest at a time, except that "stop now" may cut short a stop already running.

The last KEEP tasks are kept in memory; every task is appended to the task
log as one JSON line when it finishes.
"""
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field

from .. import actions
from ..state import read_state

log = logging.getLogger("emuctl.web")
KEEP = 200
MAX_OUTPUT = 200            # lines of output kept per task
DEADLINE = 450              # seconds: past a guest's stop (TimeoutStopSec=420), inside the
                            # web unit's own TimeoutStopSec=480
ACTIONS = {"start": [], "shutdown": [], "stop": ["--force"], "stop-now": ["--now"]}
STOP_MODE = {"shutdown": "clean", "stop": "force", "stop-now": "now"}
LABELS = {"start": "Start", "shutdown": "Shutdown", "stop": "Stop (force)", "stop-now": "Stop now",
          "console": "Console", "display": "Display"}


@dataclass
class Task:
    id: int
    user: str
    guest: str
    action: str
    started: float
    ended: float = None
    result: str = "running"     # running, ok, failed, refused, skipped, note
    message: str = ""
    output: list = field(default_factory=list)
    rc: int = None
    confirmed: bool = False
    last_line: str = ""

    def public(self):
        d = asdict(self)
        del d["last_line"]
        d["label"] = LABELS.get(self.action, self.action)
        return d

    def summary(self):
        d = self.public()
        del d["output"]
        return d


class Tasks:
    def __init__(self, settings, log_path, publish=lambda event, payload: None,
                 command=None, keep=KEEP, clock=time.time):
        self.settings, self.log_path, self.publish = settings, log_path, publish
        # The same Python and package as the daemon, as `emuctl ...`.
        self.command = command or [sys.executable, "-m", "emuctl.cli"]
        self.keep, self.clock = keep, clock
        self.recent = []                # newest last
        self.running = {}               # guest -> Task
        self.cutting = {}               # guest -> a stop-now cutting a running stop short
        self.jobs = set()               # the asyncio tasks running commands
        self._next = 1
        self._log_failed = False

    def _new(self, user, guest, action):
        t = Task(self._next, user, guest, action, self.clock())
        self._next += 1
        self.recent.append(t)
        while len(self.recent) > self.keep:
            # Forget the oldest finished task; never one still running.
            old = next((x for x in self.recent if x.result != "running"), None)
            if old is None:
                break
            self.recent.remove(old)
        return t

    def get(self, task_id):
        return next((t for t in self.recent if t.id == task_id), None)

    def list(self):
        return [t.summary() for t in reversed(self.recent)]

    def _finish(self, t, result, message="", rc=None):
        t.result, t.message, t.rc, t.ended = result, message, rc, self.clock()
        for claims in (self.running, self.cutting):
            if claims.get(t.guest) is t:
                del claims[t.guest]
        self._write(t)
        self.publish("task", t.summary())

    def _write(self, t):
        try:
            if os.path.dirname(self.log_path):
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a") as fh:
                fh.write(json.dumps({**t.public(), "output": t.output[-MAX_OUTPUT:]}) + "\n")
        except OSError as exc:
            if not self._log_failed:
                log.warning("cannot write the task log %s: %s", self.log_path, exc)
                self._log_failed = True

    def note(self, user, guest, action, message):
        """Record something done that is not a start or stop, such as taking a
        console's keyboard. Finished at once."""
        t = self._new(user, guest, action)
        self.publish("task", t.summary())
        self._finish(t, "note", message)
        return t

    def current_run(self, guest):
        return (read_state(self.settings.run_dir, guest) or {}).get("started_at")

    def decide(self, guest, action):
        if action == "start":
            return actions.plan_start(self.settings, guest)
        return actions.plan_stop(self.settings, guest, STOP_MODE[action])

    async def submit(self, user, guest, action, confirmed=False, run=None):
        """Start a task and return it at once; the action carries on in the background.

        The guest is claimed before anything is awaited, so two requests at
        once cannot both get through. A stop-now that cuts in on a running
        stop claims its own slot, so only one of those runs at a time too.
        """
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        t = self._new(user, guest, action)
        self.publish("task", t.summary())
        busy = self.running.get(guest)
        if busy is None and guest in self.cutting:
            busy = self.cutting[guest]          # a stop-now still running counts as busy
        cutting_in = (busy is not None and action == "stop-now"
                      and busy.action in ("shutdown", "stop") and guest not in self.cutting)
        if busy and not cutting_in:
            other = self.cutting.get(guest) or busy
            self._finish(t, "refused", f"{guest} already has a task running: {other.action} "
                                       f"by {other.user} (task {other.id})")
            return t
        claim = self.cutting if cutting_in else self.running
        claim[guest] = t
        try:
            plan = await asyncio.to_thread(self.decide, guest, action)
        except Exception as exc:
            self._finish(t, "failed", f"could not decide: {exc}")
            return t
        if not plan.allowed:
            self._finish(t, "refused" if plan.code else "skipped", plan.message)
            return t
        if plan.power_cut and confirmed and run != self.current_run(guest):
            self._finish(t, "refused", f"{guest} has restarted since you confirmed; "
                                       f"nothing was done. Ask again.")
            return t
        if plan.power_cut and not confirmed:
            # The page asks before a power cut. If what it asked about is out
            # of date (the guest moved on), refuse rather than cut unasked.
            self._finish(t, "refused", f"{plan.message} Not confirmed; nothing was done.")
            return t
        t.confirmed = confirmed
        if plan.message:
            t.output.append(plan.message)
        log.info("%s: %s %s (task %d)", user, action, guest, t.id)
        self._write(t)              # also when it starts, in case the daemon dies mid-way
        job = asyncio.get_running_loop().create_task(self._run(t))
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)
        return t

    async def _run(self, t):
        flags = ACTIONS[t.action]
        if t.action == "stop" and not t.confirmed:
            flags = []          # not --force: the command refuses a cut that appears late
        argv = self.command + (["stop"] if t.action != "start" else ["start"]) + [t.guest] + flags
        env = dict(os.environ, EMUCTL_ELEVATE="none", PYTHONUNBUFFERED="1")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, env=env)
            pending = b""
            deadline = asyncio.get_running_loop().time() + DEADLINE
            while True:
                left = deadline - asyncio.get_running_loop().time()
                try:
                    # chunks, so one huge line cannot stop us
                    chunk = await asyncio.wait_for(proc.stdout.read(8192), max(left, 0.1))
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    self._finish(t, "failed", f"gave up after {DEADLINE} s; the command was "
                                              f"killed. See: emuctl log {t.guest}", proc.returncode)
                    return
                if not chunk:
                    break
                *lines, pending = (pending + chunk).split(b"\n")
                pending = pending[-4096:]
                for raw in lines:
                    self._add_line(t, raw)
            self._add_line(t, pending)
            rc = await proc.wait()
        except OSError as exc:
            self._finish(t, "failed", f"could not run emuctl: {exc}")
            return
        except BaseException as exc:
            # Never leave a guest marked busy: record the task as failed.
            if proc and proc.returncode is None:
                await proc.wait()
            self._finish(t, "failed", f"lost track of the command: {exc!r}",
                         proc.returncode if proc else None)
            raise
        last = t.last_line
        self._finish(t, "ok" if rc == 0 else "failed", last if rc else (last or "done"), rc)

    def _add_line(self, t, raw):
        line = raw.decode("utf-8", "replace").rstrip()[:500]
        if not line:
            return
        t.last_line = line
        if len(t.output) < MAX_OUTPUT:
            t.output.append(line)
        elif len(t.output) == MAX_OUTPUT:
            t.output.append("…")

    async def close(self):
        """On daemon shutdown: wait for running actions to finish. They are
        emuctl commands, and a stop must not be cut off half way; the unit's
        TimeoutStopSec allows for the longest stop."""
        if self.jobs:
            log.info("waiting for %d running task(s) to finish", len(self.jobs))
            await asyncio.gather(*list(self.jobs), return_exceptions=True)
