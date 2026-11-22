# SPDX-License-Identifier: GPL-2.0-only
"""What the read-only routes return. Plain functions over the core modules,
called in a worker thread; nothing here knows about HTTP."""
import os
import re
import socket
import stat
import subprocess

from .. import actions, full_version, hardware, service
from ..config import ConfigError, load_config
from ..settings import ETC

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


def _mount_point(path):
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def host_info(settings, configs, meminfo=_meminfo, uptime=_uptime):
    mem = meminfo()
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = None
    filesystems = {}
    for name, cfg in sorted(configs.items()):
        try:
            st = os.statvfs(cfg.workdir)
            dev = os.stat(cfg.workdir).st_dev
        except OSError:
            continue
        fs = filesystems.setdefault(dev, {
            "mount": _mount_point(cfg.workdir), "guests": [],
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
    out.update({
        "kind": cfg.kind,
        "workdir": cfg.workdir,
        "command": cfg.command,
        "disks": cfg.disks,
        "stop_route": stop_route(cfg),
        "clean_stop": cfg.clean_stop,
        "exclusive_with": cfg.exclusive_with,
        "console": {"bind": cfg.bind, "port": cfg.broker},
        "display": ({"host": cfg.display[0], "port": cfg.display[1], "auth": cfg.display_auth}
                    if cfg.display else None),
        "guest_addr": cfg.guest_addr,
        "nics": cfg.nics,
        "notes": bool(cfg.notes),
        "can_start": start_ok,
        "start_refused": "" if start_ok else start_why,
        "boot_timeout": cfg.boot_timeout,
    })
    return out


def guest_hardware(cfg):
    hw = hardware.describe(cfg)
    raw = []
    for path in hw.get("sources", []):
        text = hardware.read_config(path)
        if text is not None and safe_path(path):
            raw.append({"path": path, "text": text})
    hw["raw"] = raw
    return hw


def safe_path(path):
    """False for anything under the env directory, where passwords live,
    after following symlinks."""
    real = os.path.realpath(path)
    env = os.path.realpath(ENV_DIR)
    return not (real == env or real.startswith(env + os.sep))


def clean_text(data):
    return _ESCAPES.sub(b"", data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")) \
        .decode("utf-8", "replace")


def tail_bytes(path, limit=LOG_TAIL_BYTES):
    """The last `limit` bytes of a regular file, or None."""
    if not safe_path(path):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        os.lseek(fd, max(0, st.st_size - limit), os.SEEK_SET)
        chunks, left = [], limit
        while left > 0:
            b = os.read(fd, min(65536, left))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def console_log_path(cfg):
    return os.path.join(cfg.workdir, "console.log")


def console_log(cfg, lines):
    data = tail_bytes(console_log_path(cfg))
    if data is None:
        return None
    return "\n".join(clean_text(data).rstrip("\n").split("\n")[-lines:])


def supervisor_log(settings, name, lines, run=None):
    argv = service.log_argv(settings, name, lines)
    try:
        r = (run or subprocess.run)(argv, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not read the log: {exc}"
    return clean_text((r.stdout or b"")[-LOG_TAIL_BYTES:] + (r.stderr or b"")[-4096:])
