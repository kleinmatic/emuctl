# SPDX-License-Identifier: GPL-2.0-only
"""web/console.py: the serial console over a WebSocket, against a fake broker."""
import asyncio
import json
import sys

import pytest

if sys.version_info < (3, 11):
    pytest.skip("the web interface needs Python 3.11", allow_module_level=True)
pytest.importorskip("aiohttp")
pytest.importorskip("pytest_aiohttp")

from aiohttp import WSMsgType, WSServerHandshakeError  # noqa: E402
from test_cli import add_guest  # noqa: E402
from test_web_app import PASSWORD, session  # noqa: E402

from emuctl.web import app as webapp  # noqa: E402
from emuctl.web import auth, console  # noqa: E402
from emuctl.web.conf import WebConf  # noqa: E402
from emuctl.web.sampler import Sampler  # noqa: E402


class FakeBroker:
    """Stands in for a guest's console broker: sends a telnet greeting, then
    records what clients send and lets the test push output."""

    def __init__(self):
        self.received = bytearray()
        self.writers = []

    async def handle(self, reader, writer):
        self.writers.append(writer)
        writer.write(b"\xff\xfb\x01\xff\xfb\x03login: ")
        await writer.drain()
        while True:
            d = await reader.read(4096)
            if not d:
                break
            self.received += d

    async def say(self, data):
        for w in self.writers:
            w.write(data)
            await w.drain()

    async def close(self):
        for w in self.writers:
            w.close()


@pytest.fixture
async def site(aiohttp_client, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "LOG2_N", 10)
    broker = FakeBroker()
    server = await asyncio.start_server(broker.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    add_guest(settings, tmp_path, "g1", broker=port)
    (tmp_path / "g1" / "console.log").write_bytes(b"earlier output\r\n")
    users = str(tmp_path / "users")
    auth.set_password(users, "alice", PASSWORD)
    auth.set_password(users, "bob", PASSWORD)
    conf = WebConf(tls=False, users_file=users, task_log=str(tmp_path / "tasks.log"))
    app = webapp.make_app(settings, conf, sampler=Sampler(settings, probe=lambda *a, **k: False),
                          background=False)
    c = await aiohttp_client(app)
    c.origin = f"http://{c.host}:{c.port}"
    yield c, broker
    server.close()


async def open_console(c, headers):
    return await c.ws_connect("/api/guests/g1/console",
                              headers={k: v for k, v in headers.items()
                                       if k in ("Cookie", "Origin")})


async def recv_until(ws, want, timeout=5):
    """Collect frames until the bytes seen contain `want`; return (bytes, statuses)."""
    got, statuses = bytearray(), []
    end = asyncio.get_running_loop().time() + timeout
    while want not in got:
        msg = await asyncio.wait_for(ws.receive(), end - asyncio.get_running_loop().time())
        if msg.type == WSMsgType.BINARY:
            got += msg.data
        elif msg.type == WSMsgType.TEXT:
            statuses.append(json.loads(msg.data))
        else:
            break
    return bytes(got), statuses


async def status(ws, timeout=5):
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout)
        if msg.type == WSMsgType.TEXT:
            return json.loads(msg.data)
        if msg.type != WSMsgType.BINARY:
            raise AssertionError(f"socket ended: {msg}")


async def until_broker_has(broker, data, timeout=5):
    for _ in range(int(timeout / 0.02)):
        if data in broker.received:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"broker got {bytes(broker.received)!r}")


async def test_scrollback_then_live_output_without_telnet(site):
    c, broker = site
    ws = await open_console(c, await session(c))
    got, statuses = await recv_until(ws, b"login: ")
    assert got.startswith(b"earlier output\r\n")
    assert b"\xff" not in got                       # negotiation stripped
    await broker.say(b"more\r\n")
    got, _ = await recv_until(ws, b"more")
    assert statuses and statuses[0]["typing"] is False and statuses[0]["running"] is True
    await ws.close()


async def test_keys_are_dropped_until_the_keyboard_is_taken(site):
    c, broker = site
    ws = await open_console(c, await session(c))
    await recv_until(ws, b"login: ")
    await ws.send_bytes(b"ignored\r")
    await asyncio.sleep(0.2)
    assert broker.received == b""
    await ws.send_str(json.dumps({"type": "take"}))
    while not (await status(ws))["typing"]:
        pass
    await ws.send_bytes(b"root\r\xff")
    await until_broker_has(broker, b"root\r\xff\xff")     # 0xFF doubled for telnet
    await ws.send_str(json.dumps({"type": "release"}))
    while (await status(ws))["typing"]:
        pass
    await ws.send_bytes(b"after")
    await asyncio.sleep(0.2)
    assert b"after" not in broker.received
    await ws.close()
    notes = [t for t in c.app[webapp.TASKS].list() if t["action"] == "console"]
    assert [n["message"] for n in reversed(notes)] == ["took the keyboard",
                                                       "gave back the keyboard"]
    assert all(n["user"] == "alice" for n in notes)


