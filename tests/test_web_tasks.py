# SPDX-License-Identifier: GPL-2.0-only
"""web/tasks.py and the action routes: start and stop from the page."""
import asyncio
import json
import os
import sys

import pytest

if sys.version_info < (3, 11):
    pytest.skip("the web interface needs Python 3.11", allow_module_level=True)
pytest.importorskip("aiohttp")
pytest.importorskip("pytest_aiohttp")

from test_cli import add_guest  # noqa: E402
from test_web_app import PASSWORD, session  # noqa: E402,F401

from emuctl import actions  # noqa: E402
from emuctl.web import app as webapp  # noqa: E402
from emuctl.web import auth  # noqa: E402
from emuctl.web.conf import WebConf  # noqa: E402
from emuctl.web.sampler import Sampler  # noqa: E402
from emuctl.web.tasks import Tasks  # noqa: E402

FAKE_EMUCTL = """import sys, time
args = sys.argv[1:]
print("ran:", " ".join(args))
if "slow" in args[1]:
    time.sleep(float(args[1].split("-")[1]))
if args[1] == "broken":
    print("emuctl: it broke", file=sys.stderr)
    sys.exit(3)
"""


@pytest.fixture
def fake_cmd(tmp_path):
    p = tmp_path / "fake_emuctl.py"
    p.write_text(FAKE_EMUCTL)
    return [sys.executable, str(p)]


def allow(monkeypatch, message=""):
    monkeypatch.setattr(actions, "plan_start",
                        lambda s, n: actions.Plan(True, message=message, steps=[["x"]]))
    monkeypatch.setattr(actions, "plan_stop",
                        lambda s, n, mode: actions.Plan(True, message=message, steps=[["x"]],
                                                        power_cut=mode != "clean"))


async def finished(tasks, t, timeout=10):
    for _ in range(int(timeout / 0.05)):
        if t.result != "running":
            return t
        await asyncio.sleep(0.05)
    raise AssertionError(f"task still running: {t}")


async def test_a_task_runs_emuctl_and_logs_it(settings, tmp_path, fake_cmd, monkeypatch):
    allow(monkeypatch, "for the guest it is a power cut")
    events = []
    log = tmp_path / "logs" / "tasks.log"
    tasks = Tasks(settings, str(log), lambda e, p: events.append((e, p["result"])),
                  command=fake_cmd)
    t = await tasks.submit("alice", "g1", "stop-now", confirmed=True)
    assert t.result == "running" and tasks.running["g1"] is t
    await finished(tasks, t)
    assert t.result == "ok" and t.rc == 0
    assert t.output == ["for the guest it is a power cut", "ran: stop g1 --now"]
    assert events == [("task", "running"), ("task", "ok")]
    line = json.loads(log.read_text().splitlines()[-1])
    assert (line["user"], line["guest"], line["action"], line["result"]) == \
        ("alice", "g1", "stop-now", "ok")
    assert "g1" not in tasks.running
    assert tasks.list()[0]["label"] == "Stop now" and "output" not in tasks.list()[0]


async def test_failures_and_refusals(settings, tmp_path, fake_cmd, monkeypatch):
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=fake_cmd)
    monkeypatch.setattr(actions, "plan_start",
                        lambda s, n: actions.Plan(False, 2, "REFUSING to start g1: twin is up"))
    t = await tasks.submit("alice", "g1", "start")
    assert t.result == "refused" and "twin" in t.message and t.ended
    monkeypatch.setattr(actions, "plan_start", lambda s, n: actions.Plan(False, 0, "already ready"))
    assert (await tasks.submit("alice", "g1", "start")).result == "skipped"

    def boom(s, n):
        raise RuntimeError("no conf")
    monkeypatch.setattr(actions, "plan_start", boom)
    assert "no conf" in (await tasks.submit("alice", "g1", "start")).message
    allow(monkeypatch)
    t = await finished(tasks, await tasks.submit("alice", "broken", "start"))
    assert t.result == "failed" and t.rc == 3 and t.message == "emuctl: it broke"
    with pytest.raises(ValueError):
        await tasks.submit("alice", "g1", "reboot")


async def test_one_task_per_guest_but_stop_now_cuts_in(settings, tmp_path, fake_cmd, monkeypatch):
    allow(monkeypatch)
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=fake_cmd)
    first = await tasks.submit("alice", "slow-1.0", "shutdown")
    second = await tasks.submit("bob", "slow-1.0", "start")
    assert second.result == "refused" and "shutdown by alice" in second.message
    third = await tasks.submit("bob", "slow-1.0", "stop-now", confirmed=True)
    fourth = await tasks.submit("bob", "slow-1.0", "stop-now", confirmed=True)
    assert fourth.result == "refused"
    assert third.result == "running"
    other = await tasks.submit("bob", "other", "start")         # other guests are free
    await finished(tasks, other)
    await finished(tasks, third)
    await finished(tasks, first)
    assert first.result == third.result == "ok"
    assert (await tasks.submit("bob", "slow-0", "start")).result == "running"


