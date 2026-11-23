# SPDX-License-Identifier: GPL-2.0-only
"""web/sampler.py and web/data.py: history, rates, and what the routes read."""
import os
import types

from test_cli import add_guest
from test_usage import GROUP, fake_cgroup

from emuctl.web import data, sampler
from emuctl.web.sampler import CachedProbe, Sampler, rate


def test_rate():
    assert rate(10, 20, 5) == 2
    assert rate(None, 20, 5) is None and rate(10, None, 5) is None
    assert rate(20, 10, 5) is None          # counter reset: the guest restarted
    assert rate(10, 20, 0) is None


def test_cached_probe():
    calls = []
    clock = types.SimpleNamespace(t=0)
    p = CachedProbe(lambda a, port, timeout: calls.append(a) or True, every=30,
                    clock=lambda: clock.t)
    assert p("192.0.2.1", 23) and p("192.0.2.1", 23)
    assert calls == ["192.0.2.1"]
    clock.t = 31
    p("192.0.2.1", 23)
    assert len(calls) == 2


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def running(settings, name, monkeypatch, root):
    """Make `name` look like a live guest with a cgroup under `root`."""
    import emuctl.status as status
    real = status.guest_status

    def fake_status(s, n, probe=None):
        row = real(s, n, probe)
        if n == name:
            row.update(state="ready", uptime=60)
        return row
    monkeypatch.setattr(sampler, "guest_status", fake_status)
    monkeypatch.setattr(sampler.usage, "control_groups",
                        lambda s, names, **kw: {name: GROUP} if name in names else {})
    return fake_cgroup(root)


def test_tick_works_out_cpu_and_rates(settings, tmp_path, monkeypatch):
    add_guest(settings, tmp_path, "g1")
    add_guest(settings, tmp_path, "g2", serial=21010, broker=2310)
    cg = running(settings, "g1", monkeypatch, tmp_path / "cg")
    clock = Clock()
    s = Sampler(settings, clock=clock, wall=clock, probe=lambda *a, **k: True,
                root=str(tmp_path / "cg"))
    rows = s.tick()
    assert rows[0]["cpu_pct"] is None           # one reading is not a rate
    clock.t += 5
    (cg / "cpu.stat").write_text("usage_usec 7500000\n")        # +2.5 s of CPU in 5 s
    (cg / "io.stat").write_text("8:0 rbytes=1120 wbytes=55\n")  # +1000 read
    rows = s.tick()
    assert rows[0]["cpu_pct"] == 50.0 and rows[1]["cpu_pct"] is None
    series = s.series("g1")
    assert series["cpu_pct"] == [None, 50.0]
    assert series["io_read"] == [None, 200.0] and series["io_write"] == [None, 0.0]
    assert s.series("g2")["cpu_pct"] == [None, None]
    clock.t += 5
    (cg / "cpu.stat").write_text("usage_usec 1000\n")           # restarted: no rate
    assert s.tick()[0]["cpu_pct"] is None


def test_per_nic_rates(settings, tmp_path, monkeypatch):
    add_guest(settings, tmp_path, "g1")
    running(settings, "g1", monkeypatch, tmp_path / "cg")
    counts = {"rx": 1000, "tx": 500}
    monkeypatch.setattr(sampler.network, "nic_counters_each",
                        lambda cfg, host: {"tap9": (counts["rx"], counts["tx"])})
    clock = Clock()
    s = Sampler(settings, clock=clock, wall=clock, root=str(tmp_path / "cg"),
                host_factory=object)
    s.tick()
    clock.t += 5
    counts["rx"] += 5000
    s.tick()
    assert s.series("g1")["nics"] == {"tap9": {"rx": [None, 1000.0], "tx": [None, 0.0]}}


def test_nic_failure_does_not_stop_the_sample(settings, tmp_path, monkeypatch):
    add_guest(settings, tmp_path, "g1")
    running(settings, "g1", monkeypatch, tmp_path / "cg")

    def boom(cfg, host):
        raise RuntimeError("odd config")
    monkeypatch.setattr(sampler.network, "nic_counters_each", boom)
    s = Sampler(settings, root=str(tmp_path / "cg"), host_factory=object)
    assert s.tick()[0]["name"] == "g1"


