# SPDX-License-Identifier: GPL-2.0-only
"""The web daemon: security middleware and read-only routes (aiohttp test client)."""
import asyncio
import json
import os
import sys

import pytest

if sys.version_info < (3, 11):
    pytest.skip("the web interface needs Python 3.11", allow_module_level=True)
pytest.importorskip("aiohttp")
pytest.importorskip("pytest_aiohttp")

from aiohttp import web  # noqa: E402
from test_cli import add_guest  # noqa: E402

from emuctl.web import app as webapp  # noqa: E402
from emuctl.web import auth, data  # noqa: E402
from emuctl.web.conf import WebConf  # noqa: E402
from emuctl.web.sampler import Sampler  # noqa: E402

PASSWORD = "a long password"


@pytest.fixture
def site(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "LOG2_N", 10)
    add_guest(settings, tmp_path, "g1", serial=21000, broker=2300)
    add_guest(settings, tmp_path, "g2", serial=21010, broker=2310)
    users = str(tmp_path / "users")
    auth.set_password(users, "alice", PASSWORD)
    conf = WebConf(tls=False, users_file=users, state_dir=str(tmp_path / "state"))
    sampler = Sampler(settings, probe=lambda *a, **k: False)
    return settings, conf, sampler


@pytest.fixture
async def client(aiohttp_client, site):
    settings, conf, sampler = site
    app = webapp.make_app(settings, conf, sampler=sampler, background=False)
    c = await aiohttp_client(app)
    c.origin = f"http://{c.host}:{c.port}"
    return c


async def login(client, user="alice", password=PASSWORD, origin=None):
    return await client.post("/api/login", json={"user": user, "password": password},
                             headers={"Origin": origin or client.origin})


async def session(client):
    """Headers for a logged-in browser: cookie, CSRF token and Origin."""
    r = await login(client)
    assert r.status == 200, await r.text()
    token = r.cookies[webapp.COOKIE].value
    csrf = (await r.json())["csrf"]
    client.session.cookie_jar.clear()
    return {"Cookie": f"{webapp.COOKIE}={token}", webapp.CSRF_HEADER: csrf,
            "Origin": client.origin}


# --- rule 2: sessions -----------------------------------------------------------

async def test_everything_but_the_login_page_needs_a_session(client):
    r = await client.get("/api/guests")
    assert r.status == 401 and (await r.json())["error"] == "not logged in"
    r = await client.get("/", allow_redirects=False)
    assert r.status == 302 and r.headers["Location"] == "/login"
    for path in ("/api/host", "/api/host/network", "/api/guests/g1", "/api/guests/g1/log",
                 "/api/guests/g1/hardware", "/api/guests/g1/network", "/api/guests/g1/usage",
                 "/api/events", "/api/session"):
        assert (await client.get(path)).status == 401, path
    assert (await client.get("/login")).status == 200
    assert (await client.get("/api/guests", headers={"Cookie": f"{webapp.COOKIE}=forged"})
            ).status == 401


async def test_login_sets_a_strict_cookie(client):
    r = await login(client)
    assert r.status == 200
    cookie = r.cookies[webapp.COOKIE]
    assert cookie["httponly"] and cookie["samesite"] == "Strict" and not cookie["secure"]
    assert len(cookie.value) >= 43


async def test_tls_on_makes_the_cookie_secure(aiohttp_client, site):
    settings, conf, sampler = site
    from dataclasses import replace
    app = webapp.make_app(settings, replace(conf, tls=True), sampler=sampler, background=False)
    c = await aiohttp_client(app)
    r = await c.post("/api/login", json={"user": "alice", "password": PASSWORD},
                     headers={"Origin": f"http://{c.host}:{c.port}"})
    assert r.cookies[webapp.COOKIE]["secure"]
    assert "Strict-Transport-Security" in r.headers


async def test_wrong_password_and_bad_bodies(client):
    assert (await login(client, password="nope")).status == 401
    assert (await login(client, user="nobody")).status == 401
    for body in ("not json", "[1]", '{"user": 1, "password": 2}'):
        r = await client.post("/api/login", data=body, headers={
            "Origin": client.origin, "Content-Type": "application/json"})
        assert r.status == 400, body
    r = await client.post("/api/login", data="user=a", headers={"Origin": client.origin})
    assert r.status == 415


async def test_logout_ends_the_session(client):
    h = await session(client)
    assert (await client.get("/api/session", headers=h)).status == 200
    assert (await client.post("/api/logout", headers=h)).status == 200
    assert (await client.get("/api/session", headers=h)).status == 401


