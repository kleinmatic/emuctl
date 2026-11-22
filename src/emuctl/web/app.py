# SPDX-License-Identifier: GPL-2.0-only
"""The aiohttp application: security middleware, routes and live events.

Security rules (AGENTS.md) enforced here, in one place:
  1  listen address and TLS ......... serve()
  2  sessions ....................... auth_middleware, auth.Sessions
  3  Origin and CSRF ................ auth_middleware (origin_ok, csrf_ok)
  4  guest names .................... guest_middleware
  5  no shell ....................... every subprocess is an argument list
  6  file access .................... data.safe_path, data.tail_bytes
  7  login limit .................... login(), auth.LoginLimiter
  8  CSP ............................ add_security_headers
  9  task log ....................... tasks.py; every action route goes through it,
                                     and console.py notes each keyboard take
Every route handler is marked @public or @needs(role). A handler with
neither is refused, so a route cannot be added open by mistake.
"""
import asyncio
import concurrent.futures
import ipaddress
import json
import logging
import math
import os
import socket

from aiohttp import web
from aiohttp.web_urldispatcher import StaticResource

from .. import full_version
from ..config import NAME_PATTERN, ConfigError, load_config
from . import data, tls
from .auth import LoginLimiter, Sessions, Users
from .auth import _addr_key as auth_addr_key
from .sampler import Sampler
from .tasks import Tasks

log = logging.getLogger("emuctl.web")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE = "emuctl_session"
CSRF_HEADER = "X-CSRF-Token"
BACKGROUND_HEADER = "X-Emuctl-Background"
STREAMS_PER_SESSION = 4
BODY_TIMEOUT = 10           # seconds to send a request body
WRITE_TIMEOUT = 30          # seconds a page may take to accept an event
# Inline styles are allowed because xterm.js writes its colours and sizes
# into <style> elements it makes. Scripts are not: they come from our own
# files only. The page builds everything with textContent, so there is no
# markup from the server in which a style could be injected.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'")
ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
HEARTBEAT = 15
LOGIN_CHECKS = 4            # scrypt checks at once; each takes 32 MB
LOGINS_INFLIGHT = 4         # login requests at once from one address

SETTINGS = web.AppKey("settings", object)
CONF = web.AppKey("conf", object)
USERS = web.AppKey("users", Users)
SESSIONS = web.AppKey("sessions", Sessions)
LIMITER = web.AppKey("limiter", LoginLimiter)
SAMPLER = web.AppKey("sampler", Sampler)
HUB = web.AppKey("hub", object)
TASKS = web.AppKey("tasks", Tasks)
STATE = web.AppKey("state", dict)


def public(handler):
    handler.public = True
    return handler


def needs(role):
    if role not in ROLE_RANK:
        raise ValueError(role)

    def mark(handler):
        handler.role = role
        return handler
    return mark


def json_error(cls, message, **kw):
    return cls(text=json.dumps({"error": message}), content_type="application/json", **kw)


def refuse(request, cls, message):
    """An error as JSON for the API, as text for anything else."""
    if request.path.startswith("/api/"):
        return json_error(cls, message)
    return cls(text=message)


# --- rule 8: headers on every response ---------------------------------------

async def add_security_headers(request, response):
    h = response.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Cache-Control", "no-store" if request.path.startswith("/api/") else "no-cache")
    # No Strict-Transport-Security: it would apply to every port on the host
    # name for a year, not just this one.


# --- rules 2 and 3 ------------------------------------------------------------

def host_ok(request):
    """The browser asked for this host by an address or by a name it is known
    by. An IP address cannot be rebound; a name must be the host's own (as in
    its certificate) or one listed in server_names. This stops DNS rebinding:
    a page on another site whose name has been pointed at this host."""
    host = (request.host or "").lower()
    if host.startswith("["):
        name = host[1:host.find("]")] if "]" in host else host
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return name.rstrip(".") in request.app[STATE]["names"]


def own_origin(request):
    return f"{request.scheme}://{request.host}"


def origin_ok(request):
    """The Origin header names this daemon. Browsers always send it on POSTs
    and WebSocket upgrades, and a page on another site cannot forge it."""
    return request.headers.get("Origin") == own_origin(request)


