# SPDX-License-Identifier: GPL-2.0-only
"""network.py: bridges, taps, guests behind them, and the warnings."""
import json
import os
import subprocess

import pytest

from emuctl import cli, network
from emuctl.state import write_state

ES40 = ('sys0 = tsunami {{ pci0.4 = dec21143 {{ type = "tap"; adapter = "{tap}"; '
        'mac = "{mac}"; }} }}')


class FakeNet:
    """A /sys/class/net tree in a temp dir."""

    def __init__(self, root):
        self.root = root
        self.index = 1

    def iface(self, name, kind="tap", master=None, up=True, rx=0, tx=0, iflink=None):
        d = self.root / name
        d.mkdir()
        self.index += 1
        (d / "ifindex").write_text(f"{self.index}\n")
        (d / "iflink").write_text(f"{iflink or self.index}\n")
        (d / "operstate").write_text("up\n" if up else "down\n")
        (d / "carrier").write_text("1\n" if up else "0\n")
        if kind == "tap":
            (d / "tun_flags").write_text("0x1002\n")
        elif kind == "physical":
            (d / "device").mkdir()
        elif kind == "bridge":
            (d / "bridge").mkdir()
            (d / "brif").mkdir()
        elif kind == "vlan":
            (d / "lower_eth0").mkdir()
        stats = d / "statistics"
        stats.mkdir()
        for c in network.COUNTERS:
            (stats / c).write_text(f"{rx if c == 'rx_bytes' else tx if c == 'tx_bytes' else 0}\n")
        if master:
            os.symlink(f"../{master}", d / "master")
            (self.root / master / "brif" / name).mkdir()
        return self.index


def runner(fdb=None, offload=None, addrs=None):
    def run(argv, **kw):
        if argv[0] == "bridge":
            out = json.dumps((fdb or {}).get(argv[-1], []))
        elif argv[0] == "ethtool":
            state = (offload or {}).get(argv[-1], "off")
            out = f"Features for {argv[-1]}:\ntx-checksumming: {state}\n"
        else:
            out = json.dumps([{"addr_info": [{"local": a, "prefixlen": 24, "scope": "global"}
                                             for a in (addrs or {}).get(argv[-1], [])]}])
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
    return run


@pytest.fixture
def lab(tmp_path, settings):
    """br0 on the LAN (eth0, tap1, vea0), br1 host-only (tap5); four guests."""
    net = FakeNet(tmp_path / "sys")
    net.root.mkdir()
    net.iface("br0", "bridge")
    net.iface("br1", "bridge")
    net.iface("eth0", "physical", master="br0")
    net.iface("tap1", master="br0", rx=1000, tx=5000)
    vea0 = net.iface("vea0", "other", master="br0")
    net.iface("vea1", "other", iflink=vea0)
    net.iface("tap5", master="br1", up=False)
    work = tmp_path / "work"
    work.mkdir()
    (work / "es40.cfg").write_text(ES40.format(tap="tap1", mac="02-00-00-00-00-11"))
    (work / "machine.json").write_text(json.dumps(
        {"machine": {"type": "decstation", "model": "3max"},
         "network": {"enabled": True, "type": "bridged", "tapdev": "vea1",
                     "mac": "02:00:00:00:00:22"}}))

    def guest(name, command, extra=""):
        with open(settings.conf_path(name), "w") as fh:
            fh.write(f"name = {name}\nworkdir = {work}\ncommand = {command}\ndisks = d.img\n"
                     f"diskless = yes\nserials = 1\nbroker = 2\nready = r\n{extra}")
    guest("alpha", "es40 es40.cfg")
    guest("mips", "gxemul -c machine.json", "kind = gxemul\nconfig = machine.json\n")
    guest("aix", "qemu-system-ppc -net nic,macaddr=02:00:00:00:00:33 -net tap,ifname=tap5")
    guest("ghost", "vax", "nics = tap9\n")
    host = network.Host(sysfs=str(net.root),
                        run=runner(fdb={"br0": [{"mac": "02:00:00:00:00:11", "state": ""},
                                                {"mac": "aa:aa:aa:aa:aa:aa",
                                                 "state": "permanent"}]},
                                   offload={"vea1": "on"}, addrs={"br0": ["192.0.2.10"]}),
                        which=lambda name: f"/usr/sbin/{name}")
    return host, net


