# SPDX-License-Identifier: GPL-2.0-only
"""The serial console over a WebSocket: WS /api/guests/{g}/console.

The daemon connects to the guest's console broker like any other client.
Binary frames carry console bytes both ways. Text frames carry JSON control
messages:

  browser -> daemon  {"type": "take"}      take the keyboard
                     {"type": "release"}   give it back
  daemon -> browser  {"type": "status", "typing": bool, "typist": name|null,
                      "viewers": n, "running": bool, "message": text}

A page opens viewing: what the browser sends is dropped until it takes the
keyboard. One page per guest holds the keyboard at a time; taking it from
someone else moves it. Every take and release goes in the task log.

The broker sends its telnet greeting once and then the guest's bytes as
they are, so only the greeting is removed. A page that stops reading is
cut off rather than allowed to hold anything open.
"""
import asyncio
import json
import logging

from aiohttp import WSMsgType, web

from ..telnet import TN_INIT
from . import data

log = logging.getLogger("emuctl.web")
SCROLLBACK = 64 * 1024
MAX_INPUT = 4096            # bytes in one frame from the browser (the page sends 1 KB)
SEND_TIMEOUT = 2            # seconds a page may take to accept a status
TAKE_BURST = 5              # takes and releases one page may make at once,
TAKE_RATE = 1               # then this many a second


class ConsoleRoom:
    """Everyone looking at one guest's console from the page."""

    def __init__(self):
        self.viewers = []           # [(ws, user)]
        self.typist = None          # ws holding the keyboard

    def status(self, ws, running, message=""):
        holder = next((u for w, u in self.viewers if w is self.typist), None)
        return {"type": "status", "typing": ws is self.typist and self.typist is not None,
                "typist": holder, "viewers": len(self.viewers), "running": running,
                "message": message}


class Consoles:
    def __init__(self, tasks):
        self.tasks = tasks
        self.rooms = {}             # guest -> ConsoleRoom
        self.by_session = {}        # session token -> set of ws
        self.handlers = {}          # ws -> (handler task, guest, transport)
        self.dropped = set()        # ws whose keyboard kick() took away

    def room(self, guest):
        return self.rooms.setdefault(guest, ConsoleRoom())

    def count(self, token):
        return len(self.by_session.get(token, ()))

    async def broadcast(self, guest, running=True, message=""):
        room = self.rooms.get(guest)
        for ws, _ in list(room.viewers if room else []):
            try:
                await asyncio.wait_for(ws.send_str(json.dumps(room.status(ws, running, message))),
                                       SEND_TIMEOUT)
            except asyncio.TimeoutError:
                self.kick(ws)               # a page that stopped reading
            except (ConnectionResetError, RuntimeError):
                pass

    def kick(self, ws):
        """End one console now: free its keyboard at once, then stop its
        handler, whatever the browser is doing."""
        task, guest, transport = self.handlers.get(ws, (None, None, None))
        room = self.rooms.get(guest)
        if room and room.typist is ws:
            room.typist = None
            self.dropped.add(ws)
        if transport is not None:
            transport.abort()               # a stalled browser cannot hold a close open
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def close_all(self):
        tasks = [t for t, _, _ in self.handlers.values()]
        for ws in list(self.handlers):
            self.kick(ws)
        if tasks:
            await asyncio.wait(tasks, timeout=5)

    def end_session(self, token):
        for ws in list(self.by_session.pop(token, ())):
            self.kick(ws)


def escape_iac(data):
    return data.replace(b"\xff", b"\xff\xff")


async def _watch(ws, sessions, token, consoles, every=30):
    """End a console whose session has expired, even if nobody types."""
    while not ws.closed:
        await asyncio.sleep(every)
        if sessions.get_id(token, touch=False) is None:
            consoles.kick(ws)
            return


