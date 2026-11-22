# SPDX-License-Identifier: GPL-2.0-only
"""The page in a real browser, against a real daemon and fake guests.

Needs Playwright (a development dependency only):
    pip install pytest-playwright && python -m playwright install chromium
Skipped when it is not installed. CI runs it as its own job.
"""
import os
import shutil
import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip("pytest_playwright")
if sys.version_info < (3, 11) or not shutil.which("openssl"):
    pytest.skip("needs Python 3.11 and openssl", allow_module_level=True)
pytest.importorskip("aiohttp")

from conftest import free_ports  # noqa: E402

from emuctl.web import auth  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
PASSWORD = "a long password"


@pytest.fixture
def daemon(settings, tmp_path):
    port = free_ports(1)[0]
    state = tmp_path / "state"
    auth.set_password(str(state / "users"), "alice", PASSWORD)
    wc = tmp_path / "web.conf"
    wc.write_text(f"listen = 127.0.0.1\nport = {port}\nstate_dir = {state}\n")
    env = dict(os.environ, EMUCTL_WEB_CONF=str(wc), EMUCTL_GUEST_DIR=settings.guest_dir,
               EMUCTL_RUN_DIR=settings.run_dir, EMUCTL_SERVICE="systemd", PYTHONPATH=SRC)
    proc = subprocess.Popen([sys.executable, "-m", "emuctl.cli", "web", "serve"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.2)
    yield f"https://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(10)


@pytest.fixture
def browser_context_args(browser_context_args):
    return {**browser_context_args, "ignore_https_errors": True}


def log_in(page, base):
    page.goto(base + "/")
    page.fill("input[name=user]", "alice")
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_selector("table.data")


def test_log_in_and_see_the_guests(page, daemon, guest):
    guest(name="vax", extra="boot.1 = >>> | boot\n")
    guest(name="alpha")
    page.goto(daemon + "/")
    assert page.url.endswith("/login")
    page.fill("input[name=user]", "alice")
    page.fill("input[name=password]", "wrong password")
    page.click("button[type=submit]")
    page.wait_for_selector("#msg:has-text('Wrong user name or password')")
    log_in(page, daemon)
    rows = page.locator("table.data tbody tr")
    assert rows.count() == 2
    assert "down" in rows.nth(1).inner_text()


def test_a_start_outside_the_page_shows_up_without_a_reload(page, daemon, guest):
    g = guest(name="vax", extra="boot.1 = >>> | boot\n")
    log_in(page, daemon)
    row = page.locator("table.data tbody tr", has_text="vax")
    assert "down" in row.inner_text()
    g.start()
    g.wait_state("ready", timeout=30)
    page.wait_for_selector("table.data tbody tr:has-text('vax') .st-ready", timeout=15000)
    page.click("nav.tree a:has-text('vax')")
    page.wait_for_selector("h1:has-text('vax')")
    page.wait_for_selector("pre.text:has-text('login:')", timeout=15000)


def test_no_console_errors_and_logout(page, daemon, guest):
    guest(name="vax")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    log_in(page, daemon)
    for tab in ("summary", "network", "hardware", "logs"):
        page.goto(f"{daemon}/#/g/vax/{tab}")
        page.wait_for_timeout(500)
    page.click("#logout")
    page.wait_for_url("**/login")
    assert errors == []
