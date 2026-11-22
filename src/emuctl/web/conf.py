# SPDX-License-Identifier: GPL-2.0-only
"""The web daemon's settings: /etc/emuctl/web.conf.

Every key is optional. The file has the same `key = value` format as
emuctl.conf. EMUCTL_WEB_CONF names another file.
"""
import os
from dataclasses import dataclass

from ..settings import ETC

DEFAULT_PATH = os.path.join(ETC, "web.conf")
# systemd's StateDirectory=emuctl-web makes this, owned by the daemon's account.
STATE_DIR = "/var/lib/emuctl-web"
KEYS = ("listen", "port", "tls", "tls_cert", "tls_key", "state_dir", "users_file", "task_log")


class WebConfError(Exception):
    pass


@dataclass(frozen=True)
class WebConf:
    listen: str = "0.0.0.0"
    port: int = 8600
    tls: bool = True
    tls_cert: str = ""          # empty: a self-signed one in state_dir, made on first start
    tls_key: str = ""
    state_dir: str = STATE_DIR
    users_file: str = ""        # empty: state_dir/users
    task_log: str = "/var/log/emuctl/web-tasks.log"

    @property
    def self_signed(self):
        return not self.tls_cert

    @property
    def cert_path(self):
        return self.tls_cert or os.path.join(self.state_dir, "web.crt")

    @property
    def key_path(self):
        return self.tls_key or os.path.join(self.state_dir, "web.key")

    @property
    def users_path(self):
        return self.users_file or os.path.join(self.state_dir, "users")


def parse_web_conf(text, where="web.conf"):
    values = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise WebConfError(f"{where}: not a key = value line: {raw!r}")
        k, v = (part.strip() for part in line.split("=", 1))
        if k not in KEYS:
            raise WebConfError(f"{where}: unknown key: {k}")
        values[k] = v
    out = {}
    for k, v in values.items():
        if k == "port":
            if not v.isdigit() or not 0 < int(v) < 65536:
                raise WebConfError(f"{where}: port must be 1-65535, got {v!r}")
            out[k] = int(v)
        elif k == "tls":
            if v.lower() not in ("on", "off", "yes", "no", "true", "false"):
                raise WebConfError(f"{where}: tls must be on or off, got {v!r}")
            out[k] = v.lower() in ("on", "yes", "true")
        elif k in ("tls_cert", "tls_key", "state_dir", "users_file", "task_log"):
            if v and not os.path.isabs(v):
                raise WebConfError(f"{where}: {k} must be an absolute path")
            out[k] = v
        else:
            out[k] = v
    if bool(out.get("tls_cert")) != bool(out.get("tls_key")):
        raise WebConfError(f"{where}: tls_cert and tls_key go together")
    return WebConf(**out)


def load_web_conf(environ=None):
    environ = os.environ if environ is None else environ
    path = environ.get("EMUCTL_WEB_CONF", DEFAULT_PATH)
    try:
        with open(path) as fh:
            return parse_web_conf(fh.read(), path)
    except FileNotFoundError:
        if "EMUCTL_WEB_CONF" in environ:
            raise WebConfError(f"{path}: no such file")
        return WebConf()