def test_history_is_bounded_and_follows_the_guest_list(settings, tmp_path):
    add_guest(settings, tmp_path, "g1")
    clock = Clock()
    s = Sampler(settings, interval=5, keep=20, clock=clock, wall=clock,
                probe=lambda *a, **k: False)
    for _ in range(10):
        clock.t += 5
        s.tick()
    assert len(s.history["g1"]) == 4
    assert len(s.series("g1", seconds=10)["t"]) == 2
    os.unlink(settings.conf_path("g1"))
    s.tick()
    assert s.history == {} and s.series("g1")["t"] == []


# --- data.py --------------------------------------------------------------------

def test_clean_text():
    raw = b"a\x1b[1;31mred\x1b[0m\r\nb\x1b]0;title\x07c\x00\x07\td\re\x1bMf"
    assert data.clean_text(raw) == "ared\nbc\td\nef"


def test_tail_bytes(tmp_path, monkeypatch):
    f = tmp_path / "log"
    f.write_bytes(b"x" * 100 + b"END")
    assert data.tail_bytes(str(f), limit=3) == b"END"
    assert data.tail_bytes(str(tmp_path)) is None            # not a regular file
    assert data.tail_bytes(str(tmp_path / "missing")) is None
    os.mkfifo(tmp_path / "fifo")
    assert data.tail_bytes(str(tmp_path / "fifo")) is None   # and never blocks
    env = tmp_path / "env"
    env.mkdir()
    (env / "g1.env").write_text("PASSWORD=x\n")
    os.symlink(env / "g1.env", tmp_path / "innocent.log")
    monkeypatch.setattr(data, "PROTECTED", [str(env)])
    assert data.tail_bytes(str(tmp_path / "innocent.log")) is None
    assert not data.safe_path(str(env)) and data.safe_path(str(f))


def test_host_info(settings, tmp_path):
    add_guest(settings, tmp_path, "g1")
    from emuctl.config import load_config
    cfg = load_config(settings.conf_path("g1"))
    gone = cfg.__class__(**{**cfg.__dict__, "workdir": "/nonexistent"})
    out = data.host_info(settings, {"g1": cfg, "bad": gone},
                         meminfo=lambda: {"MemTotal": 8, "MemAvailable": 4}, uptime=lambda: 9)
    assert (out["mem_total_bytes"], out["uptime"], out["guests"]) == (8, 9, 1)
    assert out["filesystems"][0]["guests"] == ["g1"]
    assert os.path.isdir(out["filesystems"][0]["mount"])


def test_filesystems_are_grouped_by_device(tmp_path):
    info = tmp_path / "mountinfo"
    info.write_text(
        "22 1 0:21 / / rw - ext4 /dev/sda1 rw\n"
        "30 22 0:40 /@a /srv/emu/a rw - btrfs /dev/sdb rw\n"
        "31 22 0:41 /@b /srv/emu/b rw - btrfs /dev/sdb rw\n"
        "32 22 0:42 / /srv/my\\040disk rw - tmpfs tmpfs rw\n")
    mounts = data._mounts(str(info))
    assert ("/srv/my disk", "tmpfs", "tmpfs") in mounts
    a = data._filesystem_of("/srv/emu/a/x", mounts)
    b = data._filesystem_of("/srv/emu/b", mounts)
    assert a[0] == b[0] == ("/dev/sdb", "btrfs") and a[1] == "/srv/emu/a"
    assert data._filesystem_of("/srv/my disk/x", mounts) == ("/srv/my disk", "/srv/my disk")
    assert data._filesystem_of("/home/x", mounts)[0] == ("/dev/sda1", "ext4")
    assert data._filesystem_of("/x", []) == ("/x", "/x")
    assert data._mounts(str(tmp_path / "none")) == []


def test_meminfo_and_uptime(tmp_path):
    m = tmp_path / "meminfo"
    m.write_text("MemTotal:       1000 kB\nMemAvailable:    500 kB\nHugePages_Total: 0\n")
    assert data._meminfo(str(m))["MemTotal"] == 1024000
    assert data._meminfo(str(tmp_path / "none")) == {}
    u = tmp_path / "uptime"
    u.write_text("123.45 678.9\n")
    assert data._uptime(str(u)) == 123 and data._uptime(str(tmp_path / "none")) is None