async def serve(request, cfg, consoles, sessions, can_type=True):
    """The WebSocket handler's body. `request` has passed every middleware."""
    guest, user, token = request["guest"], request["user"].name, request["session"].token
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=MAX_INPUT)
    # Counted before anything is awaited, so the per-session cap holds.
    consoles.by_session.setdefault(token, set()).add(ws)
    consoles.handlers[ws] = (asyncio.current_task(), guest, request.transport)
    room = consoles.room(guest)
    room.viewers.append((ws, user))
    writer = None
    watch = None
    try:
        await ws.prepare(request)
        watch = asyncio.create_task(_watch(ws, sessions, token, consoles))
        if not cfg.has_console:
            await ws.send_str(json.dumps(room.status(ws, False,
                                                     f"{guest} has no serial console")))
            return ws
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(cfg.bind, cfg.broker), 5)
        except (OSError, asyncio.TimeoutError):
            reader = None
        tail = await asyncio.to_thread(data.tail_bytes, data.console_log_path(cfg), SCROLLBACK,
                                       cfg.workdir)
        if tail:
            await ws.send_bytes(tail)
        if reader is None:
            await ws.send_str(json.dumps(room.status(ws, False, f"{guest} is not running")))
            return ws
        await consoles.broadcast(guest)
        pump = asyncio.create_task(_from_broker(reader, ws, room))
        try:
            await _from_browser(ws, writer, room, consoles, guest, user, token, sessions,
                                can_type)
        finally:
            pump.cancel()
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        if watch is not None:
            watch.cancel()
        if writer is not None:
            writer.close()
        held = room.typist is ws or ws in consoles.dropped
        consoles.dropped.discard(ws)
        room.viewers = [(w, u) for w, u in room.viewers if w is not ws]
        if room.typist is ws:
            room.typist = None
        if held:
            consoles.tasks.note(user, guest, "console", "gave back the keyboard (left the page)")
        consoles.handlers.pop(ws, None)
        socks = consoles.by_session.get(token)
        if socks is not None:
            socks.discard(ws)
            if not socks:
                del consoles.by_session[token]
        if not room.viewers:
            consoles.rooms.pop(guest, None)
        else:
            await consoles.broadcast(guest)
        if not ws.closed:
            try:
                await asyncio.wait_for(ws.close(), SEND_TIMEOUT)
            except (asyncio.TimeoutError, ConnectionResetError, asyncio.CancelledError):
                if request.transport is not None:
                    request.transport.abort()
    return ws


async def _from_broker(reader, ws, room):
    # The broker greets every client with TN_INIT (two telnet options) and
    # after that passes the guest's bytes through unchanged, 0xFF included.
    # So only the greeting is removed; the rest goes to the page as it is.
    try:
        head = await reader.readexactly(len(TN_INIT))
        if head != TN_INIT:
            await ws.send_bytes(head)
    except asyncio.IncompleteReadError as exc:
        if exc.partial and exc.partial != TN_INIT[:len(exc.partial)]:
            await ws.send_bytes(exc.partial)
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            break
        await ws.send_bytes(chunk)
    try:
        await ws.send_str(json.dumps(room.status(ws, False, "the console closed")))
    finally:
        await ws.close()


async def _from_browser(ws, writer, room, consoles, guest, user, token, sessions, can_type):
    tokens, refilled = float(TAKE_BURST), 0.0
    loop = asyncio.get_running_loop()
    async for msg in ws:
        if sessions.get_id(token, touch=False) is None:
            break                               # logged out or expired
        if msg.type == WSMsgType.BINARY:
            if room.typist is not ws:
                continue                        # viewing: keys are dropped
            sessions.get_id(token)               # typing counts as activity
            writer.write(escape_iac(msg.data))
            await writer.drain()
        elif msg.type == WSMsgType.TEXT:
            try:
                kind = json.loads(msg.data).get("type")
            except (ValueError, AttributeError):
                continue
            if kind in ("take", "release"):
                now = loop.time()
                tokens = min(TAKE_BURST, tokens + (now - refilled) * TAKE_RATE)
                refilled = now
                if tokens < 1:
                    continue                    # a flood of takes would bury the task log
                tokens -= 1
            if kind == "take" and not can_type:
                await ws.send_str(json.dumps(room.status(ws, True, "you may only watch")))
            elif kind == "take" and room.typist is not ws:
                before = next((u for w, u in room.viewers if w is room.typist), None)
                room.typist = ws
                note = f"took the keyboard from {before}" if before else "took the keyboard"
                consoles.tasks.note(user, guest, "console", note)
                log.info("%s %s of %s", user, note, guest)
                await consoles.broadcast(guest)
            elif kind == "release" and room.typist is ws:
                room.typist = None
                consoles.tasks.note(user, guest, "console", "gave back the keyboard")
                await consoles.broadcast(guest)
        elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
            break
