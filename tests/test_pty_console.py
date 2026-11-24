# SPDX-License-Identifier: GPL-2.0-only
"""console = pty end to end: the supervisor makes a pseudo-terminal and the
emulator (tests/fake_emulator.py --pty, standing in for 86Box's host serial
passthrough) opens it as its serial port."""
import os
import socket
import sys
import threading
import time

import pytest
from conftest import FAKE, free_ports

from emuctl import supervisor
from emuctl.config import ConfigError, parse_config
from emuctl.state import read_state


class PtyGuest:
    def __init__(self, settings, workdir, extra=""):
        os.makedirs(workdir, exist_ok=True)
        open(os.path.join(workdir, "disk.img"), "ab").close()
        self.settings, self.workdir = settings, workdir
        self.broker = free_ports(1)[0]
        tty = os.path.join(settings.run_dir, "p1.tty")
        text = f"""
name = p1
workdir = {workdir}
console = pty
command = {sys.executable} {FAKE} --pty {tty}
disks = disk.img
broker = {self.broker}
boot.1 = >>> | boot
ready = login:
halted = >>>
stop.1 = login: | shutdown
stop_timeout = 10
{extra}
"""
        os.makedirs(settings.run_dir, exist_ok=True)
        with open(settings.conf_path("p1"), "w") as fh:
            fh.write(text)
        self.cfg = parse_config(text)
        self.tty = tty
        self.sup = supervisor.Supervisor(self.cfg, settings)
        self.rc = None
        self.thread = threading.Thread(target=lambda: setattr(self, "rc", self.sup.run()),
                                       daemon=True)
        self.thread.start()

    def state(self):
        return (read_state(self.settings.run_dir, "p1") or {}).get("state")

    def wait_state(self, *wanted, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state() in wanted:
                return
            time.sleep(0.05)
        raise AssertionError(f"state is {self.state()!r}, wanted {wanted}; log: {self.log()!r}")

    def log(self):
        try:
            return open(os.path.join(self.workdir, "console.log"), "rb").read()
        except OSError:
            return b""


@pytest.fixture
def pty_guest(settings, tmp_path):
    made = []

    def make(**kw):
        g = PtyGuest(settings, str(tmp_path / f"w{len(made)}"), **kw)
        made.append(g)
        return g
    yield make
    for g in made:
        if g.thread.is_alive():
            g.sup.stop_requests = 2
            g.thread.join(15)
        if g.sup.proc and g.sup.proc.poll() is None:
            g.sup.proc.kill()


def test_boot_stages_drive_a_pty_console_to_ready(pty_guest):
    g = pty_guest()
    g.wait_state("ready")
    assert b"[emuctl] sent: boot" in g.log() and b"login:" in g.log()
    assert os.path.islink(g.tty) and os.readlink(g.tty).startswith("/dev/pts/")


def test_the_broker_serves_a_pty_console(pty_guest):
    g = pty_guest()
    g.wait_state("ready")
    c = socket.create_connection(("127.0.0.1", g.broker), timeout=5)
    c.settimeout(5)
    seen = b""
    while b"shared" not in seen:
        seen += c.recv(4096)
    c.sendall(b"hello\r")
    while b"?hello" not in seen:
        seen += c.recv(4096)
    c.close()


def test_a_clean_stop_over_a_pty_and_the_link_is_removed(pty_guest):
    g = pty_guest()
    g.wait_state("ready")
    g.sup.request_stop()
    g.thread.join(20)
    assert g.state() == "down" and g.rc == 0
    assert b"system halted" in g.log()                  # the guest was shut down first
    assert not os.path.lexists(g.tty)


def test_console_pty_refuses_serials():
    with pytest.raises(ConfigError, match="remove serials"):
        parse_config("name = p\nworkdir = /w\nconsole = pty\ncommand = x\ndisks = d\n"
                     "serials = 21000\nbroker = 2300\nready = login:\n")
    c = parse_config("name = p\nworkdir = /w\nconsole = pty\ncommand = x\ndisks = d\n"
                     "broker = 2300\nready = login:\n")
    assert c.has_console and c.serials == [] and c.pty_path("/run/emuctl") == "/run/emuctl/p.tty"
