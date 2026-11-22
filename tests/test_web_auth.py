# SPDX-License-Identifier: GPL-2.0-only
"""web/conf.py, web/auth.py, web/tls.py and `emuctl web passwd`: standard library only."""
import io
import os
import shutil
import stat
import types

import pytest

from emuctl.web import auth, conf, tls
from emuctl.web import main as web_main

# --- conf -----------------------------------------------------------------------


def test_web_conf_defaults():
    c = conf.WebConf()
    assert (c.listen, c.port, c.tls) == ("0.0.0.0", 8600, True)
    assert c.self_signed and c.cert_path == "/var/lib/emuctl-web/web.crt"
    assert c.users_path == "/var/lib/emuctl-web/users"


def test_web_conf_values():
    c = conf.parse_web_conf("listen = 127.0.0.1\nport = 9000   # x\ntls = off\n"
                            "tls_cert = /etc/c.pem\ntls_key = /etc/k.pem\nusers_file = /u\n")
    assert (c.listen, c.port, c.tls, c.self_signed) == ("127.0.0.1", 9000, False, False)
    assert (c.cert_path, c.key_path, c.users_path) == ("/etc/c.pem", "/etc/k.pem", "/u")


@pytest.mark.parametrize("text,match", [
    ("port = 0\n", "port"), ("port = http\n", "port"), ("tls = maybe\n", "tls"),
    ("tls_cert = /c\n", "together"), ("users_file = rel\n", "absolute"),
    ("colour = blue\n", "unknown key"), ("just words\n", "key = value"),
])
def test_web_conf_bad(text, match):
    with pytest.raises(conf.WebConfError, match=match):
        conf.parse_web_conf(text)


def test_load_web_conf(tmp_path):
    p = tmp_path / "web.conf"
    with pytest.raises(conf.WebConfError, match="no such file"):
        conf.load_web_conf({"EMUCTL_WEB_CONF": str(p)})
    p.write_text("port = 8601\n")
    assert conf.load_web_conf({"EMUCTL_WEB_CONF": str(p)}).port == 8601
    assert isinstance(conf.load_web_conf({}), conf.WebConf)


# --- tls ------------------------------------------------------------------------

needs_openssl = pytest.mark.skipif(not shutil.which("openssl"), reason="no openssl")


@needs_openssl
def test_self_signed_certificate_made_once(tmp_path):
    c = conf.WebConf(state_dir=str(tmp_path / "state"))
    assert tls.ensure_cert(c, hostname="box.example") is True
    key_mode = stat.S_IMODE(os.stat(c.key_path).st_mode)
    assert key_mode == 0o600
    first = tls.fingerprint(c.cert_path)
    assert len(first.split(":")) == 32
    assert tls.ensure_cert(c) is False                  # reused, not remade
    assert tls.fingerprint(c.cert_path) == first
    tls.server_context(c.cert_path, c.key_path)


def test_named_certificate_must_exist(tmp_path):
    c = conf.WebConf(tls_cert=str(tmp_path / "c"), tls_key=str(tmp_path / "k"))
    with pytest.raises(tls.CertError, match="does not exist"):
        tls.ensure_cert(c)
    with pytest.raises(tls.CertError, match="cannot load"):
        tls.server_context(str(tmp_path / "c"), str(tmp_path / "k"))


def test_openssl_failure_leaves_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(tls.shutil, "which", lambda name: "/usr/bin/openssl")

    def fail(argv, **kw):
        open(argv[argv.index("-keyout") + 1], "w").close()
        return types.SimpleNamespace(returncode=1, stderr="bad things")
    with pytest.raises(tls.CertError, match="bad things"):
        tls.make_self_signed(str(tmp_path / "c"), str(tmp_path / "k"), "h", run=fail)
    assert os.listdir(tmp_path) == []
    monkeypatch.setattr(tls.shutil, "which", lambda name: None)
    with pytest.raises(tls.CertError, match="openssl program"):
        tls.make_self_signed(str(tmp_path / "c"), str(tmp_path / "k"), "h")


def test_certificate_names():
    assert tls.names_for("box.example.org") == ["box", "box.example.org", "box.local",
                                                "localhost"]


# --- passwords and users --------------------------------------------------------

FAST = 10           # log2 n for tests; the real cost is 15


def test_password_hash_round_trip():
    h = auth.hash_password("correct horse", log2_n=FAST)
    assert h.startswith("scrypt$10$")
    assert auth.verify_password("correct horse", h)
    assert not auth.verify_password("wrong horse", h)


@pytest.mark.parametrize("stored", ["", "plain", "md5$x$y$z$a$b", "scrypt$30$8$1$AA==$AA==",
                                    "scrypt$15$99$1$AA==$AA==", "scrypt$x$8$1$AA==$AA==",
                                    "scrypt$10$8$1$!!$AA=="])
