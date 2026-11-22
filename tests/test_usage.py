# SPDX-License-Identifier: GPL-2.0-only
"""usage.py: resource numbers from cgroups and disk images."""
import os
import subprocess
import types

from emuctl import usage
from emuctl.config import parse_config
from emuctl.status import format_table

GROUP = "/system.slice/system-emuctl\\x2dguest.slice/emuctl-guest@g1.service"


def settings(service="systemd"):
    return types.SimpleNamespace(service=service, unit="emuctl-guest@{name}.service")


def fake_cgroup(root, path=GROUP, usec=5_000_000, mem=1048576, peak=2097152, pids=3):
    d = root / path.lstrip("/")
    d.mkdir(parents=True)
    (d / "cpu.stat").write_text(f"usage_usec {usec}\nuser_usec 1\nsystem_usec 2\n")
    (d / "memory.current").write_text(f"{mem}\n")
    (d / "memory.peak").write_text(f"{peak}\n")
    (d / "pids.current").write_text(f"{pids}\n")
    (d / "io.stat").write_text("8:0 rbytes=100 wbytes=50 rios=1 wios=1\n"
                               "8:16 rbytes=20 wbytes=5 rios=1 wios=1\n")
    return d


def show_output(*pairs):
    # The order systemd 257 really prints them in: ControlGroup before Id.
    return "\n\n".join(f"ControlGroup={g}\nId={u}" for u, g in pairs) + "\n"


def runner(text):
    def run(argv, **kw):
        run.argv = argv
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")
    return run


def test_control_groups_one_call_for_many():
    run = runner(show_output(("emuctl-guest@g1.service", GROUP),
                             ("emuctl-guest@g2.service", "")))
    groups = usage.control_groups(settings(), ["g1", "g2"], run=run)
    assert groups == {"g1": GROUP}
    assert run.argv[:4] == ["systemctl", "show", "-p", "Id,ControlGroup"]
    assert "emuctl-guest@g2.service" in run.argv


def test_control_groups_either_property_order():
    text = ("Id=emuctl-guest@g1.service\nControlGroup=/a\n\n"
            "ControlGroup=/b\nId=emuctl-guest@g2.service\n")
    assert usage.control_groups(settings(), ["g1", "g2"], run=runner(text)) == \
        {"g1": "/a", "g2": "/b"}


def test_control_groups_not_systemd_or_failure():
    assert usage.control_groups(settings("rcd"), ["g1"]) == {}
    assert usage.control_groups(settings(), []) == {}

    def boom(argv, **kw):
        raise OSError("no systemctl")
    assert usage.control_groups(settings(), ["g1"], run=boom) == {}


def test_cgroup_usage(tmp_path):
    fake_cgroup(tmp_path)
    u = usage.cgroup_usage(GROUP, root=str(tmp_path))
    assert u == {"cpu_seconds": 5.0, "mem_bytes": 1048576, "mem_peak_bytes": 2097152,
                 "tasks": 3, "io_read_bytes": 120, "io_write_bytes": 55}


def test_cgroup_usage_missing_files_are_none(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    (d / "memory.current").write_text("garbage\n")
    u = usage.cgroup_usage("/x", root=str(tmp_path))
    assert set(u.values()) == {None}


def test_disk_usage(tmp_path):
    a, b = tmp_path / "a.img", tmp_path / "b.img"
    a.write_bytes(b"x" * 10000)
    with open(b, "wb") as fh:          # sparse: nominal size without blocks
        fh.truncate(10 * 1024 * 1024)
    used, nominal = usage.disk_usage([str(a), str(b)])
    assert nominal == 10000 + 10 * 1024 * 1024
    assert used < nominal
    assert usage.disk_usage([str(tmp_path / "missing")]) == (None, None)


def conf(tmp_path, name="g1"):
    (tmp_path / "disk.img").write_bytes(b"x" * 4096)
    return parse_config(f"name = {name}\nworkdir = {tmp_path}\ncommand = x\ndisks = disk.img\n"
                        "serials = 1\nbroker = 2\nready = r\n")


def test_attach_running_and_down(tmp_path):
    root = tmp_path / "cg"
    fake_cgroup(root)
    rows = [{"name": "g1", "uptime": 10}, {"name": "g2", "uptime": None}, {"name": "bad"}]
    configs = {"g1": conf(tmp_path), "g2": conf(tmp_path, "g2")}
    usage.attach(settings(), rows, configs, groups={"g1": GROUP}, root=str(root))
    assert rows[0]["cpu_seconds"] == 5.0 and rows[0]["disk_nominal_bytes"] == 4096
    assert rows[1]["cpu_seconds"] is None and rows[1]["disk_nominal_bytes"] == 4096
    assert rows[2]["mem_bytes"] is None and rows[2]["disk_bytes"] is None


def test_attach_looks_up_groups_itself(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(usage, "control_groups",
                        lambda s, names, **kw: seen.setdefault("names", names) and {})
    usage.attach(settings(), [{"name": "g1", "uptime": 5}, {"name": "g2", "uptime": None}], {})
    assert seen["names"] == ["g1"]          # only live guests are asked about


def test_cpu_percent():
    assert usage.cpu_percent(1.0, 1.5, 1.0) == 50.0
    assert usage.cpu_percent(None, 1.0, 1.0) is None
    assert usage.cpu_percent(2.0, 1.0, 1.0) is None     # restarted between samples
    assert usage.cpu_percent(1.0, 2.0, 0) is None


def test_sample_adds_cpu_pct(tmp_path, monkeypatch):
    root = tmp_path / "cg"
    d = fake_cgroup(root)
    monkeypatch.setattr(usage, "control_groups", lambda s, names, **kw: {"g1": GROUP})
    clock = iter([100.0, 102.0])
    monkeypatch.setattr(usage.time, "monotonic", lambda: next(clock))

    def sleep(_):
        (d / "cpu.stat").write_text("usage_usec 6000000\n")     # +1 s of CPU
    rows = [{"name": "g1", "uptime": 5}]
    usage.sample(settings(), rows, {}, root=str(root), sleep=sleep)
    assert rows[0]["cpu_pct"] == 50.0


def test_human_bytes():
    assert usage.human_bytes(None) == "-"
    assert usage.human_bytes(512) == "512B"
    assert usage.human_bytes(1536) == "1.5K"
    assert usage.human_bytes(3 * 1024 ** 3) == "3.0G"


def test_table_with_usage_columns():
    row = {"name": "g1", "node": "g1", "os": "", "emulator": "", "state": "ready",
           "uptime": 65, "detail": "", "cpu_pct": 7.25, "mem_bytes": 2 * 1024 ** 2}
    down = dict(row, name="g2", state="down", uptime=None, cpu_pct=None, mem_bytes=None)
    out = format_table([row, down], usage=True).splitlines()
    assert out[0].split()[:5] == ["GUEST", "NODE", "STATE", "UPTIME", "CPU%"]
    assert "7.2" in out[1] and "2.0M" in out[1]
    assert out[2].split()[3:5] == ["-", "-"]


def test_unit_turns_accounting_on():
    unit = open(os.path.join(os.path.dirname(__file__), "..", "systemd",
                             "emuctl-guest@.service")).read()
    for key in ("CPUAccounting", "MemoryAccounting", "TasksAccounting", "IOAccounting"):
        assert f"{key}=yes" in unit
