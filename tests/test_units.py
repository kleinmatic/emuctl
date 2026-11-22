"""Small pieces: telnet bytes, state trust, stage driving, pattern matching."""
import os
import socket
import threading
import time

import pytest

from emuctl import supervisor
from emuctl.config import Stage
from emuctl.state import effective_state, read_state, write_state
from emuctl.supervisor import StageDriver, Supervisor, match_stuck, match_tail, should_poke
from emuctl.telnet import crlf_fix, strip_telnet

# --- telnet ------------------------------------------------------------------

def test_iac_negotiation_is_stripped_even_across_reads():
    out, st = strip_telnet(b"a\xff\xfb", 0)
    assert out == b"a" and st == 2
    out, st = strip_telnet(b"\x01b\xff\xffc", st)
    assert out == b"b\xffc" and st == 0


@pytest.mark.parametrize("raw, want", [
    (b"ls\r\n", b"ls\r"), (b"ls\r\x00", b"ls\r"), (b"ls\n", b"ls\r"), (b"ls\r", b"ls\r"),
    (b"a\r\nb\r\n", b"a\rb\r"),
])
def test_enter_is_always_one_cr(raw, want):
    assert crlf_fix(raw, False)[0] == want


def test_cr_lf_split_across_reads_is_still_one_enter():
    out1, pend = crlf_fix(b"ls\r", False)
    out2, pend = crlf_fix(b"\nx", pend)
    assert out1 + out2 == b"ls\rx"


# --- state -------------------------------------------------------------------

def test_state_file_round_trips_and_is_world_readable(tmp_path):
    write_state(str(tmp_path), "g", state="ready", pid=1)
    assert read_state(str(tmp_path), "g") == {"state": "ready", "pid": 1}
    assert os.stat(tmp_path / "g.json").st_mode & 0o777 == 0o644
    assert read_state(str(tmp_path), "absent") is None


@pytest.mark.parametrize("state, alive, matches, want", [
    (None, True, True, "down"),
    ({"state": "ready", "pid": 5, "supervisor_pid": 6}, True, True, "ready"),
    ({"state": "ready", "pid": 5}, False, True, "down"),
    ({"state": "ready", "pid": 5}, True, False, "down"),            # recycled pid
    ({"state": "crashed", "pid": None}, False, False, "crashed"),
    ({"state": "nonsense", "pid": 5}, True, True, "down"),
])
def test_recorded_state_is_checked_against_processes(state, alive, matches, want):
    assert effective_state(state, lambda pid: alive, lambda pid, cmd: matches) == want


def test_live_emulator_with_dead_supervisor_is_orphaned():
    state = {"state": "ready", "pid": 5, "supervisor_pid": 6}
    assert effective_state(state, lambda pid: pid == 5, lambda pid, cmd: True) == "orphaned"


# --- stages and patterns -----------------------------------------------------

STAGES = [Stage(b"sim>", "boot cpu"), Stage(b">>>", "boot dua0")]


def test_stages_fire_in_order_and_once():
    d = StageDriver(STAGES)
    assert d.feed(b">>> ") is None                  # a later prompt seen early
    assert d.feed(b"\r\nsi") is None
    assert d.feed(b"m> ") == "boot cpu"             # split across reads
    assert d.feed(b"sim> ") is None
    assert d.feed(b"\x00>\x00>\x00>") == "boot dua0"    # NUL framing ignored
    assert d.done and d.feed(b">>>") is None


def test_patterns_match_only_near_the_tail():
    assert match_tail(b"x" * 5000 + b"\r\nlogin: ", r"login: $")
    assert not match_tail(b"login: " + b"x" * 5000, "login:")
    buf = b"y" * 2000 + b"Please enter date and time (DD-MMM-YYYY  HH:MM)  "
    assert match_stuck(buf, ["waiting to form", "enter date"]) == "enter date"
    assert match_stuck(buf, []) is None


def test_poke_only_into_total_silence():
    assert should_poke([1], saw_output=False, elapsed=5, after=3)
    assert not should_poke([1], saw_output=False, elapsed=1, after=3)
    assert not should_poke([1], saw_output=True, elapsed=3600, after=3)
    assert not should_poke([], saw_output=False, elapsed=5, after=3)


# --- broker and monitor ------------------------------------------------------

def bare_supervisor():
    sup = Supervisor.__new__(Supervisor)
    sup.clients, sup.clk, sup.log_fh = [], threading.Lock(), None
    return sup


def test_client_that_never_reads_is_dropped_not_waited_for():
    sup = bare_supervisor()
    a, b = socket.socketpair()
    a.settimeout(0.2)
    a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)
    b.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    sup.clients.append(a)
    started = time.time()
    for _ in range(40):
        sup.broadcast(b"z" * 4096)
    assert sup.clients == [] and time.time() - started < 10
    b.close()


def test_monitor_quit_waits_for_qemu_to_hang_up(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "MONITOR_DRAIN", 3)
    path = str(tmp_path / "mon.sock")
    ls = socket.socket(socket.AF_UNIX)
    ls.bind(path)
    ls.listen(1)
    seen = {}

    def qemu():
        c, _ = ls.accept()
        seen["line"] = c.recv(64)
        c.sendall(b"q\x1b[Kqu\x1b[Kquit\r\n")       # readline echo, then a pause
        time.sleep(0.5)
        seen["closed_at"] = time.time()
        c.close()

    threading.Thread(target=qemu, daemon=True).start()
    sup = bare_supervisor()
    sup.cfg = type("C", (), {"monitor_sock": path})()
    sup.monitor_quit()
    assert seen["line"] == b"quit\n"
    assert time.time() >= seen["closed_at"], "returned before QEMU hung up"


def test_monitor_quit_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "MONITOR_DRAIN", 0.5)
    path = str(tmp_path / "mon.sock")
    ls = socket.socket(socket.AF_UNIX)
    ls.bind(path)
    ls.listen(1)
    sup = bare_supervisor()
    sup.cfg = type("C", (), {"monitor_sock": path})()
    started = time.time()
    sup.monitor_quit()                              # nobody ever answers
    assert time.time() - started < 5
