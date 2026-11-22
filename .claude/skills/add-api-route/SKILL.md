---
name: add-api-route
description: Add or change a route in emuctl's web API (src/emuctl/web) with its role check, input validation, tests and docs/api.md entry. Use when adding an endpoint, WebSocket or server-sent event to the web interface.
---

# Add a web API route

> Written before the web package exists, from the plan. The file and function
> names below are the intended ones. When phase 2 lands, correct this skill
> against the real code in the same commit.

Read the web security rules in `AGENTS.md` first. Every route follows them.

## Steps

1. **Decide what it needs.** The role it requires (`viewer`, `operator`,
   `admin`; until roles exist, every route needs a logged-in user). Whether
   it changes anything; if so it is a POST (or a WebSocket that writes) and
   needs the CSRF token and the Origin check.
2. **Put the logic in the core, not the handler.** If the route shows or
   decides something the CLI could also use, the work goes in a core module
   (`status.py`, `usage.py`, `network.py`, `hardware.py`, `actions.py`) and
   the handler only calls it. The page and the CLI must never disagree.
3. **Write the handler** in the right module under `src/emuctl/web/`, with the
   role decorator. Take guest names only through the shared lookup that
   checks the name pattern and that the guest exists. Run subprocesses only
   with argument lists. Return JSON with the same field names the CLI's
   `--json` uses.
4. **Anything that acts on a guest** goes through a task, so it appears in the
   task log with the user's name. Do not call `systemctl` from a handler.
5. **Register the route** where the others are registered, under `/api`.
6. **Tests**, with aiohttp's test client:
   - it works for a logged-in user with the right role;
   - it is refused without a session, and with the wrong role;
   - for a POST: refused without the CSRF token and with a foreign Origin;
   - a bad or unknown guest name gives 404 before the handler runs;
   - any input it parses, given nonsense.
7. **Docs.** An entry in `docs/api.md`: method, path, role, what it returns,
   one example request and response. If the page uses it, update
   `docs/web-tour.md`.
8. **Changelog** line under `## Unreleased`.
9. Run `pytest`, `ruff`, and the coverage check. The `web/` package must stay
   at 90% or more.
10. Run the `web-security-check` skill on the diff before calling it done.
