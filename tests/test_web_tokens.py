# SPDX-License-Identifier: GPL-2.0-only
"""API tokens and /metrics: read-only access for scripts and monitoring."""
import sys
import types

import pytest

if sys.version_info < (3, 11):
    pytest.skip("the web interface needs Python 3.11", allow_module_level=True)
pytest.importorskip("aiohttp")
pytest.importorskip("pytest_aiohttp")

from test_web_app import client, site  # noqa: E402,F401

from emuctl.web import app as webapp  # noqa: E402
from emuctl.web import auth, metrics, tokens  # noqa: E402
from emuctl.web import main as web_main  # noqa: E402


def test_add_list_revoke(tmp_path):
    path = str(tmp_path / "tokens")
    tok = tokens.add_token(path, "prometheus")
    assert tok.startswith("emuctl_") and len(tok) > 40
    assert tok not in (tmp_path / "tokens").read_text()          # only the hash is kept
    assert tokens.Tokens(path).check(tok).name == "prometheus"
    assert tokens.Tokens(path).check(tok + "x") is None
    with pytest.raises(auth.AuthError, match="already"):
        tokens.add_token(path, "prometheus")
    with pytest.raises(auth.AuthError, match="token name"):
        tokens.add_token(path, "no spaces")
    assert [t.name for t in tokens.list_tokens(path)] == ["prometheus"]
    tokens.revoke_token(path, "prometheus")
    assert tokens.Tokens(path).check(tok) is None
    with pytest.raises(auth.AuthError, match="no such token"):
        tokens.revoke_token(path, "prometheus")


def test_token_command(tmp_path, monkeypatch, capsys):
    wc = tmp_path / "web.conf"
    wc.write_text(f"state_dir = {tmp_path}\ntokens_file = {tmp_path}/tokens\n")
    monkeypatch.setenv("EMUCTL_WEB_CONF", str(wc))
    assert web_main.cmd_token(types.SimpleNamespace(token_action="list")) == 0
    assert "no API tokens" in capsys.readouterr().out
    assert web_main.cmd_token(types.SimpleNamespace(token_action="add", name="grafana")) == 0
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("emuctl_")
    assert tokens.Tokens(str(tmp_path / "tokens")).check(printed).name == "grafana"
    assert web_main.cmd_token(types.SimpleNamespace(token_action="list")) == 0
    assert "grafana" in capsys.readouterr().out
    assert web_main.cmd_token(types.SimpleNamespace(token_action="revoke", name="grafana")) == 0
    assert web_main.cmd_token(types.SimpleNamespace(token_action="revoke", name="grafana")) == 1


@pytest.fixture
def token(client, tmp_path):  # noqa: F811
    path = str(tmp_path / "tokens")
    client.app[webapp.TOKENS].path = path
    return {"Authorization": f"Bearer {tokens.add_token(path, 'scraper')}"}


async def test_a_token_reads(client, token):  # noqa: F811
    for path in ("/api/guests", "/api/guests/g1", "/api/host", "/api/energy", "/metrics",
                 "/api/guests/g1/usage?window=24h", "/api/guests/g1/hardware"):
        r = await client.get(path, headers=token)
        assert r.status == 200, path


async def test_a_token_reads_no_logs_notes_or_task_output(client, token):  # noqa: F811
    for path in ("/api/guests/g1/log", "/api/guests/g1/notes", "/api/guests/g1/check",
                 "/api/tasks", "/api/tasks/1", "/api/guests/g1/thumbnail"):
        assert (await client.get(path, headers=token)).status == 403, path


async def test_a_token_changes_nothing_and_opens_nothing(client, token):  # noqa: F811
    origin = {"Origin": client.origin, **token}
    assert (await client.post("/api/guests/g1/start", headers=origin)).status == 403
    assert (await client.post("/api/logout", headers=origin)).status == 403
    for path in ("/api/session", "/api/events", "/", "/api/guests/g1/console"):
        assert (await client.get(path, headers=token)).status == 403, path
    r = await client.get("/api/guests/g1/console", headers={
        **origin, "Upgrade": "websocket", "Connection": "Upgrade"})
    assert r.status == 403


async def test_a_wrong_token_is_refused(client, token):  # noqa: F811
    for bad in ("emuctl_nope", "nope", ""):
        r = await client.get("/api/guests", headers={"Authorization": f"Bearer {bad}"})
        assert r.status == 401
    assert (await client.get("/metrics")).status == 401        # no redirect to /login


async def test_metrics_text(client, token):  # noqa: F811
    client.app[webapp.SAMPLER].rows = [
        {"name": "g1", "state": "ready", "uptime": 60, "cpu_pct": 7.5, "cpu_seconds": 4.5,
         "mem_bytes": 1024, "locked": ""},
        {"name": "g2", "state": "down", "uptime": None, "locked": "copying"}]
    text = await (await client.get("/metrics", headers=token)).text()
    assert 'emuctl_guest_up{guest="g1"} 1' in text and 'emuctl_guest_up{guest="g2"} 0' in text
    assert 'emuctl_guest_state{guest="g2",state="down"} 1' in text
    assert 'emuctl_guest_cpu_seconds_total{guest="g1"} 4.5' in text
    assert 'emuctl_guest_locked{guest="g2"} 1' in text
    assert 'emuctl_guest_uptime_seconds{guest="g2"}' not in text


def test_an_unreadable_tokens_file_is_not_called_empty(tmp_path):
    import os
    if os.geteuid() == 0:
        pytest.skip("root reads any file")
    p = tmp_path / "tokens"
    tokens.add_token(str(p), "grafana")
    p.chmod(0)                                  # as for a user not in the daemon's group
    with pytest.raises(auth.AuthError, match="cannot read"):
        tokens.list_tokens(str(p))


def test_metric_labels_are_escaped():
    text = metrics.render([], 'v"1\\n')
    assert 'version="v\\"1\\\\n"' in text
