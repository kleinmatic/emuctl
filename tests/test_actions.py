# SPDX-License-Identifier: GPL-2.0-only
"""actions.py: the start and stop decisions, and `emuctl stop --now`."""
import dataclasses
import os
import signal
import time

import pytest
from test_cli import add_guest

from emuctl import actions, cli, service, supervisor
from emuctl.config import load_config
from emuctl.state import write_state

BOOT = "boot.1 = >>> | boot\n"


def set_state(settings, name, state, **extra):
    extra.setdefault("stop_now", True)
    write_state(settings.run_dir, name, state=state, pid=os.getpid(),
                supervisor_pid=os.getpid(), cmd="", started_at=0, **extra)


# --- plan_start -------------------------------------------------------------

def test_start_allowed_when_down(settings, tmp_path):
    add_guest(settings, tmp_path)
    plan = actions.plan_start(settings, "g1")
    assert plan.allowed and plan.steps == [service.start_argv(settings, "g1")]


def test_start_when_already_running_is_nothing_to_do(settings, tmp_path):
    add_guest(settings, tmp_path)
    set_state(settings, "g1", "booting")
    plan = actions.plan_start(settings, "g1")
    assert not plan.allowed and plan.code == 0 and "already booting" in plan.message


def test_start_after_a_crash_is_allowed(settings, tmp_path):
    add_guest(settings, tmp_path)
    write_state(settings.run_dir, "g1", state="crashed")
    assert actions.plan_start(settings, "g1").allowed


def test_start_refused_by_the_guard(settings, tmp_path):
    add_guest(settings, tmp_path, name="a")
    add_guest(settings, tmp_path, name="b", serial=21010, broker=2310, extra="exclusive_with = a")
    set_state(settings, "a", "ready")
    plan = actions.plan_start(settings, "b")
    assert not plan.allowed and plan.code == 2 and "exclusive" in plan.message


# --- plan_stop --------------------------------------------------------------

@pytest.mark.parametrize("state,mode,allowed,cut", [
    ("ready", "clean", False, True),        # no clean stop configured: refused
    ("ready", "force", True, True),
    ("stuck", "clean", False, True),
    ("halted", "clean", True, False),       # guest already down: nothing to lose
    ("down", "clean", True, False),
])
def test_stop_without_a_clean_route(settings, tmp_path, state, mode, allowed, cut):
    add_guest(settings, tmp_path)
    if state != "down":
        set_state(settings, "g1", state)
    plan = actions.plan_stop(settings, "g1", mode)
    assert (plan.allowed, plan.power_cut) == (allowed, cut)
    if not allowed:
        assert plan.code == 1 and "power cut" in plan.message


def test_stop_with_stages_is_clean_only_when_ready(settings, tmp_path):
    add_guest(settings, tmp_path, extra="stop.1 = login: | shutdown")
    set_state(settings, "g1", "ready")
    assert actions.plan_stop(settings, "g1").power_cut is False
    set_state(settings, "g1", "booting")        # stages only run from ready
    assert not actions.plan_stop(settings, "g1").allowed


def test_freeze_is_never_a_power_cut(settings, tmp_path):
    add_guest(settings, tmp_path, extra="stop_mode = freeze")
    set_state(settings, "g1", "booting")
    assert actions.plan_stop(settings, "g1").allowed
    assert actions.plan_stop(settings, "g1", "now").power_cut is False


def test_stop_now_signals_then_stops(settings, tmp_path):
    add_guest(settings, tmp_path, extra="stop.1 = login: | shutdown")
    set_state(settings, "g1", "ready")
    plan = actions.plan_stop(settings, "g1", "now")
    assert plan.allowed and plan.power_cut and "skips" in plan.message
    kill, stop = plan.steps
    assert kill[-5:] == ["kill", "--kill-whom=main", "-s", "USR2", "emuctl-guest@g1.service"]
    assert stop == service.stop_argv(settings, "g1")


def test_stop_now_on_a_down_guest_only_stops(settings, tmp_path):
    add_guest(settings, tmp_path)
    plan = actions.plan_stop(settings, "g1", "now")
    assert plan.steps == [service.stop_argv(settings, "g1")] and not plan.power_cut


def test_stop_now_on_an_old_supervisor_sends_term(settings, tmp_path):
    # A supervisor from before 0.2 has no SIGUSR2 handler and would die of it.
    add_guest(settings, tmp_path)
    write_state(settings.run_dir, "g1", state="ready", pid=os.getpid(),
                supervisor_pid=os.getpid(), cmd="", started_at=0)
    kill, _ = actions.plan_stop(settings, "g1", "now").steps
    assert kill[-2:] == ["TERM", "emuctl-guest@g1.service"]


