# SPDX-License-Identifier: GPL-2.0-only
"""web/des.py and web/rfb.py: the VNC proxy that logs in for the browser."""
import asyncio
import os
import struct
import sys

import pytest

from emuctl.web import des

# --- DES: published vectors ------------------------------------------------------


@pytest.mark.parametrize("key,plain,cipher", [
    ("133457799BBCDFF1", "0123456789ABCDEF", "85E813540F0AB405"),
    ("0000000000000000", "0000000000000000", "8CA64DE9C1B123A7"),
    ("FFFFFFFFFFFFFFFF", "FFFFFFFFFFFFFFFF", "7359B2163E4EDC58"),
    ("3000000000000000", "1000000000000001", "958E6E627A05557B"),
    ("0123456789ABCDEF", "4E6F772069732074", "3FA40E8A984D4815"),
])
def test_des_vectors(key, plain, cipher):
    assert des.encrypt_block(bytes.fromhex(key), bytes.fromhex(plain)).hex().upper() == cipher


def test_vnc_response_matches_openssl():
    # Computed with OpenSSL's DES, not this code: password, challenge, response.
    assert des.vnc_response("s3cret", bytes(range(16))).hex() == \
        "fc9a2bb8546a63388eb45b530d3a6337"
    assert des.vnc_response("12345678ignored", bytes(16)) == des.vnc_response("12345678", bytes(16))


if sys.version_info < (3, 11):
    pytest.skip("the web interface needs Python 3.11", allow_module_level=True)
pytest.importorskip("aiohttp")
pytest.importorskip("pytest_aiohttp")

from aiohttp import WSMsgType  # noqa: E402
from test_cli import add_guest  # noqa: E402
from test_web_app import PASSWORD, session  # noqa: E402

from emuctl.web import app as webapp  # noqa: E402
from emuctl.web import auth, rfb  # noqa: E402
from emuctl.web.conf import WebConf  # noqa: E402
from emuctl.web.sampler import Sampler  # noqa: E402

VNC_PASSWORD = "s3cret"
INIT = struct.pack(">HH16xI", 640, 480, 4) + b"test"


class FakeVnc:
    """A VNC server: version `minor`, security `kind`, then a ServerInit."""

    def __init__(self, minor=8, kind=2, password=VNC_PASSWORD, reason=b"bad"):
        self.minor, self.kind, self.password, self.reason = minor, kind, password, reason
        self.after_init = bytearray()
        self.shared = None
        self.writers = []
        self.challenge = os.urandom(16)

    async def handle(self, r, w):
        try:
            w.write(b"RFB 003.%03d\n" % self.minor)
            await r.readexactly(12)
            if self.minor == 3:
                w.write(struct.pack(">I", self.kind))
            else:
                w.write(bytes([1, self.kind]))
                await r.readexactly(1)
            if self.kind == 2:
                w.write(self.challenge)
                answer = await r.readexactly(16)
                ok = answer == des.vnc_response(self.password, self.challenge)
                w.write(struct.pack(">I", 0 if ok else 1))
                if not ok:
                    if self.minor == 8:
                        w.write(struct.pack(">I", len(self.reason)) + self.reason)
                    await w.drain()
                    w.close()
                    return
            elif self.minor == 8:
                w.write(struct.pack(">I", 0))
            self.shared = (await r.readexactly(1))[0]     # ClientInit
            self.writers.append(w)
            w.write(INIT)
            await w.drain()
            while True:
                d = await r.read(4096)
                if not d:
                    break
                self.after_init += d
                w.write(b"echo:" + d)
                await w.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass


async def start(server):
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


@pytest.mark.parametrize("minor,kind,auth", [(8, 2, "vnc"), (7, 2, "vnc"), (3, 2, "vnc"),
                                              (8, 1, "none"), (3, 1, "none")])
