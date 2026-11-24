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
           "uptime": None, "broker": None, "detail": "",
           "locked": ""}
    try:
        cfg = load_config(settings.conf_path(name))
    except (OSError, ConfigError) as exc:
        row["detail"] = str(exc)
        return row
    raw = read_state(settings.run_dir, name)
    state = effective_state(raw)
    raw = raw or {}
    row.update(node=cfg.node, os=cfg.os, emulator=cfg.emulator, broker=cfg.broker)
    row["locked"] = cfg.locked

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


WIDTH = 80


def format_table(rows, usage=False, node=False, os=False, emulator=False):
    """The `emuctl list` table.

    By default: guest, state, uptime and detail, kept within 80 columns by
    cutting DETAIL short. --usage adds CPU% and MEM and still fits. The label
    columns (--node, --os, --emulator) are asked for by name, so with any of
    them the line is allowed to run past 80.
    """
    from .usage import human_bytes
    cols = [("GUEST", lambda r: r["name"])]
    if node:
        cols.append(("NODE", lambda r: r["node"]))
    if os:
        cols.append(("OS", lambda r: r["os"]))
    if emulator:
        cols.append(("EMULATOR", lambda r: r["emulator"]))
    cols += [("STATE", lambda r: r["state"]),
             ("UPTIME", lambda r: uptime_str(r["uptime"]) if r["uptime"] is not None else "-")]
    if usage:
        cols += [("CPU%", lambda r: "-" if r.get("cpu_pct") is None else f"{r['cpu_pct']:.1f}"),
                 ("MEM", lambda r: human_bytes(r.get("mem_bytes")))]
    cols.append(("DETAIL", lambda r: r["detail"] or ""))
    table = [[h for h, _ in cols]] + [[str(get(r) or "") for _, get in cols] for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(cols))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in table]
    if node or os or emulator:
        return "\n".join(lines)
    return "\n".join(line if len(line) <= WIDTH else line[:WIDTH - 1] + "…" for line in lines)


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
    line("kind", cfg.kind)
    line("state", row["state"] + (f"  ({row['detail']})" if row["detail"] else ""))
    if row["uptime"] is not None:
        line("uptime", uptime_str(row["uptime"]))
    out.append("")
    line("command", " ".join(cfg.command))
    line("workdir", cfg.workdir)
    line("disks", ", ".join(cfg.disks) or "none (diskless)")
    if cfg.has_console:
        line("console", f"emuctl console {cfg.name}   (broker {cfg.bind}:{cfg.broker}, shared)")
    if cfg.console == "pty":
        line("pty", f"{cfg.pty_path(settings.run_dir)}   (the emulator's end of the console)")
    elif cfg.has_console:
        line("serials", ", ".join(str(p) for p in cfg.serials)
             + "   (held by the supervisor; do not connect)")
    else:
        line("console", f"none; ready {cfg.ready_after}s after the emulator starts")
    if cfg.display_command:
        line("helper", " ".join(cfg.display_command)
             + (f"   (waits for {cfg.display_wait})" if cfg.display_wait else ""))
    if cfg.config_files:
        line("config", ", ".join(cfg.config_files))
    if cfg.guest_addr:
        line("network", f"{cfg.guest_addr} port {cfg.guest_port}")
    if cfg.nics:
        line("nics", ", ".join(cfg.nics))
    if cfg.display:
        auth = {"vnc": "password", "x509vnc": "TLS and password"}.get(cfg.display_auth,
                                                                       "no password")
        line("display", f"vnc://{cfg.display[0]}:{cfg.display[1]}  ({auth})")
    if cfg.notes:
        line("notes", cfg.notes)
    line("logs", (f"{cfg.workdir}/console.log, " if cfg.has_console else "")
         + f"{cfg.workdir}/{cfg.name}.out")
    if cfg.stop_command:
        how = "runs stop_command, then stops the emulator"
    elif cfg.stop_stages:
        how = "types the stop sequence, then stops the emulator"
    elif cfg.stop_mode == "freeze":
        how = "saves the machine state and quits (SIMH)"
    else:
        how = "stops the emulator only: a POWER CUT for the guest OS"
    if cfg.stop_signal != "TERM" and cfg.stop_mode == "signal":
        how += f" (with SIG{cfg.stop_signal})"
    line("stop", how)
    if cfg.exclusive_with:
        line("exclusive", ", ".join(cfg.exclusive_with))
    if cfg.locked:
        line("locked", f"{cfg.locked}   (starts are refused)")
    return "\n".join(out)
