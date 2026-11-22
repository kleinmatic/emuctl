# Working on emuctl

Instructions for anyone changing this repository, human or AI agent.
`CLAUDE.md` is a symlink to this file. `CONTRIBUTING.md` is the short version
for people; this file has the detail.

## What emuctl is

A service layer for emulated machines (SIMH, es40, QEMU, gxemul). One
supervisor process per guest holds the emulator's console, types the boot and
shutdown commands the guest's conf lists, logs the console, re-serves it to
any number of clients, and records the guest's state. `docs/design.md`
explains why it works this way; read it before changing the supervisor.

`emuctl web serve` is a web interface on top (`src/emuctl/web/`, an aiohttp
daemon and a page of plain JavaScript modules with no build step). It is
being built in phases; `docs/web.md` describes what exists.

## Layout

| Path | What it is |
|---|---|
| `src/emuctl/cli.py` | the `emuctl` command |
| `src/emuctl/supervisor.py` | the per-guest supervisor and console broker |
| `src/emuctl/config.py` | guest conf parsing; every key is in `docs/config.md` |
| `src/emuctl/settings.py` | host settings (`emuctl.conf`, `EMUCTL_*` variables) |
| `src/emuctl/state.py`, `status.py` | the state file, and what `list` and `status` report |
| `src/emuctl/guard.py` | the checks that stop two emulators sharing a disk |
| `src/emuctl/service.py` | systemctl and rc.d command lines |
| `src/emuctl/telnet.py` | the bit of telnet the broker speaks |
| `src/emuctl/usage.py`, `network.py`, `hardware.py`, `actions.py` | resource numbers, the network report, emulated hardware, start and stop decisions; the CLI and the web page both use them |
| `src/emuctl/web/app.py` | the web daemon: security middleware, every route, live events |
| `src/emuctl/web/auth.py`, `conf.py`, `tls.py` | users and sessions, `web.conf`, the certificate (standard library only) |
| `src/emuctl/web/sampler.py`, `data.py` | the 5-second sampler and history; what the read-only routes return |
| `src/emuctl/web/static/` | the page: `index.html`, `css/`, `js/` (ES modules), `vendor/` (third-party, from `tools/vendor.sh`) |
| `systemd/`, `rc.d/`, `install.sh` | service files and the installer (`install.sh web` adds the web daemon) |
| `examples/polkit/` | the rule that lets the web daemon manage guest units |
| `tests/` | pytest; `fake_emulator.py` stands in for a real emulator |
| `tests/js/` | `node --test` tests for the page's pure modules |
| `tests/browser/` | Playwright tests of the page against a real daemon |
| `tools/devhost` | a throwaway host with fake guests |
| `tools/release_check.py`, `tools/coverage_check.py` | used by CI |
| `tools/vendor.sh` | fetches the page's third-party files at pinned versions |
| `tools/webdemo` | the page with fake guests and invented numbers; `--screenshots` remakes `docs/images/` |
| `docs/` | user documentation, one page per topic |
| `.claude/skills/` | step-by-step guides for common changes (below) |

## Running things

pytest, pytest-cov and ruff are the only tools needed for the core. The web
tests also need Python 3.11, `aiohttp` and `pytest-aiohttp`; without them
they skip themselves. Install everything with
`python3 -m pip install pytest pytest-cov ruff aiohttp pytest-aiohttp`, or
make a virtualenv: `uv venv && uv pip install -e '.[test]' ruff`.

```sh
python3 -m pytest -q                              # all tests, no root, no emulator
python3 -m pytest -q --cov=emuctl --cov-report=json   # writes coverage.json ...
python3 tools/coverage_check.py                       # ... which this reads
ruff check src tests tools tools/devhost tools/webdemo
node --test tests/js/*.test.mjs                   # the page's pure modules; needs only node
EMUCTL_BROWSER=1 python3 -m pytest tests/browser  # Playwright; on its own, not with the rest
eval "$(tools/devhost up)"                        # then: emuctl list, start, console, stop
emuctl web serve                                  # the page for that devhost (address printed by up)
tools/devhost down                                # the devhost this shell set up
```

The browser tests need `pip install pytest-playwright` and
`python -m playwright install chromium`. They cannot run in the same pytest
session as the asyncio web tests, so `tests/conftest.py` leaves them out
unless `EMUCTL_BROWSER` is set.

The full suite takes under a minute. The end-to-end tests start real
supervisors against `tests/fake_emulator.py`, which serves a firmware prompt,
boots on `boot`, halts on `shutdown`, and has options to misbehave (`--stuck`,
`--ignore-term`, `--die-after`, `drop`). Use it to test any supervisor change.

To try a change by hand, use `tools/devhost`: four fake guests (`vax` with a
clean stop, `alpha` without one, `stuck` that waits at a prompt, `twin` that
is exclusive with `vax`), a stand-in `systemctl` and `journalctl`, and no
root. See the `dev-host` skill.

## Rules

1. **No dependencies in the core.** `src/emuctl/` outside `web/` uses the
   Python standard library only, and must run on Python 3.10. The web part
   (`src/emuctl/web/`, when it exists) may use `aiohttp` and needs 3.11.
