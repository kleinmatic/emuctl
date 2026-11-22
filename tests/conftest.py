import os
import socket
import sys
import threading
import time

import pytest

from emuctl import supervisor
from emuctl.config import parse_config
from emuctl.settings import Settings
from emuctl.state import read_state

FAKE = os.path.join(os.path.dirname(__file__), "fake_emulator.py")


def free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


@pytest.fixture(autouse=True)
def fast_timers(monkeypatch):
    monkeypatch.setattr(supervisor, "CONSOLE_POLL", 0.1)
    monkeypatch.setattr(supervisor, "CONSOLE_RETRY", 0.05)
    monkeypatch.setattr(supervisor, "POKE_AFTER", 0.5)
    monkeypatch.setattr(supervisor, "STOP_GRACE", 3)


@pytest.fixture
def settings(tmp_path):
    guests = tmp_path / "guests"
    guests.mkdir()
    return Settings(guest_dir=str(guests), run_dir=str(tmp_path / "run"), service="systemd",
                    unit="emuctl-guest@{name}.service", rc_script="emuctl", elevate="none")


class Guest:
    """A fake emulator under a real Supervisor, running in a thread."""

    def __init__(self, settings, workdir, name="g1", fake_args="", extra="", serials=2):
        ports = free_ports(serials + 1)
        self.broker, self.serials = ports[0], ports[1:]
        self.settings = settings
        self.workdir = workdir
        self.name = name
        os.makedirs(workdir, exist_ok=True)
        open(os.path.join(workdir, "disk.img"), "ab").close()
        serial_args = " ".join(f"--serial {p}" for p in self.serials)
        text = f"""
name = {name}
workdir = {workdir}
command = {sys.executable} {FAKE} {serial_args} {fake_args}
disks = disk.img
serials = {",".join(map(str, self.serials))}
broker = {self.broker}
ready = login:
halted = >>>
boot_timeout = 30
console_timeout = 10
stop_timeout = 5
{extra}
"""
        with open(settings.conf_path(name), "w") as fh:
            fh.write(text)
        self.cfg = parse_config(text)
        self.sup = supervisor.Supervisor(self.cfg, settings)
        self.rc = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        self.rc = self.sup.run()

    def start(self):
        self.thread.start()
        return self

    def state(self):
        return (read_state(self.settings.run_dir, self.name) or {}).get("state")

    def wait_state(self, *wanted, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state() in wanted:
                return self.state()
            time.sleep(0.05)
        raise AssertionError(f"state is {self.state()!r}, wanted one of {wanted}; "
                             f"console: {self.console()[-300:]!r}")

    def console(self):
        try:
            with open(os.path.join(self.workdir, "console.log"), "rb") as fh:
                return fh.read()
        except OSError:
            return b""

    def client(self):
        s = socket.create_connection(("127.0.0.1", self.broker), timeout=5)
        s.settimeout(5)
        return s

    def stop(self, timeout=20):
        self.sup.request_stop()
        self.thread.join(timeout)
        assert not self.thread.is_alive(), "supervisor did not stop"

    def cleanup(self):
        if self.thread.is_alive():
            self.sup.stop_requests = 2
            self.thread.join(15)
        proc = self.sup.proc
        if proc and proc.poll() is None:
            proc.kill()


@pytest.fixture
def guest(settings, tmp_path):
    made = []

    def make(**kw):
        kw.setdefault("workdir", str(tmp_path / f"work{len(made)}"))
        g = Guest(settings, **kw)
        made.append(g)
        return g

    yield make
    for g in made:
        g.cleanup()
