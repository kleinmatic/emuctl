# SPDX-License-Identifier: GPL-2.0-only
"""web/thumb.py: one frame from a VNC server, made into a small PNG."""
import asyncio
import struct
import sys
import types
import zlib

import pytest

if sys.version_info < (3, 11):
    pytest.skip("the web interface needs Python 3.11", allow_module_level=True)
pytest.importorskip("aiohttp")
pytest.importorskip("pytest_aiohttp")

from test_web_app import client, session, site  # noqa: E402,F401

from emuctl.web import app as webapp  # noqa: E402
from emuctl.web import thumb  # noqa: E402
from emuctl.web.rfb import RfbError  # noqa: E402


def read_png(body):
    """(width, height, RGB rows without filter bytes) of a PNG thumb.png() wrote."""
    assert body.startswith(b"\x89PNG\r\n\x1a\n")
    w, h = struct.unpack(">II", body[16:24])
    at = body.index(b"IDAT")
    n = struct.unpack(">I", body[at - 4:at])[0]
    raw = zlib.decompress(body[at + 4:at + 4 + n])
    rows = [raw[i * (w * 3 + 1) + 1:(i + 1) * (w * 3 + 1)] for i in range(h)]
    return w, h, rows


async def serve_frame(w, h, bgrx, chatter=b"", name=b"test"):
    """A VNC server (3.8, no password) that answers one update request with
    `chatter` and then the whole screen in one raw rectangle."""
    async def client_(r, wr):
        wr.write(b"RFB 003.008\n")
        await r.readexactly(12)
        wr.write(bytes([1, 1]))
        await r.readexactly(1)
        wr.write(b"\0\0\0\0")
        await r.readexactly(1)                                   # ClientInit
        wr.write(struct.pack(">HH", w, h) + bytes(16) + struct.pack(">I", len(name)) + name)
        await r.readexactly(20 + 8 + 10)                         # format, encodings, request
        wr.write(chatter + struct.pack(">BxH", 0, 1)
                 + struct.pack(">HHHHi", 0, 0, w, h, 0) + bgrx * (w * h))
        await wr.drain()
        await r.read()
        wr.close()
    srv = await asyncio.start_server(client_, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


def cfg_for(port):
    return types.SimpleNamespace(name="g1", display=("127.0.0.1", port), display_auth="none",
                                 display_password_file=None)


async def test_a_frame_becomes_a_png():
    bell, cut = b"\x02", struct.pack(">B3sI", 3, b"", 2) + b"hi"
    colour_map = struct.pack(">BBHH", 1, 0, 0, 1) + bytes(6)
    srv, port = await serve_frame(640, 400, bytes([0x10, 0x80, 0xF0, 0]),
                                  chatter=bell + cut + colour_map)
    async with srv:
        w, h, rows = read_png(await thumb.snapshot(cfg_for(port)))
    assert (w, h) == (thumb.WIDTH, 200)
    assert rows[100][:3] == bytes([0xF0, 0x80, 0x10])           # red, green, blue


async def test_a_small_screen_is_not_scaled_up():
    srv, port = await serve_frame(80, 50, bytes([0, 0, 0xFF, 0]))
    async with srv:
        w, h, rows = read_png(await thumb.snapshot(cfg_for(port)))
    assert (w, h) == (80, 50) and rows[0][:3] == b"\xff\x00\x00"


async def test_long_text_is_read_past_and_too_much_refused():
    srv, port = await serve_frame(80, 50, bytes([0, 0, 0xFF, 0]), name=b"A" * 70000)
    async with srv:
        w, h, rows = read_png(await thumb.snapshot(cfg_for(port)))
    assert rows[0][:3] == b"\xff\x00\x00"                    # the stream stayed in step
    srv, port = await serve_frame(80, 50, bytes(4), name=b"A" * (thumb.MAX_SKIP + 1))
    async with srv:
        with pytest.raises(RfbError, match="too much"):
            await thumb.snapshot(cfg_for(port))


async def test_empty_or_endless_rectangles_are_refused():
    async def server(rects):
        async def client_(r, wr):
            for say, hear in ((b"RFB 003.008\n", 12), (bytes([1, 1]), 1), (bytes(4), 1)):
                wr.write(say)
                await r.readexactly(hear)
            wr.write(struct.pack(">HH", 4, 100) + bytes(16) + struct.pack(">I", 0))
            await r.readexactly(38)
            wr.write(struct.pack(">BxH", 0, len(rects)) + b"".join(
                struct.pack(">HHHHi", *rc) + bytes(rc[2] * rc[3] * 4) for rc in rects))
            await wr.drain()
            await r.read()
            wr.close()
        srv = await asyncio.start_server(client_, "127.0.0.1", 0)
        return srv, srv.sockets[0].getsockname()[1]
    for rects, match in (([(0, 0, 0, 100, 0)] * 3, "empty"),
                         ([(0, 0, 4, 100, 0)] * 3, "more picture")):
        srv, port = await server(rects)
        async with srv:
            with pytest.raises(RfbError, match=match):
                await thumb.snapshot(cfg_for(port))


async def test_nothing_listening():
    srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    srv.close()
    await srv.wait_closed()
    with pytest.raises(RfbError, match="nothing answers"):
        await thumb.snapshot(cfg_for(port))


async def test_one_picture_a_minute_whoever_asks():
    taken = []
    clock = types.SimpleNamespace(t=0)

    async def take(cfg):
        taken.append(cfg.name)
        if len(taken) == 2:
            raise RfbError("display went away")
        return b"png"
    t = thumb.Thumbnails(take=take, clock=lambda: clock.t)
    cfg = types.SimpleNamespace(name="g1")
    assert await asyncio.gather(t.get(cfg), t.get(cfg)) == [b"png", b"png"]
    assert taken == ["g1"]
    clock.t = thumb.EVERY
    with pytest.raises(RfbError):
        await t.get(cfg)
    clock.t += thumb.RETRY                                      # a failure is retried sooner
    assert await t.get(cfg) == b"png" and len(taken) == 3


async def test_route(client, settings, monkeypatch):  # noqa: F811
    with open(settings.conf_path("g1"), "a") as fh:
        fh.write("display = vnc://127.0.0.1:5999\n")

    async def take(cfg):
        return b"\x89PNG fake"
    client.app[webapp.STATE]["thumbs"].take = take
    h = await session(client)
    assert (await client.get("/api/guests/g2/thumbnail", headers=h)).status == 404
    r = await client.get("/api/guests/g1/thumbnail", headers=h)
    assert r.status == 409                                      # not running: never connected to
    client.app[webapp.SAMPLER].rows = [{"name": "g1", "state": "ready"}]
    r = await client.get("/api/guests/g1/thumbnail", headers=h)
    assert r.status == 200 and r.content_type == "image/png"

