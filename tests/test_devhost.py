# SPDX-License-Identifier: GPL-2.0-only
"""tools/devhost: the throwaway host contributors try changes on."""
import json
import os
import re
import socket
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVHOST = os.path.join(ROOT, "tools", "devhost")

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="devhost is for Linux development hosts")


def devhost_env(d):
    out = subprocess.run([sys.executable, DEVHOST, "up", d], capture_output=True,
                         text=True, check=True).stdout
    env = dict(os.environ)
    for line in out.splitlines():
        k, v = re.fullmatch(r'export (\w+)="?(.*?)"?', line).groups()
        env[k] = v.replace("$PATH", os.environ["PATH"])
    return env


@pytest.fixture
def host(tmp_path):
    d = str(tmp_path / "dh")
    env = devhost_env(d)
    yield d, env
    subprocess.run([sys.executable, DEVHOST, "down", d], capture_output=True, env=env)


def emuctl(env, *args, check=True):
    return subprocess.run(["emuctl", *args], capture_output=True, text=True, env=env,
                          check=check, timeout=120)


def test_lists_the_fake_guests(host):
    _, env = host
    rows = json.loads(emuctl(env, "list", "--json").stdout)
    assert [r["name"] for r in rows] == ["alpha", "stuck", "twin", "vax"]
    assert {r["state"] for r in rows} == {"down"}


def test_start_console_guard_stop(host):
    d, env = host
    emuctl(env, "start", "vax", "--wait")
    row = json.loads(emuctl(env, "status", "vax", "--json", check=False).stdout)
    assert row["state"] == "ready"

    # The console broker answers and shows the guest's prompt.
    s = socket.create_connection(("127.0.0.1", row["broker"]), timeout=5)
    s.settimeout(5)
    s.sendall(b"\r")
    data, deadline = b"", time.time() + 10
    while b"login:" not in data and time.time() < deadline:
        data += s.recv(4096)
    s.close()
    assert b"login:" in data

    # The start guard refuses the exclusive twin.
    r = emuctl(env, "start", "twin", check=False)
    assert r.returncode == 2 and "exclusive" in r.stderr

    # A clean stop types the shutdown and the supervisor goes away.
    emuctl(env, "stop", "vax")
    assert json.loads(emuctl(env, "status", "vax", "--json", check=False).stdout)["state"] == "down"
    assert "shutdown" in open(os.path.join(d, "work", "vax", "console.log"), "rb").read().decode()
    assert "[emuctl]" in emuctl(env, "log", "vax", "-n", "200").stdout


def test_stand_in_systemctl_refuses_other_units(host):
    _, env = host
    r = subprocess.run(["systemctl", "start", "ssh.service"], capture_output=True, text=True,
                       env=env)
    assert r.returncode != 0 and "only emuctl-guest@" in r.stderr


def test_down_removes_everything(tmp_path):
    d = str(tmp_path / "dh")
    env = devhost_env(d)
    emuctl(env, "start", "alpha")
    subprocess.run([sys.executable, DEVHOST, "down", d], check=True, capture_output=True,
                   env=env)
    assert not os.path.exists(d)


def test_down_uses_the_shells_devhost(tmp_path):
    """`down` with no directory acts on the devhost the exports name."""
    d = str(tmp_path / "custom")
    env = devhost_env(d)
    subprocess.run([sys.executable, DEVHOST, "down"], check=True, capture_output=True, env=env)
    assert not os.path.exists(d)