async def test_idle_session_expires(client):
    h = await session(client)
    sessions = client.app[webapp.SESSIONS]
    for s in sessions._by_token.values():
        s.seen -= auth.IDLE + 1
    assert (await client.get("/api/guests", headers=h)).status == 401


# --- rule 3: Origin and CSRF ----------------------------------------------------

async def test_login_needs_our_origin(client):
    assert (await client.post("/api/login", json={"user": "alice", "password": PASSWORD})
            ).status == 403
    assert (await login(client, origin="https://evil.example")).status == 403
    assert (await login(client, origin=client.origin.replace("http:", "https:"))).status == 403


async def test_posts_need_the_csrf_token(client):
    h = await session(client)
    no_token = {k: v for k, v in h.items() if k != webapp.CSRF_HEADER}
    assert (await client.post("/api/logout", headers=no_token)).status == 403
    assert (await client.post("/api/logout", headers=dict(h, **{webapp.CSRF_HEADER: "x"}))
            ).status == 403
    assert (await client.post("/api/logout", headers=dict(h, Origin="https://evil.example"))
            ).status == 403
    assert (await client.get("/api/session", headers=h)).status == 200     # still logged in


async def test_websocket_upgrade_needs_our_origin(client):
    h = await session(client)
    r = await client.get("/api/guests", headers=dict(h, Origin="https://evil.example",
                                                     Upgrade="websocket", Connection="Upgrade"))
    assert r.status == 403


# --- rule 4: guest names --------------------------------------------------------