async def test_one_typist_at_a_time(site):
    c, broker = site
    a = await open_console(c, await session(c))
    r = await c.post("/api/login", json={"user": "bob", "password": PASSWORD},
                     headers={"Origin": c.origin})
    bob = {"Cookie": f"{webapp.COOKIE}={r.cookies[webapp.COOKIE].value}", "Origin": c.origin}
    c.session.cookie_jar.clear()
    b = await open_console(c, bob)
    await recv_until(a, b"login: ")
    await recv_until(b, b"login: ")
    await asyncio.sleep(0.1)
    await a.send_str(json.dumps({"type": "take"}))
    while not (await status(a))["typing"]:
        pass
    await b.send_str(json.dumps({"type": "take"}))
    s = await status(a)
    while s["typing"]:
        s = await status(a)
    assert s["typist"] == "bob"
    await a.send_bytes(b"from-alice")
    await b.send_bytes(b"from-bob")
    await until_broker_has(broker, b"from-bob")
    assert b"from-alice" not in broker.received
    msgs = [t["message"] for t in c.app[webapp.TASKS].list() if t["action"] == "console"]
    assert "took the keyboard from alice" in msgs
    await b.close()                                 # leaving gives it back, and says so
    await asyncio.sleep(0.2)
    msgs = [t["message"] for t in c.app[webapp.TASKS].list() if t["action"] == "console"]
    assert any("left the page" in m for m in msgs)
    await a.close()


async def test_the_console_closing_is_reported(site):
    c, broker = site
    ws = await open_console(c, await session(c))
    await recv_until(ws, b"login: ")
    await broker.close()
    seen = []
    while True:
        msg = await asyncio.wait_for(ws.receive(), 5)
        if msg.type == WSMsgType.TEXT:
            seen.append(json.loads(msg.data))
        elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
            break
    assert any(s["running"] is False and s["message"] == "the console closed" for s in seen)


async def test_a_guest_that_is_not_running(site, settings, tmp_path):
    c, _ = site
    add_guest(settings, tmp_path, "g2", serial=21010, broker=1)   # nothing listens on port 1
    h = await session(c)
    ws = await c.ws_connect("/api/guests/g2/console",
                            headers={"Cookie": h["Cookie"], "Origin": c.origin})
    s = await status(ws)
    assert s["running"] is False and "not running" in s["message"]


async def test_logout_closes_the_console(site):
    c, _ = site
    h = await session(c)
    ws = await open_console(c, h)
    await recv_until(ws, b"login: ")
    await c.post("/api/logout", headers=h)
    msg = await asyncio.wait_for(ws.receive(), 5)
    while msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
        msg = await asyncio.wait_for(ws.receive(), 5)
    assert ws.closed or msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED)


async def test_console_needs_a_session_and_our_origin(site):
    c, _ = site
    h = await session(c)
    with pytest.raises(WSServerHandshakeError) as e:
        await c.ws_connect("/api/guests/g1/console", headers={"Origin": c.origin})
    assert e.value.status == 401
    with pytest.raises(WSServerHandshakeError) as e:
        await c.ws_connect("/api/guests/g1/console",
                           headers={"Cookie": h["Cookie"], "Origin": "https://evil.example"})
    assert e.value.status == 403
    with pytest.raises(WSServerHandshakeError) as e:
        await c.ws_connect("/api/guests/g1/console", headers={"Cookie": h["Cookie"]})
    assert e.value.status == 403
    with pytest.raises(WSServerHandshakeError) as e:
        await c.ws_connect("/api/guests/nope/console",
                           headers={"Cookie": h["Cookie"], "Origin": c.origin})
    assert e.value.status == 404


async def test_too_many_consoles(site, monkeypatch):
    c, _ = site
    monkeypatch.setattr(webapp, "CONSOLES_PER_SESSION", 2)
    h = await session(c)
    a = await open_console(c, h)
    b = await open_console(c, h)
    with pytest.raises(WSServerHandshakeError) as e:
        await open_console(c, h)
    assert e.value.status == 429
    await a.close()
    await b.close()


async def test_a_watcher_cannot_take_the_keyboard(site, monkeypatch):
    c, broker = site
    real = console.serve

    async def watch_only(request, cfg, consoles, sessions, can_type=True):
        return await real(request, cfg, consoles, sessions, can_type=False)
    monkeypatch.setattr(console, "serve", watch_only)
    ws = await open_console(c, await session(c))
    await recv_until(ws, b"login: ")
    await ws.send_str(json.dumps({"type": "take"}))
    s = await status(ws)
    while "only watch" not in s["message"]:
        s = await status(ws)
    await ws.send_bytes(b"nope")
    await asyncio.sleep(0.2)
    assert broker.received == b""
    await ws.close()


def test_escape_iac():
    assert console.escape_iac(b"a\xffb") == b"a\xff\xffb"