def test_bad_hashes_never_match_or_raise(stored):
    assert auth.verify_password("anything", stored) is False


def test_users_file(tmp_path):
    p = str(tmp_path / "users")
    auth.set_password(p, "alice", "a long password")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    with open(p, "a") as fh:
        fh.write("# comment\nbad line\nmallory:god:x\n../x:admin:y\n")
    users = auth.read_users(p)
    assert list(users) == ["alice"] and users["alice"].role == "admin"
    auth.set_password(p, "bob", "another password")
    auth.delete_user(p, "alice")
    assert list(auth.read_users(p)) == ["bob"]
    with pytest.raises(auth.AuthError, match="no such user"):
        auth.delete_user(p, "alice")
    with pytest.raises(auth.AuthError, match="at least"):
        auth.set_password(p, "carol", "short")
    with pytest.raises(auth.AuthError, match="user name"):
        auth.set_password(p, "carol:admin", "a long password")
    assert auth.read_users(str(tmp_path / "missing")) == {}


def test_users_reload_and_check(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "LOG2_N", FAST)
    p = str(tmp_path / "users")
    users = auth.Users(p)
    assert users.all() == {}
    auth.set_password(p, "alice", "a long password")
    assert users.check("alice", "a long password").name == "alice"
    assert users.check("alice", "nope") is None
    assert users.check("nobody", "a long password") is None
    assert users.check(None, "x") is None
    auth.delete_user(p, "alice")
    assert users.get("alice") is None


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_sessions_expire_when_idle_and_when_user_goes():
    clock = Clock()
    present = {"alice": object()}
    users = types.SimpleNamespace(get=present.get)
    ended = []
    s = auth.Sessions(users, idle=100, clock=clock)
    s.on_end.append(ended.append)
    a = s.new("alice")
    assert len(a.token) >= 43 and a.csrf != a.token
    clock.t += 90
    assert s.get(a.token) is a                  # touched: idle restarts
    clock.t += 90
    assert s.get(a.token, touch=False) is a
    clock.t += 20
    assert s.get(a.token) is None and ended == [a.token]
    b = s.new("alice")
    del present["alice"]
    assert s.get(b.token) is None               # user removed: session gone
    assert s.get(None) is None and s.get("made-up") is None


def test_login_limiter():
    clock = Clock()
    lim = auth.LoginLimiter(clock=clock)
    for _ in range(5):
        assert lim.wait("a") == 0
        lim.failed("a")
    assert lim.wait("a") == 1 and lim.wait("b") == 0
    lim.failed("a")
    assert lim.wait("a") == 2
    clock.t += 2
    assert lim.wait("a") == 0
    for _ in range(20):
        lim.failed("a")
    assert lim.wait("a") == 300                 # capped
    lim.succeeded("a")
    assert lim.wait("a") == 0


def test_login_limiter_forgets_oldest():
    clock = Clock()
    lim = auth.LoginLimiter(clock=clock)
    for i in range(10001):
        clock.t += 1
        lim.failed(f"10.0.{i // 256}.{i % 256}")
    assert len(lim._seen) <= 5001 and "10.0.39.16" in lim._seen


# --- emuctl web passwd ----------------------------------------------------------

def test_passwd_command(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "LOG2_N", FAST)
    wc = tmp_path / "web.conf"
    wc.write_text(f"state_dir = {tmp_path}\n")
    monkeypatch.setenv("EMUCTL_WEB_CONF", str(wc))
    args = types.SimpleNamespace(user="alice", delete=False, stdin=True)
    monkeypatch.setattr("sys.stdin", io.StringIO("a long password\n"))
    assert web_main.cmd_passwd(args) == 0
    assert auth.Users(str(tmp_path / "users")).check("alice", "a long password")
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert web_main.cmd_passwd(args) == 1
    args.delete = True
    assert web_main.cmd_passwd(args) == 0
    assert web_main.cmd_passwd(args) == 1
    wc.write_text("port = x\n")
    assert web_main.cmd_passwd(args) == 2


def test_passwd_prompts_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "LOG2_N", FAST)
    monkeypatch.setenv("EMUCTL_WEB_CONF", str(tmp_path / "web.conf"))
    (tmp_path / "web.conf").write_text(f"state_dir = {tmp_path}\n")
    monkeypatch.setattr("sys.stdin", types.SimpleNamespace(isatty=lambda: True))
    answers = iter(["a long password", "a different one"])
    monkeypatch.setattr(web_main.getpass, "getpass", lambda prompt: next(answers))
    args = types.SimpleNamespace(user="alice", delete=False, stdin=False)
    assert web_main.cmd_passwd(args) == 1
    assert auth.read_users(str(tmp_path / "users")) == {}
