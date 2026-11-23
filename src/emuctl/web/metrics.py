# SPDX-License-Identifier: GPL-2.0-only
"""GET /metrics: the guests in Prometheus' text format.

Taken from the sampler's last reading, so a scrape costs nothing and is at
most one sample (5 seconds) old. Counters are the emulator's own since it
started, from its cgroup; they go back to zero when a guest restarts, which
Prometheus' rate() expects.
"""
from ..status import LIVE

STATES = ("down", "starting", "booting", "stuck", "ready", "degraded", "halted", "stopping",
          "crashed", "orphaned", "error")

# name, type, help, row key
GAUGES = (
    ("emuctl_guest_uptime_seconds", "gauge", "Seconds since the guest was started.", "uptime"),
    ("emuctl_guest_cpu_percent", "gauge", "CPU over the last sample, per cent of one host CPU.",
     "cpu_pct"),
    ("emuctl_guest_cpu_seconds_total", "counter", "CPU time the emulator has used.",
     "cpu_seconds"),
    ("emuctl_guest_memory_bytes", "gauge", "Memory the emulator uses.", "mem_bytes"),
    ("emuctl_guest_io_read_bytes_total", "counter", "Bytes the emulator has read from disk.",
     "io_read_bytes"),
    ("emuctl_guest_io_write_bytes_total", "counter", "Bytes the emulator has written to disk.",
     "io_write_bytes"),
    ("emuctl_guest_network_receive_bytes_total", "counter",
     "Bytes the guest has received on its host interfaces.", "net_rx_bytes"),
    ("emuctl_guest_network_transmit_bytes_total", "counter",
     "Bytes the guest has sent on its host interfaces.", "net_tx_bytes"),
    ("emuctl_guest_disk_bytes", "gauge", "Space the guest's disk images use.", "disk_bytes"),
)


def _label(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _num(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    return repr(float(v))


def render(rows, version):
    out = [
        "# HELP emuctl_build_info The emuctl version serving these metrics.",
        "# TYPE emuctl_build_info gauge",
        f'emuctl_build_info{{version="{_label(version)}"}} 1',
        "# HELP emuctl_guest_up 1 if the guest's emulator is running.",
        "# TYPE emuctl_guest_up gauge",
    ]
    out += [f'emuctl_guest_up{{guest="{_label(r["name"])}"}} {_num(r["state"] in LIVE)}'
            for r in rows]
    out += ["# HELP emuctl_guest_state 1 for the state the guest is in (see docs/states.md).",
            "# TYPE emuctl_guest_state gauge"]
    for r in rows:
        for st in STATES:
            out.append(f'emuctl_guest_state{{guest="{_label(r["name"])}",state="{st}"}} '
                       f'{_num(r["state"] == st)}')
    out += ["# HELP emuctl_guest_locked 1 if the guest's conf says locked = <reason>.",
            "# TYPE emuctl_guest_locked gauge"]
    out += [f'emuctl_guest_locked{{guest="{_label(r["name"])}"}} {_num(bool(r.get("locked")))}'
            for r in rows]
    for name, kind, text, key in GAUGES:
        lines = [f'{name}{{guest="{_label(r["name"])}"}} {_num(r[key])}'
                 for r in rows if r.get(key) is not None]
        if lines:
            out += [f"# HELP {name} {text}", f"# TYPE {name} {kind}"] + lines
    return "\n".join(out) + "\n"