def changes_something(request):
    return (request.method not in SAFE_METHODS
            or request.headers.get("Upgrade", "").lower() == "websocket")


def csrf_ok(request, session):
    import hmac
    got = request.headers.get(CSRF_HEADER, "")
    return hmac.compare_digest(got.encode(), session.csrf.encode())


def is_public(request):
    mi = request.match_info
    if mi.http_exception is not None:
        return True                     # no such route: the 404 or 405 reveals nothing
    if isinstance(mi.route.resource, StaticResource):
        return True                     # the page's own code, the same for everyone
    return getattr(mi.handler, "public", False)


@web.middleware
async def auth_middleware(request, handler):
    if not host_ok(request):
        log.warning("refused a request for host %r from %s (add it to server_names in "
                    "web.conf if it is one of ours)", request.host, request.remote)
        raise refuse(request, web.HTTPMisdirectedRequest, "unknown host name")
    if changes_something(request) and not origin_ok(request):
        log.warning("refused %s %r from %s: Origin %r is not %s", request.method, request.path,
                    request.remote, request.headers.get("Origin"), own_origin(request))
        raise refuse(request, web.HTTPForbidden, "request from another site refused")
    if is_public(request):
        return await handler(request)

    # The page's own timed refreshes say so, and do not count as the user
    # being active: a forgotten tab still lets the session time out.
    background = (request.headers.get(BACKGROUND_HEADER) == "1"
                  or request.path == "/api/events")   # EventSource cannot send headers
    session = request.app[SESSIONS].get(request.cookies.get(COOKIE), touch=not background)
    if session is None:
        if request.path.startswith("/api/"):
            raise json_error(web.HTTPUnauthorized, "not logged in")
        raise web.HTTPFound("/login")
    user = request.app[USERS].get(session.user)
    role = getattr(request.match_info.handler, "role", None)
    if role is None or user is None or ROLE_RANK[user.role] < ROLE_RANK[role]:
        raise refuse(request, web.HTTPForbidden, "not allowed")
    if request.method not in SAFE_METHODS and not csrf_ok(request, session):
        raise refuse(request, web.HTTPForbidden, "missing or wrong CSRF token")
    request["session"], request["user"] = session, user
    return await handler(request)


# --- rule 4 -------------------------------------------------------------------

@web.middleware
async def guest_middleware(request, handler):
    name = request.match_info.get("g")
    if name is not None:
        if not NAME_PATTERN.fullmatch(name) or name not in request.app[SETTINGS].guests():
            raise json_error(web.HTTPNotFound, "no such guest")
        request["guest"] = name
    return await handler(request)


def guest_config(request):
    try:
        return load_config(request.app[SETTINGS].conf_path(request["guest"]))
    except (OSError, ConfigError) as exc:
        raise json_error(web.HTTPConflict, f"the guest's conf cannot be read: {exc}")


# --- live events ----------------------------------------------------------------

END = object()


class Hub:
    """Fans server-sent events out to every open page."""

    def __init__(self, depth=64):
        self.depth = depth
        self.subs = {}                  # queue -> session token

    def subscribe(self, token):
        q = asyncio.Queue(self.depth)
        self.subs[q] = token
        return q

    def unsubscribe(self, q):
        self.subs.pop(q, None)

    def publish(self, event, payload):
        for q in list(self.subs):
            try:
                q.put_nowait((event, payload))
            except asyncio.QueueFull:   # a page that stopped reading: drop it
                self._close(q)

    def close_all(self):
        for q in list(self.subs):
            self._close(q)

    def end_session(self, token):
        for q, t in list(self.subs.items()):
            if t == token:
                self._close(q)

    def _close(self, q):
        self.unsubscribe(q)
        while not q.empty():
            q.get_nowait()
        q.put_nowait((END, None))


def sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


# --- routes ---------------------------------------------------------------------

routes = web.RouteTableDef()


@routes.get("/login")
@public
async def login_page(request):
    return web.FileResponse(os.path.join(STATIC, "login.html"))


@routes.get("/")
@needs("viewer")
async def index_page(request):
    return web.FileResponse(os.path.join(STATIC, "index.html"))


