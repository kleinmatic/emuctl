# SPDX-License-Identifier: GPL-2.0-only
"""Where emuctl keeps things on this host.

Every value has a platform default, can be set in emuctl.conf, and can be
overridden by an EMUCTL_<KEY> environment variable. Nothing about a particular
host is compiled in.
"""
import os
import platform
from dataclasses import dataclass

FREEBSD = platform.system() == "FreeBSD"
ETC = "/usr/local/etc/emuctl" if FREEBSD else "/etc/emuctl"

DEFAULTS = {
    "guest_dir": os.path.join(ETC, "guests"),
    "run_dir": "/var/run/emuctl" if FREEBSD else "/run/emuctl",
    "service": "rcd" if FREEBSD else "systemd",
    "unit": "emuctl-guest@{name}.service",
    "rc_script": "emuctl",
    "elevate": "sudo",
}
SERVICES = ("systemd", "rcd")


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    guest_dir: str
    run_dir: str
    service: str
    unit: str
    rc_script: str
    elevate: str

    def conf_path(self, name):
        return os.path.join(self.guest_dir, f"{name}.conf")

    def guests(self):
        try:
            return sorted(f[:-5] for f in os.listdir(self.guest_dir) if f.endswith(".conf"))
        except OSError:
            return []


def _read(path):
    out = {}
    try:
        with open(path) as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return out
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise SettingsError(f"{path}: not a key = value line: {raw!r}")
        k, v = (part.strip() for part in line.split("=", 1))
        if k not in DEFAULTS:
            raise SettingsError(f"{path}: unknown key: {k}")
        out[k] = v
    return out


def load_settings(environ=None):
    environ = os.environ if environ is None else environ
    values = dict(DEFAULTS)
    values.update(_read(environ.get("EMUCTL_CONF", os.path.join(ETC, "emuctl.conf"))))
    for key in DEFAULTS:
        env = environ.get("EMUCTL_" + key.upper())
        if env:
            values[key] = env
    if values["service"] not in SERVICES:
        raise SettingsError(f"service must be one of {SERVICES}, got {values['service']!r}")
    if values["elevate"] not in ("sudo", "doas", "mdo", "none"):
        raise SettingsError(f"elevate must be sudo, doas, mdo or none, got {values['elevate']!r}")
    return Settings(**values)