async def test_bad_guest_names_are_404_before_the_handler(client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("handler ran")
    monkeypatch.setattr(data, "guest_detail", boom)
    monkeypatch.setattr(data, "guest_hardware", boom)
    h = await session(client)
    for name in ("nope", "..", "g1%2F..%2Fg2", ".hidden", "g1.conf", "-g1", "%00"):
        for tail in ("", "/hardware"):
            r = await client.get(f"/api/guests/{name}{tail}", headers=h)
            assert r.status == 404, (name, tail)


# --- rule 7: the login limit ----------------------------------------------------

async def test_login_limit(client):
    for _ in range(5):
        assert (await login(client, password="wrong")).status == 401
    r = await login(client)                 # even the right password waits
    assert r.status == 429 and int(r.headers["Retry-After"]) >= 1
    client.app[webapp.LIMITER]._seen.clear()
    assert (await login(client)).status == 200


async def test_parallel_logins_cannot_outrun_the_limit(client):
    rs = await asyncio.gather(*[login(client, password="wrong") for _ in range(12)])
    assert sum(r.status == 401 for r in rs) <= 6
    assert any(r.status == 429 for r in rs)


# --- rule 8 and the role marks --------------------------------------------------

async def test_security_headers_everywhere(client):
    h = await session(client)
    for path, headers in (("/login", {}), ("/api/guests", h), ("/api/guests/nope", h),
                          ("/api/guests", {}), ("/static/login.html", {}), ("/nothing", {})):
        r = await client.get(path, headers=headers)
        assert r.headers["Content-Security-Policy"] == webapp.CSP, path
        assert "unsafe" not in r.headers["Content-Security-Policy"]
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert (await client.get("/api/guests", headers=h)).headers["Cache-Control"] == "no-store"


async def test_a_route_without_a_mark_is_refused(aiohttp_client, site):
    settings, conf, sampler = site
    app = webapp.make_app(settings, conf, sampler=sampler, background=False)

    async def unmarked(request):
        return web.json_response({"secret": 1})
    app.router.add_get("/api/unmarked", unmarked)
    c = await aiohttp_client(app)
    c.origin = f"http://{c.host}:{c.port}"
    h = await session(c)
    assert (await c.get("/api/unmarked", headers=h)).status == 403
    with pytest.raises(ValueError):
        webapp.needs("god")


async def test_static_files_do_not_follow_symlinks(client, tmp_path):
    link = os.path.join(webapp.STATIC, "test-link")
    secret = tmp_path / "secret"
    secret.write_text("secret")
    os.symlink(secret, link)
    try:
        assert (await client.get("/static/test-link")).status == 404
        assert (await client.get("/static/../app.py")).status in (403, 404)
    finally:
        os.unlink(link)


# --- read-only routes -----------------------------------------------------------

async def test_guest_list_and_detail(client):
    h = await session(client)
    rows = await (await client.get("/api/guests", headers=h)).json()
    assert [r["name"] for r in rows] == ["g1", "g2"]
    assert rows[0]["state"] == "down" and "disk_bytes" in rows[0]
    g = await (await client.get("/api/guests/g1", headers=h)).json()
    assert g["can_start"] is True and g["start_refused"] == ""
    assert g["console"]["port"] == 2300 and g["display"] is None
    assert "power cut" in g["stop_route"]


async def test_guest_with_broken_conf(client, settings):
    h = await session(client)
    with open(settings.conf_path("g2"), "w") as fh:
        fh.write("name = g2\n")
    client.app[webapp.SAMPLER].rows = []
    g = await (await client.get("/api/guests/g2", headers=h)).json()
    assert g["state"] == "error" and "missing required key" in g["conf_error"]
    assert (await client.get("/api/guests/g2/hardware", headers=h)).status == 409


async def test_host_and_network(client):
    h = await session(client)
    host = await (await client.get("/api/host", headers=h)).json()
    assert host["guests"] == 2 and host["filesystems"][0]["guests"] == ["g1", "g2"]
    net = await (await client.get("/api/host/network", headers=h)).json()
    assert "bridges" in net
    g = await (await client.get("/api/guests/g1/network", headers=h)).json()
    assert "nics" in g


async def test_hardware(client, settings, tmp_path):
    with open(settings.conf_path("g1"), "a") as fh:
        fh.write("config = boot.ini\nkind = simh\n")
    (tmp_path / "g1" / "boot.ini").write_text("set cpu 64m\n")
    h = await session(client)
    hw = await (await client.get("/api/guests/g1/hardware", headers=h)).json()
    assert hw["memory"] == "64 MB"
    assert hw["raw"][0]["text"] == "set cpu 64m\n"


async def test_logs(client, settings, tmp_path, monkeypatch):
    h = await session(client)
    r = await client.get("/api/guests/g1/log?source=console", headers=h)
    assert (await r.json())["missing"] is True
    (tmp_path / "g1" / "console.log").write_bytes(b"one\r\n\x1b[1mtwo\x1b[0m\r\nthree\n")
    r = await client.get("/api/guests/g1/log?source=console&lines=2", headers=h)
    assert (await r.json())["text"] == "two\nthree"
    for q in ("source=elsewhere", "lines=many"):
        assert (await client.get(f"/api/guests/g1/log?{q}", headers=h)).status == 400

    def fake_run(argv, **kw):
        fake_run.argv = argv
        return type("R", (), {"stdout": b"supervisor says hi\n", "stderr": b""})()
    monkeypatch.setattr(data.subprocess, "run", fake_run)
    r = await client.get("/api/guests/g1/log?lines=999999", headers=h)
    assert (await r.json())["text"] == "supervisor says hi\n"
    assert "5000" in fake_run.argv and "journalctl" in fake_run.argv


async def test_console_log_under_the_env_directory_is_refused(client, tmp_path, monkeypatch):
    (tmp_path / "g1" / "console.log").write_text("password=hunter2\n")
    monkeypatch.setattr(data, "ENV_DIR", str(tmp_path / "g1"))
    h = await session(client)
    r = await client.get("/api/guests/g1/log?source=console", headers=h)
    assert (await r.json()) == {"source": "console", "text": "", "missing": True}


async def test_usage_series(client):
    h = await session(client)
    r = await client.get("/api/guests/g1/usage", headers=h)
    body = await r.json()
    assert body["interval"] == 5 and len(body["t"]) == len(body["cpu_pct"]) >= 1
    assert (await client.get("/api/guests/g1/usage?window=1y", headers=h)).status == 400


async def test_events_stream_and_logout_closes_it(client):
    h = await session(client)
    r = await client.get("/api/events", headers=h)
    assert r.headers["Content-Type"].startswith("text/event-stream")
    line = await r.content.readline()
    assert line == b"event: guests\n"
    payload = json.loads((await r.content.readline())[len(b"data: "):])
    assert [g["name"] for g in payload] == ["g1", "g2"]
    await r.content.readline()
    client.app[webapp.HUB].publish("guests", [])
    assert await r.content.readline() == b"event: guests\n"
    await r.content.readline()
    await r.content.readline()
    await client.post("/api/logout", headers=h)
    rest = await asyncio.wait_for(r.content.read(), 5)
    assert b"event" not in rest


def test_hub_drops_a_page_that_stopped_reading():
    hub = webapp.Hub(depth=2)
    q = hub.subscribe("t")
    for _ in range(3):
        hub.publish("guests", [])
    assert q not in hub.subs and q.get_nowait()[0] is webapp.END


async def test_background_sampler_publishes(aiohttp_client, site, monkeypatch):
    settings, conf, sampler = site
    sampler.interval = 0.05
    app = webapp.make_app(settings, conf, sampler=sampler)
    c = await aiohttp_client(app)
    q = app[webapp.HUB].subscribe("t")
    event, rows = await asyncio.wait_for(q.get(), 5)
    assert event == "guests" and len(rows) == 2
    await c.close()


# --- emuctl web serve -----------------------------------------------------------

def serve_env(tmp_path, monkeypatch, text):
    wc = tmp_path / "web.conf"
    wc.write_text(f"state_dir = {tmp_path}/state\n" + text)
    monkeypatch.setenv("EMUCTL_WEB_CONF", str(wc))


def test_serve_sets_up_tls_and_runs(settings, tmp_path, monkeypatch, caplog):
    import logging

    from emuctl.web import main as web_main
    serve_env(tmp_path, monkeypatch, "listen = 127.0.0.1\nport = 18999\n")
    ran = {}
    monkeypatch.setattr(web, "run_app", lambda app, **kw: ran.update(kw, app=app))
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: None)
    with caplog.at_level(logging.INFO, logger="emuctl.web"):
        assert web_main.cmd_serve(settings, None) == 0
    assert ran["ssl_context"] is not None and (ran["host"], ran["port"]) == ("127.0.0.1", 18999)
    assert ran["app"][webapp.SETTINGS].elevate == "none"     # acts through polkit, never sudo
    assert (tmp_path / "state" / "web.key").exists()
    text = caplog.text
    assert "self-signed" in text and "fingerprint" in text and "no users" in text


