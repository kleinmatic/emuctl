# SPDX-License-Identifier: GPL-2.0-only
"""Reads every guest's state and usage every few seconds and keeps a day.

The page's guest list, graphs and live updates all come from here, so the
number of browsers open does not change how often guests are looked at.

Two histories per guest: every sample for the last hour, and one-minute
averages for the last day. The day is saved to `history_file` every few
minutes and when the daemon stops, and read back when it starts.
"""
import json
import logging
import os
import threading
import time
from collections import deque

from .. import network, usage
from ..config import ConfigError, load_config
from ..status import guest_status, probe_guest

INTERVAL = 5                    # seconds between samples
KEEP = 3600                     # seconds of every-sample history per guest
DAY = 86400                     # seconds of one-minute history per guest
MINUTE = 60
PROBE_EVERY = 30                # seconds; a TCP connect to an old OS is not free
log = logging.getLogger("emuctl.web")


def _mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _round(v, places=1):
    return None if v is None else round(v, places)


def minute_point(pts, t):
    """One point standing for a minute of samples: averages of the rates and
    CPU, and the largest memory reading."""
    ifnames = {i for p in pts for i in p["nics"]}
    mem = [p["mem_bytes"] for p in pts if p["mem_bytes"] is not None]
    return {
        "t": t,
        "cpu_pct": _round(_mean(p["cpu_pct"] for p in pts)),
        "mem_bytes": max(mem) if mem else None,
        "io_read": _round(_mean(p["io_read"] for p in pts)),
        "io_write": _round(_mean(p["io_write"] for p in pts)),
        "nics": {i: [_round(_mean(p["nics"].get(i, [None, None])[0] for p in pts)),
                     _round(_mean(p["nics"].get(i, [None, None])[1] for p in pts))]
                 for i in sorted(ifnames)},
    }


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
                 wall=time.time, probe=None, root=usage.CGROUP_ROOT, host_factory=network.Host,
                 history_file=None):
        self.settings, self.interval, self.clock, self.wall = settings, interval, clock, wall
        self.points = max(2, int(keep / interval))
        self.history_file = history_file
        self.day = {}               # name -> deque of one-minute points
        self._minute = {}           # name -> the minute (wall // 60) of its last sample
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
                fine = self.history.setdefault(name, deque(maxlen=self.points))
                fine.append(point)
                self._roll_minute(name, fine, wall)
        with self._lock:
            for gone in set(self.history) - set(names):
                del self.history[gone]
                self._last.pop(gone, None)
            for gone in set(self.day) - set(names):
                del self.day[gone]
                self._minute.pop(gone, None)
        self.rows = rows
        return rows

    def _roll_minute(self, name, fine, wall):
        """When a sample starts a new minute, the minute before becomes one
        point of the day's history. Called with the lock held."""
        minute = wall // MINUTE
        before = self._minute.get(name)
        self._minute[name] = minute
        if before is None or before == minute:
            return
        start = before * MINUTE
        pts = [p for p in fine if start <= p["t"] < start + MINUTE]
        if pts:
            self.day.setdefault(name, deque(maxlen=DAY // MINUTE)).append(
                minute_point(pts, start))

    def series(self, name, seconds=KEEP):
        """The guest's history as columns, the shape the graphs draw from.
        Up to an hour: every sample. Longer: one-minute points."""
        with self._lock:
            if seconds <= KEEP:
                pts, step = list(self.history.get(name, ())), self.interval
            else:
                pts, step = list(self.day.get(name, ())), MINUTE
        if pts:
            since = int(self.wall()) - seconds
            pts = [p for p in pts if p["t"] > since]
        ifnames = sorted({i for p in pts for i in p["nics"]})
        return {
            "interval": step,
            "t": [p["t"] for p in pts],
            "cpu_pct": [p["cpu_pct"] for p in pts],
            "mem_bytes": [p["mem_bytes"] for p in pts],
            "io_read": [p["io_read"] for p in pts],
            "io_write": [p["io_write"] for p in pts],
            "nics": {i: {"rx": [p["nics"].get(i, [None, None])[0] for p in pts],
                         "tx": [p["nics"].get(i, [None, None])[1] for p in pts]}
                     for i in ifnames},
        }

    def energy(self):
        """Per guest: how long it has been up and what CPU it has used, now,
        over the hour and over the day. CPU is in percent of one host CPU;
        averages count only the time the guest was up."""
        with self._lock:
            fine = {n: [p["cpu_pct"] for p in d] for n, d in self.history.items()}
            day = {n: [p["cpu_pct"] for p in d] for n, d in self.day.items()}
        out = []
        for row in self.rows:
            name = row["name"]
            d = [v for v in day.get(name, ()) if v is not None]
            out.append({
                "name": name, "state": row["state"], "uptime": row.get("uptime"),
                "cpu_pct": row.get("cpu_pct"),
                "cpu_1h": _round(_mean(fine.get(name, ()))),
                "cpu_24h": _round(_mean(d)),
                "up_minutes_24h": len(d),
                # percent of a CPU for a minute, summed: CPU-hours over the day
                "cpu_hours_24h": round(sum(d) / 100 * MINUTE / 3600, 2),
            })
        return out

    # --- the day's history on disk ---------------------------------------------

    def save(self):
        """Write the day's history to history_file, replacing it whole."""
        if not self.history_file:
            return
        with self._lock:
            guests = {n: list(d) for n, d in self.day.items()}
        body = json.dumps({"version": 1, "step": MINUTE, "guests": guests},
                          separators=(",", ":"))
        tmp = f"{self.history_file}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
            os.replace(tmp, self.history_file)
        except OSError as exc:
            log.warning("could not save the graph history to %s: %s", self.history_file, exc)

    def load(self):
        """Read history_file back, keeping the last day for guests that still exist."""
        if not self.history_file:
            return
        try:
            with open(self.history_file) as fh:
                saved = json.load(fh)
            guests = saved["guests"] if saved.get("version") == 1 else {}
            if not isinstance(guests, dict):
                raise TypeError("guests is not an object")
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("ignored the graph history in %s: %s", self.history_file, exc)
            return
        since = int(self.wall()) - DAY
        known = set(self.settings.guests())
        with self._lock:
            for name, pts in guests.items():
                if name not in known or not isinstance(pts, list):
                    continue
                keep = [p for p in pts if isinstance(p, dict) and _valid_point(p)
                        and p["t"] > since]
                if keep:
                    self.day[name] = deque(keep[-(DAY // MINUTE):], maxlen=DAY // MINUTE)


def _valid_point(p):
    num = (int, float, type(None))
    return (isinstance(p.get("t"), int)
            and all(isinstance(p.get(k), num)
                    for k in ("cpu_pct", "mem_bytes", "io_read", "io_write"))
            and isinstance(p.get("nics"), dict)
            and all(isinstance(v, list) and len(v) == 2 and all(isinstance(x, num) for x in v)
                    for v in p["nics"].values()))
