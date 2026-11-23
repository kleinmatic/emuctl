# SPDX-License-Identifier: GPL-2.0-only
"""Users, sessions and the login limit for the web interface.

Standard library only. The users file has one line per user:

    name:role:scrypt$<log2 n>$<r>$<p>$<salt>$<hash>

Every user is an admin until roles arrive; the role is in the file already so
adding roles changes no file format.
"""
import base64
import contextlib
import fcntl
import grp
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("emuctl.web")
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


def read_users(path, strict=False, parse=None):
    """The users in `path`. A file that cannot be read gives no users, so
    nobody can log in, rather than an error on every request. With strict
    (when about to rewrite it), anything but a missing file is an error."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        if strict:
            raise AuthError(f"cannot read {path}: {exc.strerror or exc}; not changing it")
        log.warning("cannot read %s: %s", path, exc)
        return {}
    with os.fdopen(fd) as fh:
        return (parse or parse_users)(fh.read())


@contextlib.contextmanager
def _as_owner_of(d):
    """Run as root, act as the owner of directory `d` for the duration.

    The state directory belongs to the daemon's account. If root worked in it
    directly, that account could swap a file for a symlink between root's
    steps and have root change some other file. As the directory's owner,
    root can do nothing there the account could not do itself.
    """
    if os.geteuid() != 0:
        yield
        return
    st = os.lstat(d)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise AuthError(f"{d} is not a directory")
    egid = os.getegid()
    try:
        os.setegid(st.st_gid)
        os.seteuid(st.st_uid)
        yield
    finally:
        os.seteuid(0)
        os.setegid(egid)


def _check_dir(path):
    d = os.path.dirname(path) or "."
    if not os.path.isdir(d):
        if os.geteuid() == 0:
            # Made by root it would be root's, and the daemon could not read it.
            raise AuthError(f"{d} does not exist; start emuctl-web once (it makes the "
                            f"directory), then run this again")
        os.makedirs(d)
    return d


DAEMON_GROUP = "emuctl-web"


def _daemon_gid():
    try:
        return grp.getgrnam(DAEMON_GROUP).gr_gid
    except KeyError:
        return None


def write_users(path, users, line=lambda u: f"{u.name}:{u.role}:{u.hash}"):
    """Replace the users file (or the tokens file, with `line`). Call inside _update().

    Written by root in a root-owned directory (the default, /etc/emuctl), it is
    root's, mode 0640, group emuctl-web: the daemon can read it but not change
    it. Anywhere else it is the writer's own, mode 0600.
    """
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".emuctl-web.")
    try:
        gid = _daemon_gid() if os.geteuid() == 0 else None
        if gid is not None:
            os.fchown(fd, 0, gid)
            os.fchmod(fd, 0o640)
        else:
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            for u in users.values():
                fh.write(line(u) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def _update(path, parse=None, line=None):
    """Read, change and write the users file under a lock, as its directory's
    owner. `parse` and `line` read and write another file the same way."""
    d = _check_dir(path)
    root_dir = os.lstat(d).st_uid == 0
    with (contextlib.nullcontext() if root_dir else _as_owner_of(d)):
        lock = os.open(os.path.join(d, ".users.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                       0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            users = read_users(path, strict=True, parse=parse or parse_users)
            yield users
            if line:
                write_users(path, users, line)
            else:
                write_users(path, users)
        finally:
            os.close(lock)


def set_password(path, name, password, role="admin"):
    if not USER_PATTERN.fullmatch(name):
        raise AuthError("a user name is letters, digits, '_', '.' or '-', up to 32 characters")
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"the password must be at least {MIN_PASSWORD} characters")
    h = hash_password(password)
    with _update(path) as users:
        users[name] = User(name, role, h)


def delete_user(path, name):
    with _update(path) as users:
        if name not in users:
            raise AuthError(f"no such user: {name}")
        del users[name]


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
MAX_AGE = 24 * 3600         # however busy, a session ends a day after logging in


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Session:
    token: str                  # the session's id: SHA-256 of its cookie, never the cookie
    user: str
    csrf: str
    created: float
    seen: float
    hash: str = ""              # SHA-256 of the password hash at login: a new password ends it
    cookie: str = field(default="", repr=False)   # only until the login reply sets it


def _valid_session(sid, v, now):
    hexed = re.compile(r"[0-9a-f]{64}")
    return (isinstance(sid, str) and hexed.fullmatch(sid) is not None
            and isinstance(v, dict) and isinstance(v.get("user"), str)
            and isinstance(v.get("csrf"), str) and len(v["csrf"]) >= 32
            and isinstance(v.get("hash"), str) and hexed.fullmatch(v["hash"]) is not None
            and all(isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool)
                    and 0 < v[k] <= now + 60 for k in ("created", "seen")))


class Sessions:
    """Sessions, kept across restarts of the daemon in `path` if given.

    The browser holds a random 256-bit cookie; the daemon knows a session only
    by the cookie's SHA-256, in memory and in the file. So the file (0600, in
    the daemon's own state directory) cannot be turned back into cookies, and
    a copy of it logs nobody in."""

    def __init__(self, users, idle=IDLE, max_age=MAX_AGE, clock=time.time, path=None):
        self.users, self.idle, self.max_age, self.clock = users, idle, max_age, clock
        self.path = path
        self._by_token = {}         # session id -> Session
        self.on_end = []            # callbacks(session id), for open streams
        self._save_lock = threading.Lock()

    def new(self, user):
        now = self.clock()
        u = self.users.get(user)
        cookie = secrets.token_urlsafe(32)
        s = Session(_digest(cookie), user, secrets.token_urlsafe(32), now, now,
                    _digest(getattr(u, "hash", "")), cookie)
        self._by_token[s.token] = s
        self._prune(now, save=False)
        self.save()
        return s

    def get(self, cookie, touch=True):
        """The live session for a browser's cookie, or None."""
        if not isinstance(cookie, str) or len(cookie) > 200:
            return None
        return self.get_id(_digest(cookie), touch)

    def get_id(self, sid, touch=True):
        """The live session with this id, or None. A session ends after IDLE
        seconds without a request the user made (the page's own refreshes pass
        touch=False), MAX_AGE seconds after login, when its user is removed, or
        when the user's password changes."""
        s = self._by_token.get(sid) if isinstance(sid, str) else None
        if s is None:
            return None
        now = self.clock()
        u = self.users.get(s.user)
        if (now - s.seen > self.idle or now - s.created > self.max_age or u is None
                or not hmac.compare_digest(_digest(getattr(u, "hash", "")), s.hash)):
            self.end(sid)
            return None
        if touch:
            s.seen = now
        return s

    def end(self, sid, save=True):
        if self._by_token.pop(sid, None) is not None:
            for cb in list(self.on_end):
                cb(sid)
            if save:
                self.save()

    def _prune(self, now, save=True):
        gone = [sid for sid, s in self._by_token.items()
                if now - s.seen > self.idle or now - s.created > self.max_age]
        for sid in gone:
            self.end(sid, save=False)
        if gone and save:
            self.save()

    # --- across restarts ----------------------------------------------------------

    def save(self):
        """Write the sessions to `path`, replacing it whole. Called on login and
        logout, and from time to time for the idle clocks."""
        if not self.path:
            return
        with self._save_lock:
            # The snapshot inside the lock, so an older one can never be
            # written over a newer one (a logout undone).
            body = json.dumps({"version": 1, "sessions": {
                sid: {"user": s.user, "csrf": s.csrf, "created": s.created, "seen": s.seen,
                      "hash": s.hash} for sid, s in list(self._by_token.items())}})
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".",
                                           prefix=".sessions.")
                with os.fdopen(fd, "w") as fh:
                    fh.write(body)
                os.replace(tmp, self.path)
                tmp = None
            except OSError as exc:
                log.warning("could not save the sessions to %s: %s; removing it, so a "
                            "restart logs everyone out rather than bring back an ended "
                            "session", self.path, exc)
                try:
                    os.unlink(self.path)
                except OSError:
                    pass
            finally:
                if tmp:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

    def load(self):
        """Read the sessions back, keeping those still within their limits."""
        if not self.path:
            return
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as fh:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError("not a regular file")
                saved = json.loads(fh.read(4 * 1024 * 1024))
            found = saved["sessions"] if saved.get("version") == 1 else {}
            if not isinstance(found, dict):
                raise TypeError("sessions is not an object")
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError, AttributeError,
                RecursionError) as exc:
            log.warning("ignored the saved sessions in %s: %s", self.path, exc)
            return
        now = self.clock()
        for sid, v in found.items():
            if (_valid_session(sid, v, now) and now - v["seen"] <= self.idle
                    and now - v["created"] <= self.max_age):
                self._by_token[sid] = Session(sid, v["user"], v["csrf"], v["created"],
                                              v["seen"], v["hash"])