def test_host_classifies_interfaces(lab):
    host, _ = lab
    assert [host.kind(n) for n in ("br0", "eth0", "tap1", "vea1")] == \
        ["bridge", "physical", "tap", "veth"]
    assert host.up("tap1") and not host.up("tap5")
    assert host.uplinks("br0") == ["eth0"] and host.uplinks("br1") == []
    assert host.bridge_of("vea1") == ("br0", "vea0")
    assert host.bridge_of("tap1") == ("br0", None)
    assert host.addresses("br0") == ["192.0.2.10/24"]


def test_guest_on_a_tap(lab, settings):
    host, _ = lab
    write_state(settings.run_dir, "alpha", state="ready", pid=os.getpid(),
                supervisor_pid=os.getpid(), cmd="", started_at=0)
    rep = network.guest_network(settings, "alpha", host)
    (nic,) = rep["nics"]
    assert nic["host_if"] == "tap1" and nic["bridge"] == "br0" and nic["bridge_uplink"]
    assert nic["mac_seen"] is True and nic["up"]
    assert (nic["rx_bytes"], nic["tx_bytes"]) == (5000, 1000)     # guest's point of view
    assert rep["warnings"] == []


def test_guest_through_a_veth_peer(lab, settings):
    host, _ = lab
    write_state(settings.run_dir, "mips", state="ready", pid=os.getpid(),
                supervisor_pid=os.getpid(), cmd="", started_at=0)
    rep = network.guest_network(settings, "mips", host)
    (nic,) = rep["nics"]
    assert nic["bridge"] == "br0" and nic["via"] == "vea0" and nic["kind"] == "veth"
    text = " | ".join(rep["warnings"])
    assert "has not been seen on br0" in text           # ready, MAC not learned
    assert "checksum offload is on for vea1" in text
    assert "not in any bridge" not in text


def test_mac_not_checked_unless_ready(lab, settings):
    host, _ = lab
    rep = network.guest_network(settings, "mips", host)        # down
    assert not any("not been seen" in w for w in rep["warnings"])
    assert rep["nics"][0]["mac_seen"] is False


def test_missing_interface_and_host_only(lab, settings):
    host, _ = lab
    ghost = network.guest_network(settings, "ghost", host)
    assert ghost["warnings"] == ["ghost: interface tap9 does not exist"]
    aix = network.guest_network(settings, "aix", host)
    assert aix["nics"][0]["bridge"] == "br1" and aix["nics"][0]["bridge_uplink"] is False
    assert aix["nics"][0]["mac_seen"] is False         # br1's table is empty: not seen


def test_not_in_a_bridge(lab, settings, tmp_path):
    host, net = lab
    net.iface("tap7")
    with open(settings.conf_path("lone"), "w") as fh:
        fh.write(f"name = lone\nworkdir = {tmp_path}\ncommand = vax\ndisks = d.img\n"
                 "diskless = yes\nserials = 1\nbroker = 2\nready = r\nnics = tap7\n")
    assert "not in any bridge" in network.guest_network(settings, "lone", host)["warnings"][0]


def test_host_report_and_shared_interface(lab, settings, tmp_path):
    host, _ = lab
    with open(settings.conf_path("copy"), "w") as fh:
        fh.write(f"name = copy\nworkdir = {tmp_path}\ncommand = vax\ndisks = d.img\n"
                 "diskless = yes\nserials = 1\nbroker = 2\nready = r\nnics = tap1\n")
    rep = network.host_network(settings, host)
    br0 = next(b for b in rep["bridges"] if b["name"] == "br0")
    members = {m["name"]: m["guests"] for m in br0["members"]}
    assert members["tap1"] == ["alpha", "copy"] and members["vea0"] == ["mips"]
    assert members["eth0"] == []
    shared = [w for w in rep["warnings"] if "as does" in w]
    assert "alpha: uses interface tap1, as does copy" in shared[0]
    assert any(w.startswith("copy: uses interface tap1") for w in shared)
    text = network.format_host(rep)
    assert "br0  (LAN via eth0) 192.0.2.10/24" in text and "br1  (host-only)" in text


