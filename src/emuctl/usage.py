# SPDX-License-Identifier: GPL-2.0-only
"""How much of the host each guest uses.

Live numbers (CPU time, memory, tasks, disk I/O) come from the guest's systemd
unit: systemd puts each guest in its own cgroup and the kernel counts what
everything in it uses, the emulator and any helper it starts. Disk numbers come
from stat() on the guest's images and work whether the guest is up or not.

Every number is None when it cannot be read: a guest that is down, a host
without systemd, or a file the caller may not read. Nothing here raises for
those cases.
"""
import os
import subprocess
import time

CGROUP_ROOT = "/sys/fs/cgroup"
LIVE_KEYS = ("cpu_seconds", "mem_bytes", "mem_peak_bytes", "tasks",
             "io_read_bytes", "io_write_bytes")


def control_groups(settings, names, run=subprocess.run):
    """{guest: cgroup path} for running guests, from one `systemctl show` call.

    A guest whose unit is not running has an empty ControlGroup and is left out.
    """
    if settings.service != "systemd" or not names:
        return {}
    units = {settings.unit.format(name=n): n for n in names}
    try:
        out = run(["systemctl", "show", "-p", "Id,ControlGroup", "--", *units],
                  capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    # One block per unit, separated by blank lines. systemctl does not keep the
    # order the properties were asked for in (it prints ControlGroup first).
    groups = {}
    for block in out.split("\n\n"):
        props = dict(line.partition("=")[::2] for line in block.splitlines() if "=" in line)
        if props.get("ControlGroup") and props.get("Id") in units:
            groups[units[props["Id"]]] = props["ControlGroup"]
    return groups


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def _int(text):
    try:
        return int(text.strip())
    except (AttributeError, ValueError):
        return None


def cgroup_usage(path, root=CGROUP_ROOT):
    """The live numbers for one cgroup (path as systemd reports it)."""
    base = os.path.join(root, path.lstrip("/"))
    out = dict.fromkeys(LIVE_KEYS)
    stat = _read(os.path.join(base, "cpu.stat"))
    if stat:
        for line in stat.splitlines():
            k, _, v = line.partition(" ")
            if k == "usage_usec" and _int(v) is not None:
                out["cpu_seconds"] = _int(v) / 1e6
    out["mem_bytes"] = _int(_read(os.path.join(base, "memory.current")))
    out["mem_peak_bytes"] = _int(_read(os.path.join(base, "memory.peak")))
    out["tasks"] = _int(_read(os.path.join(base, "pids.current")))
    io = _read(os.path.join(base, "io.stat"))
    if io is not None:
        rd = wr = 0
        for line in io.splitlines():
            for field in line.split()[1:]:
                k, _, v = field.partition("=")
                if k == "rbytes":
                    rd += _int(v) or 0
                elif k == "wbytes":
                    wr += _int(v) or 0
        out["io_read_bytes"], out["io_write_bytes"] = rd, wr
    return out


def disk_usage(paths):
    """(bytes the images really use, their nominal size); None if any is unreadable."""
    used = nominal = 0
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            return None, None
        used += st.st_blocks * 512
        nominal += st.st_size
    return used, nominal


def attach(settings, rows, configs, groups=None, root=CGROUP_ROOT, net_host=None):
    """Add usage fields to `guest_status` rows, in place.

    `configs` maps guest name to its GuestConfig (missing for a broken conf).
    `groups` is control_groups() output; looked up here if not given.
    """
    if groups is None:
        groups = control_groups(settings, [r["name"] for r in rows if r.get("uptime") is not None])
    for row in rows:
        live = cgroup_usage(groups[row["name"]], root) if row["name"] in groups \
            else dict.fromkeys(LIVE_KEYS)
        row.update(live)
        cfg = configs.get(row["name"])
        row["disk_bytes"], row["disk_nominal_bytes"] = disk_usage(cfg.disks) if cfg \
            else (None, None)
        row["net_rx_bytes"], row["net_tx_bytes"] = _net(cfg, net_host) if cfg else (None, None)
    return rows


def _net(cfg, host):
    from . import network
    try:
        return network.nic_counters(cfg, host)
    except Exception:           # the report must not fail over one odd config
        return None, None


def cpu_percent(before, after, seconds):
    """Percent of one CPU used between two cpu_seconds readings."""
    if before is None or after is None or seconds <= 0 or after < before:
        return None
    return (after - before) / seconds * 100


def sample(settings, rows, configs, interval=1.0, root=CGROUP_ROOT, sleep=time.sleep):
    """attach(), twice `interval` apart, adding cpu_pct to each row."""
    groups = control_groups(settings, [r["name"] for r in rows if r.get("uptime") is not None])
    first = {r["name"]: r.get("cpu_seconds") for r in attach(settings, rows, configs, groups, root)}
    t0 = time.monotonic()
    sleep(interval)
    attach(settings, rows, configs, groups, root)
    elapsed = time.monotonic() - t0
    for row in rows:
        row["cpu_pct"] = cpu_percent(first.get(row["name"]), row.get("cpu_seconds"), elapsed)
    return rows


def human_bytes(n):
    if n is None:
        return "-"
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
