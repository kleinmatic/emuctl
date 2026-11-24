# SPDX-License-Identifier: GPL-2.0-only
"""hardware.py: emulated hardware read from each emulator's config."""
import json
import os
import random
import shlex

import pytest
from test_cli import add_guest

from emuctl import cli, hardware
from emuctl.config import parse_config

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "hardware")


def fixture(*parts):
    with open(os.path.join(FIX, *parts)) as fh:
        return fh.read()


def test_simh_microvax():
    hw = hardware.parse_simh(fixture("simh", "vax.ini"), "/usr/local/bin/vax")
    assert hw["machine"] == "MicroVAX 3900" and hw["memory"] == "64 MB"
    disks = {d["unit"]: d for d in hw["disks"]}
    assert disks["rq0"] == {"unit": "rq0", "model": "RA73", "file": "root.dsk", "kind": "disk",
                            "readonly": False}
    assert disks["rq1"]["model"] == "RA92" and disks["rq1"]["file"] == "usr.dsk"   # upper case
    assert disks["rq2"]["kind"] == "cdrom" and disks["rq2"]["readonly"] is True
    assert disks["tq0"]["kind"] == "tape" and disks["tq0"]["file"] == "tape.tap"  # -f fmt skipped
    assert "nvr0" not in disks and "rl" not in disks
    assert hw["nics"] == [{"unit": "xq", "model": "DELQA", "mac": "02:00:00:00:00:01",
                           "host_if": "tap9"}]
    assert "DZ terminal lines on port 2005" in hw["other"]


def test_simh_model_and_nat():
    hw = hardware.parse_simh(fixture("simh", "vax8600.ini"), "vax8600")
    assert hw["machine"] == "VAX 8650" and hw["memory"] == "128 MB"
    assert hw["nics"][0]["host_if"] is None and hw["nics"][0]["mac"] == "02:00:00:00:00:02"
    assert "xu: nat networking" in hw["other"]


def test_simh_unknown_program():
    assert hardware.parse_simh("", "/opt/simh/i1401")["machine"] == "i1401"
    assert hardware.parse_simh("", None)["machine"] is None


def test_es40():
    hw = hardware.parse_es40(fixture("es40", "es40.cfg"))
    assert hw["machine"] == "AlphaServer ES40"
    assert hw["memory"] == "1 GB" and hw["cpu"] == "1 × EV68CB, 800 MHz"
    assert hw["disks"][0] == {"unit": "disk0.0", "model": "file", "file": "dka0.img",
                              "kind": "disk", "readonly": False}
    assert hw["disks"][1]["kind"] == "cdrom" and hw["disks"][1]["readonly"]
    assert hw["nics"] == [{"unit": "pci0.4", "model": "DEC 21143", "mac": "02:00:00:00:00:03",
                           "host_if": "tap9"}]
    assert hw["other"] == ["serial0 on port 21900", "serial1 on port 21901"]


def test_es40_two_cpus_and_device_path():
    hw = hardware.parse_es40(fixture("es40", "es40-2cpu.cfg"))
    assert hw["cpu"] == "2 × EV67, 1000 MHz" and hw["memory"] == "2 GB"
    assert hw["nics"][0]["host_if"] == "tap7"     # /dev/tap7 on FreeBSD


def test_qemu_old_style():
    argv = shlex.split(fixture("qemu", "args.txt"))
    hw = hardware.parse_qemu(argv)
    assert hw["machine"] == "40p" and hw["memory"] == "192 MB"
    assert [d["unit"] for d in hw["disks"]] == ["hda", "cdrom"]
    assert hw["disks"][1]["readonly"]
    assert hw["nics"] == [{"unit": "nic0", "model": None, "mac": "02:00:00:00:00:06",
                           "host_if": "tap8"}]
    assert "QEMU ppc" in hw["other"]


