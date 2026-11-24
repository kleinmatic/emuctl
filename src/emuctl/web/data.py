# SPDX-License-Identifier: GPL-2.0-only
"""What the read-only routes return. Plain functions over the core modules,
called in a worker thread; nothing here knows about HTTP."""
import os
import re
import socket
import stat
import subprocess
import threading

from .. import actions, full_version, hardware, service
from ..config import ConfigError, load_config
from ..settings import ETC
from ..state import read_state

ENV_DIR = os.path.join(ETC, "env")
LOG_TAIL_BYTES = 256 * 1024
MAX_LINES = 5000
# ANSI escape sequences and C0 controls other than tab and newline: console
# logs are full of them and they mean nothing in a text view.
_ESCAPES = re.compile(rb"\x1b(\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)|[@-Z\\-_])"
                      rb"|[\x00-\x08\x0b-\x1f\x7f]")


def _meminfo(path="/proc/meminfo"):
    out = {}
    try:
        with open(path) as fh:
            for line in fh:
                k, _, v = line.partition(":")
                parts = v.split()
                if parts and parts[0].isdigit():
                    out[k] = int(parts[0]) * 1024
    except OSError:
        pass
    return out


def _uptime(path="/proc/uptime"):
    try:
        with open(path) as fh:
            return int(float(fh.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


def _mounts(path="/proc/self/mountinfo"):
    """[(mount point, source, fstype)] from mountinfo."""
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                left, _, right = line.partition(" - ")
                f, r = left.split(), right.split()
                if len(f) >= 5 and len(r) >= 2:
                    point = f[4].replace("\\040", " ").replace("\\011", "\t")
                    out.append((point, r[1], r[0]))
    except OSError:
        pass
    return out


def _filesystem_of(path, mounts):
    """(key, mount point) for the filesystem holding `path`. Btrfs subvolumes
    and bind mounts of one device share a key, so their space is shown once."""
    real = os.path.realpath(path)
    best = None
    for point, source, fstype in mounts:
        if (real == point or real.startswith(point.rstrip("/") + "/")) \
                and (best is None or len(point) > len(best[0])):
            best = (point, source, fstype)
    if best is None:
        return real, real
    point, source, fstype = best
    shared = source.startswith("/dev/") or fstype in ("btrfs", "zfs", "xfs", "ext4")
    return ((source, fstype) if shared else point), point


def host_info(settings, configs, meminfo=_meminfo, uptime=_uptime, mounts=_mounts):
    mem = meminfo()
    table = mounts()
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = None
    filesystems = {}
    for name, cfg in sorted(configs.items()):
        try:
            st = os.statvfs(cfg.workdir)
        except OSError:
            continue
        key, point = _filesystem_of(cfg.workdir, table)
        fs = filesystems.setdefault(key, {
            "mount": point, "guests": [],
            "total_bytes": st.f_blocks * st.f_frsize, "free_bytes": st.f_bavail * st.f_frsize})
        fs["guests"].append(name)
    return {
        "hostname": socket.gethostname(),
        "version": full_version(),
        "cpus": os.cpu_count(),
        "mem_total_bytes": mem.get("MemTotal"),
        "mem_available_bytes": mem.get("MemAvailable"),
        "load": load,
        "uptime": uptime(),
        "service": settings.service,
        "guests": len(settings.guests()),
        "filesystems": list(filesystems.values()),
    }


def stop_route(cfg):
    if cfg.stop_command:
        return "runs stop_command, then stops the emulator"
    if cfg.stop_stages:
        return "types the stop sequence, then stops the emulator"
    if cfg.stop_mode == "freeze":
        return "saves the machine state and quits"
    return "stops the emulator only: a power cut for the guest OS"


SERVICES = {22: "ssh", 23: "telnet"}


def reach(cfg):
    """How to reach the guest from outside: its own address, and ports the
    emulator forwards from the host (86Box's and QEMU's user-mode NAT).
    A forward's host is None: the page fills in the name it reached us by."""
    out = []
    if cfg.guest_addr:
        out.append({"host": cfg.guest_addr, "port": cfg.guest_port,
                    "service": SERVICES.get(cfg.guest_port)})
    if cfg.kind in ("86box", "qemu"):
        try:
            hw = hardware.describe(cfg, read=careful_read)
        except Exception:                       # never let a config break the page
            hw = {}
        for f in hw.get("forwards", []):
            out.append({"host": None, "port": f["host_port"],
                        "service": SERVICES.get(f["guest_port"]), "guest_port": f["guest_port"]})
    return out


def guest_detail(settings, name, row):
    """One guest's row plus what the page needs to know about its conf."""
    out = dict(row)
    try:
        cfg = load_config(settings.conf_path(name))
    except (OSError, ConfigError) as exc:
        out["conf_error"] = str(exc)
        return out
    try:
        start = actions.plan_start(settings, name, cfg)
        start_ok, start_why = start.allowed, start.message
    except Exception as exc:                    # never let the guard break the page
        start_ok, start_why = False, f"could not check: {exc}"
    stops = {}
    for mode in actions.STOP_MODES:
        try:
            p = actions.plan_stop(settings, name, mode, cfg)
            stops[mode] = {"allowed": p.allowed and bool(p.steps), "message": p.message,
                           "power_cut": p.power_cut}
        except Exception as exc:
            stops[mode] = {"allowed": False, "message": f"could not check: {exc}",
                           "power_cut": False}
    out.update({
        "kind": cfg.kind,
        "workdir": cfg.workdir,
        "command": cfg.command,
        "disks": cfg.disks,
        "stop_route": stop_route(cfg),
        "clean_stop": cfg.clean_stop,
        "exclusive_with": cfg.exclusive_with,
        "console": {"bind": cfg.bind, "port": cfg.broker} if cfg.has_console else None,
        "display": ({"host": cfg.display[0], "port": cfg.display[1], "auth": cfg.display_auth}
                    if cfg.display else None),
        "guest_addr": cfg.guest_addr,
        "reach": reach(cfg),
        "nics": cfg.nics,
        "notes": bool(cfg.notes),
        "stop": stops,
        # Which run of the guest this is: a power-cut confirmation names it,
        # so one given for an earlier run cannot cut a later one.
        "run": (read_state(settings.run_dir, name) or {}).get("started_at"),
        "can_start": start_ok,
        "start_refused": "" if start_ok else start_why,
        "boot_timeout": cfg.boot_timeout,
    })
    return out


def careful_read(path):
    if not safe_path(path):
        raise OSError(0, "not shown: a protected file")
    return hardware.read_config(path)


def guest_hardware(cfg):
    hw = hardware.describe(cfg, read=careful_read)
    raw = []
    for path in hw.get("sources", []):
        if path == "command":
            continue
        try:
            raw.append({"path": path, "text": careful_read(path)})
        except OSError:
            pass
    hw["raw"] = raw
    return hw


# Files no route may show, even through a symlink: the env directory, where
# guests' passwords live, plus the daemon's own key, users file and state
# (app.make_app adds those).
PROTECTED = [ENV_DIR]


def _inside(real, top):
    top = os.path.realpath(top)
    return real == top or real.startswith(top.rstrip(os.sep) + os.sep)


def safe_path(path):
    """False for a protected file or anything under a protected directory,
    after following symlinks."""
    real = os.path.realpath(path)
    return not any(_inside(real, p) for p in PROTECTED)


def clean_text(data):
    return _ESCAPES.sub(b"", data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")) \
        .decode("utf-8", "replace")


def _open_checked(path, within=None):
    """An fd for the regular file at `path`, or None: never a protected file,
    even through a symlink swapped while we look.

    With `within`, the file must really be inside that directory and the last
    part of the path may not be a symlink. The file opened must be the one
    the checks looked at."""
    real = os.path.realpath(path)
    if not safe_path(path) or (within and not _inside(real, within)):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | (os.O_NOFOLLOW if within else 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        again = os.stat(real)
        if stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) == (again.st_dev, again.st_ino):
            return fd
    except OSError:
        pass
    os.close(fd)                                # not a file, or swapped while we looked
    return None


def _read_up_to(fd, limit):
    chunks, left = [], limit
    while left > 0:
        b = os.read(fd, min(65536, left))
        if not b:
            break
        chunks.append(b)
        left -= len(b)
    return b"".join(chunks)


def tail_bytes(path, limit=LOG_TAIL_BYTES, within=None):
    """The last `limit` bytes of a regular file, or None (see _open_checked)."""
    fd = _open_checked(path, within)
    if fd is None:
        return None
    try:
        os.lseek(fd, max(0, os.fstat(fd).st_size - limit), os.SEEK_SET)
        return _read_up_to(fd, limit)
    finally:
        os.close(fd)


NOTES_MAX = 256 * 1024


def read_notes(cfg):
    """{"text", "truncated"} from the guest's notes file, or None if there is none to show."""
    fd = _open_checked(cfg.notes) if cfg.notes else None
    if fd is None:
        return None
    try:
        data = _read_up_to(fd, NOTES_MAX + 1)
    finally:
        os.close(fd)
    return {"text": data[:NOTES_MAX].decode("utf-8", "replace"),
            "truncated": len(data) > NOTES_MAX}


def console_log_path(cfg):
    return os.path.join(cfg.workdir, "console.log")


def console_log(cfg, lines):
    data = tail_bytes(console_log_path(cfg), within=cfg.workdir)
    if data is None:
        return None
    return "\n".join(clean_text(data).rstrip("\n").split("\n")[-lines:])


MAX_JOURNAL = 4 * 1024 * 1024


def supervisor_log(settings, name, lines, run=None):
    """The supervisor's log, read with a cap on how much is held at once."""
    argv = service.log_argv(settings, name, lines)
    if run:                                     # tests
        try:
            r = run(argv, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"could not read the log: {exc}"
        return clean_text((r.stdout or b"")[-LOG_TAIL_BYTES:])
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
    except OSError as exc:
        return f"could not read the log: {exc}"
    buf, total = bytearray(), 0
    timer = threading.Timer(15, proc.kill)     # a hung journalctl gives up after 15 s
    timer.start()
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            total += len(chunk)
            buf += chunk
            del buf[:-LOG_TAIL_BYTES]
            if total > MAX_JOURNAL:
                break
    finally:
        timer.cancel()
        proc.kill()
        proc.wait()
    return clean_text(bytes(buf))
