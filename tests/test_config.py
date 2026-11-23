import pytest

from emuctl.config import ConfigError, Stage, parse_config

MINIMAL = """
name = pdp11
workdir = /srv/emu/pdp11
command = /usr/bin/pdp11 boot.ini
disks = rp0.dsk
serials = 21020,21021
broker = 2320
ready = login:
"""


def test_minimal_config_parses_with_defaults():
    c = parse_config(MINIMAL)
    assert c.name == "pdp11" and c.node == "pdp11"
    assert c.command == ["/usr/bin/pdp11", "boot.ini"]
    assert c.serials == [21020, 21021] and c.broker == 2320
    assert c.disks == ["/srv/emu/pdp11/rp0.dsk"]
    assert c.bind == "127.0.0.1"
    assert (c.boot_timeout, c.stop_timeout, c.guest_port) == (900, 300, 23)
    assert c.stop_mode == "signal" and not c.clean_stop
    assert c.boot_stages == [] and c.stuck == [] and c.unknown == []


def test_absolute_disk_paths_are_kept():
    c = parse_config(MINIMAL.replace("rp0.dsk", "/images/a.dsk, b.dsk"))
    assert c.disks == ["/images/a.dsk", "/srv/emu/pdp11/b.dsk"]


@pytest.mark.parametrize("key", ["name", "workdir", "command", "disks", "serials",
                                 "broker", "ready"])
def test_each_required_key_is_required(key):
    text = "\n".join(ln for ln in MINIMAL.splitlines() if not ln.startswith(key))
    with pytest.raises(ConfigError, match=key):
        parse_config(text)


@pytest.mark.parametrize("bad", [
    "serials = 21020,21020", "broker = 21020", "broker = x", "serials = a,b",
    "workdir = relative/path", "name = has space", "stop_mode = yank",
    "stop_mode = monitor", "ready = (unclosed", "stuck.1 = (unclosed",
    "boot.1 = no separator", "boot.x = a | b", "env = NOEQUALS", "stop_wake = maybe",
    "disks =", "name = pdp11",
])
def test_bad_values_are_rejected(bad):
    key = bad.split("=")[0].strip()
    lines = [ln for ln in MINIMAL.splitlines() if ln.split("=")[0].strip() != key]
    text = "\n".join(lines + [bad] + (["name = other"] if bad == "name = pdp11" else []))
    with pytest.raises(ConfigError):
        parse_config(text)


def test_stages_are_ordered_by_index_not_by_position():
    c = parse_config(MINIMAL + "boot.10 = >>> | boot dua0\nboot.2 = sim> | boot cpu\n")
    assert c.boot_stages == [Stage(b"sim>", "boot cpu"), Stage(b">>>", "boot dua0")]


def test_stop_stages_and_command_are_mutually_exclusive():
    assert parse_config(MINIMAL + "stop.1 = # | halt\n").clean_stop
    assert parse_config(MINIMAL + "stop_command = /bin/true --flag 'a b'\n").stop_command == [
        "/bin/true", "--flag", "a b"]
    with pytest.raises(ConfigError):
        parse_config(MINIMAL + "stop.1 = # | halt\nstop_command = /bin/true\n")


def test_hash_is_data_in_patterns_and_a_comment_elsewhere():
    c = parse_config(MINIMAL.replace("ready = login:", "ready = ^# $")
                     + "halted = P00>>> # x\nboot.1 = # | sync # now\nguest_port = 22 # ssh\n")
    assert c.ready == "^# $" and c.halted == "P00>>> # x"
    assert c.boot_stages == [Stage(b"#", "sync # now")]
    assert c.guest_port == 22


def test_diskless_needs_saying_so():
    c = parse_config(MINIMAL.replace("disks = rp0.dsk", "disks =") + "diskless = yes\n")
    assert c.disks == []


def test_unknown_keys_are_collected_not_fatal():
    c = parse_config(MINIMAL + "taps = tap0\ncolour = blue\n")
    assert c.unknown == ["colour", "taps"]


def test_monitor_and_freeze_modes():
    assert parse_config(MINIMAL + "stop_mode = freeze\n").clean_stop
    c = parse_config(MINIMAL + "stop_mode = monitor\nmonitor = /run/q.sock\n")
    assert c.monitor_sock == "/run/q.sock"


def test_locked_takes_a_reason_not_a_no():
    assert parse_config(MINIMAL + "locked = disk being copied\n").locked == "disk being copied"
    with pytest.raises(ConfigError, match="takes a reason"):
        parse_config(MINIMAL + "locked = no\n")
