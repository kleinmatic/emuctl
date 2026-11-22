# SPDX-License-Identifier: GPL-2.0-only
"""What the host's network looks like to each guest. Reads only.

emuctl never creates or changes an interface. This module reports what is
there: each guest's host interfaces (taps or veths), the bridge each sits in,
whether that bridge reaches the LAN, whether the guest's MAC address has been
seen on it, traffic counters, and the mistakes that are easy to make.

Sources: /sys/class/net, and `bridge -j fdb` and `ethtool -k` when installed.
Nothing here needs root.
"""
import json
import os
import shutil
import subprocess

from . import hardware
from .config import ConfigError, load_config
from .state import effective_state, read_state

SYSFS = "/sys/class/net"
COUNTERS = ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_errors", "tx_errors",
            "rx_dropped", "tx_dropped")


def which_sbin(name):
    """shutil.which, also looking in the sbin directories an ordinary user's
    PATH leaves out on Debian, where `bridge` and `ethtool` live."""
    path = os.environ.get("PATH", "") + os.pathsep + "/usr/sbin:/sbin"
    return shutil.which(name, path=path)


class Host:
    """Reads one host's interfaces. Caches per instance; make a new one per report."""

    def __init__(self, sysfs=SYSFS, run=subprocess.run, which=which_sbin):
        self.sysfs, self.run, self.which = sysfs, run, which
        self._fdb, self._offload = {}, {}

    def _read(self, name, *parts):
        try:
            with open(os.path.join(self.sysfs, name, *parts)) as fh:
                return fh.read().strip()
        except OSError:
            return None

    def exists(self, name):
        return os.path.isdir(os.path.join(self.sysfs, name))

    def names(self):
        try:
            return sorted(os.listdir(self.sysfs))
        except OSError:
            return []

    def kind(self, name):
        """tap, veth, bridge, physical, or other."""
        if os.path.exists(os.path.join(self.sysfs, name, "tun_flags")):
            return "tap"
        if os.path.isdir(os.path.join(self.sysfs, name, "bridge")):
            return "bridge"
        if os.path.exists(os.path.join(self.sysfs, name, "device")):
            return "physical"
        iflink, ifindex = self._read(name, "iflink"), self._read(name, "ifindex")
        if iflink and ifindex and iflink != ifindex:
            return "veth"
        if os.path.isdir(os.path.join(self.sysfs, name, "bonding")):
            return "bond"
        return "other"

    def up(self, name):
        state = self._read(name, "operstate")
        if state in ("up", "unknown"):
            # A tap's carrier is on only while a process has it open.
            return self._read(name, "carrier") == "1"
        return False

    def master(self, name):
        link = os.path.join(self.sysfs, name, "master")
        return os.path.basename(os.readlink(link)) if os.path.islink(link) else None

    def peer(self, name):
        """The other end of a veth pair, by its ifindex, or None."""
        want = self._read(name, "iflink")
        if not want or want == self._read(name, "ifindex"):
            return None
        for other in self.names():
            if other != name and self._read(other, "ifindex") == want:
                return other
        return None

    def bridge_of(self, name):
        """(bridge, via): the bridge `name` is in, directly or through its veth peer.

        An emulator that reads an interface raw (gxemul) sits on one end of a
        veth pair whose other end is in the bridge.
        """
        if self.master(name):
            return self.master(name), None
        p = self.peer(name) if self.kind(name) == "veth" else None
        if p and self.master(p):
            return self.master(p), p
        return None, None

    def members(self, bridge):
        try:
            return sorted(os.listdir(os.path.join(self.sysfs, bridge, "brif")))
        except OSError:
            return []

    def bridges(self):
        return [n for n in self.names() if self.kind(n) == "bridge"]

    def counters(self, name):
        out = {}
        for c in COUNTERS:
            v = self._read(name, "statistics", c)
            out[c] = int(v) if v and v.isdigit() else None
        return out

    def fdb_macs(self, bridge):
        """Learned (not permanent) MAC addresses on `bridge`, or None if unknown."""
        if bridge in self._fdb:
            return self._fdb[bridge]
        macs = None
        if self.which("bridge"):
            try:
                out = self.run([self.which("bridge"), "-j", "fdb", "show", "br", bridge],
                               capture_output=True, text=True, timeout=5).stdout
                macs = {e["mac"].lower() for e in json.loads(out or "[]")
                        if "permanent" not in (e.get("state") or "")
                        and "self" not in (e.get("flags") or [])}
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
                macs = None
        self._fdb[bridge] = macs
        return macs

    def tx_offload(self, name):
        """True if transmit checksum offload is on, None if it cannot be read."""
        if name in self._offload:
            return self._offload[name]
        on = None
        if self.which("ethtool"):
            try:
                out = self.run([self.which("ethtool"), "-k", name], capture_output=True, text=True,
                               timeout=5).stdout
                for line in out.splitlines():
                    if line.strip().startswith("tx-checksumming:"):
                        on = line.split(":", 1)[1].split()[0] == "on"
            except (OSError, subprocess.SubprocessError, IndexError):
                on = None
        self._offload[name] = on
        return on

    def addresses(self, name):
        if not self.which("ip"):
            return []
        try:
            out = self.run([self.which("ip"), "-j", "addr", "show", "dev", name],
                           capture_output=True, text=True, timeout=5).stdout
            return [f"{a['local']}/{a['prefixlen']}" for dev in json.loads(out or "[]")
                    for a in dev.get("addr_info", []) if a.get("scope") == "global"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            return []

    def uplinks(self, bridge):
        """Members that lead off the host: a real NIC, a bond, or a VLAN on one."""
        def is_uplink(m):
            if self.kind(m) in ("physical", "bond"):
                return True
            try:        # a VLAN or macvlan links to the device under it as lower_<name>
                return any(f.startswith("lower_") for f in os.listdir(os.path.join(self.sysfs, m)))
            except OSError:
                return False
        return [m for m in self.members(bridge) if is_uplink(m)]


def guest_interfaces(cfg, hw=None):
    """[{host_if, mac, model}] for a guest: `nics` from the conf, else the
    emulator's config. MACs are matched to names where the config says."""
    hw = hw if hw is not None else hardware.describe(cfg)
    parsed = [n for n in hw["nics"] if n.get("host_if")]
    if not cfg.nics:
        return [{"host_if": n["host_if"], "mac": n["mac"], "model": n["model"]} for n in parsed]
    by_if = {n["host_if"]: n for n in parsed}
    out = []
    for i, name in enumerate(cfg.nics):
        n = by_if.get(name) or (hw["nics"][i] if i < len(hw["nics"]) else {})
        out.append({"host_if": name, "mac": n.get("mac"), "model": n.get("model")})
    return out


def _load(settings, name):
    try:
        return load_config(settings.conf_path(name))
    except (OSError, ConfigError):
        return None


def guest_network(settings, name, host=None, cfg=None, state=None):
    """The network facts for one guest, from the guest's point of view.

    rx_bytes is traffic into the guest and tx_bytes traffic out of it: the
    host side of a tap counts the other way round.
    """
    host = host or Host()
    cfg = cfg or _load(settings, name)
    if cfg is None:
        return {"guest": name, "nics": [], "warnings": [f"{name}: conf does not parse"]}
    state = state or effective_state(read_state(settings.run_dir, name))
    nics, warnings = [], []
    for n in guest_interfaces(cfg):
        ifname = n["host_if"]
        info = {"host_if": ifname, "mac": n["mac"], "model": n["model"], "exists": False,
                "kind": None, "up": False, "bridge": None, "bridge_uplink": None,
                "via": None, "mac_seen": None, "rx_bytes": None, "tx_bytes": None, "errors": None,
                "dropped": None}
        nics.append(info)
        if not host.exists(ifname):
            warnings.append(f"{name}: interface {ifname} does not exist")
            continue
        c = host.counters(ifname)
        swap = host.kind(ifname) in ("tap", "veth")
        bridge, via = host.bridge_of(ifname)
        info.update(exists=True, kind=host.kind(ifname), up=host.up(ifname),
                    bridge=bridge, via=via,
                    rx_bytes=c["tx_bytes"] if swap else c["rx_bytes"],
                    tx_bytes=c["rx_bytes"] if swap else c["tx_bytes"],
                    errors=_sum(c["rx_errors"], c["tx_errors"]),
                    dropped=_sum(c["rx_dropped"], c["tx_dropped"]))
        if info["bridge"]:
            info["bridge_uplink"] = bool(host.uplinks(info["bridge"]))
            macs = host.fdb_macs(info["bridge"])
            if macs is not None and n["mac"]:
                info["mac_seen"] = n["mac"].lower() in macs
                if state == "ready" and not info["mac_seen"]:
                    warnings.append(f"{name}: ready, but its MAC {n['mac']} has not been seen "
                                    f"on {info['bridge']}")
        else:
            warnings.append(f"{name}: {ifname} is not in any bridge")
        if info["kind"] == "veth" and host.tx_offload(ifname):
            warnings.append(f"{name}: transmit checksum offload is on for {ifname}; an "
                            f"emulator reading it raw can see bad TCP checksums "
                            f"(ethtool -K {ifname} tx off)")
    return {"guest": name, "nics": nics, "warnings": warnings}


def _sum(a, b):
    return None if a is None or b is None else a + b


def shared_warnings(settings, interfaces):
    """Two guests on one interface or one MAC without exclusive_with."""
    warnings, seen_if, seen_mac = [], {}, {}
    configs = {n: _load(settings, n) for n in interfaces}
    for name, nics in sorted(interfaces.items()):
        for n in nics:
            for key, seen, what in ((n["host_if"], seen_if, "interface"),
                                    ((n["mac"] or "").lower() or None, seen_mac, "MAC")):
                if not key:
                    continue
                other = seen.get(key)
                if other and other != name:
                    ex = set(configs[name].exclusive_with if configs[name] else []) | set(
                        configs[other].exclusive_with if configs[other] else [])
                    if name not in ex and other not in ex:
                        for a, b in ((other, name), (name, other)):
                            warnings.append(f"{a}: uses {what} {key}, as does {b}, but they "
                                            f"are not exclusive_with each other")
                seen.setdefault(key, name)
    return warnings


def host_network(settings, host=None):
    """Every bridge, its members and the guests behind them, plus warnings."""
    host = host or Host()
    reports = {name: guest_network(settings, name, host) for name in settings.guests()}
    owners = {}
    for name, rep in reports.items():
        for n in rep["nics"]:
            owners.setdefault(n.get("via") or n["host_if"], []).append(name)
    bridges = []
    for br in host.bridges():
        uplinks = host.uplinks(br)
        bridges.append({
            "name": br, "up": host._read(br, "operstate") == "up",
            "addresses": host.addresses(br), "uplinks": uplinks,
            "members": [{"name": m, "kind": host.kind(m), "up": host.up(m),
                         "guests": owners.get(m, [])} for m in host.members(br)],
        })
    warnings = [w for rep in reports.values() for w in rep["warnings"]]
    warnings += shared_warnings(settings, {n: r["nics"] for n, r in reports.items()})
    return {"bridges": bridges, "guests": reports, "warnings": warnings}


def format_host(report):
    out = []
    for br in report["bridges"]:
        where = "LAN via " + ", ".join(br["uplinks"]) if br["uplinks"] else "host-only"
        addr = (" " + ", ".join(br["addresses"])) if br["addresses"] else ""
        out.append(f"{br['name']}  ({where}){addr}{'' if br['up'] else '  DOWN'}")
        for m in br["members"]:
            who = ", ".join(m["guests"]) or ("uplink" if m["name"] in br["uplinks"] else "-")
            out.append(f"  {m['name']:<12} {m['kind']:<9} {'up' if m['up'] else 'down':<5} {who}")
    if not report["bridges"]:
        out.append("no bridges on this host")
    for w in report["warnings"]:
        out.append(f"warning: {w}")
    return "\n".join(out)


def format_guest(report):
    from .usage import human_bytes
    out = []
    for n in report["nics"]:
        if not n["exists"]:
            out.append(f"{n['host_if']}: does not exist")
            continue
        where = (f"in {n['bridge']} ({'LAN' if n['bridge_uplink'] else 'host-only'})"
                 + (f" through its peer {n['via']}" if n.get("via") else "")
                 if n["bridge"] else "in no bridge")
        seen = {True: "seen on the bridge", False: "NOT seen on the bridge",
                None: "not checked"}[n["mac_seen"]]
        out.append(f"{n['host_if']}: {n['kind']}, {'up' if n['up'] else 'down'}, {where}")
        if n["mac"]:
            out.append(f"  guest MAC   {n['mac']}  ({seen})")
        out.append(f"  traffic     in {human_bytes(n['rx_bytes'])}, "
                   f"out {human_bytes(n['tx_bytes'])}, "
                   f"errors {n['errors']}, dropped {n['dropped']}")
    if not report["nics"]:
        out.append("no host interfaces known; set `nics` in the conf or check `config`")
    for w in report["warnings"]:
        out.append(f"warning: {w}")
    return "\n".join(out)


def nic_counters(cfg, host=None):
    """Total traffic into and out of the guest over all its interfaces."""
    host = host or Host()
    rx = tx = None
    for n in guest_interfaces(cfg):
        if not host.exists(n["host_if"]):
            continue
        c = host.counters(n["host_if"])
        swap = host.kind(n["host_if"]) in ("tap", "veth")
        r, t = (c["tx_bytes"], c["rx_bytes"]) if swap else (c["rx_bytes"], c["tx_bytes"])
        rx = (rx or 0) + (r or 0)
        tx = (tx or 0) + (t or 0)
    return rx, tx