async def test_login(minor, kind, auth):
    srv, port = await start(FakeVnc(minor, kind))
    r, w = await asyncio.open_connection("127.0.0.1", port)
    assert await rfb.login(r, w, auth, VNC_PASSWORD) == minor
    w.write(b"\x01")
    await w.drain()
    assert await r.readexactly(len(INIT)) == INIT
    w.close()
    srv.close()


@pytest.mark.parametrize("minor,kind,auth,password,match", [
    (8, 2, "vnc", "wrong", "refused the password: bad"),
    (3, 2, "vnc", "wrong", "refused the password"),
    (8, 1, "vnc", VNC_PASSWORD, "does not offer VNC password"),
    (8, 2, "none", None, "does not offer no-password"),
    (3, 1, "vnc", VNC_PASSWORD, "wants security type 1"),
    (8, 2, "vnc", None, "none is configured"),
])
async def test_login_failures(minor, kind, auth, password, match):
    srv, port = await start(FakeVnc(minor, kind))
    r, w = await asyncio.open_connection("127.0.0.1", port)
    with pytest.raises(rfb.RfbError, match=match):
        await rfb.login(r, w, auth, password)
    w.close()
    srv.close()


async def test_not_vnc_at_all():
    async def http(r, w):
        w.write(b"HTTP/1.1 400\r\n")
        await w.drain()
    srv = await asyncio.start_server(http, "127.0.0.1", 0)
    r, w = await asyncio.open_connection("127.0.0.1", srv.sockets[0].getsockname()[1])
    with pytest.raises(rfb.RfbError, match="does not speak VNC"):
        await rfb.login(r, w, "none")
    srv.close()


def test_password_file(tmp_path, monkeypatch):
    from emuctl.web import data
    p = tmp_path / "vnc.pass"
    p.write_text("hunter2\nignored\n")
    assert rfb.read_password(str(p)) == "hunter2"
    with pytest.raises(rfb.RfbError, match="cannot be read"):
        rfb.read_password(str(tmp_path / "missing"))
    monkeypatch.setattr(data, "PROTECTED", [str(tmp_path)])      # e.g. /etc/emuctl/env
    with pytest.raises(rfb.RfbError, match="cannot be read"):
        rfb.read_password(str(p))


# --- the WebSocket, as noVNC sees it ------------------------------------------------

@pytest.fixture
async def site(aiohttp_client, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "LOG2_N", 10)
    vnc = FakeVnc()
    srv, port = await start(vnc)
    (tmp_path / "g1").mkdir()
    (tmp_path / "g1" / "vnc.pass").write_text(VNC_PASSWORD + "\n")
    add_guest(settings, tmp_path, "g1", extra=f"display = vnc://127.0.0.1:{port}\n"
                                               f"display_password_file = vnc.pass\n")
    add_guest(settings, tmp_path, "g2", serial=21010, broker=2310)
    users = str(tmp_path / "users")
    auth.set_password(users, "alice", PASSWORD)
    conf = WebConf(tls=False, users_file=users, task_log=str(tmp_path / "tasks.log"))
    app = webapp.make_app(settings, conf, sampler=Sampler(settings, probe=lambda *a, **k: False),
                          background=False)
    c = await aiohttp_client(app)
    c.origin = f"http://{c.host}:{c.port}"
    yield c, vnc
    srv.close()


async def open_display(c, h, guest="g1"):
    return await c.ws_connect(f"/api/guests/{guest}/display", protocols=("binary",),
                              headers={"Cookie": h["Cookie"], "Origin": c.origin})


async def take(ws, n):
    buf = b""
    while len(buf) < n:
        msg = await asyncio.wait_for(ws.receive(), 5)
        assert msg.type == WSMsgType.BINARY, msg
        buf += msg.data
    return buf


