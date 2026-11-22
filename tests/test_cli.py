"""Settings, status formatting, `check`, and the built-in console client."""
import json
import os
import socket
import threading

import pytest

from emuctl import cli, service
from emuctl.settings import Settings, SettingsError, load_settings
from emuctl.state import write_state
from emuctl.status import format_info, format_table, guest_status, uptime_str

CONF = """
name = {name}
node = MINI
os = Unix V7
workdir = {workdir}
command = /bin/sleep 600
disks = disk.img
serials = {serial}
broker = {broker}
ready = login:
{extra}
"""


def add_guest(settings, tmp_path, name="g1", serial=21000, broker=2300, extra=""):
    work = tmp_path / name
    work.mkdir(exist_ok=True)
    (work / "disk.img").write_bytes(b"")
    with open(settings.conf_path(name), "w") as fh:
        fh.write(CONF.format(name=name, workdir=work, serial=serial, broker=broker, extra=extra))


def test_settings_come_from_file_then_environment(tmp_path):
    conf = tmp_path / "emuctl.conf"
    conf.write_text("guest_dir = /a   # where confs live\nservice = rcd\n")
    s = load_settings({"EMUCTL_CONF": str(conf), "EMUCTL_RUN_DIR": "/b"})
    assert (s.guest_dir, s.run_dir, s.service) == ("/a", "/b", "rcd")
    conf.write_text("colour = blue\n")
    with pytest.raises(SettingsError):
        load_settings({"EMUCTL_CONF": str(conf)})
    conf.write_text("service = launchd\n")
    with pytest.raises(SettingsError):
        load_settings({"EMUCTL_CONF": str(conf)})


def test_service_commands_follow_the_settings(settings):
    assert service.start_argv(settings, "g1") == ["systemctl", "start", "emuctl-guest@g1.service"]
    bsd = Settings(**{**settings.__dict__, "service": "rcd", "elevate": "doas"})
    if os.geteuid() != 0:
        assert service.stop_argv(bsd, "g1") == ["doas", "service", "emuctl", "onestop", "g1"]


def test_status_of_a_guest_that_is_not_running(settings, tmp_path):
    add_guest(settings, tmp_path)
    row = guest_status(settings, "g1")
    assert (row["state"], row["node"], row["uptime"]) == ("down", "MINI", None)
    assert "MINI" not in format_table([row]) and "MINI" in format_table([row], node=True)


def test_ready_guest_whose_address_is_silent_is_degraded(settings, tmp_path):
    add_guest(settings, tmp_path, extra="guest_addr = 192.0.2.7")
    write_state(settings.run_dir, "g1", state="ready", pid=os.getpid(),
                supervisor_pid=os.getpid(), cmd="", started_at=0)
    assert guest_status(settings, "g1", probe=lambda a, p: True)["state"] == "ready"
    row = guest_status(settings, "g1", probe=lambda a, p: False)
    assert row["state"] == "degraded" and "192.0.2.7:23" in row["detail"]


def test_broken_conf_is_a_row_not_a_traceback(settings):
    with open(settings.conf_path("bad"), "w") as fh:
        fh.write("name = bad\n")
    row = guest_status(settings, "bad")
    assert row["state"] == "error" and "missing required key" in row["detail"]


def test_info_says_when_stop_is_a_power_cut(settings, tmp_path):
    add_guest(settings, tmp_path)
    assert "POWER CUT" in format_info(settings, "g1")
    add_guest(settings, tmp_path, name="g2", serial=21010, broker=2310,
              extra="stop.1 = # | halt")
    assert "types the stop sequence" in format_info(settings, "g2")


def test_uptime_formats():
    assert [uptime_str(s) for s in (59, 3600, 90000)] == ["0:59", "1:00:00", "1d01h"]


def test_check_reports_errors_and_warnings(settings, tmp_path):
    add_guest(settings, tmp_path)
    errors, warnings = cli.check_guest(settings, "g1")
    assert errors == []
    assert any("power cut" in w for w in warnings)

    add_guest(settings, tmp_path, name="g2", broker=2300, serial=21010,
              extra="exclusive_with = nobody\nbind = 0.0.0.0\ncolour = blue")
    os.unlink(tmp_path / "g2" / "disk.img")
    errors, warnings = cli.check_guest(settings, "g2")
    text = " | ".join(errors)
    assert "does not exist" in text and "'nobody'" in text and "also used by guest 'g1'" in text
    assert any("unauthenticated" in w for w in warnings)
    assert any("colour" in w for w in warnings)


def test_check_sees_a_port_already_in_use(settings, tmp_path):
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    add_guest(settings, tmp_path, broker=holder.getsockname()[1])
    errors, _ = cli.check_guest(settings, "g1")
    assert any("already in use" in e for e in errors)
    holder.close()


def test_list_json_and_status_exit_codes(settings, tmp_path, monkeypatch, capsys):
    add_guest(settings, tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert cli.main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "g1"
    assert cli.main(["status", "g1"]) == 1                  # down
    write_state(settings.run_dir, "g1", state="booting", pid=os.getpid(),
                supervisor_pid=os.getpid(), cmd="", started_at=0)
    assert cli.main(["status", "g1"]) == 3                  # running, not ready
    with pytest.raises(SystemExit) as exc:
        cli.main(["info", "nope"])
    assert exc.value.code == 2


def test_stop_refuses_a_power_cut_without_force(settings, tmp_path, monkeypatch, capsys):
    add_guest(settings, tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    called = []
    monkeypatch.setattr(cli, "run", lambda argv: called.append(argv) or 0)
    write_state(settings.run_dir, "g1", state="ready", pid=os.getpid(),
                supervisor_pid=os.getpid(), cmd="", started_at=0)
    assert cli.main(["stop", "g1"]) == 1 and called == []
    assert "power cut" in capsys.readouterr().err
    assert cli.main(["stop", "g1", "--force"]) == 0 and len(called) == 1
    write_state(settings.run_dir, "g1", state="halted", pid=os.getpid(),
                supervisor_pid=os.getpid(), cmd="", started_at=0)
    assert cli.main(["stop", "g1"]) == 0 and len(called) == 2   # halted: nothing to lose


def test_console_client_relays_both_ways_and_detaches(tmp_path):
    server, client = socket.socketpair()
    r, w = os.pipe()
    out_r, out_w = os.pipe()
    t = threading.Thread(target=cli.console_client, daemon=True,
                         args=(client, os.fdopen(r, "rb", 0), os.fdopen(out_w, "wb", 0)))
    t.start()
    server.sendall(b"\xff\xfb\x01login: ")                   # negotiation is not shown
    assert os.read(out_r, 64) == b"login: "
    os.write(w, b"root\r")
    assert server.recv(64) == b"root\r"
    os.write(w, b"x\x1dignored")                             # Ctrl-] detaches
    assert server.recv(64) == b"x"
    t.join(5)
    assert not t.is_alive()


def test_start_reports_a_refusal_without_calling_the_service_manager(
        settings, tmp_path, monkeypatch, capsys):
    add_guest(settings, tmp_path, name="a")
    add_guest(settings, tmp_path, name="b", serial=21010, broker=2310, extra="exclusive_with = a")
    write_state(settings.run_dir, "a", state="ready", pid=os.getpid(),
                supervisor_pid=os.getpid(), cmd="", started_at=0)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    called = []
    monkeypatch.setattr(cli, "run", lambda argv: called.append(argv) or 0)
    assert cli.main(["start", "b"]) == 2
    assert called == [] and "'a' is ready" in capsys.readouterr().err
    assert cli.main(["start", "a"]) == 0 and called == []        # already running
