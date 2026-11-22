# SPDX-License-Identifier: GPL-2.0-only
"""The conf keys the web page needs: kind, config, nics, display, notes, hw.*"""
import pytest
from test_cli import add_guest

from emuctl import cli
from emuctl.config import ConfigError, guess_kind, parse_config
from emuctl.status import format_info

BASE = ("name = g1\nworkdir = /srv/g1\ncommand = {cmd}\ndisks = d.img\nserials = 1\n"
        "broker = 2\nready = login:\n")


def conf(extra="", cmd="/usr/bin/es40 es40.cfg"):
    return parse_config(BASE.format(cmd=cmd) + extra)


@pytest.mark.parametrize("cmd,kind", [
    ("/opt/es40-0.95/bin/es40 x.cfg", "es40"),
    ("qemu-system-ppc -M 40p", "qemu"),
    ("bin/linux-x86_64/gxemul -c machine.json", "gxemul"),
    ("/usr/local/bin/vax vax.ini", "simh"),
    ("microvax3900 boot.ini", "simh"),
    ("pdp11", "simh"),
    ("/usr/bin/86Box", "other"),
])
def test_kind_is_guessed_from_the_program(cmd, kind):
    assert conf(cmd=cmd).kind == kind


def test_kind_can_be_set_and_is_checked():
    assert conf("kind = simh\n").kind == "simh"
    with pytest.raises(ConfigError, match="kind"):
        conf("kind = mame\n")
    assert guess_kind([]) == "other"


def test_config_and_notes_are_relative_to_workdir():
    c = conf("config = es40.cfg, /etc/shared.cfg\nnotes = NOTES.txt\n")
    assert c.config_files == ["/srv/g1/es40.cfg", "/etc/shared.cfg"]
    assert c.notes == "/srv/g1/NOTES.txt"


def test_defaults():
    c = conf()
    assert (c.config_files, c.nics, c.display, c.display_auth, c.notes, c.hw) == \
        ([], [], None, "none", None, {})


def test_nics():
    assert conf("nics = tap1, vea1\n").nics == ["tap1", "vea1"]
    for bad in ("tap1; rm -rf /", "../etc", "-v", ".x"):
        with pytest.raises(ConfigError, match="interface name"):
            conf(f"nics = {bad}\n")


def test_display():
    c = conf("display = vnc://127.0.0.1:5906\ndisplay_password_file = vnc.pass\n")
    assert c.display == ("127.0.0.1", 5906)
    assert c.display_auth == "vnc"          # a password file implies vnc auth
    assert c.display_password_file == "/srv/g1/vnc.pass"
    assert conf("display = vnc://[::1]:5900\n").display == ("::1", 5900)
    assert conf("display = vnc://localhost:5900\n").display_auth == "none"


@pytest.mark.parametrize("extra,match", [
    ("display = 127.0.0.1:5906\n", "vnc://"),
    ("display = vnc://host:70000\n", "vnc://"),
    ("display = vnc://host:5900\ndisplay_auth = vnc\n", "display_password_file"),
    ("display = vnc://host:5900\ndisplay_auth = tls\n", "display_auth"),
    ("display_password_file = p\n", "need display"),
    ("display = vnc://h:5900\ndisplay_auth = none\ndisplay_password_file = p\n",
     "display_auth = none"),
])
def test_bad_display(extra, match):
    with pytest.raises(ConfigError, match=match):
        conf(extra)


def test_hw_lines_keep_order_and_are_not_unknown():
    c = conf("hw.memory = 128 MB\nhw.cpu = R3000 at 25 MHz\n")
    assert list(c.hw.items()) == [("memory", "128 MB"), ("cpu", "R3000 at 25 MHz")]
    assert c.unknown == []
    with pytest.raises(ConfigError, match="label"):
        conf("hw. = x\n")


def test_check_reports_missing_named_files(settings, tmp_path):
    add_guest(settings, tmp_path, extra="config = nope.cfg\nnotes = nope.txt\n"
                                         "display = vnc://127.0.0.1:5999\n"
                                         "display_password_file = nope.pass\n")
    errors, warnings = cli.check_guest(settings, "g1")
    assert any("config file" in e for e in errors)
    assert any("display_password_file" in e for e in errors)
    assert any("notes file" in w for w in warnings)


def test_info_shows_the_new_keys(settings, tmp_path):
    add_guest(settings, tmp_path, extra="config = a.cfg\nnics = tap9\n"
                                         "display = vnc://127.0.0.1:5999\nnotes = n.txt\n")
    out = format_info(settings, "g1", probe=lambda *a: False)
    for text in ("kind", "a.cfg", "tap9", "vnc://127.0.0.1:5999  (no password)", "n.txt"):
        assert text in out
