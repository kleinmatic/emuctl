# SPDX-License-Identifier: GPL-2.0-only
"""The graphical console: WS /api/guests/{g}/display, a VNC proxy that logs in.

The daemon connects to the VNC server the guest's conf names, does the RFB
version and security handshake itself, and logs in with the password from
`display_password_file`. To the browser (noVNC) it then plays a VNC server
that needs no password, and from ClientInit on passes bytes through
unchanged both ways. The browser never sees the password. Proxmox works the
same way.

Speaks RFB 3.3, 3.7 and 3.8 to the guest's server, with security types None
(1), VNC authentication (2), and VeNCrypt (19) subtype X509Vnc (261): TLS,
with the server's certificate checked against `display_ca`, then VNC
authentication inside it (TigerVNC's Xvnc with -SecurityTypes X509Vnc).
Offers 3.8 and None to the browser.
"""
import asyncio
import logging
import ssl
import struct

from aiohttp import WSMsgType, web

from .des import vnc_response

log = logging.getLogger("emuctl.web")
HANDSHAKE_TIMEOUT = 10
MAX_REASON = 4096
NONE, VNC_AUTH, VENCRYPT = 1, 2, 19
X509VNC = 261                   # VeNCrypt subtype: X.509-verified TLS, then VNC auth


class RfbError(Exception):
    pass


def read_password(path):
    """The first line of `path`, or raise RfbError. Never logged or sent anywhere."""
    from . import data
    raw = data.tail_bytes(path, limit=1024) if path else None
    if raw is None:
        raise RfbError("the display password file cannot be read by the web daemon")
    if len(raw) == 1024:
        raise RfbError("the display password file is too long to be a VNC password file")
    return raw.decode("latin-1").split("\n", 1)[0].rstrip("\r")


async def _read_reason(reader):
    """A server's refusal text: printable ASCII only, so it cannot forge log
    lines, and short enough for a WebSocket close frame."""
    (n,) = struct.unpack(">I", await reader.readexactly(4))
    raw = await reader.readexactly(min(n, MAX_REASON))
    return "".join(chr(b) if 32 <= b < 127 else "?" for b in raw[:100])


def tls_context(ca):
    """TLS that trusts only `ca`: the VNC server's own certificate (Xvnc's are
    usually self-signed) or the CA that signed it. The certificate is pinned
    by that file, so the host name in it is not checked: the daemon reaches
    the server as 127.0.0.1, which a certificate rarely names."""
    if not ca:
        raise RfbError("display_auth = x509vnc needs display_ca")
    try:
        ctx = ssl.create_default_context(cafile=ca)
    except (OSError, ssl.SSLError) as exc:
        raise RfbError(f"cannot read display_ca {ca}: {exc}")
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


async def vencrypt(reader, writer, ca):
    """VeNCrypt, after security type 19 was chosen: agree version 0.2, pick
    X509Vnc, and switch the connection to TLS in place."""
    major, minor = await reader.readexactly(2)
    if (major, minor) < (0, 2):
        raise RfbError(f"the display speaks VeNCrypt {major}.{minor}; 0.2 is needed")
    writer.write(b"\x00\x02")
    await writer.drain()
    if (await reader.readexactly(1))[0] != 0:
        raise RfbError("the display refused VeNCrypt 0.2")
    (n,) = await reader.readexactly(1)
    subtypes = struct.unpack(f">{n}I", await reader.readexactly(4 * n))
    if X509VNC not in subtypes:
        raise RfbError(f"the display does not offer X509Vnc (it offers VeNCrypt subtypes "
                       f"{list(subtypes)}); check display_auth")
    writer.write(struct.pack(">I", X509VNC))
    await writer.drain()
    if (await reader.readexactly(1))[0] != 1:
        raise RfbError("the display refused X509Vnc")
    ctx = tls_context(ca)
    try:
        await writer.start_tls(ctx)
    except ssl.SSLCertVerificationError as exc:
        raise RfbError(f"the display's certificate is not the one display_ca trusts: "
                       f"{exc.verify_message}")
    except ssl.SSLError as exc:
        raise RfbError(f"TLS with the display failed: {exc.reason or exc}")


async def login(reader, writer, auth, password=None, ca=None):
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
    want = {"vnc": VNC_AUTH, "x509vnc": VENCRYPT}.get(auth, NONE)
    if want == VENCRYPT and minor < 7:
        raise RfbError("the display speaks RFB 3.3, which has no VeNCrypt")

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
            what = {VNC_AUTH: "VNC password", VENCRYPT: "VeNCrypt"}.get(want, "no-password")
            raise RfbError(f"the display does not offer {what} login (it offers types "
                           f"{list(offered)}); check display_auth")
        kind = want
        writer.write(bytes([kind]))
        await writer.drain()
    if kind != want:
        raise RfbError(f"the display wants security type {kind}, the conf says {auth}")

    if kind == VENCRYPT:
        await vencrypt(reader, writer, ca)
        kind = VNC_AUTH                         # X509Vnc: VNC authentication, inside TLS

    if kind == VNC_AUTH:
        if password is None:
            raise RfbError("the display needs a password and none is configured")
        challenge = await reader.readexactly(16)
        writer.write(vnc_response(password, challenge))
        await writer.drain()
    if kind == VNC_AUTH or minor == 8:          # always after VeNCrypt, too
        (result,) = struct.unpack(">I", await reader.readexactly(4))
        if result != 0:
            reason = await _read_reason(reader) if minor == 8 else ""
            raise RfbError("the display refused the password" + (f": {reason}" if reason else ""))
    return minor


