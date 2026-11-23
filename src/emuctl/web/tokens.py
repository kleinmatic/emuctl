# SPDX-License-Identifier: GPL-2.0-only
"""API tokens: read-only access for scripts and monitoring.

A token is sent as `Authorization: Bearer <token>` and reads status only:
GET requests to the routes app.py marks @token_ok (guests, host, numbers,
/metrics). It cannot read logs, notes, task output or a screen, and can
start, stop or type at nothing. The tokens file has one line per token:

    name:sha256 of the token:created (Unix time)

Only the hash is kept; the token itself is shown once, when it is made. A
token is 256 random bits, so a fast hash is enough: nobody can guess one.
"""
import hashlib
import os
import re
import secrets
import time
from dataclasses import dataclass

from . import auth

NAME_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}")
PREFIX = "emuctl_"          # so a token is recognisable in a config file or a leak scan


@dataclass(frozen=True)
class Token:
    name: str
    hash: str
    created: int


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def parse_tokens(text):
    out = {}
    for line in text.splitlines():
        parts = line.strip().split(":")
        if (len(parts) == 3 and NAME_PATTERN.fullmatch(parts[0])
                and re.fullmatch(r"[0-9a-f]{64}", parts[1]) and parts[2].isdigit()):
            out[parts[0]] = Token(parts[0], parts[1], int(parts[2]))
    return out


def _line(t):
    return f"{t.name}:{t.hash}:{t.created}"


def add_token(path, name, clock=time.time):
    """Make a token called `name` and return it. The file keeps only its hash."""
    if not NAME_PATTERN.fullmatch(name):
        raise auth.AuthError("a token name is letters, digits, '_', '.' or '-', "
                             "up to 32 characters")
    token = PREFIX + secrets.token_urlsafe(32)
    with auth._update(path, parse=parse_tokens, line=_line) as tokens:
        if name in tokens:
            raise auth.AuthError(f"there is already a token called {name}; revoke it first")
        tokens[name] = Token(name, digest(token), int(clock()))
    return token


def revoke_token(path, name):
    with auth._update(path, parse=parse_tokens, line=_line) as tokens:
        if name not in tokens:
            raise auth.AuthError(f"no such token: {name}")
        del tokens[name]


def list_tokens(path):
    """The tokens in `path`; AuthError if it exists but cannot be read."""
    return list(auth.read_users(path, strict=True, parse=parse_tokens).values())


class Tokens:
    """The tokens file, read again whenever it changes."""

    def __init__(self, path):
        self.path = path
        self._stamp = None
        self._by_hash = {}

    def check(self, token):
        """The Token for this bearer token, or None."""
        if not isinstance(token, str) or not token.startswith(PREFIX) or len(token) > 200:
            return None
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            stamp = None
        if stamp != self._stamp:
            found = auth.read_users(self.path, parse=parse_tokens) if stamp else {}
            self._by_hash = {t.hash: t for t in found.values()}
            self._stamp = stamp
        return self._by_hash.get(digest(token))
