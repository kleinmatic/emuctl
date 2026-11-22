# SPDX-License-Identifier: GPL-2.0-only
"""Reads every guest's state and usage every few seconds and keeps an hour.

The page's guest list, graphs and live updates all come from here, so the
number of browsers open does not change how often guests are looked at.
"""
import threading
import time
from collections import deque

from .. import network, usage
from ..config import ConfigError, load_config
from ..status import guest_status, probe_guest

INTERVAL = 5                    # seconds between samples
KEEP = 3600                     # seconds of history per guest
PROBE_EVERY = 30                # seconds; a TCP connect to an old OS is not free


def rate(before, after, seconds):
    """Per-second rate between two counter readings, or None. A counter that
    went backwards was reset (the guest restarted); that sample has no rate."""
    if before is None or after is None or seconds <= 0 or after < before:
        return None
    return (after - before) / seconds


class CachedProbe:
    """probe_guest, but each address is connected to at most once per `every` seconds."""

    def __init__(self, probe=probe_guest, every=PROBE_EVERY, clock=time.monotonic):
        self.probe, self.every, self.clock = probe, every, clock
        self._seen = {}

    def __call__(self, addr, port, timeout=2):
        now = self.clock()
        hit = self._seen.get((addr, port))
        if hit and now - hit[0] < self.every:
            return hit[1]
        ok = self.probe(addr, port, timeout)
        self._seen[(addr, port)] = (now, ok)
        return ok


class Sampler:
    def __init__(self, settings, interval=INTERVAL, keep=KEEP, clock=time.monotonic,
                 wall=time.time, probe=None, root=usage.CGROUP_ROOT, host_factory=network.Host):
        self.settings, self.interval, self.clock, self.wall = settings, interval, clock, wall
        self.points = max(2, int(keep / interval))
        self.probe = probe or CachedProbe()
        self.root, self.host_factory = root, host_factory
        self.rows = []
        self.history = {}           # name -> deque of points
        self._lock = threading.Lock()   # tick() runs in a thread; series() in the event loop
        self._last = {}             # name -> (monotonic time, counters)

    def configs(self, names):
        out = {}
        for name in names:
            try:
                out[name] = load_config(self.settings.conf_path(name))
            except (OSError, ConfigError):
                pass
        return out

    def tick(self):
        """Take one sample. Returns the guest rows. Blocking; run it in a thread."""
        names = self.settings.guests()
        rows = [guest_status(self.settings, n, self.probe) for n in names]
        configs = self.configs(names)
        host = self.host_factory()
        usage.attach(self.settings, rows, configs, root=self.root, net_host=host)
        now, wall = self.clock(), int(self.wall())
        for row in rows:
            name = row["name"]
            cfg = configs.get(name)
            live = row.get("uptime") is not None
            nics = {}
            if cfg and live:
                try:
                    nics = network.nic_counters_each(cfg, host)
                except Exception:
                    nics = {}
            counters = {"cpu": row.get("cpu_seconds"), "io_r": row.get("io_read_bytes"),
                        "io_w": row.get("io_write_bytes"), "nics": nics}
            prev = self._last.get(name)
            if prev and live:
                dt = now - prev[0]
                p = prev[1]
                cpu = rate(p["cpu"], counters["cpu"], dt)
                row["cpu_pct"] = None if cpu is None else round(cpu * 100, 1)
                point_nics = {}
                for ifname, (rx, tx) in nics.items():
                    old = p["nics"].get(ifname, (None, None))
                    point_nics[ifname] = [rate(old[0], rx, dt), rate(old[1], tx, dt)]
                point = {"t": wall, "cpu_pct": row["cpu_pct"], "mem_bytes": row.get("mem_bytes"),
                         "io_read": rate(p["io_r"], counters["io_r"], dt),
                         "io_write": rate(p["io_w"], counters["io_w"], dt), "nics": point_nics}
            else:
                row["cpu_pct"] = None
                point = {"t": wall, "cpu_pct": None, "mem_bytes": row.get("mem_bytes"),
                         "io_read": None, "io_write": None, "nics": {}}
            self._last[name] = (now, counters) if live else None
            with self._lock:
                self.history.setdefault(name, deque(maxlen=self.points)).append(point)
        with self._lock:
            for gone in set(self.history) - set(names):
                del self.history[gone]
                self._last.pop(gone, None)
        self.rows = rows
        return rows

    def series(self, name, seconds=KEEP):
        """The guest's history as columns, the shape the graphs draw from."""
        with self._lock:
            pts = list(self.history.get(name, ()))
        if pts:
            since = pts[-1]["t"] - seconds
            pts = [p for p in pts if p["t"] > since]
        ifnames = sorted({i for p in pts for i in p["nics"]})
        return {
            "interval": self.interval,
            "t": [p["t"] for p in pts],
            "cpu_pct": [p["cpu_pct"] for p in pts],
            "mem_bytes": [p["mem_bytes"] for p in pts],
            "io_read": [p["io_read"] for p in pts],
            "io_write": [p["io_write"] for p in pts],
            "nics": {i: {"rx": [p["nics"].get(i, [None, None])[0] for p in pts],
                         "tx": [p["nics"].get(i, [None, None])[1] for p in pts]}
                     for i in ifnames},
        }