def test_qemu_modern_style():
    hw = hardware.parse_qemu(shlex.split(fixture("qemu", "args-modern.txt")))
    assert hw["machine"] == "q35" and hw["memory"] == "2 GB" and hw["cpu"] == "4 × host"
    assert hw["disks"][0]["file"] == "os.qcow2" and hw["disks"][1]["kind"] == "cdrom"
    tap, user = hw["nics"]
    assert tap == {"unit": "nic0", "model": "e1000", "mac": "02:00:00:00:00:07",
                   "host_if": "tap6"}
    assert user["model"] == "virtio-net-pci" and user["host_if"] is None


def test_qemu_tap_without_nic_and_machine_type_key():
    hw = hardware.parse_qemu(["qemu-system-arm", "-machine", "type=virt", "-net",
                              "tap,ifname=tap5", "-m", "size=512M", "-nographic"])
    assert hw["machine"] == "virt" and hw["memory"] == "512 MB"
    assert hw["nics"][0]["host_if"] == "tap5"


def test_gxemul():
    hw = hardware.parse_gxemul(fixture("gxemul", "machine.json"))
    assert hw["machine"] == "DECstation 5000/200" and hw["memory"] == "128 MB"
    assert hw["cpu"] is None                      # implied by the model, not stated
    assert hw["disks"][1] == {"unit": "id 4", "model": "RRD42", "file": "cd.iso",
                              "kind": "cdrom", "readonly": True}
    assert hw["nics"] == [{"unit": "net0", "model": None, "mac": "02:00:00:00:00:05",
                           "host_if": "vea9"}]
    assert "graphics display" in hw["other"]
    assert "TURBOchannel slot 0: pmagb_ba_1.1.bin" in hw["other"]


def test_gxemul_cpu_and_nat():
    text = json.dumps({"machine": {"type": "sgi", "model": "ip22", "cpu": "R4400", "cpus": 2},
                       "network": {"enabled": True, "type": "simulated"}})
    hw = hardware.parse_gxemul(text)
    assert hw["machine"] == "sgi ip22" and hw["cpu"] == "2 × R4400"
    assert hw["nics"][0]["host_if"] is None and "network: gxemul simulated" in hw["other"]


def test_86box():
    hw = hardware.parse_86box(fixture("86box", "86box.cfg"))
    assert hw["machine"] == "ASUS P/I-P54TP4XE" and hw["cpu"] == "Pentium (P54C), 200 MHz"
    assert hw["memory"] == "128 MB"
    assert [d["model"] for d in hw["disks"]] == ["SCSI disk, 2 GB", "IDE disk, 100 MB", None]
    assert hw["disks"][2] == {"unit": "cdrom 1", "model": None, "file": "install.iso",
                              "kind": "cdrom", "readonly": True}
    assert [(n["model"], n["host_if"]) for n in hw["nics"]] == [("novell_ne2k", None),
                                                                ("pcnetfast", "tap7")]
    assert "net 1: SLiRP, user-mode NAT on the host" in hw["other"]
    assert hw["forwards"] == [{"host_port": 2223, "guest_port": 22},
                              {"host_port": 2324, "guest_port": 23}]


def test_qemu_user_network_forwards():
    hw = hardware.parse_qemu(["qemu-system-x86_64", "-netdev",
                              "user,id=n0,hostfwd=tcp::2222-:22,hostfwd=tcp:127.0.0.1:2323-:23"])
    assert hw["forwards"] == [{"host_port": 2222, "guest_port": 22},
                              {"host_port": 2323, "guest_port": 23}]


def test_86box_hostile_numbers_do_not_raise():
    huge = "9" * 5000
    text = (f"[Machine]\ncpu_family = x\ncpu_speed = \u00b2\nmem_size = {huge}\n"
            f"[Hard disks]\nhdd_{huge}_fn = a\nhdd_01_fn = b\n"
            f"hdd_01_parameters = {huge},{huge},{huge},0,ide\n[Network]\nnet_{huge}_card = ne2k\n")
    hw = hardware.parse_86box(text)
    assert hw["memory"] is None and hw["cpu"] == "x" and len(hw["disks"]) == 1