async def read_json(request):
    if request.content_type != "application/json":
        raise json_error(web.HTTPUnsupportedMediaType, "send JSON")
    try:
        body = await asyncio.wait_for(request.json(), BODY_TIMEOUT)
    except ValueError:
        raise json_error(web.HTTPBadRequest, "not valid JSON")
    except asyncio.TimeoutError:
        raise json_error(web.HTTPRequestTimeout, "the request body did not arrive")
    if not isinstance(body, dict):
        raise json_error(web.HTTPBadRequest, "send a JSON object")
    return body


@routes.post("/api/login")
@public
async def login(request):
    app, addr = request.app, request.remote or "?"
    limiter = app[LIMITER]
    # A few logins at a time per address, before the body is even read, so
    # slow senders cannot tie up the daemon either.
    inflight = app[STATE]["logins_inflight"]
    key = auth_addr_key(addr)
    if inflight.get(key, 0) >= LOGINS_INFLIGHT:
        raise json_error(web.HTTPTooManyRequests, "too many logins at once from this address")
    inflight[key] = inflight.get(key, 0) + 1
    try:
        return await _login(request, app, addr, limiter)
    finally:
        inflight[key] -= 1
        if not inflight[key]:
            del inflight[key]


async def _login(request, app, addr, limiter):
    body = await read_json(request)
    name, password = body.get("user"), body.get("password")
    if not (isinstance(name, str) and isinstance(password, str)
            and 0 < len(name) <= 64 and len(password) <= 1024):
        raise json_error(web.HTTPBadRequest, "send user and password")
    # Checked and charged together, with nothing awaited in between, so many
    # attempts sent at once cannot all pass the check before any is counted.
    # A good password gives the charge back.
    wait = limiter.wait(addr, name)
    if wait > 0:
        raise json_error(web.HTTPTooManyRequests,
                         f"too many failed logins; try again in {math.ceil(wait)} s",
                         headers={"Retry-After": str(math.ceil(wait))})
    charge = limiter.failed(addr, name)
    loop = asyncio.get_running_loop()
    user = await loop.run_in_executor(app[STATE]["login_pool"], app[USERS].check, name, password)
    if user is None:
        log.warning("failed login for %r from %s", name[:64], addr)
        raise json_error(web.HTTPUnauthorized, "wrong user name or password")
    limiter.succeeded(addr, name, charge)
    session = app[SESSIONS].new(user.name)
    log.info("%s logged in from %s", user.name, addr)
    resp = web.json_response({"user": user.name, "role": user.role, "csrf": session.csrf})
    resp.set_cookie(COOKIE, session.token, httponly=True, secure=app[CONF].tls,
                    samesite="Strict", path="/")
    return resp


@routes.post("/api/logout")
@needs("viewer")
async def logout(request):
    request.app[SESSIONS].end(request["session"].token)
    log.info("%s logged out", request["user"].name)
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE, path="/")
    return resp


@routes.get("/api/session")
@needs("viewer")
async def session_info(request):
    s, u = request["session"], request["user"]
    return web.json_response({"user": u.name, "role": u.role, "csrf": s.csrf,
                              "version": full_version()})


async def current_rows(app):
    if not app[SAMPLER].rows and app[SETTINGS].guests():
        async with app[STATE]["tick"]:
            if not app[SAMPLER].rows:
                await asyncio.to_thread(app[SAMPLER].tick)
    return app[SAMPLER].rows


def find_row(rows, name):
    return next((r for r in rows if r["name"] == name), None)


@routes.get("/api/host")
@needs("viewer")
async def host(request):
    s = request.app[SETTINGS]
    out = await asyncio.to_thread(
        lambda: data.host_info(s, request.app[SAMPLER].configs(s.guests())))
    return web.json_response(out)


NET_CACHE = 10     # seconds; the network report runs ip, bridge and ethtool


async def cached(app, key, build):
    """The result of build() in a thread, shared by every page for NET_CACHE seconds."""
    cache = app[STATE]["net_cache"]
    lock = app[STATE].setdefault("net_locks", {}).setdefault(key, asyncio.Lock())
    async with lock:            # many pages at once: one report
        now = asyncio.get_running_loop().time()
        hit = cache.get(key)
        if hit and now - hit[0] < NET_CACHE:
            return hit[1]
        out = await asyncio.to_thread(build)
        cache[key] = (asyncio.get_running_loop().time(), out)
        return out