async def greet_browser(ws, receive):
    """The server side of the handshake with the browser: no password. Speaks
    whichever of 3.3, 3.7 and 3.8 the client answers with (noVNC says 3.8)."""
    await ws.send_bytes(b"RFB 003.008\n")
    hello = await receive(12)
    try:
        minor = int(hello[8:11]) if hello.startswith(b"RFB 003.") else -1
    except ValueError:
        minor = -1
    if minor < 3:
        raise RfbError("the browser does not speak VNC")
    if minor < 7:
        await ws.send_bytes(struct.pack(">I", NONE))       # 3.3: the server picks
        return
    await ws.send_bytes(bytes([1, NONE]))
    choice = await receive(1)
    if choice != bytes([NONE]):
        raise RfbError("the browser chose a security type that was not offered")
    if minor >= 8:
        await ws.send_bytes(struct.pack(">I", 0))          # 3.8 confirms even for None


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


USER_INPUT = (4, 5, 6, 255)  # KeyEvent, PointerEvent, ClientCutText, QEMU extended key
SESSION_CHECK = 30           # seconds between checks that the session is still alive
CLOSE_TIMEOUT = 2


def close_reason(text):
    return text.encode("ascii", "replace")[:120]


async def serve(request, cfg, user, tasks, sessions, token, running=True):
    """The WebSocket handler's body. `request` has passed every middleware."""
    guest = request["guest"]
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20, protocols=("binary",),
                               compress=False)
    await ws.prepare(request)
    writer = None
    try:
        if not cfg.display:
            raise RfbError(f"{guest} has no display")
        if not running:
            # Not even a connection attempt: with the guest down, anyone could
            # be listening on its display port, and would get the login.
            raise RfbError(f"{guest} is not running")
        password = (await asyncio.to_thread(read_password, cfg.display_password_file)
                    if cfg.display_auth in ("vnc", "x509vnc") else None)
        host, port = cfg.display
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port),
                                                    HANDSHAKE_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            raise RfbError(f"nothing answers on {guest}'s display; is it running?")
        frames = FrameReader(ws)
        try:
            await asyncio.wait_for(
                login(reader, writer, cfg.display_auth, password, cfg.display_ca),
                HANDSHAKE_TIMEOUT)
            await asyncio.wait_for(greet_browser(ws, frames), HANDSHAKE_TIMEOUT)
            # ClientInit's shared flag, set to 1 whatever the browser asked, so
            # one page cannot throw everyone else off the guest's screen.
            await asyncio.wait_for(frames(1), HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            raise RfbError("the display handshake timed out")
        except asyncio.IncompleteReadError:
            raise RfbError("the display closed the connection during login")
        except OSError as exc:
            raise RfbError(f"the display connection failed: {exc.strerror or exc}")
        writer.write(b"\x01" + bytes(frames.buf))
        await writer.drain()
        tasks.note(user, guest, "display", "opened the display")
        await _pipe(ws, reader, writer, sessions, token)
    except RfbError as exc:
        log.info("display of %s for %s: %s", guest, user, exc)
        if not ws.closed:
            await ws.close(code=4000, message=close_reason(str(exc)))
    except (OSError, asyncio.CancelledError):
        pass
    finally:
        if writer is not None:
            writer.close()
        if not ws.closed:
            try:
                await asyncio.wait_for(ws.close(), CLOSE_TIMEOUT)
            except (asyncio.TimeoutError, OSError, asyncio.CancelledError):
                if request.transport is not None:
                    request.transport.abort()
    return ws


async def _pipe(ws, reader, writer, sessions, token):
    """Bytes both ways until either side goes, or the session ends."""
    async def down():
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                await ws.send_bytes(chunk)
        except OSError:
            pass                                # a reset from the server
        finally:
            if not ws.closed:
                await ws.close(code=4000, message=b"the display closed")

    async def watch():
        while not ws.closed:
            await asyncio.sleep(SESSION_CHECK)
            if sessions.get_id(token, touch=False) is None:
                await ws.close(code=4000, message=b"session ended")
                return

    pump = asyncio.create_task(down())
    watcher = asyncio.create_task(watch())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                # Keys and the mouse count as activity; noVNC's own update
                # requests do not, or an open screen would keep a session alive.
                touch = bool(msg.data) and msg.data[0] in USER_INPUT
                if sessions.get_id(token, touch=touch) is None:
                    break
                writer.write(msg.data)
                await writer.drain()
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        pump.cancel()
        watcher.cancel()
