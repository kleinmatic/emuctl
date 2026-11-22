---
name: add-api-route
description: Add or change a route in emuctl's web API (src/emuctl/web/app.py) with its role mark, guest-name handling, tests and docs/api.md entry. Use when adding an endpoint, WebSocket or server-sent event to the web interface.
---

# Add a web API route

Read the web security rules in `AGENTS.md` first. Every route follows them,
and most of them are enforced for you by the middleware in
`src/emuctl/web/app.py` if you use it as below.

## Steps

1. **Decide what it needs.** The role it requires: `viewer` (reads),
   `operator` (starts, stops, consoles), `admin` (users). Until roles exist
   every user is an admin, but mark the route with the role it would need.
   Whether it changes anything: if so it is a POST, or a WebSocket that
   writes. `auth_middleware` then checks the Origin header for you, and the
   CSRF token for POSTs.
2. **Put the logic in the core, not the handler.** If the route shows or
   decides something the CLI could also use, the work goes in a core module
   (`status.py`, `usage.py`, `network.py`, `hardware.py`, `actions.py`) or in
   `web/data.py` (web-only, but still plain functions with no HTTP in them).
   The handler only calls it. The page and the CLI must never disagree.
3. **Write the handler** in `web/app.py` next to the others:

   ```python
   @routes.get("/api/guests/{g}/thing")
   @needs("viewer")
   async def guest_thing(request):
       cfg = guest_config(request)              # 409 if the conf is broken
       out = await asyncio.to_thread(data.guest_thing, cfg)
       return web.json_response(out)
   ```

   - The decorator order matters: `@routes.<method>` outermost, then
     `@needs(role)` or `@public`. A handler with neither is refused with a
     403, on purpose.
   - Name the guest part of the path `{g}`. `guest_middleware` then checks it
     against the conf-name pattern and the real guests, and 404s before your
     handler runs. Use `request["guest"]`, never `request.match_info["g"]`.
   - Anything that blocks (files, subprocesses, sockets, the guard) runs in
     `asyncio.to_thread`.
   - Subprocesses get argument lists only. The daemon runs with
     `elevate = none`; never add sudo.
   - Use the same field names as the CLI's `--json`.
   - Errors: `raise json_error(web.HTTPBadRequest, "plain message")`.
   - Read files only through `data.tail_bytes` or `hardware.read_config`,
     and only paths a guest's conf names; `data.safe_path` keeps
     `/etc/emuctl/env/` out.
4. **Anything that acts on a guest** goes through a task (`web/tasks.py`),
   so it appears in the task log with the user's name. Do not call
   `systemctl` from a handler.
5. **Live data** the page should see without asking goes through the hub:
   `request.app[HUB].publish("event-name", payload)`, and the page listens
   for it in `static/js/app.js`.
6. **Tests** in `tests/test_web_app.py`, with the `client` fixture and the
   `session(client)` helper (which returns headers with the cookie, CSRF
   token and Origin):
   - it works for a logged-in user;
   - it is 401 without a session (add the path to
     `test_everything_but_the_login_page_needs_a_session`);
   - for a POST: 403 without the CSRF token and with a foreign Origin;
   - a bad or unknown guest name is 404 (add the path to
     `test_bad_guest_names_are_404_before_the_handler`);
   - any input it parses, given nonsense.
   Logic in `data.py` gets plain pytest tests in `tests/test_web_sampler.py`.
7. **The page**, if it uses the route: build with `h()` from
   `static/js/dom.js`, call through `get`/`post` in `static/js/api.js`.
   Pure logic (formatting, maths) goes in a module tested by
   `tests/js/*.test.mjs`.
8. **Docs.** An entry in `docs/api.md`: method, path, role, what it
   returns, an example. If the page shows it, update `docs/web-tour.md`.
9. **Changelog** line under `## Unreleased`.
10. Run `pytest`, `ruff`, `node --test tests/js/*.test.mjs`, the coverage
    check (`web/` must stay at 90% or more), and the browser tests if the
    page changed. Then the `web-security-check` skill on the diff.