@routes.get("/api/host/network")
@needs("viewer")
async def host_network(request):
    from .. import network
    s = request.app[SETTINGS]
    return web.json_response(await cached(request.app, "host", lambda: network.host_network(s)))


@routes.get("/api/guests")
@needs("viewer")
async def guests(request):
    return web.json_response(await current_rows(request.app))


@routes.get("/api/guests/{g}")
@needs("viewer")
async def guest(request):
    from ..status import guest_status
    s, name = request.app[SETTINGS], request["guest"]
    row = find_row(await current_rows(request.app), name) \
        or await asyncio.to_thread(guest_status, s, name)
    return web.json_response(await asyncio.to_thread(data.guest_detail, s, name, row))


@routes.get("/api/guests/{g}/usage")
@needs("viewer")
async def guest_usage(request):
    window = request.query.get("window", "1h")
    if window != "1h":
        raise json_error(web.HTTPBadRequest, "window must be 1h")
    await current_rows(request.app)
    return web.json_response(request.app[SAMPLER].series(request["guest"], 3600))


@routes.get("/api/guests/{g}/network")
@needs("viewer")
async def guest_network(request):
    from .. import network
    s, name = request.app[SETTINGS], request["guest"]
    cfg = guest_config(request)

    def build():
        host = network.Host()
        rep = network.guest_network(s, name, host=host, cfg=cfg)
        rep["warnings"] += [w for w in network.host_network(s, host)["warnings"]
                            if w.startswith(f"{name}: uses ")]
        return rep
    return web.json_response(await cached(request.app, ("guest", name), build))


@routes.get("/api/guests/{g}/hardware")
@needs("viewer")
async def guest_hardware(request):
    cfg = guest_config(request)
    return web.json_response(await asyncio.to_thread(data.guest_hardware, cfg))


@routes.get("/api/guests/{g}/log")
@needs("viewer")
async def guest_log(request):
    source = request.query.get("source", "supervisor")
    try:
        lines = int(request.query.get("lines", "200"))
    except ValueError:
        raise json_error(web.HTTPBadRequest, "lines must be a number")
    lines = max(1, min(lines, data.MAX_LINES))
    name = request["guest"]
    if source == "console":
        cfg = guest_config(request)
        text = await asyncio.to_thread(data.console_log, cfg, lines)
        if text is None:
            return web.json_response({"source": source, "text": "", "missing": True})
    elif source == "supervisor":
        text = await asyncio.to_thread(data.supervisor_log, request.app[SETTINGS], name, lines)
    else:
        raise json_error(web.HTTPBadRequest, "source must be supervisor or console")
    return web.json_response({"source": source, "text": text})


# --- actions --------------------------------------------------------------------

STOP_ACTIONS = {"clean": "shutdown", "force": "stop", "now": "stop-now"}


@routes.post("/api/guests/{g}/start")
@needs("operator")
async def guest_start(request):
    t = await request.app[TASKS].submit(request["user"].name, request["guest"], "start")
    return web.json_response(t.public())


@routes.post("/api/guests/{g}/stop")
@needs("operator")
async def guest_stop(request):
    body = await read_json(request)
    action = STOP_ACTIONS.get(body.get("mode")) if isinstance(body.get("mode"), str) else None
    if action is None:
        raise json_error(web.HTTPBadRequest, "mode must be clean, force or now")
    # The page sends power_cut: true only after the user confirmed one.
    t = await request.app[TASKS].submit(request["user"].name, request["guest"], action,
                                        confirmed=body.get("power_cut") is True)
    return web.json_response(t.public())


@routes.get("/api/tasks")
@needs("viewer")
async def task_list(request):
    return web.json_response(request.app[TASKS].list())


@routes.get("/api/tasks/{id}")
@needs("viewer")
async def task_one(request):
    try:
        t = request.app[TASKS].get(int(request.match_info["id"]))
    except ValueError:
        t = None
    if t is None:
        raise json_error(web.HTTPNotFound, "no such task")
    return web.json_response(t.public())


CONSOLES_PER_SESSION = 8