async def test_guest_bytes_pass_through_unchanged(site):
    c, broker = site
    ws = await open_console(c, await session(c))
    await recv_until(ws, b"login: ")
    await broker.say(b"A\xff\xfdBC\xffDE!")
    got, _ = await recv_until(ws, b"DE!")
    assert b"A\xff\xfdBC\xffDE!" in got
    await ws.close()


async def test_a_flood_of_takes_is_limited(site):
    c, _ = site
    ws = await open_console(c, await session(c))
    await recv_until(ws, b"login: ")
    for _ in range(50):
        await ws.send_str(json.dumps({"type": "take"}))
        await ws.send_str(json.dumps({"type": "release"}))
    await asyncio.sleep(0.5)
    notes = [t for t in c.app[webapp.TASKS].list() if t["action"] == "console"]
    assert len(notes) <= 6
    await ws.close()


async def test_logout_frees_the_keyboard_at_once(site):
    c, broker = site
    h = await session(c)
    ws = await open_console(c, h)
    await recv_until(ws, b"login: ")
    await ws.send_str(json.dumps({"type": "take"}))
    while not (await status(ws))["typing"]:
        pass
    await c.post("/api/logout", headers=h)
    assert c.app[webapp.STATE]["consoles"].rooms.get("g1") is None or \
        c.app[webapp.STATE]["consoles"].rooms["g1"].typist is None
    for _ in range(50):
        if not c.app[webapp.STATE]["consoles"].handlers:
            break
        await asyncio.sleep(0.05)
    assert not c.app[webapp.STATE]["consoles"].handlers
    msgs = [t["message"] for t in c.app[webapp.TASKS].list() if t["action"] == "console"]
    assert any("left the page" in m for m in msgs)


async def test_close_all_does_not_wait_for_a_stalled_page(site):
    c, _ = site
    ws = await open_console(c, await session(c))
    await recv_until(ws, b"login: ")
    consoles = c.app[webapp.STATE]["consoles"]
    await asyncio.wait_for(consoles.close_all(), 6)
    assert not consoles.handlers


class FakeWS:
    def __init__(self, stall=False):
        self.stall, self.sent, self.closed = stall, [], False

    async def send_str(self, text):
        if self.stall:
            await asyncio.sleep(60)
        self.sent.append(text)

    async def send_bytes(self, data):
        self.sent.append(data)

    async def close(self, **kw):
        self.closed = True


class FakeTransport:
    aborted = False

    def abort(self):
        self.aborted = True


async def test_a_stalled_viewer_is_cut_off(monkeypatch):
    monkeypatch.setattr(console, "SEND_TIMEOUT", 0.1)
    consoles = console.Consoles(tasks=None)
    room = consoles.room("g1")
    good, bad = FakeWS(), FakeWS(stall=True)
    room.viewers = [(good, "alice"), (bad, "bob")]
    room.typist = bad
    transport = FakeTransport()
    handler = asyncio.ensure_future(asyncio.sleep(60))
    consoles.handlers[bad] = (handler, "g1", transport)
    await consoles.broadcast("g1")
    assert good.sent and transport.aborted and room.typist is None and bad in consoles.dropped
    await asyncio.sleep(0)
    assert handler.cancelled()


async def test_an_expired_session_ends_an_idle_console():
    consoles = console.Consoles(tasks=None)
    ws = FakeWS()
    transport = FakeTransport()
    consoles.handlers[ws] = (None, "g1", transport)
    sessions = type("S", (), {"get_id": lambda self, t, touch=False: None})()
    await asyncio.wait_for(console._watch(ws, sessions, "tok", consoles, every=0.01), 2)
    assert transport.aborted


@pytest.mark.parametrize("stream,want", [
    (b"\xff\xfb\x01\xff\xfb\x03hello", b"hello"),       # the greeting is dropped
    (b"no greeting at all", b"no greeting at all"),      # anything else is kept
    (b"\xff\xfb", b""),                                 # a cut-off greeting
    (b"hi", b"hi"),                                     # a short stream that is not one
])
async def test_only_the_greeting_is_stripped(stream, want):
    reader = asyncio.StreamReader()
    reader.feed_data(stream)
    reader.feed_eof()
    ws = FakeWS()
    room = console.ConsoleRoom()
    await console._from_broker(reader, ws, room)
    assert b"".join(x for x in ws.sent if isinstance(x, bytes)) == want
    assert "the console closed" in ws.sent[-1] and ws.closed


async def test_a_guest_with_no_console_says_so(site, settings, tmp_path):
    c, _ = site
    (tmp_path / "s1").mkdir()
    with open(settings.conf_path("s1"), "w") as fh:
        fh.write(f"name = s1\nworkdir = {tmp_path}/s1\nconsole = none\ncommand = x\n"
                 f"disks = d.img\n")
    h = await session(c)
    detail = await (await c.get("/api/guests/s1", headers=h)).json()
    assert detail["console"] is None
    ws = await c.ws_connect("/api/guests/s1/console",
                            headers={"Cookie": h["Cookie"], "Origin": c.origin})
    s = await status(ws)
    assert s["running"] is False and "no serial console" in s["message"]
    await ws.close()
