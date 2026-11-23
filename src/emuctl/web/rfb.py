# SPDX-License-Identifier: GPL-2.0-only
"""The graphical console: WS /api/guests/{g}/display, a VNC proxy that logs in.

The daemon connects to the VNC server the guest's conf names, does the RFB
version and security handshake itself, and logs in with the password from
`display_password_file`. To the browser (noVNC) it then plays a VNC server
that needs no password, and from ClientInit on passes bytes through
unchanged both ways. The browser never sees the password. Proxmox works the
same way.

Speaks RFB 3.3, 3.7 and 3.8 to the guest's server, with security types None
(1) and VNC authentication (2). Offers 3.8 and None to the browser.
"""
import asyncio
import logging
import struct

from aiohttp import WSMsgType, web

from .des import vnc_response

log = logging.getLogger("emuctl.web")
HANDSHAKE_TIMEOUT = 10
MAX_REASON = 4096
NONE, VNC_AUTH = 1, 2


class RfbError(Exception):
    pass


def read_password(path):
    """The first line of `path`, or raise RfbError. Never logged or sent anywhere."""
    from . import data
    raw = data.tail_bytes(path, limit=1024) if path else None
    if raw is None:
        raise RfbError("the display password file cannot be read by the web daemon")
    return raw.decode("latin-1").split("\n", 1)[0].rstrip("\r")


async def _read_reason(reader):
    (n,) = struct.unpack(">I", await reader.readexactly(4))
    return (await reader.readexactly(min(n, MAX_REASON))).decode("latin-1", "replace")


async def login(reader, writer, auth, password=None):
    """Do the client side of the handshake with the guest's VNC server, up to
    and including the security result. Returns the server's minor version."""
    hello = await reader.readexactly(12)
    if not (hello.startswith(b"RFB ") and hello.endswith(b"\n")):
        raise RfbError("the display does not speak VNC")
    try:
        major, minor = int(hello[4:7]), int(hello[8:11])
    except ValueError:
        raise RfbError("the display sent a bad VNC version")
    if major != 3 or minor < 3:
        raise RfbError(f"unsupported VNC version {major}.{minor}")
    minor = 8 if minor >= 8 else 7 if minor >= 7 else 3
    writer.write(b"RFB 003.%03d\n" % minor)
    await writer.drain()
    want = VNC_AUTH if auth == "vnc" else NONE

    if minor == 3:
        (kind,) = struct.unpack(">I", await reader.readexactly(4))
        if kind == 0:
            raise RfbError(f"the display refused the connection: {await _read_reason(reader)}")
    else:
        (count,) = struct.unpack(">B", await reader.readexactly(1))
        if count == 0:
            raise RfbError(f"the display refused the connection: {await _read_reason(reader)}")
        offered = await reader.readexactly(count)
        if want not in offered:
            what = "VNC password" if want == VNC_AUTH else "no-password"
            raise RfbError(f"the display does not offer {what} login (it offers types "
                           f"{list(offered)}); check display_auth")
        kind = want
        writer.write(bytes([kind]))
        await writer.drain()
    if kind != want:
        raise RfbError(f"the display wants security type {kind}, the conf says {auth}")

    if kind == VNC_AUTH:
        if password is None:
            raise RfbError("the display needs a password and none is configured")
        challenge = await reader.readexactly(16)
        writer.write(vnc_response(password, challenge))
        await writer.drain()
    if kind == VNC_AUTH or minor == 8:
        (result,) = struct.unpack(">I", await reader.readexactly(4))
        if result != 0:
            reason = await _read_reason(reader) if minor == 8 else ""
            raise RfbError("the display refused the password" + (f": {reason}" if reason else ""))
    return minor


async def greet_browser(ws, receive):
    """The server side of the handshake with noVNC: version 3.8, no password."""
    await ws.send_bytes(b"RFB 003.008\n")
    hello = await receive(12)
    if not hello.startswith(b"RFB 003."):
        raise RfbError("the browser does not speak VNC")
    await ws.send_bytes(bytes([1, NONE]))
    choice = await receive(1)
    if choice != bytes([NONE]):
        raise RfbError("the browser chose a security type that was not offered")
    await ws.send_bytes(struct.pack(">I", 0))


class FrameReader:
    """Reads exact byte counts out of a WebSocket's binary frames."""

    def __init__(self, ws):
        self.ws, self.buf = ws, bytearray()

    async def __call__(self, n):
        while len(self.buf) < n:
            msg = await self.ws.receive()
            if msg.type != WSMsgType.BINARY:
                raise RfbError("the browser went away during the handshake")
            self.buf += msg.data
        out, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return out


async def serve(request, cfg, user, tasks):
    """The WebSocket handler's body. `request` has passed every middleware."""
    guest = request["guest"]
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20, protocols=("binary",))
    await ws.prepare(request)
    writer = None
    try:
        if not cfg.display:
            raise RfbError(f"{guest} has no display")
        password = (await asyncio.to_thread(read_password, cfg.display_password_file)
                    if cfg.display_auth == "vnc" else None)
        host, port = cfg.display
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port),
                                                    HANDSHAKE_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            raise RfbError(f"nothing answers on {guest}'s display; is it running?")
        try:
            await asyncio.wait_for(login(reader, writer, cfg.display_auth, password),
                                   HANDSHAKE_TIMEOUT)
            await asyncio.wait_for(greet_browser(ws, FrameReader(ws)), HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            raise RfbError("the display handshake timed out")
        except asyncio.IncompleteReadError:
            raise RfbError("the display closed the connection during login")
        tasks.note(user, guest, "display", "opened the display")
        await _pipe(ws, reader, writer)
    except RfbError as exc:
        log.info("display of %s for %s: %s", guest, user, exc)
        if not ws.closed:
            await ws.close(code=4000, message=str(exc)[:120].encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        if writer is not None:
            writer.close()
        if not ws.closed:
            await ws.close()
    return ws


async def _pipe(ws, reader, writer):
    async def down():
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            await ws.send_bytes(chunk)
        await ws.close()

    pump = asyncio.create_task(down())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        pump.cancel()
