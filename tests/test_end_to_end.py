"""The whole supervisor loop, against tests/fake_emulator.py."""
import dataclasses
import os
import sys
import time

from emuctl import guard
from emuctl.status import guest_status

BOOT = "boot.1 = >>> | boot\n"


def read_until(sock, needle, timeout=5):
    data = b""
    deadline = time.time() + timeout
    while needle not in data and time.time() < deadline:
        try:
            chunk = sock.recv(4096)
        except OSError:
            break
        if not chunk:
            break
        data += chunk
    return data


def test_boot_is_driven_to_ready_and_logged(guest):
    g = guest(extra=BOOT).start()
    g.wait_state("ready")
    assert b"[emuctl] sent: boot" in g.console()
    assert b"login: " in g.console()
    g.stop()
    assert g.state() == "down"
    assert g.sup.proc.poll() is not None


def test_self_booting_guest_needs_no_stages(guest):
    g = guest(fake_args="--self-boot").start()
    g.wait_state("ready")
    g.stop()


def test_broker_serves_several_clients_and_passes_keys(guest):
    g = guest(extra=BOOT).start()
    g.wait_state("ready")
    a, b = g.client(), g.client()
    assert b"shared" in read_until(a, b"attached")
    read_until(b, b"attached")
    a.sendall(b"hello\r\n")                  # CR-LF must arrive as one Enter
    assert b"?hello" in read_until(b, b"?hello")
    assert g.console().count(b"?hello") == 1
    a.close()
    b.close()
    g.stop()


def test_stuck_prompt_is_reported_then_cleared(guest, settings):
    g = guest(fake_args="--stuck 'Please enter date and time'",
              extra=BOOT + "stuck.1 = enter date and time\n").start()
    g.wait_state("stuck")
    row = guest_status(settings, g.name)
    assert row["state"] == "stuck" and "enter date and time" in row["detail"]
    g.stop()
    assert g.state() == "down"


def test_guest_shut_down_from_inside_reads_halted(guest):
    g = guest(extra=BOOT).start()
    g.wait_state("ready")
    c = g.client()
    c.sendall(b"shutdown\r")
    g.wait_state("halted")
    c.close()
    g.stop()
    assert g.state() == "down"


def test_emulator_that_exits_zero_is_down_not_crashed(guest):
    g = guest(fake_args="--exit-on-shutdown", extra=BOOT).start()
    g.wait_state("ready")
    c = g.client()
    c.sendall(b"shutdown\r")
    g.wait_state("down")
    g.thread.join(5)
    assert g.rc == 0


def test_emulator_that_dies_is_crashed(guest, settings):
    g = guest(fake_args="--die-after 1", extra=BOOT).start()
    g.wait_state("crashed")
    assert "exit 3" in guest_status(settings, g.name)["detail"]


def test_console_dropped_by_the_emulator_is_reattached(guest):
    g = guest(extra=BOOT).start()
    g.wait_state("ready")
    c = g.client()
    read_until(c, b"attached")
    pid = g.sup.proc.pid
    c.sendall(b"drop\r")
    assert b"lost and reattached" in read_until(c, b"reattached")
    c.sendall(b"again\r")                    # the new connection carries keys
    assert b"?again" in read_until(c, b"?again")
    assert g.sup.proc.pid == pid and g.state() == "ready"
    c.close()
    g.stop()


def test_stop_stages_shut_the_guest_down_before_the_emulator(guest):
    g = guest(extra=BOOT + "stop.1 = login: | shutdown\n").start()
    g.wait_state("ready")
    g.stop()
    log = g.console()
    assert b"[emuctl] sent: shutdown" in log
    assert log.rindex(b"system halted") > log.rindex(b"sent: shutdown")
    assert g.state() == "down"


def test_stop_stage_variables_come_from_the_environment_and_stay_out_of_the_log(guest):
    g = guest(extra=BOOT + "env = HIDDEN_CMD=shutdown\nstop.1 = login: | ${HIDDEN_CMD}\n").start()
    g.wait_state("ready")
    g.stop()
    log = g.console()
    assert b"[emuctl] sent: ${HIDDEN_CMD}" in log
    assert b"sent: shutdown" not in log
    assert b"system halted" in log
    assert g.state() == "down"


