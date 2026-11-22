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
"""
import asyncio
import json
import logging

from aiohttp import WSMsgType, web

from ..telnet import strip_telnet
from . import data

log = logging.getLogger("emuctl.web")
SCROLLBACK = 64 * 1024
MAX_INPUT = 4096            # bytes in one frame from the browser


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

    def room(self, guest):
        return self.rooms.setdefault(guest, ConsoleRoom())

    def count(self, token):
        return len(self.by_session.get(token, ()))

    async def broadcast(self, guest, running=True, message=""):
        room = self.rooms.get(guest)
        for ws, _ in list(room.viewers if room else []):
            try:
                await ws.send_str(json.dumps(room.status(ws, running, message)))
            except (ConnectionResetError, RuntimeError):
                pass

    async def close_all(self):
        for socks in list(self.by_session.values()):
            for ws in list(socks):
                await ws.close(message=b"the daemon is stopping")

    def end_session(self, token):
        for ws in list(self.by_session.get(token, ())):
            asyncio.get_running_loop().create_task(ws.close(message=b"session ended"))


def escape_iac(data):
    return data.replace(b"\xff", b"\xff\xff")


async def _watch(ws, sessions, token, every=30):
    """Close a console whose session has expired, even if nobody types."""
    while not ws.closed:
        await asyncio.sleep(every)
        if sessions.get(token, touch=False) is None:
            await ws.close(message=b"session ended")


async def serve(request, cfg, consoles, sessions, can_type=True):
    """The WebSocket handler's body. `request` has passed every middleware."""
    guest, user, token = request["guest"], request["user"].name, request["session"].token
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=MAX_INPUT * 2)
    await ws.prepare(request)
    room = consoles.room(guest)
    room.viewers.append((ws, user))
    consoles.by_session.setdefault(token, set()).add(ws)
    writer = None
    watch = asyncio.create_task(_watch(ws, sessions, token))
    try:
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
            await ws.close()
            return ws
        await consoles.broadcast(guest)
        pump = asyncio.create_task(_from_broker(reader, ws))
        try:
            await _from_browser(ws, writer, room, consoles, guest, user, token, sessions,
                                can_type)
        finally:
            pump.cancel()
        if pump.done() and not pump.cancelled() and not ws.closed:
            await ws.send_str(json.dumps(room.status(ws, False, "the console closed")))
    finally:
        watch.cancel()
        if writer is not None:
            writer.close()
        held = room.typist is ws
        room.viewers = [(w, u) for w, u in room.viewers if w is not ws]
        if held:
            room.typist = None
            consoles.tasks.note(user, guest, "console", "gave back the keyboard (left the page)")
        consoles.by_session.get(token, set()).discard(ws)
        if not room.viewers:
            consoles.rooms.pop(guest, None)
        else:
            await consoles.broadcast(guest)
        if not ws.closed:
            await ws.close()
    return ws


async def _from_broker(reader, ws):
    st = 0
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            break
        clean, st = strip_telnet(chunk, st)
        if clean:
            await ws.send_bytes(clean)
    await ws.close()


async def _from_browser(ws, writer, room, consoles, guest, user, token, sessions, can_type):
    async for msg in ws:
        if sessions.get(token, touch=False) is None:
            break                               # logged out or expired
        if msg.type == WSMsgType.BINARY:
            if room.typist is not ws or len(msg.data) > MAX_INPUT:
                continue                        # viewing: keys are dropped
            sessions.get(token)                 # typing counts as activity
            writer.write(escape_iac(msg.data))
            await writer.drain()
        elif msg.type == WSMsgType.TEXT:
            try:
                kind = json.loads(msg.data).get("type")
            except (ValueError, AttributeError):
                continue
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
