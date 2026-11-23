# SPDX-License-Identifier: GPL-2.0-only
"""A small picture of a guest's screen: GET /api/guests/{g}/thumbnail.

The daemon logs in to the guest's VNC server as the Display tab does (shared,
so nobody else is thrown off), asks for one full frame in raw encoding,
scales it down and returns it as a PNG. Standard library only: zlib for the
PNG, no image library. One picture per guest per minute at most, whoever asks.
"""
import asyncio
import struct
import time
import zlib

from .rfb import HANDSHAKE_TIMEOUT, RfbError, login, read_password

WIDTH = 320                 # pixels across the thumbnail
MAX_PIXELS = 4096 * 4096    # a bigger screen is refused rather than read
EVERY = 60                  # seconds a picture is reused for
RETRY = 10                  # seconds a failure is reused for
FRAME_TIMEOUT = 15
# 32 bits a pixel, little-endian, true colour, blue in the low byte:
# each pixel arrives as B, G, R, unused.
PIXEL_FORMAT = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)


async def grab(reader, writer):
    """(width, height, BGRX bytes) of one full frame, after login()."""
    writer.write(b"\x01")                                   # ClientInit, shared
    await writer.drain()
    w, h = struct.unpack(">HH", await reader.readexactly(4))
    await reader.readexactly(16)                            # the server's pixel format
    (n,) = struct.unpack(">I", await reader.readexactly(4))
    await reader.readexactly(min(n, 1 << 16))               # its desktop name
    if not 0 < w * h <= MAX_PIXELS:
        raise RfbError(f"the screen is {w} x {h}; too big for a thumbnail")
    writer.write(b"\x00\x00\x00\x00" + PIXEL_FORMAT          # SetPixelFormat
                 + struct.pack(">BxHi", 2, 1, 0)             # SetEncodings: raw only
                 + struct.pack(">BBHHHH", 3, 0, 0, 0, w, h))  # a full update, please
    await writer.drain()
    fb = bytearray(w * h * 4)
    while True:
        kind = (await reader.readexactly(1))[0]
        if kind == 0:                                       # FramebufferUpdate
            _, rects = struct.unpack(">BH", await reader.readexactly(3))
            for _ in range(rects):
                x, y, rw, rh, enc = struct.unpack(">HHHHi", await reader.readexactly(12))
                if enc != 0:
                    raise RfbError(f"the display sent encoding {enc}, not raw")
                if x + rw > w or y + rh > h:
                    raise RfbError("the display sent a rectangle off the screen")
                body = await reader.readexactly(rw * rh * 4)
                for row in range(rh):
                    at = ((y + row) * w + x) * 4
                    fb[at:at + rw * 4] = body[row * rw * 4:(row + 1) * rw * 4]
            return w, h, bytes(fb)
        if kind == 1:                                       # SetColourMapEntries
            _, _, count = struct.unpack(">BHH", await reader.readexactly(5))
            await reader.readexactly(count * 6)
        elif kind == 2:                                     # Bell
            continue
        elif kind == 3:                                     # ServerCutText
            _, n = struct.unpack(">3sI", await reader.readexactly(7))
            await reader.readexactly(min(n, 1 << 20))
        else:
            raise RfbError(f"the display sent message type {kind}")


def shrink(w, h, fb, width=WIDTH):
    """(width, height, RGB bytes): the frame scaled down, each pixel the mean
    of four samples so that text thins rather than vanishes."""
    if w <= width:
        width = w
    scale = w / width
    height = max(1, round(h / scale))
    sx = [(min(w - 1, int((x + 0.25) * scale)), min(w - 1, int((x + 0.75) * scale)))
          for x in range(width)]
    out = bytearray()
    for y in range(height):
        r0 = min(h - 1, int((y + 0.25) * scale)) * w
        r1 = min(h - 1, int((y + 0.75) * scale)) * w
        out.append(0)                                       # PNG filter: none
        for a, b in sx:
            p, q, r, s = (r0 + a) * 4, (r0 + b) * 4, (r1 + a) * 4, (r1 + b) * 4
            out += bytes(((fb[p + 2] + fb[q + 2] + fb[r + 2] + fb[s + 2]) >> 2,
                          (fb[p + 1] + fb[q + 1] + fb[r + 1] + fb[s + 1]) >> 2,
                          (fb[p] + fb[q] + fb[r] + fb[s]) >> 2))
    return width, height, bytes(out)


def png(width, height, rows):
    """A PNG from filtered RGB rows (each starting with its filter byte)."""
    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


async def snapshot(cfg):
    """PNG bytes of the guest's screen now. Raises RfbError."""
    password = (await asyncio.to_thread(read_password, cfg.display_password_file)
                if cfg.display_auth == "vnc" else None)
    host, port = cfg.display
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port),
                                                HANDSHAKE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        raise RfbError("nothing answers on the display")
    try:
        await asyncio.wait_for(login(reader, writer, cfg.display_auth, password),
                               HANDSHAKE_TIMEOUT)
        w, h, fb = await asyncio.wait_for(grab(reader, writer), FRAME_TIMEOUT)
    except asyncio.TimeoutError:
        raise RfbError("the display did not send a picture in time")
    except asyncio.IncompleteReadError:
        raise RfbError("the display closed the connection")
    except OSError as exc:
        raise RfbError(f"the display connection failed: {exc.strerror or exc}")
    finally:
        writer.close()
    return await asyncio.to_thread(lambda: png(*shrink(w, h, fb)))


class Thumbnails:
    """One picture per guest, kept EVERY seconds, taken once however many ask."""

    def __init__(self, take=snapshot, clock=time.monotonic):
        self.take, self.clock = take, clock
        self._kept = {}                 # name -> (time, png or RfbError)
        self._locks = {}

    async def get(self, cfg):
        lock = self._locks.setdefault(cfg.name, asyncio.Lock())
        async with lock:
            hit = self._kept.get(cfg.name)
            keep = RETRY if hit and isinstance(hit[1], RfbError) else EVERY
            if hit and self.clock() - hit[0] < keep:
                result = hit[1]
            else:
                try:
                    result = await self.take(cfg)
                except RfbError as exc:
                    result = exc
                self._kept[cfg.name] = (self.clock(), result)
        if isinstance(result, RfbError):
            raise result
        return result

    def forget(self, name):
        self._kept.pop(name, None)