def test_stop_sequence_with_an_unset_variable_is_skipped(guest):
    g = guest(extra=BOOT + "stop.1 = login: | ${EMUCTL_TEST_UNSET_VAR}\n").start()
    g.wait_state("ready")
    g.stop()
    assert b"sent:" not in g.console().split(b"login:")[-1]
    assert b"system halted" not in g.console()


def test_halt_grace_waits_after_the_halted_prompt(guest, capsys):
    g = guest(extra=BOOT + "stop.1 = login: | shutdown\nhalt_grace = 1\n").start()
    g.wait_state("ready")
    g.stop()
    out = capsys.readouterr().out
    assert out.index("waiting 1s (halt_grace)") > out.index("reached its halted prompt")


def test_boot_stage_with_an_unset_variable_does_not_kill_the_supervisor(guest):
    g = guest(extra="boot.1 = >>> | ${EMUCTL_TEST_UNSET_VAR}\n").start()
    time.sleep(2)
    assert g.thread.is_alive()
    assert g.state() in ("booting", "starting")
    g.stop()


def test_stop_command_can_use_the_broker(guest, tmp_path):
    hook = tmp_path / "hook.py"
    hook.write_text(
        "import os, socket, time\n"
        "s = socket.create_connection((os.environ['EMUCTL_BROKER_HOST'],"
        " int(os.environ['EMUCTL_BROKER_PORT'])))\n"
        "time.sleep(0.3)\n"
        "s.sendall(b'shutdown\\r')\n"
        "time.sleep(0.5)\n")
    g = guest(extra=BOOT + f"stop_command = {sys.executable} {hook}\n").start()
    g.wait_state("ready")
    g.stop()
    assert b"system halted" in g.console()
    assert g.state() == "down"


def test_stop_command_exit_zero_counts_as_halted_without_a_pattern(guest, tmp_path, capsys):
    hook = tmp_path / "hook.py"
    hook.write_text("raise SystemExit(0)\n")
    g = guest(extra=BOOT + f"stop_command = {sys.executable} {hook}\n")
    g.sup.cfg = dataclasses.replace(g.cfg, halted=None)
    g.start()
    g.wait_state("ready")
    g.stop()
    out = capsys.readouterr().out
    assert "stop_command exited 0" in out
    assert "power cut" not in out


def test_no_in_guest_shutdown_is_tried_before_ready(guest):
    g = guest(fake_args="--stuck WAITING",
              extra=BOOT + "stuck.1 = WAITING\nstop.1 = login: | shutdown\n").start()
    g.wait_state("stuck")
    started = time.time()
    g.stop()
    assert time.time() - started < 4, "must not wait out stop_timeout"
    assert b"sent: shutdown" not in g.console()


def test_emulator_that_ignores_sigterm_is_killed(guest):
    g = guest(fake_args="--ignore-term", extra=BOOT).start()
    g.wait_state("ready")
    g.stop()
    assert g.sup.proc.returncode == -9
    assert g.state() == "down"


def test_second_start_of_the_same_guest_is_refused(guest):
    g = guest(extra=BOOT).start()
    g.wait_state("ready")
    twin = guest(name=g.name, workdir=g.workdir)
    assert twin.sup.run() == 2
    assert g.state() == "ready"
    g.stop()


def test_exclusive_peer_blocks_a_start(guest):
    a = guest(name="a", extra=BOOT).start()
    a.wait_state("ready")
    b = guest(name="b", extra=BOOT + "exclusive_with = a\n")
    assert b.sup.run() == 2
    a.stop()


def test_stale_exclusive_name_refuses_rather_than_passing(guest):
    b = guest(name="b", extra=BOOT + "exclusive_with = gone\n")
    assert b.sup.run() == 2


def test_shared_disk_image_blocks_a_second_guest(guest, tmp_path):
    a = guest(name="a", extra=BOOT).start()
    a.wait_state("ready")
    shared = os.path.join(a.workdir, "disk.img")
    b = guest(name="b", extra=BOOT)
    b.sup.cfg = b.cfg.__class__(**{**b.cfg.__dict__, "disks": [shared]})
    assert b.sup.run() == 2
    a.stop()


def test_disk_held_by_a_hand_started_emulator_is_seen(guest):
    g = guest(fake_args="--disk disk.img", extra=BOOT).start()
    g.wait_state("ready")
    disk = os.path.join(g.workdir, "disk.img")
    pids = [pid for pid, _ in guard.holders(disk)]
    assert g.sup.proc.pid in pids
    g.stop()