2. **Linux with systemd is the platform.** Debian 13 is the reference. Keep
   the existing FreeBSD paths working and their tests passing, but new
   features may report "not available" elsewhere (`docs/platforms.md`).
3. **Be careful with `supervisor.py`.** It runs real machines whose disks can
   be damaged by a wrong stop or a stray keystroke. Change it only when the
   task needs it, and add an end-to-end test against the fake emulator for
   every change in behaviour.
4. **Never type at a guest unless the conf says to.** The supervisor sends
   only configured stages, plus one Enter to a console that has been silent
   since attach. Do not add others.
5. **Nothing about a particular host in the code or tests.** Paths, names and
   addresses come from confs, `emuctl.conf` or the environment. Test fixtures
   use invented names, locally administered MAC addresses (`02:00:00:00:00:xx`) and
   documentation addresses (`192.0.2.0/24`, `198.51.100.0/24`).
6. **No secrets anywhere in the repo**, and never pass one on a command line.
   Passwords reach the supervisor through the service's environment file.
7. **Docs in plain English.** Say what it does, how to use it and what to
   watch for, in short sentences. No marketing tone. Every change a user can
   see updates the relevant page in `docs/`.
8. **Changelog.** A change a user could notice adds a line under
   `## Unreleased` in `CHANGELOG.md`, in the same commit. Anything that could
   break someone's setup also goes under `### Breaking`. See
   `docs/releasing.md`. Never push a tag; the maintainer releases.
9. **Coverage.** The whole project may not fall below the floor in
   `tools/coverage_check.py`; raise the floor when coverage rises. New modules
   go in its `NEW_MODULES` list and must reach 90%.
10. **Tests and lint pass before a change is called done.** If they do not,
    say so plainly.

## Who is trusted

Whoever can write a guest conf is trusted as much as the account the guest
runs as: the conf names the command that runs. Emulator configs the conf
points at (`config`) are read as untrusted input all the same, because they
are often copied from elsewhere and a future web page shows what is parsed
from them. So parsers must never raise, hang or run slowly on hostile input,
files are read only if they are regular files of bounded size
(`hardware.read_config`), and interface names are checked
(`network.valid_ifname`) before they touch sysfs or a command line.

## The web interface's security rules

Every change to `src/emuctl/web/` follows these. A console is a root login on
many guests, so the page is as powerful as root. Rules 1 to 8 are enforced in
one place, `web/app.py` (its docstring maps each rule to the code); keep it
that way rather than checking in individual handlers. `docs/web-security.md`
explains them to users.

1. The daemon listens on port 8600 on all addresses by default and serves
   HTTPS, with a self-signed certificate it makes on first start unless the
   admin names one. Plain HTTP only when `tls = off` is set explicitly; then
   the session cookie loses `Secure` and the daemon logs a warning.
2. Every request except the login page needs a session: a random 256-bit
   token in a cookie marked `HttpOnly`, `Secure` and `SameSite=Strict`, kept
   in memory, expiring after 12 hours idle (timed refreshes, sent with
   `X-Emuctl-Background: 1`, do not count) and 24 hours after login.
3. Every request that changes something, and every WebSocket upgrade, must
   carry an `Origin` header matching the daemon's own address; POSTs also
   carry the CSRF token.
4. A guest name from a URL is checked against the conf-name pattern and the
   list of real guests before it is used for anything.
5. No shell. Every subprocess gets an argument list.
6. A file viewer serves only paths named in the guest's conf, after resolving
   symlinks, and never anything under `/etc/emuctl/env/`.
7. Login attempts are limited: five failures, then a growing delay, per
   address and per user name, checked and charged with nothing awaited in
   between.
8. Responses carry a Content-Security-Policy allowing only the daemon's own
   origin.
9. Every action is written to the task log with the user's name.
10. Each rule has at least one test that tries to break it.

In the page's JavaScript: build the page with `dom.js`'s `h()` and
`textContent`, never `innerHTML`, since guest names, console text and log
lines are untrusted. No inline scripts (the CSP forbids them). The CSP allows
inline styles only because xterm.js needs them; our own code still uses CSS
classes, and `el.style` for a computed size, not `style=` attributes.

The `web-security-check` skill walks a diff against these.

## Skills

Step-by-step guides in `.claude/skills/<name>/SKILL.md`. Agents that support
skills load them automatically; people can read them as checklists.

| Skill | Use it to |
|---|---|
| `dev-host` | start a throwaway host with fake guests and try a change by hand |
| `add-emulator-kind` | support a new emulator: docs page, example conf, and later a hardware parser |
| `add-api-route` | add a route to the web API with its session check, test and docs |
| `web-security-check` | review a diff against the web security rules |
| `release` | prepare a release commit, stopping before the tag |

**Keep them current.** These files describe how the project works today. A
change that alters a process one of them describes (a new test command, a new
file every route needs, a new rule) updates the skill and this file in the
same commit. A skill that is wrong is worse than none.