def test_86box_config_is_found_from_minus_p(tmp_path):
    from emuctl.config import parse_config
    cfg = parse_config(f"name = d\nworkdir = {tmp_path}\nconsole = none\ndisks = hdd.img\n"
                       f"command = /usr/bin/86Box -P vm -N\n")
    assert hardware.config_files(cfg) == [str(tmp_path / "vm" / "86box.cfg")]


@pytest.mark.parametrize("parser", [hardware.parse_simh, hardware.parse_es40,
                                    hardware.parse_gxemul, hardware.parse_86box])
def test_parsers_never_raise_on_nonsense(parser):
    rng = random.Random(1)
    junk = [
        "", "\x00\xff garbage {{{ ;;; = = =", "set\nattach\nattach -r\nset cpu\n",
        "set cpu model=\nattach xq tap:\n", "cpu0 = { speed = ; }", '{"machine": 5}', "[1,2]",
        '{"drives": [5, {"id": "x"}], "slots": {}}', "set rq0 \"unclosed\n",
        "".join(chr(rng.randrange(1, 300)) for _ in range(2000)),
    ]
    for text in junk:
        hw = parser(text)
        assert set(hw) == set(hardware.empty())


def test_qemu_never_raises_on_nonsense():
    for argv in ([], ["qemu-system-x86_64"], ["qemu", "-m"], ["qemu", "-m", "lots"],
                 ["qemu", "-smp", "x", "-net", "", "-device", "", "-drive", ""], ["x", "loose"]):
        assert set(hardware.parse_qemu(argv)) == set(hardware.empty())


def test_mb():
    assert hardware.mb(64) == "64 MB" and hardware.mb(2048) == "2 GB"
    assert hardware.mb(1536) == "1536 MB"
    assert hardware.size_to_mb("512k") == 0 and hardware.size_to_mb("x") is None


# --- describe(): files, guesses, overrides ---------------------------------------

def conf(tmp_path, cmd, extra=""):
    return parse_config(f"name = g1\nworkdir = {tmp_path}\ncommand = {cmd}\ndisks = d.img\n"
                        f"serials = 1\nbroker = 2\nready = r\n{extra}")


def test_describe_guesses_the_config_from_the_command(tmp_path):
    (tmp_path / "es40.cfg").write_text(fixture("es40", "es40.cfg"))
    hw = hardware.describe(conf(tmp_path, "/opt/es40/bin/es40 es40.cfg"))
    assert hw["machine"] == "AlphaServer ES40"
    assert hw["sources"] == [str(tmp_path / "es40.cfg")]


def test_describe_uses_config_key_and_reports_unreadable(tmp_path):
    (tmp_path / "a.ini").write_text("set cpu 32m\n")
    hw = hardware.describe(conf(tmp_path, "vax", "config = a.ini, missing.ini\n"))
    assert hw["memory"] == "32 MB" and hw["machine"] == "MicroVAX 3900"
    assert any("cannot read" in p and "missing.ini" in p for p in hw["problems"])


def test_describe_qemu_reads_the_command(tmp_path):
    hw = hardware.describe(conf(tmp_path, fixture("qemu", "args.txt").strip()))
    assert hw["sources"] == ["command"] and hw["machine"] == "40p"


def test_describe_overrides(tmp_path):
    (tmp_path / "machine.json").write_text(fixture("gxemul", "machine.json"))
    hw = hardware.describe(conf(tmp_path, "gxemul -c machine.json",
                                "hw.cpu = R3000A, 25 MHz\nhw.frame buffer = HX, 1280x1024\n"))
    assert hw["cpu"] == "R3000A, 25 MHz"
    assert "frame buffer: HX, 1280x1024" in hw["other"]


def test_describe_other_kind_and_no_config(tmp_path):
    assert hardware.describe(conf(tmp_path, "/usr/bin/86Box"))["sources"] == []
    assert hardware.describe(conf(tmp_path, "vax"))["sources"] == []      # no .ini given