def test_stop_now_under_rcd_checks_the_supervisor_pid(settings, tmp_path):
    rcd = dataclasses.replace(settings, service="rcd")
    add_guest(rcd, tmp_path)
    write_state(rcd.run_dir, "g1", state="ready", pid=os.getpid(), supervisor_pid=os.getpid(),
                cmd="", started_at=0, stop_now=True)
    kill, _ = actions.plan_stop(rcd, "g1", "now", matches=lambda pid, n: True).steps
    assert kill[-2:] == ["-USR2", str(os.getpid())]
    # A recycled pid, or none at all: nothing is signalled and nothing stopped.
    plan = actions.plan_stop(rcd, "g1", "now", matches=lambda pid, n: False)
    assert not plan.allowed and plan.code == 1 and plan.steps == []
    assert service.kill_argv(rcd, "g1", "USR2", pid=None) is None


def test_stop_now_on_an_orphan_is_refused(settings, tmp_path):
    add_guest(settings, tmp_path)
    set_state(settings, "g1", "ready")
    write_state(settings.run_dir, "g1", state="ready", pid=os.getpid(),
                supervisor_pid=999999999, cmd="", started_at=0, stop_now=True)
    plan = actions.plan_stop(settings, "g1", "now")
    assert plan.state == "orphaned" and not plan.allowed


def test_freeze_while_starting_is_a_power_cut(settings, tmp_path):
    # Before the console answers, the supervisor cannot freeze; it terminates.
    add_guest(settings, tmp_path, extra="stop_mode = freeze")
    set_state(settings, "g1", "starting")
    assert actions.plan_stop(settings, "g1", "now").power_cut is True


def test_a_second_plain_stop_while_stopping_does_nothing(settings, tmp_path):
    add_guest(settings, tmp_path, extra="stop.1 = login: | shutdown")
    set_state(settings, "g1", "stopping")
    for mode in ("clean", "force"):
        plan = actions.plan_stop(settings, "g1", mode)
        assert not plan.allowed and plan.code == 0 and "--now" in plan.message
    assert actions.plan_stop(settings, "g1", "now").allowed


def test_bad_mode(settings, tmp_path):
    add_guest(settings, tmp_path)
    with pytest.raises(ValueError):
        actions.plan_stop(settings, "g1", "gently")


def test_plans_take_a_preloaded_conf(settings, tmp_path):
    add_guest(settings, tmp_path)
    cfg = load_config(settings.conf_path("g1"))
    os.unlink(settings.conf_path("g1"))     # proves the file is not read again
    assert actions.plan_start(settings, "g1", cfg).allowed
    assert actions.plan_stop(settings, "g1", "clean", cfg).allowed


# --- the CLI on top ---------------------------------------------------------

def test_cli_stop_now(settings, tmp_path, monkeypatch, capsys):
    add_guest(settings, tmp_path, extra="stop.1 = login: | shutdown")
    set_state(settings, "g1", "ready")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    called = []
    monkeypatch.setattr(cli, "run", lambda argv: called.append(argv) or 0)
    assert cli.main(["stop", "g1", "--now"]) == 0
    assert [a[1] for a in called] == ["kill", "stop"]
    assert "power cut" in capsys.readouterr().err


def test_cli_stop_now_stops_nothing_when_the_signal_fails(settings, tmp_path, monkeypatch,
                                                          capsys):
    add_guest(settings, tmp_path, extra="stop.1 = login: | shutdown")
    set_state(settings, "g1", "ready")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    called = []
    monkeypatch.setattr(cli, "run", lambda argv: called.append(argv) or 1)
    assert cli.main(["stop", "g1", "--now"]) == 1
    assert len(called) == 1                 # the stop after the failed signal never ran
    assert "nothing was stopped" in capsys.readouterr().err


def test_cli_second_stop_while_stopping(settings, tmp_path, monkeypatch, capsys):
    add_guest(settings, tmp_path, extra="stop.1 = login: | shutdown")
    set_state(settings, "g1", "stopping")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run", lambda argv: 1 / 0)
    assert cli.main(["stop", "g1"]) == 0
    assert "already stopping" in capsys.readouterr().out


