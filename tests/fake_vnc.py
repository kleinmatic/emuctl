#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""A tiny VNC server for tests and tools/webdemo: a 160 x 100 screen in one
colour, RFB 3.8 with VNC password authentication, raw encoding only.

    fake_vnc.py PORT PASSWORD_FILE [RRGGBB]
"""
import asyncio
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from emuctl.web.des import vnc_response  # noqa: E402

WIDTH, HEIGHT = 160, 100
# 32 bits per pixel, depth 24, little-endian, true colour, red in the low byte: the format
# noVNC asks for with SetPixelFormat, which this server takes as given rather than parsing.
PIXEL_FORMAT = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 0, 8, 16)


def serve(port, password, rgb):
    pixel = bytes([(rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF, 0])
    frame = pixel * (WIDTH * HEIGHT)

    async def client(r, w):
        try:
            w.write(b"RFB 003.008\n")
            await r.readexactly(12)
            w.write(bytes([1, 2]))
            await r.readexactly(1)
            challenge = os.urandom(16)
            w.write(challenge)
            ok = await r.readexactly(16) == vnc_response(password, challenge)
            w.write(struct.pack(">I", 0 if ok else 1))
            if not ok:
                w.write(struct.pack(">I", 14) + b"wrong password")
                await w.drain()
                return
            await r.readexactly(1)
            name = b"fake screen"
            w.write(struct.pack(">HH", WIDTH, HEIGHT) + PIXEL_FORMAT
                    + struct.pack(">I", len(name)) + name)
            await w.drain()
            sizes = {0: 19, 2: None, 3: 9, 4: 7, 5: 5, 6: None}
            while True:
                kind = (await r.readexactly(1))[0]
                if kind == 2:                                   # SetEncodings
                    _, n = struct.unpack(">BH", await r.readexactly(3))
                    await r.readexactly(4 * n)
                elif kind == 6:                                 # ClientCutText
                    _, n = struct.unpack(">3sI", await r.readexactly(7))
                    await r.readexactly(n)
                elif kind in sizes:
                    body = await r.readexactly(sizes[kind])
                    if kind == 3 and body[0] == 0:              # a full update request
                        w.write(struct.pack(">BxH", 0, 1)
                                + struct.pack(">HHHHi", 0, 0, WIDTH, HEIGHT, 0) + frame)
                        await w.drain()
                else:
                    return
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            w.close()

    async def main():
        srv = await asyncio.start_server(client, "127.0.0.1", port)
        async with srv:
            await srv.serve_forever()
    asyncio.run(main())


if __name__ == "__main__":
    port, pwfile = int(sys.argv[1]), sys.argv[2]
    colour = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x2A6FBF
    serve(port, open(pwfile).read().split("\n", 1)[0], colour)