def _addr_key(addr):
    """One key per IPv4 address, and per /64 for IPv6, since anyone with one
    IPv6 address usually has the whole /64."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return addr
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


@dataclass
class _Failures:
    count: int = 0
    last: float = 0.0


@dataclass
class LoginLimiter:
    """Counts failed logins per address and per user name.

    Five failures are free, then each further try waits twice as long as the
    last, up to max_wait. A count is forgotten `forget` seconds after its last
    failure. A good login clears its user name's count, never the address's,
    so a valid account cannot be used to reset the limit for guessing others.
    Across everyone, more than `global_rate` failures a minute makes every
    login wait.
    """
    free: int = 5
    max_wait: float = 300
    forget: float = 900
    global_rate: int = 60
    clock: object = time.time
    _seen: dict = field(default_factory=dict)
    _recent: list = field(default_factory=list)
    _good: set = field(default_factory=set)

    def _wait_for(self, key, now):
        f = self._seen.get(key)
        if not f or now - f.last > self.forget:
            return 0
        if f.count < self.free:
            return 0
        delay = min(2 ** (f.count - self.free), self.max_wait)
        return max(0, f.last + delay - now)

    def wait(self, addr, user=None):
        """Seconds before this address (and user name) may try again; 0 means now."""
        now = self.clock()
        self._recent = [r for r in self._recent if now - r[0] < 60]
        busy = 0
        if len(self._recent) >= self.global_rate:
            busy = self._recent[0][0] + 60 - now
        keys = [("addr", _addr_key(addr))]
        # The per-name count slows guessing from many addresses, but must not
        # lock the real user out: it does not apply where that name has
        # logged in before.
        if user and (_addr_key(addr), user) not in self._good:
            keys.append(("user", user))
        return max([busy] + [self._wait_for(k, now) for k in keys])

    def failed(self, addr, user=None):
        """Charge a failure. Returns a token for succeeded() to give it back."""
        now = self.clock()
        token = object()
        self._recent.append((now, token))
        for key in [("addr", _addr_key(addr))] + ([("user", user)] if user else []):
            f = self._seen.get(key)
            if not f or now - f.last > self.forget:
                f = self._seen[key] = _Failures()
            f.count += 1
            f.last = now
        if len(self._seen) > 10000:
            for k, old in list(self._seen.items()):
                if now - old.last > self.forget:
                    del self._seen[k]
        return token

    def succeeded(self, addr, user=None, token=None):
        """A good login: give back the failure charged for this attempt (by
        its token), and clear the user name."""
        f = self._seen.get(("addr", _addr_key(addr)))
        if f and f.count > 0:
            f.count -= 1
        self._recent = [r for r in self._recent if r[1] is not token]
        if user:
            if len(self._good) > 10000:
                self._good.clear()
            self._good.add((_addr_key(addr), user))
        if user:
            self._seen.pop(("user", user), None)