async def test_note_and_history_limit(settings, tmp_path):
    tasks = Tasks(settings, str(tmp_path / "t.log"), keep=3)
    for i in range(5):
        tasks.note("alice", "g1", "console", f"took the keyboard {i}")
    assert [t["message"] for t in tasks.list()] == [f"took the keyboard {i}" for i in (4, 3, 2)]
    assert tasks.get(1) is None and tasks.get(5).result == "note"
    assert len((tmp_path / "t.log").read_text().splitlines()) == 5


async def test_unwritable_log_is_not_fatal(settings, tmp_path, caplog):
    (tmp_path / "file").write_text("")
    tasks = Tasks(settings, str(tmp_path / "file" / "t.log"))
    tasks.note("alice", "g1", "console", "x")
    tasks.note("alice", "g1", "console", "y")
    assert caplog.text.count("cannot write the task log") == 1


async def test_missing_program(settings, tmp_path, monkeypatch):
    allow(monkeypatch)
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=["/nonexistent/emuctl"])
    t = await finished(tasks, await tasks.submit("alice", "g1", "start"))
    assert t.result == "failed" and "could not run" in t.message


# --- the routes -----------------------------------------------------------------

@pytest.fixture
async def client(aiohttp_client, settings, tmp_path, monkeypatch, fake_cmd):
    monkeypatch.setattr(auth, "LOG2_N", 10)
    add_guest(settings, tmp_path, "g1")
    users = str(tmp_path / "users")
    auth.set_password(users, "alice", PASSWORD)
    conf = WebConf(tls=False, users_file=users, task_log=str(tmp_path / "tasks.log"))
    app = webapp.make_app(settings, conf, sampler=Sampler(settings, probe=lambda *a, **k: False),
                          background=False)
    app[webapp.TASKS].command = fake_cmd
    c = await aiohttp_client(app)
    c.origin = f"http://{c.host}:{c.port}"
    return c


async def test_start_and_stop_routes(client, monkeypatch):
    allow(monkeypatch)
    h = await session(client)
    r = await client.post("/api/guests/g1/start", headers=h)
    t = await r.json()
    assert t["user"] == "alice" and t["action"] == "start" and t["guest"] == "g1"
    tasks = client.app[webapp.TASKS]
    await finished(tasks, tasks.get(t["id"]))
    r = await client.post("/api/guests/g1/stop", json={"mode": "now", "power_cut": True},
                          headers=h)
    t2 = await r.json()
    await finished(tasks, tasks.get(t2["id"]))
    got = await (await client.get(f"/api/tasks/{t2['id']}", headers=h)).json()
    assert got["output"] == ["ran: stop g1 --now"] and got["result"] == "ok"
    listed = await (await client.get("/api/tasks", headers=h)).json()
    assert [x["id"] for x in listed] == [t2["id"], t["id"]]
    for bad in ({"mode": "reboot"}, {"mode": 1}, {}):
        assert (await client.post("/api/guests/g1/stop", json=bad, headers=h)).status == 400
    assert (await client.get("/api/tasks/999", headers=h)).status == 404
    assert (await client.get("/api/tasks/x", headers=h)).status == 404


async def test_action_routes_are_protected(client, monkeypatch):
    allow(monkeypatch)
    h = await session(client)
    no_csrf = {k: v for k, v in h.items() if k != webapp.CSRF_HEADER}
    for path, body in (("/api/guests/g1/start", None), ("/api/guests/g1/stop", {"mode": "now"})):
        assert (await client.post(path, json=body)).status == 403          # no Origin
        assert (await client.post(path, json=body, headers={"Origin": client.origin})).status == 401
        assert (await client.post(path, json=body, headers=no_csrf)).status == 403
        assert (await client.post(path, json=body, headers=dict(h, Origin="https://evil.example"))
                ).status == 403
        assert (await client.post(path.replace("g1", "nope"), json=body, headers=h)).status == 404
    assert client.app[webapp.TASKS].recent == []                       # nothing ran
    assert (await client.get("/api/tasks")).status == 401


async def test_detail_carries_the_stop_decisions(client):
    h = await session(client)
    g = await (await client.get("/api/guests/g1", headers=h)).json()
    assert set(g["stop"]) == {"clean", "force", "now"}
    assert g["stop"]["now"]["allowed"] is True


def test_task_log_path_default():
    assert WebConf().task_log == "/var/log/emuctl/web-tasks.log"
    assert os.path.isabs(WebConf().task_log)


