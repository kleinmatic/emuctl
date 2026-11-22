# SPDX-License-Identifier: GPL-2.0-only
"""What each guest is doing.

Reads the supervisor's state file, checks it against live processes, and for
a `ready` guest with `guest_addr` set, checks that the address accepts a
connection. Nothing is ever written to a guest.
"""
import socket
import time

from .config import ConfigError, load_config
from .state import effective_state, read_state

LIVE = ("starting", "booting", "stuck", "ready", "degraded", "halted", "stopping", "orphaned")


def probe_guest(addr, port, timeout=2):
    """True if the guest accepts a TCP connection. Sends nothing."""
    try:
        socket.create_connection((addr, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def uptime_str(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}d{h:02}h"
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def guest_status(settings, name, probe=probe_guest):
    """A dict describing one guest. Never raises for a broken conf."""
    row = {"name": name, "node": "", "os": "", "emulator": "", "state": "error",
           "uptime": None, "broker": None, "detail": ""}
    try:
        cfg = load_config(settings.conf_path(name))
    except (OSError, ConfigError) as exc:
        row["detail"] = str(exc)
        return row
    raw = read_state(settings.run_dir, name)
    state = effective_state(raw)
    raw = raw or {}
    row.update(node=cfg.node, os=cfg.os, emulator=cfg.emulator, broker=cfg.broker)

    if state == "ready" and cfg.guest_addr:
        if probe(cfg.guest_addr, cfg.guest_port):
            row["detail"] = f"{cfg.guest_addr} answers"
        else:
            state = "degraded"
            row["detail"] = f"{cfg.guest_addr}:{cfg.guest_port} does not answer"
    elif state == "stuck":
        row["detail"] = f"boot is waiting at: {raw.get('stuck_on', '?')}"
    elif state == "halted":
        row["detail"] = "guest OS is down; safe to stop"
    elif state == "crashed":
        row["detail"] = raw.get("error") or f"exit {raw.get('exit_code', '?')}"
    elif state == "orphaned":
        row["detail"] = f"emulator pid {raw.get('pid')} has no supervisor"

    row["state"] = state
    if state in LIVE and raw.get("started_at"):
        row["uptime"] = int(time.time() - raw["started_at"])
    return row


def format_table(rows):
    head = ("GUEST", "NODE", "OS", "EMULATOR", "STATE", "UPTIME", "DETAIL")
    table = [head] + [
        (r["name"], r["node"], r["os"], r["emulator"], r["state"],
         uptime_str(r["uptime"]) if r["uptime"] is not None else "-", r["detail"])
        for r in rows]
    # Drop columns nobody filled in (os and emulator are optional labels).
    keep = [i for i in range(len(head))
            if i in (0, 4) or any(row[i] not in ("", "-") for row in table[1:])]
    widths = [max(len(row[i]) for row in table) for i in keep]
    return "\n".join(
        "  ".join(row[i].ljust(w) for i, w in zip(keep, widths)).rstrip() for row in table)


def format_info(settings, name, probe=probe_guest):
    cfg = load_config(settings.conf_path(name))
    row = guest_status(settings, name, probe)
    out = []

    def line(key, value):
        out.append(f"{key:<12}{value}")

    line("guest", cfg.name)
    if cfg.node != cfg.name:
        line("node", cfg.node)
    if cfg.os:
        line("os", cfg.os)
    if cfg.emulator:
        line("emulator", cfg.emulator)
    line("state", row["state"] + (f"  ({row['detail']})" if row["detail"] else ""))
    if row["uptime"] is not None:
        line("uptime", uptime_str(row["uptime"]))
    out.append("")
    line("command", " ".join(cfg.command))
    line("workdir", cfg.workdir)
    line("disks", ", ".join(cfg.disks) or "none (diskless)")
    line("console", f"emuctl console {cfg.name}   (broker {cfg.bind}:{cfg.broker}, shared)")
    line("serials", ", ".join(str(p) for p in cfg.serials)
         + "   (held by the supervisor; do not connect)")
    if cfg.guest_addr:
        line("network", f"{cfg.guest_addr} port {cfg.guest_port}")
    line("logs", f"{cfg.workdir}/console.log, {cfg.workdir}/{cfg.name}.out")
    if cfg.stop_command:
        how = "runs stop_command, then stops the emulator"
    elif cfg.stop_stages:
        how = "types the stop sequence, then stops the emulator"
    elif cfg.stop_mode == "freeze":
        how = "saves the machine state and quits (SIMH)"
    else:
        how = "stops the emulator only: a POWER CUT for the guest OS"
    line("stop", how)
    if cfg.exclusive_with:
        line("exclusive", ", ".join(cfg.exclusive_with))
    return "\n".join(out)