def test_serve_tls_off_warns(settings, tmp_path, monkeypatch, caplog):
    import logging

    from emuctl.web import main as web_main
    serve_env(tmp_path, monkeypatch, "tls = off\n")
    ran = {}
    monkeypatch.setattr(web, "run_app", lambda app, **kw: ran.update(kw))
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: None)
    with caplog.at_level(logging.INFO, logger="emuctl.web"):
        assert web_main.cmd_serve(settings, None) == 0
    assert ran["ssl_context"] is None and "in clear" in caplog.text
    assert not (tmp_path / "state" / "web.crt").exists()


def test_serve_refuses_bad_setups(settings, tmp_path, monkeypatch, capsys):
    from emuctl.web import main as web_main
    monkeypatch.setattr(web, "run_app", lambda app, **kw: None)
    serve_env(tmp_path, monkeypatch, "port = nope\n")
    assert web_main.cmd_serve(settings, None) == 2
    serve_env(tmp_path, monkeypatch, "tls_cert = /nonexistent/c\ntls_key = /nonexistent/k\n")
    assert web_main.cmd_serve(settings, None) == 1
    assert "does not exist" in capsys.readouterr().err
    monkeypatch.setitem(sys.modules, "aiohttp", None)
    assert web_main.cmd_serve(settings, None) == 2
    assert "python3-aiohttp" in capsys.readouterr().err


def test_web_command_dispatch(monkeypatch, capsys):
    from emuctl import cli
    from emuctl.web import main as web_main
    seen = []
    monkeypatch.setattr(web_main, "cmd_serve", lambda s, a: seen.append("serve") or 0)
    monkeypatch.setattr(web_main, "cmd_passwd", lambda a: seen.append(a.user) or 0)
    monkeypatch.setenv("EMUCTL_GUEST_DIR", "/nonexistent")
    assert cli.main(["web", "serve"]) == 0
    assert cli.main(["web", "passwd", "alice"]) == 0
    assert cli.main(["web"]) == 2
    assert seen == ["serve", "alice"]


def test_real_daemon_over_https(tmp_path, settings):
    """The whole thing as a process: makes its certificate, serves HTTPS, keeps the cert."""
    import socket
    import ssl
    import subprocess
    import time
    import urllib.request

    from conftest import free_ports
    port = free_ports(1)[0]
    wc = tmp_path / "web.conf"
    wc.write_text(f"listen = 127.0.0.1\nport = {port}\nstate_dir = {tmp_path}/state\n")
    env = dict(os.environ, EMUCTL_WEB_CONF=str(wc), EMUCTL_GUEST_DIR=settings.guest_dir,
               EMUCTL_RUN_DIR=settings.run_dir, PYTHONPATH=os.path.join(
                   os.path.dirname(__file__), "..", "src"))
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE

    def run_once():
        proc = subprocess.Popen([sys.executable, "-m", "emuctl.cli", "web", "serve"], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.2)
            r = urllib.request.urlopen(f"https://127.0.0.1:{port}/login", context=ctx, timeout=5)
            assert r.status == 200 and "default-src 'none'" in r.headers["Content-Security-Policy"]
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(f"https://127.0.0.1:{port}/api/guests", context=ctx)
            assert e.value.code == 401
        finally:
            proc.terminate()
            proc.wait(10)
        return (tmp_path / "state" / "web.crt").read_bytes()

    if not __import__("shutil").which("openssl"):
        pytest.skip("no openssl")
    first = run_once()
    assert run_once() == first