def test_stop_route():
    base = types.SimpleNamespace(stop_command=None, stop_stages=[], stop_mode="signal")
    assert "power cut" in data.stop_route(base)
    assert "stop sequence" in data.stop_route(types.SimpleNamespace(**{**base.__dict__,
                                                                       "stop_stages": [1]}))
    assert "stop_command" in data.stop_route(types.SimpleNamespace(**{**base.__dict__,
                                                                      "stop_command": ["x"]}))
    assert "saves" in data.stop_route(types.SimpleNamespace(**{**base.__dict__,
                                                               "stop_mode": "freeze"}))


def test_guest_detail_reports_a_refused_start(settings, tmp_path, monkeypatch):
    add_guest(settings, tmp_path, "g1")
    from emuctl import actions
    monkeypatch.setattr(actions, "plan_start",
                        lambda s, n, c: actions.Plan(False, 2, "REFUSING: twin is ready"))
    out = data.guest_detail(settings, "g1", {"name": "g1", "state": "down"})
    assert out["can_start"] is False and "twin" in out["start_refused"]

    def boom(*a):
        raise RuntimeError("no /proc")
    monkeypatch.setattr(actions, "plan_start", boom)
    out = data.guest_detail(settings, "g1", {"name": "g1", "state": "down"})
    assert out["can_start"] is False and "no /proc" in out["start_refused"]


def test_supervisor_log_failure():
    import subprocess

    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 15)
    s = types.SimpleNamespace(service="systemd", unit="u@{name}", elevate="none")
    assert "could not read" in data.supervisor_log(s, "g1", 10, run=boom)


def test_a_day_of_one_minute_points_saved_and_read_back(settings, tmp_path, monkeypatch):
    add_guest(settings, tmp_path, "g1")
    cg = running(settings, "g1", monkeypatch, tmp_path / "cg")
    clock = Clock()
    clock.t = 600.0                              # the start of a minute
    path = str(tmp_path / "history.json")
    s = Sampler(settings, clock=clock, wall=clock, root=str(tmp_path / "cg"), history_file=path)
    usec = 5000000
    for _ in range(13):                          # 0, 5, ... 60 s: one whole minute, then one more
        (cg / "cpu.stat").write_text(f"usage_usec {usec}\n")
        s.tick()
        clock.t += 5
        usec += 1000000                          # 1 s of CPU every 5 s: 20 %
    day = s.series("g1", seconds=sampler.DAY)
    assert day["interval"] == 60 and day["t"] == [600] and day["cpu_pct"] == [20.0]

    s.save()
    again = Sampler(settings, clock=clock, wall=clock, history_file=path)
    again.load()
    assert again.series("g1", seconds=sampler.DAY)["cpu_pct"] == [20.0]
    clock.t += sampler.DAY                       # a day later the point has aged out
    old = Sampler(settings, clock=clock, wall=clock, history_file=path)
    old.load()
    assert old.series("g1", seconds=sampler.DAY)["t"] == []


def test_a_damaged_history_file_is_ignored(settings, tmp_path):
    add_guest(settings, tmp_path, "g1")
    path = tmp_path / "history.json"
    for text in ("not json", '{"version": 1, "guests": {"g1": [{"t": "x"}]}}',
                 '{"version": 1, "guests": []}', '[]'):
        path.write_text(text)
        s = Sampler(settings, history_file=str(path))
        s.load()
        assert s.day == {}, text


def test_energy(settings, tmp_path, monkeypatch):
    add_guest(settings, tmp_path, "g1")
    add_guest(settings, tmp_path, "g2", serial=21010, broker=2310)
    cg = running(settings, "g1", monkeypatch, tmp_path / "cg")
    clock = Clock()
    clock.t = 600.0
    s = Sampler(settings, clock=clock, wall=clock, root=str(tmp_path / "cg"))
    usec = 5000000
    for _ in range(13):
        (cg / "cpu.stat").write_text(f"usage_usec {usec}\n")
        s.tick()
        clock.t += 5
        usec += 2500000                          # 50 % of a CPU
    g1, g2 = s.energy()
    assert g1["cpu_pct"] == 50.0 and g1["cpu_1h"] == 50.0 and g1["cpu_24h"] == 50.0
    assert g1["up_minutes_24h"] == 1 and g1["cpu_hours_24h"] == round(0.5 / 60, 2)
    assert g2["cpu_1h"] is None and g2["cpu_hours_24h"] == 0