def test_describe_survives_a_parser_bug(tmp_path, monkeypatch):
    (tmp_path / "x.cfg").write_text("sys0 = tsunami")
    monkeypatch.setitem(hardware.PARSERS, "es40", lambda text: 1 / 0)
    hw = hardware.describe(conf(tmp_path, "es40 x.cfg"))
    assert hw["machine"] is None and "could not read" in hw["problems"][0]


def test_format_hardware():
    hw = hardware.parse_simh(fixture("simh", "vax.ini"), "vax")
    hw["sources"] = ["vax.ini"]
    hw["problems"] = ["example"]
    out = hardware.format_hardware(hw)
    assert "machine     MicroVAX 3900" in out
    assert "disk        rq2: CD-ROM, install.iso, cdrom, read-only" in out
    assert "nic         xq: DELQA, 02:00:00:00:00:01, on tap9" in out
    assert "problem     example" in out
    assert "nothing (set `config`" in hardware.format_hardware(hardware.empty())


def test_cli_info_hardware(settings, tmp_path, monkeypatch, capsys):
    add_guest(settings, tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert cli.main(["info", "g1", "--hardware", "--json"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == set(hardware.empty())
    assert cli.main(["info", "g1", "--hardware"]) == 0
    assert "read from" in capsys.readouterr().out


def test_fixtures_hold_nothing_real():
    """Rule 5 of AGENTS.md: invented MACs (locally administered) only."""
    import re
    for root, _, files in os.walk(FIX):
        for f in files:
            text = open(os.path.join(root, f)).read()
            for mac in re.findall(r"\b([0-9a-fA-F]{2})[:-](?:[0-9a-fA-F]{2}[:-]){4}[0-9a-fA-F]{2}",
                                  text):
                assert int(mac, 16) & 0x02, f"{f}: MAC is not locally administered"


def test_hostile_values_do_not_raise():
    assert hardware.parse_simh("set cpu " + "9" * 5000 + "m\n")["memory"] is None
    assert hardware.parse_es40("memory.bits = " + "9" * 5000)["memory"] is None
    for text in ('{"machine":{"cpu":"R4000","cpus":null}}', '{"slots":[]}',
                 '{"machine":{"memory_mb":"lots"}}', '{"network":{"enabled":true,"tapdev":["a"]}}',
                 '[' * 100000):
        hw = hardware.parse_gxemul(text)
        assert set(hw) == set(hardware.empty())


def test_malformed_es40_is_fast():
    import time
    text = "disk0.0 = file {\n" * 20000           # 340 KB of unterminated blocks
    t0 = time.time()
    hardware.parse_es40(text)
    assert time.time() - t0 < 2


def test_es40_comment_marks_inside_quotes_are_kept():
    hw = hardware.parse_es40('sys0 = tsunami { disk0.0 = file { file = "/a//b#1.img"; } }')
    assert hw["disks"][0]["file"] == "/a//b#1.img"


def test_simh_nic_attached_by_name_and_set_flags():
    hw = hardware.parse_simh("set xq mac=02-00-00-00-00-09\nattach xq eth0\n"
                             "set rq0 writeenabled\nset rq0 ra81\n", "vax")
    assert hw["nics"][0]["host_if"] == "eth0"
    assert hw["disks"][0]["model"] == "RA81"


def test_qemu_smp_before_cpu_and_nic_none():
    hw = hardware.parse_qemu(["qemu-system-x86_64", "-smp", "2", "-cpu", "host", "-nic", "none"])
    assert hw["cpu"] == "2 × host" and hw["nics"] == []


def test_config_reads_refuse_fifos_and_huge_files(tmp_path):
    fifo = tmp_path / "hang.ini"
    os.mkfifo(fifo)
    with pytest.raises(OSError, match="regular"):
        hardware.read_config(str(fifo))            # returns at once, no hang
    big = tmp_path / "big.cfg"
    big.write_bytes(b"x" * (hardware.MAX_BYTES + 1))
    with pytest.raises(OSError, match="larger"):
        hardware.read_config(str(big))
    hw = hardware.describe(conf(tmp_path, "vax", f"config = {fifo}\n"))
    assert "not a regular file" in hw["problems"][0]