def test_exclusive_guests_may_share(lab, settings, tmp_path):
    host, _ = lab
    with open(settings.conf_path("copy"), "w") as fh:
        fh.write(f"name = copy\nworkdir = {tmp_path}\ncommand = vax\ndisks = d.img\n"
                 "diskless = yes\nserials = 1\nbroker = 2\nready = r\nnics = tap1\n"
                 "exclusive_with = alpha\n")
    assert not any("as does" in w for w in network.host_network(settings, host)["warnings"])


def test_shared_mac(settings, tmp_path):
    for n in ("a", "b"):
        with open(settings.conf_path(n), "w") as fh:
            fh.write(f"name = {n}\nworkdir = {tmp_path}\ncommand = x\ndisks = d.img\n"
                     "diskless = yes\nserials = 1\nbroker = 2\nready = r\n")
    nics = {"a": [{"host_if": "tap1", "mac": "02:00:00:00:00:01"}],
            "b": [{"host_if": "tap2", "mac": "02:00:00:00:00:01"}]}
    out = network.shared_warnings(settings, nics)
    assert out == ["a: uses MAC 02:00:00:00:00:01, as does b, but they are not exclusive_with "
                   "each other",
                   "b: uses MAC 02:00:00:00:00:01, as does a, but they are not exclusive_with "
                   "each other"]


def test_tools_missing_or_failing(tmp_path):
    def boom(argv, **kw):
        raise OSError("gone")
    h = network.Host(sysfs=str(tmp_path), run=boom, which=lambda n: "/bin/x")
    assert h.fdb_macs("br0") is None and h.tx_offload("x") is None and h.addresses("x") == []
    h2 = network.Host(sysfs=str(tmp_path / "nope"), which=lambda n: None)
    assert h2.names() == [] and h2.fdb_macs("br0") is None and h2.addresses("br0") == []
    assert h2.tx_offload("x") is None and h2.members("br0") == [] and h2.peer("x") is None


def test_nic_counters_and_usage_rows(lab, settings):
    host, _ = lab
    from emuctl import usage
    from emuctl.config import load_config
    cfg = load_config(settings.conf_path("alpha"))
    assert network.nic_counters(cfg, host) == (5000, 1000)
    rows = [{"name": "alpha", "uptime": None}]
    usage.attach(settings, rows, {"alpha": cfg}, groups={}, net_host=host)
    assert (rows[0]["net_rx_bytes"], rows[0]["net_tx_bytes"]) == (5000, 1000)


def test_guest_interfaces_with_nics_key(settings, tmp_path):
    from emuctl.config import parse_config
    (tmp_path / "es40.cfg").write_text(ES40.format(tap="tap1", mac="02-00-00-00-00-11"))
    cfg = parse_config(f"name = g\nworkdir = {tmp_path}\ncommand = es40 es40.cfg\n"
                       "disks = d\nserials = 1\nbroker = 2\nready = r\nnics = tap1, tap2\n")
    out = network.guest_interfaces(cfg)
    assert out[0] == {"host_if": "tap1", "mac": "02:00:00:00:00:11", "model": "DEC 21143"}
    assert out[1]["host_if"] == "tap2" and out[1]["mac"] is None


def test_format_guest(lab, settings):
    host, _ = lab
    text = network.format_guest(network.guest_network(settings, "mips", host))
    assert "vea1: veth" in text and "through its peer vea0" in text
    assert "NOT seen on the bridge" in text
    assert "does not exist" in network.format_guest(network.guest_network(settings, "ghost",
                                                                           host))
    assert "no host interfaces known" in network.format_guest(
        {"guest": "x", "nics": [], "warnings": []})


def test_broken_conf(settings):
    with open(settings.conf_path("bad"), "w") as fh:
        fh.write("name = bad\n")
    assert "does not parse" in network.guest_network(settings, "bad")["warnings"][0]


def test_cli_net_and_check(lab, settings, monkeypatch, capsys):
    host, _ = lab
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(network, "Host", lambda *a, **k: host)
    assert cli.main(["net"]) == 0
    assert "br0" in capsys.readouterr().out
    assert cli.main(["net", "mips", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["nics"][0]["via"] == "vea0"
    cli.main(["check", "ghost"])
    assert "interface tap9 does not exist" in capsys.readouterr().out