def test_cli_start_failure_from_the_service_manager(settings, tmp_path, monkeypatch, capsys):
    add_guest(settings, tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run", lambda argv: 5)
    assert cli.main(["start", "g1"]) == 5
    assert "start failed" in capsys.readouterr().err


# --- the supervisor end to end ----------------------------------------------

def test_stop_now_abandons_a_stop_sequence_under_way(guest, capsys):
    # The stop stage waits for a prompt that never comes, so the clean stop
    # would sit until stop_timeout. Stop-now must end it promptly.
    g = guest(extra=BOOT + "stop.1 = NEVER-PRINTED | shutdown\n").start()
    g.wait_state("ready")
    g.sup.request_stop()
    g.wait_state("stopping")
    time.sleep(0.5)
    t0 = time.time()
    g.sup.request_stop_now()
    g.thread.join(15)
    assert not g.thread.is_alive() and time.time() - t0 < 10
    assert g.state() == "down"
    assert "abandoning the stop sequence" in capsys.readouterr().out


def test_stop_now_on_a_ready_guest_skips_the_shutdown(guest, capsys):
    g = guest(extra=BOOT + "stop.1 = login: | shutdown\n").start()
    g.wait_state("ready")
    g.sup.request_stop_now()
    g.thread.join(15)
    assert not g.thread.is_alive() and g.state() == "down"
    out = capsys.readouterr().out
    assert "skipping the in-guest shutdown" in out and "power cut" in out
    assert b"sent: shutdown" not in g.console()


def test_stop_now_abandons_a_stop_command(guest, tmp_path, capsys):
    hook = tmp_path / "slow.sh"
    hook.write_text("#!/bin/sh\nsleep 60\n")
    hook.chmod(0o755)
    g = guest(extra=BOOT + f"stop_command = {hook}\n").start()
    g.wait_state("ready")
    g.sup.request_stop()
    g.wait_state("stopping")
    time.sleep(0.5)
    g.sup.request_stop_now()
    g.thread.join(15)
    assert not g.thread.is_alive()
    assert "abandoning stop_command" in capsys.readouterr().out


def test_stop_now_when_pump_gives_up_still_stops_the_emulator(guest):
    # A console reattach abandoned by stop-now makes pump() return False with
    # the emulator still running. The supervisor must stop it, not hang.
    g = guest(extra=BOOT).start()
    g.wait_state("ready")

    def gone():
        g.sup.stop_requests = 2
        return False
    g.sup.pump = gone
    g.thread.join(15)
    assert not g.thread.is_alive() and g.sup.proc.poll() is not None
    assert g.state() == "down"


def test_state_records_stop_now_support(guest, settings):
    g = guest(extra=BOOT).start()
    g.wait_state("ready")
    from emuctl.state import read_state
    assert read_state(settings.run_dir, g.name)["stop_now"] is True
    g.stop()


def test_supervisor_matches_checks_the_command_line():
    from emuctl.state import supervisor_matches
    assert not supervisor_matches(os.getpid(), "g1")       # pytest, not a supervisor
    assert not supervisor_matches(999999999, "g1")


def test_main_installs_the_usr2_handler(monkeypatch, settings, tmp_path):
    add_guest(settings, tmp_path)
    cfg = load_config(settings.conf_path("g1"))
    handlers = {}
    monkeypatch.setattr(supervisor.signal, "signal", lambda sig, fn: handlers.__setitem__(sig, fn))
    monkeypatch.setattr(supervisor.Supervisor, "run", lambda self: 0)
    assert supervisor.main(cfg, settings) == 0
    assert signal.SIGUSR2 in handlers and signal.SIGTERM in handlers


def test_request_stop_now_counts_as_a_second_request():
    sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
    sup.stop_requests = 0
    sup.request_stop_now()
    assert sup.stop_requests == 2
    sup.request_stop_now()
    assert sup.stop_requests == 3


def test_a_crash_left_by_the_last_run_is_not_this_runs_result(settings, tmp_path):
    add_guest(settings, tmp_path)
    write_state(settings.run_dir, "g1", state="crashed", started_at=int(time.time()) - 300)
    assert cli.current_state(settings, "g1", time.time()) == "starting"
    write_state(settings.run_dir, "g1", state="crashed", started_at=int(time.time()))
    assert cli.current_state(settings, "g1", time.time()) == "crashed"


def test_start_wait_ignores_a_stale_crash(settings, tmp_path, monkeypatch, capsys):
    add_guest(settings, tmp_path)
    write_state(settings.run_dir, "g1", state="crashed", started_at=int(time.time()) - 300)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def start(argv):
        write_state(settings.run_dir, "g1", state="ready", pid=os.getpid(),
                    supervisor_pid=os.getpid(), cmd="", started_at=int(time.time()))
        return 0
    monkeypatch.setattr(cli, "run", start)
    assert cli.main(["start", "g1", "--wait"]) == 0
    assert "crashed" not in capsys.readouterr().out
