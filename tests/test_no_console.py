# SPDX-License-Identifier: GPL-2.0-only
"""Guests with no serial console (console = none), such as 86Box, end to end:
a real Supervisor, tests/fake_screen.py as the emulator and
tests/fake_display.py as the display helper."""
import os
import sys
import threading
import time

import pytest

from emuctl import supervisor
from emuctl.config import ConfigError, parse_config
from emuctl.state import read_state

HERE = os.path.dirname(__file__)
SCREEN = os.path.join(HERE, "fake_screen.py")
DISPLAY = os.path.join(HERE, "fake_display.py")


class ScreenGuest:
    def __init__(self, settings, workdir, screen_args="", extra=""):
        os.makedirs(workdir, exist_ok=True)
        open(os.path.join(workdir, "disk.img"), "ab").close()
        self.settings, self.workdir = settings, workdir
        text = f"""
name = s1
workdir = {workdir}
console = none
command = {sys.executable} {SCREEN} {screen_args}
disks = disk.img
stop_timeout = 5
{extra}
{"" if "ready_after" in extra else "ready_after = 1"}
"""
        with open(settings.conf_path("s1"), "w") as fh:
            fh.write(text)
        self.cfg = parse_config(text)
        self.sup = supervisor.Supervisor(self.cfg, settings)
        self.rc = None
        self.thread = threading.Thread(target=lambda: setattr(self, "rc", self.sup.run()),
                                       daemon=True)

    def state(self):
        return (read_state(self.settings.run_dir, "s1") or {}).get("state")

    def wait_state(self, *wanted, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state() in wanted:
                return
            time.sleep(0.05)
        raise AssertionError(f"state is {self.state()!r}, wanted {wanted}")

    def stop(self):
        self.sup.request_stop()
        self.thread.join(20)
        assert not self.thread.is_alive()


@pytest.fixture
def screen(settings, tmp_path):
    made = []

    def make(**kw):
        g = ScreenGuest(settings, str(tmp_path / f"w{len(made)}"), **kw)
        made.append(g)
        g.thread.start()
        return g
    yield make
    for g in made:
        if g.thread.is_alive():
            g.sup.stop_requests = 2
            g.thread.join(15)
        for p in (g.sup.proc, g.sup.helper):
            if p and p.poll() is None:
                p.kill()


def test_ready_after_a_while_with_no_broker_and_no_log(screen):
    g = screen()
    g.wait_state("booting")
    g.wait_state("ready")
    st = read_state(g.settings.run_dir, "s1")
    assert st["broker"] is None
    assert not os.path.exists(os.path.join(g.workdir, "console.log"))
    g.stop()
    assert g.state() == "down" and g.rc == 0


def test_stop_signal_hup_stops_an_emulator_that_ignores_term(screen, monkeypatch, capsys):
    g = screen(screen_args="--hup-only", extra="stop_signal = HUP")
    g.wait_state("ready")
    t = time.time()
    g.stop()
    assert time.time() - t < 2                       # not a SIGKILL after the grace
    assert g.state() == "down" and g.sup.proc.returncode == 0
    assert "SIGKILL" not in capsys.readouterr().out


def test_the_display_helper_comes_first_and_goes_last(screen, tmp_path):
    sock, marker = str(tmp_path / "X1"), str(tmp_path / "helper-stopped")
    g = screen(screen_args=f"--needs {sock}",
               extra=f"display_command = {sys.executable} {DISPLAY} {sock} {marker}\n"
                     f"display_wait = {sock}")
    g.wait_state("ready")                            # the emulator found its display
    assert g.sup.helper.poll() is None
    g.stop()
    assert g.state() == "down" and open(marker).read() == "stopped\n"


def test_a_display_that_never_answers_stops_the_start(screen, tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "DISPLAY_WAIT", 1)
    sock, marker = str(tmp_path / "X1"), str(tmp_path / "helper-stopped")
    g = screen(extra=f"display_command = {sys.executable} {DISPLAY} {sock} {marker} --never\n"
                     f"display_wait = {sock}")
    g.thread.join(10)
    assert g.rc == 1 and g.sup.proc is None          # the emulator never started
    st = read_state(g.settings.run_dir, "s1")
    assert st["state"] == "crashed" and "did not answer" in st["error"]
    assert open(marker).read() == "stopped\n"


def test_an_emulator_that_dies_is_crashed_and_its_helper_stopped(screen, tmp_path):
    sock, marker = str(tmp_path / "X1"), str(tmp_path / "helper-stopped")
    g = screen(screen_args="--die-after 0.5",
               extra=f"display_command = {sys.executable} {DISPLAY} {sock} {marker}\n"
                     f"display_wait = {sock}")
    g.thread.join(15)
    assert g.state() == "crashed" and open(marker).read() == "stopped\n"


@pytest.mark.parametrize("extra,match", [
    ("serials = 21000", "need a serial console"),
    ("boot.1 = >>> | boot", "need a serial console"),
    ("stop_mode = freeze", "freeze"),
    ("display_wait = relative", "absolute"),
    ("display_wait = /tmp/x", "needs display_command"),
    ("stop_signal = KILL", "stop_signal"),
])
def test_console_none_refuses_what_needs_a_console(extra, match):
    text = f"name = s\nworkdir = /w\nconsole = none\ncommand = x\ndisks = d\n{extra}\n"
    with pytest.raises(ConfigError, match=match):
        parse_config(text)


def test_console_none_parses_without_the_console_keys():
    c = parse_config("name = s\nworkdir = /w\nconsole = none\ncommand = /usr/bin/86Box -P .\n"
                     "disks = d.img\nstop_signal = sighup\n")
    assert not c.has_console and c.serials == [] and c.broker is None
    assert c.stop_signal == "HUP" and c.kind == "86box" and c.ready_after == 10


def test_a_display_someone_else_already_holds_is_refused(screen, tmp_path):
    import socket as _s
    sock, marker = str(tmp_path / "X1"), str(tmp_path / "helper-stopped")
    other = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)    # another guest's server, say
    other.bind(sock)
    other.listen(1)
    try:
        g = screen(extra=f"display_command = {sys.executable} {DISPLAY} {sock} {marker}\n"
                         f"display_wait = {sock}")
        g.thread.join(10)
        assert g.rc == 1 and g.sup.proc is None and g.sup.helper is None
        assert "already answers" in read_state(g.settings.run_dir, "s1")["error"]
    finally:
        other.close()


def test_a_stop_while_waiting_for_the_display_is_down(screen, tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "DISPLAY_WAIT", 5)
    sock, marker = str(tmp_path / "X1"), str(tmp_path / "helper-stopped")
    g = screen(extra=f"display_command = {sys.executable} {DISPLAY} {sock} {marker} --never\n"
                     f"display_wait = {sock}")
    time.sleep(0.5)
    g.sup.request_stop()
    g.thread.join(10)
    assert g.rc == 0 and g.state() == "down" and g.sup.proc is None


def test_a_stop_while_booting_and_a_stop_command(screen, tmp_path):
    out = tmp_path / "ran"
    g = screen(extra=f"ready_after = 30\nstop_command = /bin/sh -c 'echo \"[$EMUCTL_BROKER_PORT]\""
                     f" > {out}'")
    g.wait_state("booting")
    g.stop()
    assert g.state() == "down"
    assert not out.exists()                          # not ready: no in-guest shutdown tried
    g2 = screen(extra=f"stop_command = /bin/sh -c 'echo \"[$EMUCTL_BROKER_PORT]\" > {out}'")
    g2.wait_state("ready")
    g2.stop()
    assert out.read_text() == "[]\n"                 # no broker: empty, not "None"
