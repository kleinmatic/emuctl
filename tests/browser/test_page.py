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
    wc.write_text(f"listen = 127.0.0.1\nport = {port}\nstate_dir = {state}\n"
                  f"users_file = {state}/users\n")
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


# --- actions, against a devhost (its stand-in systemctl starts real supervisors) ---

DEVHOST = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "devhost")


@pytest.fixture
def devhost_daemon(tmp_path):
    import re
    d = str(tmp_path / "dh")
    out = subprocess.run([sys.executable, DEVHOST, "up", d], capture_output=True, text=True,
                         check=True).stdout
    env = dict(os.environ)
    for line in out.splitlines():
        k, v = re.fullmatch(r'export (\w+)="?(.*?)"?', line).groups()
        env[k] = v.replace("$PATH", os.environ["PATH"])
    port = re.search(r"port = (\d+)", open(env["EMUCTL_WEB_CONF"]).read()).group(1)
    password = open(os.path.join(d, "web-password")).read().strip()
    proc = subprocess.Popen(["emuctl", "web", "serve"], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", int(port)), timeout=1).close()
            break
        except OSError:
            time.sleep(0.2)
    yield f"https://127.0.0.1:{port}", password
    proc.terminate()
    proc.wait(10)
    subprocess.run([sys.executable, DEVHOST, "down", d], capture_output=True, env=env)


def wait_state(page, state, timeout=40000):
    page.wait_for_selector(f".titlebar .st-{state}", timeout=timeout)


def test_start_shutdown_start_stop_now(page, devhost_daemon):
    base, password = devhost_daemon
    page.goto(base + "/")
    page.fill("input[name=user]", "dev")
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_selector("table.data")
    page.goto(base + "/#/g/vax/summary")
    page.wait_for_selector(".titlebar button:has-text('Start'):enabled")
    assert page.locator(".titlebar button:has-text('Shutdown')").is_disabled()

    page.click(".titlebar button:has-text('Start')")
    page.wait_for_selector(".taskbar td:has-text('Start')")
    wait_state(page, "ready")
    page.wait_for_selector(".taskbar tr:has-text('Start') .res-ok")

    page.click(".titlebar button:has-text('Shutdown')")            # clean: no dialog
    wait_state(page, "down")
    page.wait_for_selector(".taskbar tr:has-text('Shutdown') .res-ok", timeout=40000)

    page.click(".titlebar button:has-text('Start')")
    wait_state(page, "ready")
    page.click(".titlebar button:has-text('Stop ▾')")
    page.click(".menu button:has-text('Stop now')")
    dialog = page.locator("dialog.dialog")
    assert "power cut" in dialog.inner_text()
    dialog.locator("button:has-text('Stop now')").click()
    wait_state(page, "down")
    page.wait_for_selector(".taskbar tr:has-text('Stop now') .res-ok", timeout=40000)

    page.click(".taskbar tr:has-text('Stop now')")
    page.wait_for_selector("dialog.dialog.wide pre.log")
    assert "power cut" in page.locator("dialog.dialog.wide").inner_text()


def test_refused_start_says_why(page, devhost_daemon):
    base, password = devhost_daemon
    page.goto(base + "/")
    page.fill("input[name=user]", "dev")
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_selector("table.data")
    page.goto(base + "/#/g/vax/summary")
    page.click(".titlebar button:has-text('Start')")
    wait_state(page, "ready")
    page.goto(base + "/#/g/twin/summary")
    start = page.locator(".titlebar button:has-text('Start')")
    start.wait_for()
    page.wait_for_function(
        "() => document.querySelector('.titlebar button')?.title.includes('exclusive')")
    assert start.is_disabled()


def test_console_types_at_the_guest(page, devhost_daemon):
    base, password = devhost_daemon
    page.goto(base + "/")
    page.fill("input[name=user]", "dev")
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")
    page.wait_for_selector("table.data")
    page.goto(base + "/#/g/vax/summary")
    page.click(".titlebar button:has-text('Start')")
    wait_state(page, "ready")
    page.goto(base + "/#/g/vax/console")
    page.wait_for_selector(".xterm-rows:has-text('login:')", timeout=15000)
    assert "Viewing" in page.locator(".console-banner").inner_text()
    page.click(".terminal")
    page.keyboard.type("dropped")                   # viewing: not sent
    page.click(".console-banner button:has-text('Take keyboard')")
    page.wait_for_selector(".console-banner:has-text('Typing')")
    page.click(".terminal")
    page.keyboard.type("hello")
    page.keyboard.press("Enter")
    page.wait_for_selector(".xterm-rows:has-text('?hello')", timeout=10000)
    assert "dropped" not in page.locator(".xterm-rows").inner_text()
    page.click(".console-banner button:has-text('Release keyboard')")
    page.wait_for_selector(".console-banner:has-text('Viewing')")