async def test_an_unconfirmed_power_cut_is_refused(settings, tmp_path, fake_cmd, monkeypatch):
    allow(monkeypatch, "for the guest it is a power cut")
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=fake_cmd)
    t = await tasks.submit("alice", "g1", "stop-now")
    assert t.result == "refused" and "Not confirmed" in t.message
    assert "g1" not in tasks.running


async def test_force_without_a_cut_runs_without_force(settings, tmp_path, fake_cmd, monkeypatch):
    """Confirmed nothing because no cut was planned: the command may not cut either."""
    monkeypatch.setattr(actions, "plan_stop",
                        lambda s, n, mode: actions.Plan(True, steps=[["x"]], power_cut=False))
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=fake_cmd)
    t = await finished(tasks, await tasks.submit("alice", "g1", "stop"))
    assert t.output == ["ran: stop g1"]
    t = await finished(tasks, await tasks.submit("alice", "g1", "stop", confirmed=True))
    assert t.output == ["ran: stop g1 --force"]


async def test_concurrent_submits_claim_the_guest_first(settings, tmp_path, fake_cmd, monkeypatch):
    allow(monkeypatch)
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=fake_cmd)
    ts = await asyncio.gather(*[tasks.submit("alice", "slow-0.5", a)
                                for a in ("start", "start", "shutdown")])
    assert sorted(t.result for t in ts) == ["refused", "refused", "running"]
    for t in ts:
        await finished(tasks, t)


async def test_a_huge_line_does_not_wedge_the_guest(settings, tmp_path, monkeypatch):
    allow(monkeypatch)
    cmd = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 300000); print(); "
           "print('the real error'); sys.exit(1)"]
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=cmd)
    t = await finished(tasks, await tasks.submit("alice", "g1", "start"))
    assert t.result == "failed" and t.message == "the real error"
    assert "g1" not in tasks.running


async def test_a_hung_command_is_killed(settings, tmp_path, monkeypatch):
    allow(monkeypatch)
    import emuctl.web.tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "DEADLINE", 0.5)
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=cmd)
    t = await finished(tasks, await tasks.submit("alice", "g1", "start"))
    assert t.result == "failed" and "gave up" in t.message


async def test_close_waits_for_running_tasks(settings, tmp_path, fake_cmd, monkeypatch):
    allow(monkeypatch)
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=fake_cmd)
    t = await tasks.submit("alice", "slow-0.5", "start")
    await tasks.close()
    assert t.result == "ok"
    lines = [json.loads(x) for x in (tmp_path / "t.log").read_text().splitlines()]
    assert [x["result"] for x in lines] == ["running", "ok"]       # logged at start too


async def test_flood_keeps_the_last_line(settings, tmp_path, monkeypatch):
    allow(monkeypatch)
    cmd = [sys.executable, "-c", "for i in range(1000): print('line', i)\nprint('the end')"]
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=cmd)
    t = await finished(tasks, await tasks.submit("alice", "g1", "start"))
    assert t.message == "the end" and len(t.output) == 201 and t.output[-1] == "…"


async def test_running_tasks_are_never_forgotten(settings, tmp_path, fake_cmd, monkeypatch):
    allow(monkeypatch)
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=fake_cmd, keep=3)
    t = await tasks.submit("alice", "slow-0.5", "start")
    for i in range(5):
        tasks.note("alice", "g1", "console", f"note {i}")
    assert tasks.get(t.id) is t
    await finished(tasks, t)


async def test_a_running_stop_now_keeps_the_guest_busy(settings, tmp_path, fake_cmd, monkeypatch):
    allow(monkeypatch)
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=fake_cmd)
    first = await tasks.submit("alice", "slow-0.3", "shutdown")
    cut = await tasks.submit("alice", "slow-0.3", "stop-now", confirmed=True)
    await finished(tasks, first)
    tasks.cutting["slow-0.3"] = cut if cut.result == "running" else cut
    if cut.result == "running":
        assert (await tasks.submit("bob", "slow-0.3", "start")).result == "refused"
    await finished(tasks, cut)


async def test_a_confirmation_from_an_earlier_run_is_refused(settings, tmp_path, fake_cmd,
                                                              monkeypatch):
    from emuctl.state import write_state
    allow(monkeypatch, "for the guest it is a power cut")
    write_state(settings.run_dir, "g1", state="ready", started_at=2000)
    tasks = Tasks(settings, str(tmp_path / "t.log"), command=fake_cmd)
    t = await tasks.submit("alice", "g1", "stop-now", confirmed=True, run=1000)
    assert t.result == "refused" and "restarted" in t.message
    t = await finished(tasks, await tasks.submit("alice", "g1", "stop-now", confirmed=True,
                                                 run=2000))
    assert t.result == "ok"
