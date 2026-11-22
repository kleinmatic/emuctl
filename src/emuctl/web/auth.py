# SPDX-License-Identifier: GPL-2.0-only
"""Users, sessions and the login limit for the web interface.

Standard library only. The users file has one line per user:

    name:role:scrypt$<log2 n>$<r>$<p>$<salt>$<hash>

Every user is an admin until roles arrive; the role is in the file already so
adding roles changes no file format.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass, field

USER_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}")
ROLES = ("admin",)
# scrypt cost: about 0.1 s and 32 MB per check on a small server.
LOG2_N, R, P = 15, 8, 1
MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD = 8


class AuthError(Exception):
    pass


def _b64(b):
    return base64.b64encode(b).decode()


def hash_password(password, salt=None, log2_n=LOG2_N):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2 ** log2_n, r=R, p=P, maxmem=MAXMEM,
                       dklen=32)
    return f"scrypt${log2_n}${R}${P}${_b64(salt)}${_b64(h)}"


def verify_password(password, stored):
    """True if `password` matches `stored`. Never raises for a malformed hash."""
    try:
        kind, log2_n, r, p, salt, want = stored.split("$")
        if kind != "scrypt" or not (10 <= int(log2_n) <= 20) or int(r) > 32 or int(p) > 4:
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** int(log2_n),
                             r=int(r), p=int(p), maxmem=MAXMEM, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


# Checked when the user does not exist, so a wrong name costs as long as a wrong password.
_DUMMY = hash_password("not a password", salt=b"\0" * 16)


@dataclass(frozen=True)
class User:
    name: str
    role: str
    hash: str


def parse_users(text):
    users = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3 or not USER_PATTERN.fullmatch(parts[0]) or parts[1] not in ROLES:
            continue                        # a bad line locks nobody else out
        users[parts[0]] = User(*parts)
    return users


def read_users(path):
    try:
        with open(path) as fh:
            return parse_users(fh.read())
    except FileNotFoundError:
        return {}


def write_users(path, users):
    """Replace the users file, mode 0600, keeping its owner.

    Run as root for a daemon account, the new file takes the owner of the old
    one, or of its directory, so the daemon can still read it.
    """
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    ref = path if os.path.exists(path) else d
    st = os.stat(ref)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".users.")
    try:
        with os.fdopen(fd, "w") as fh:
            for u in users.values():
                fh.write(f"{u.name}:{u.role}:{u.hash}\n")
        os.chmod(tmp, 0o600)
        if os.geteuid() == 0:
            os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_password(path, name, password, role="admin"):
    if not USER_PATTERN.fullmatch(name):
        raise AuthError("a user name is letters, digits, '_', '.' or '-', up to 32 characters")
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"the password must be at least {MIN_PASSWORD} characters")
    users = read_users(path)
    users[name] = User(name, role, hash_password(password))
    write_users(path, users)


def delete_user(path, name):
    users = read_users(path)
    if name not in users:
        raise AuthError(f"no such user: {name}")
    del users[name]
    write_users(path, users)


class Users:
    """The users file, read again whenever it changes."""

    def __init__(self, path):
        self.path = path
        self._stamp = None
        self._users = {}

    def all(self):
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            stamp = None
        if stamp != self._stamp:
            self._users = read_users(self.path) if stamp else {}
            self._stamp = stamp
        return self._users

    def get(self, name):
        return self.all().get(name)

    def check(self, name, password):
        """The User if the password is right, else None. Takes the same time
        whether or not the user exists."""
        user = self.get(name) if isinstance(name, str) else None
        ok = verify_password(password, user.hash if user else _DUMMY)
        return user if (ok and user) else None


IDLE = 12 * 3600


@dataclass
class Session:
    token: str
    user: str
    csrf: str
    created: float
    seen: float


class Sessions:
    """Sessions in memory. A restart of the daemon logs everyone out."""

    def __init__(self, users, idle=IDLE, clock=time.time):
        self.users, self.idle, self.clock = users, idle, clock
        self._by_token = {}
        self.on_end = []            # callbacks(token), for open streams

    def new(self, user):
        now = self.clock()
        s = Session(secrets.token_urlsafe(32), user, secrets.token_urlsafe(32), now, now)
        self._by_token[s.token] = s
        self._prune(now)
        return s

    def get(self, token, touch=True):
        """The live session for `token`, or None. A session ends after IDLE
        seconds without a request, or when its user is removed."""
        s = self._by_token.get(token) if isinstance(token, str) else None
        if s is None:
            return None
        now = self.clock()
        if now - s.seen > self.idle or self.users.get(s.user) is None:
            self.end(token)
            return None
        if touch:
            s.seen = now
        return s

    def end(self, token):
        if self._by_token.pop(token, None) is not None:
            for cb in list(self.on_end):
                cb(token)

    def _prune(self, now):
        for tok, s in list(self._by_token.items()):
            if now - s.seen > self.idle:
                self.end(tok)


@dataclass
class _Failures:
    count: int = 0
    last: float = 0.0


@dataclass
class LoginLimiter:
    """Five failures free per address, then a wait that doubles each time,
    up to MAX_WAIT seconds. A success clears the address."""
    free: int = 5
    max_wait: float = 300
    clock: object = time.time
    _seen: dict = field(default_factory=dict)

    def wait(self, addr):
        """Seconds before `addr` may try again; 0 means now."""
        f = self._seen.get(addr)
        if not f or f.count < self.free:
            return 0
        delay = min(2 ** (f.count - self.free), self.max_wait)
        return max(0, f.last + delay - self.clock())

    def failed(self, addr):
        now = self.clock()
        f = self._seen.setdefault(addr, _Failures())
        f.count += 1
        f.last = now
        if len(self._seen) > 10000:             # forget the oldest, never the newest
            for a, old in sorted(self._seen.items(), key=lambda kv: kv[1].last)[:5000]:
                del self._seen[a]

    def succeeded(self, addr):
        self._seen.pop(addr, None)
