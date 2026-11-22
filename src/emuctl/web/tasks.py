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

log = logging.getLogger("emuctl.web")
KEEP = 200
MAX_OUTPUT = 200            # lines of output kept per task
ACTIONS = {"start": [], "shutdown": [], "stop": ["--force"], "stop-now": ["--now"]}
STOP_MODE = {"shutdown": "clean", "stop": "force", "stop-now": "now"}
LABELS = {"start": "Start", "shutdown": "Shutdown", "stop": "Stop (force)", "stop-now": "Stop now"}


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

    def public(self):
        d = asdict(self)
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
        self.procs = {}                 # task id -> process
        self._next = 1
        self._log_failed = False

    def _new(self, user, guest, action):
        t = Task(self._next, user, guest, action, self.clock())
        self._next += 1
        self.recent.append(t)
        del self.recent[:-self.keep]
        return t

    def get(self, task_id):
        return next((t for t in self.recent if t.id == task_id), None)

    def list(self):
        return [t.summary() for t in reversed(self.recent)]

    def _finish(self, t, result, message="", rc=None):
        t.result, t.message, t.rc, t.ended = result, message, rc, self.clock()
        if self.running.get(t.guest) is t:
            del self.running[t.guest]
        self._write(t)
        self.publish("task", t.summary())

    def _write(self, t):
        try:
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

    def decide(self, guest, action):
        if action == "start":
            return actions.plan_start(self.settings, guest)
        return actions.plan_stop(self.settings, guest, STOP_MODE[action])

    async def submit(self, user, guest, action):
        """Start a task and return it at once; the action carries on in the background."""
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        t = self._new(user, guest, action)
        self.publish("task", t.summary())
        busy = self.running.get(guest)
        if busy and not (action == "stop-now" and busy.action in ("shutdown", "stop")):
            self._finish(t, "refused", f"{guest} already has a task running: {busy.action} "
                                       f"by {busy.user} (task {busy.id})")
            return t
        try:
            plan = await asyncio.to_thread(self.decide, guest, action)
        except Exception as exc:
            self._finish(t, "failed", f"could not decide: {exc}")
            return t
        if not plan.allowed:
            self._finish(t, "refused" if plan.code else "skipped", plan.message)
            return t
        if plan.message:
            t.output.append(plan.message)
        if not busy:
            self.running[guest] = t
        log.info("%s: %s %s (task %d)", user, action, guest, t.id)
        asyncio.get_running_loop().create_task(self._run(t, plan))
        return t

    async def _run(self, t, plan):
        argv = self.command + (["stop"] if t.action != "start" else ["start"]) \
            + [t.guest] + ACTIONS[t.action]
        env = dict(os.environ, EMUCTL_ELEVATE="none", PYTHONUNBUFFERED="1")
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, env=env)
        except OSError as exc:
            self._finish(t, "failed", f"could not run emuctl: {exc}")
            return
        self.procs[t.id] = proc
        try:
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                if line and len(t.output) < MAX_OUTPUT:
                    t.output.append(line[:500])
            rc = await proc.wait()
        finally:
            self.procs.pop(t.id, None)
        result = "ok" if rc == 0 else "failed"
        last = next((ln for ln in reversed(t.output) if ln.strip()), "")
        self._finish(t, result, last if rc else (last or "done"), rc)

    async def close(self):
        """On daemon shutdown: let running actions finish on their own (they
        are emuctl commands, and a stop must not be cut off half way)."""
        for proc in list(self.procs.values()):
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                pass