async def test_browser_gets_a_passwordless_vnc_and_then_the_guest(site):
    c, vnc = site
    ws = await open_display(c, await session(c))
    assert await take(ws, 12) == b"RFB 003.008\n"
    await ws.send_bytes(b"RFB 003.008\n")
    assert await take(ws, 2) == bytes([1, 1])          # one type offered: None
    await ws.send_bytes(b"\x01")
    assert await take(ws, 4) == b"\0\0\0\0"
    await ws.send_bytes(b"\x00")                       # ClientInit, not shared...
    assert await take(ws, len(INIT)) == INIT
    assert vnc.shared == 1                             # ...but the guest is asked to share
    await ws.send_bytes(b"keys")
    assert await take(ws, 9) == b"echo:keys"
    await ws.close()
    notes = [t for t in c.app[webapp.TASKS].list() if t["action"] == "display"]
    assert notes and notes[0]["user"] == "alice"


async def test_failures_close_with_a_reason(site, tmp_path):
    c, _ = site
    h = await session(c)
    ws = await open_display(c, h, "g2")                # no display configured
    msg = await asyncio.wait_for(ws.receive(), 5)
    assert msg.type == WSMsgType.CLOSE and ws.close_code == 4000
    (tmp_path / "g1" / "vnc.pass").write_text("wrong\n")
    ws = await open_display(c, h)
    msg = await asyncio.wait_for(ws.receive(), 5)
    assert msg.type == WSMsgType.CLOSE and "refused the password" in (msg.extra or "")


async def test_display_needs_an_operator_session_and_our_origin(site):
    from aiohttp import WSServerHandshakeError
    c, _ = site
    h = await session(c)
    for headers, code in (({"Origin": c.origin}, 401),
                          ({"Cookie": h["Cookie"], "Origin": "https://evil.example"}, 403)):
        with pytest.raises(WSServerHandshakeError) as e:
            await c.ws_connect("/api/guests/g1/display", headers=headers)
        assert e.value.status == code


async def handshake(ws):
    await take(ws, 12)
    await ws.send_bytes(b"RFB 003.008\n")
    await take(ws, 2)
    await ws.send_bytes(b"\x01")
    await take(ws, 4)
    await ws.send_bytes(b"\x01")
    await take(ws, len(INIT))


async def test_a_reset_from_the_guest_closes_the_page(site):
    c, vnc = site
    ws = await open_display(c, await session(c))
    await handshake(ws)
    for w in vnc.writers:
        w.transport.abort()
    msg = await asyncio.wait_for(ws.receive(), 5)
    assert msg.type == WSMsgType.CLOSE and "the display closed" in (msg.extra or "")


async def test_a_hostile_refusal_is_cleaned_up(site, tmp_path, caplog):
    c, vnc = site
    vnc.reason = b"\xff\xfe\x1b[31mFAKE\nINFO root logged in" * 20
    (tmp_path / "g1" / "vnc.pass").write_text("wrong\n")
    ws = await open_display(c, await session(c))
    msg = await asyncio.wait_for(ws.receive(), 5)
    assert msg.type == WSMsgType.CLOSE and ws.close_code == 4000
    assert "\x1b" not in caplog.text and "\nINFO root" not in caplog.text


async def test_a_stopped_guest_is_not_connected_to(site):
    c, vnc = site
    c.app[webapp.SAMPLER].rows = [{"name": "g1", "state": "down"}]
    ws = await open_display(c, await session(c))
    msg = await asyncio.wait_for(ws.receive(), 5)
    assert msg.type == WSMsgType.CLOSE and "not running" in (msg.extra or "")
    assert vnc.shared is None


async def test_an_expired_session_ends_the_display(site, monkeypatch):
    c, _ = site
    monkeypatch.setattr(rfb, "SESSION_CHECK", 0.05)
    ws = await open_display(c, await session(c))
    await handshake(ws)
    for s in c.app[webapp.SESSIONS]._by_token.values():
        s.created -= auth.MAX_AGE + 1
    msg = await asyncio.wait_for(ws.receive(), 5)
    assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED)        # cut off either way


async def test_logout_cuts_the_display(site):
    c, _ = site
    h = await session(c)
    ws = await open_display(c, h)
    await handshake(ws)
    await c.post("/api/logout", headers=h)
    msg = await asyncio.wait_for(ws.receive(), 5)
    assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR)