@routes.get("/api/guests/{g}/console")
@needs("viewer")
async def guest_console(request):
    """WebSocket. Viewing needs a viewer; taking the keyboard, an operator."""
    from . import console
    consoles = request.app[STATE]["consoles"]
    if consoles.count(request["session"].token) >= CONSOLES_PER_SESSION:
        raise json_error(web.HTTPTooManyRequests, "too many consoles open for this session")
    cfg = guest_config(request)
    can_type = ROLE_RANK[request["user"].role] >= ROLE_RANK["operator"]
    return await console.serve(request, cfg, consoles, request.app[SESSIONS], can_type)


@routes.get("/api/events")
@needs("viewer")
async def events(request):
    app, token = request.app, request["session"].token
    if sum(t == token for t in app[HUB].subs.values()) >= STREAMS_PER_SESSION:
        raise json_error(web.HTTPTooManyRequests, "too many open pages for this session")
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                       "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    q = app[HUB].subscribe(token)

    async def send(data):
        await asyncio.wait_for(resp.write(data), WRITE_TIMEOUT)
    try:
        await send(sse("guests", await current_rows(app)))
        await send(sse("tasks", app[TASKS].list()))
        while True:
            try:
                event, payload = await asyncio.wait_for(q.get(), HEARTBEAT)
            except asyncio.TimeoutError:
                event, payload = None, None
            if event is END or app[SESSIONS].get(token, touch=False) is None:
                break
            await send(sse(event, payload) if event else b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError, asyncio.TimeoutError):
        pass
    finally:
        app[HUB].unsubscribe(q)
    return resp


# --- the application ------------------------------------------------------------

async def sample_forever(app):
    sampler, hub = app[SAMPLER], app[HUB]
    while True:
        try:
            async with app[STATE]["tick"]:
                rows = await asyncio.to_thread(sampler.tick)
            hub.publish("guests", rows)
        except Exception:
            log.exception("sampling failed")
        await asyncio.sleep(sampler.interval)


async def end_streams(app):
    app[HUB].close_all()        # open pages would otherwise hold a shutdown for a minute
    await app[STATE]["consoles"].close_all()
    await app[TASKS].close()
    app[STATE]["login_pool"].shutdown(wait=False)


async def start_background(app):
    app[STATE]["sampler_task"] = asyncio.create_task(sample_forever(app))


async def stop_background(app):
    for key in ("sampler_task",):
        task = app[STATE].pop(key, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def known_names(conf):
    names = set(tls.names_for(socket.gethostname())) | set(conf.server_names)
    try:
        names.add(socket.getfqdn().lower())
    except OSError:
        pass
    return {n.lower().rstrip(".") for n in names}


def make_app(settings, conf, users=None, sampler=None, background=True):
    app = web.Application(middlewares=[auth_middleware, guest_middleware],
                          client_max_size=64 * 1024)
    app[SETTINGS], app[CONF] = settings, conf
    app[USERS] = users or Users(conf.users_path)
    app[SESSIONS] = Sessions(app[USERS])
    app[LIMITER] = LoginLimiter()
    app[SAMPLER] = sampler or Sampler(settings)
    hub = Hub()
    app[HUB] = hub
    app[SESSIONS].on_end.append(hub.end_session)
    app[TASKS] = Tasks(settings, conf.task_log, hub.publish)
    app[STATE] = {
        "tick": asyncio.Lock(),
        # scrypt gets threads of its own, so logins cannot starve the sampler
        "login_pool": concurrent.futures.ThreadPoolExecutor(LOGIN_CHECKS, "login"),
        "names": known_names(conf),
        "net_cache": {},
        "logins_inflight": {},
    }
    from .console import Consoles
    app[STATE]["consoles"] = Consoles(app[TASKS])
    app[SESSIONS].on_end.append(app[STATE]["consoles"].end_session)
    for p in (conf.state_dir, conf.cert_path, conf.key_path, conf.users_path):
        if p not in data.PROTECTED:
            data.PROTECTED.append(p)
    app.on_response_prepare.append(add_security_headers)
    app.router.add_routes(routes)
    app.router.add_static("/static/", STATIC, follow_symlinks=False)
    app.on_shutdown.append(end_streams)
    if background:
        app.on_startup.append(start_background)
        app.on_cleanup.append(stop_background)
    return app
