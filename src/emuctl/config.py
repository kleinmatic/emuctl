# SPDX-License-Identifier: GPL-2.0-only
"""Parse a guest .conf into a validated GuestConfig.

Pure except for load_config(). The file format is `key = value`, one per line,
`#` for comments. See docs/config.md for every key.
"""
import os
import re
import shlex
from dataclasses import dataclass, field

REQUIRED = ("name", "workdir", "command", "disks", "serials", "broker", "ready")
VALID_STOP_MODES = ("signal", "freeze", "monitor")
# Keys whose value may contain '#', so an inline comment cannot be stripped.
PATTERN_KEYS = ("ready", "halted", "command", "stop_command")
STAGED = ("boot.", "stop.", "stuck.")
KNOWN = set(REQUIRED) | {
    "node", "os", "emulator", "env", "diskless", "bind", "halted",
    "boot_timeout", "console_timeout", "guest_addr", "guest_port",
    "stop_mode", "monitor", "stop_command", "stop_timeout", "stop_wake",
    "exclusive_with",
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Stage:
    prompt: bytes
    command: str


@dataclass(frozen=True)
class GuestConfig:
    name: str
    node: str
    os: str
    emulator: str
    workdir: str
    command: list
    env: dict
    disks: list                 # absolute paths
    serials: list
    broker: int
    bind: str
    boot_stages: list
    ready: str
    halted: object
    stuck: list
    boot_timeout: int
    console_timeout: int
    guest_addr: object
    guest_port: int
    stop_mode: str
    monitor_sock: object
    stop_stages: list
    stop_command: list
    stop_timeout: int
    stop_wake: bool
    exclusive_with: list
    unknown: list = field(default_factory=list)

    @property
    def clean_stop(self):
        """True if a stop does more than cut the guest's power."""
        return bool(self.stop_stages or self.stop_command) or self.stop_mode == "freeze"


def _split_kv(text):
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"not a key = value line: {raw!r}")
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k not in PATTERN_KEYS and not k.startswith(STAGED) and "#" in v:
            v = v.split("#", 1)[0].strip()
        if k in out:
            raise ConfigError(f"duplicate key: {k}")
        out[k] = v
    return out


def _int(kv, key, default):
    if key not in kv:
        return default
    try:
        return int(kv[key])
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got {kv[key]!r}")


def _bool(kv, key, default):
    if key not in kv:
        return default
    value = kv[key].lower()
    if value in ("yes", "true", "1", "on"):
        return True
    if value in ("no", "false", "0", "off"):
        return False
    raise ConfigError(f"{key} must be yes or no, got {kv[key]!r}")


def _index(key):
    family = key.split(".", 1)[0]
    try:
        return int(key.split(".", 1)[1])
    except (IndexError, ValueError):
        raise ConfigError(f"{family} key must be {family}.<int>, got {key!r}")


def _family(kv, prefix):
    return [(k, kv[k]) for k in sorted((k for k in kv if k.startswith(prefix)), key=_index)]


def _stages(kv, prefix):
    stages = []
    for key, spec in _family(kv, prefix):
        if "|" not in spec:
            raise ConfigError(f"{key} must be 'prompt | command', got {spec!r}")
        prompt, cmd = (part.strip() for part in spec.split("|", 1))
        if not prompt or not cmd:
            raise ConfigError(f"{key} needs a non-empty prompt and command")
        stages.append(Stage(prompt=prompt.encode(), command=cmd))
    return stages


def _regex(key, value):
    try:
        re.compile(value)
    except re.error as exc:
        raise ConfigError(f"{key} is not a valid regex: {exc}")
    return value


def parse_config(text):
    kv = _split_kv(text)
    for key in REQUIRED:
        if key not in kv:
            raise ConfigError(f"missing required key: {key}")

    name = kv["name"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ConfigError(f"name must be letters, digits, '.', '_' or '-', got {name!r}")

    workdir = kv["workdir"]
    if not os.path.isabs(workdir):
        raise ConfigError("workdir must be an absolute path")

    try:
        serials = [int(p) for p in kv["serials"].split(",") if p.strip()]
    except ValueError:
        raise ConfigError(f"serials must be a comma-separated list of ports, got {kv['serials']!r}")
    if not serials:
        raise ConfigError("serials must list at least one port")
    if len(set(serials)) != len(serials):
        raise ConfigError("duplicate port in serials")

    broker = _int(kv, "broker", None)
    if broker in serials:
        raise ConfigError("broker port collides with a serial port")

    env = {}
    for pair in kv.get("env", "").split():
        if "=" not in pair:
            raise ConfigError(f"env entry must be KEY=VALUE, got {pair!r}")
        k, v = pair.split("=", 1)
        env[k] = v

    stop_mode = kv.get("stop_mode", "signal")
    if stop_mode not in VALID_STOP_MODES:
        raise ConfigError(f"stop_mode must be one of {VALID_STOP_MODES}, got {stop_mode!r}")
    monitor_sock = kv.get("monitor")
    if stop_mode == "monitor" and not monitor_sock:
        raise ConfigError("stop_mode = monitor requires a 'monitor' socket path")

    stop_stages = _stages(kv, "stop.")
    try:
        stop_command = shlex.split(kv.get("stop_command", ""))
    except ValueError as exc:
        raise ConfigError(f"stop_command: {exc}")
    if stop_stages and stop_command:
        raise ConfigError("use stop.<n> stages or stop_command, not both")

    # An empty `disks =` is accepted only with `diskless = yes`, so a typo
    # cannot silently switch off the disk guard.
    disks = [d.strip() for d in kv["disks"].split(",") if d.strip()]
    if not disks and not _bool(kv, "diskless", False):
        raise ConfigError("disks must list at least one image, or set 'diskless = yes'")
    disks = [d if os.path.isabs(d) else os.path.join(workdir, d) for d in disks]

    try:
        command = shlex.split(kv["command"])
    except ValueError as exc:
        raise ConfigError(f"command: {exc}")
    if not command:
        raise ConfigError("command is empty")

    halted = kv.get("halted")
    return GuestConfig(
        name=name, node=kv.get("node", name), os=kv.get("os", ""),
        emulator=kv.get("emulator", ""), workdir=workdir, command=command, env=env,
        disks=disks, serials=serials, broker=broker,
        bind=kv.get("bind", "127.0.0.1"),
        boot_stages=_stages(kv, "boot."),
        ready=_regex("ready", kv["ready"]),
        halted=_regex("halted", halted) if halted else None,
        stuck=[_regex(k, v) for k, v in _family(kv, "stuck.")],
        boot_timeout=_int(kv, "boot_timeout", 900),
        console_timeout=_int(kv, "console_timeout", 180),
        guest_addr=kv.get("guest_addr"), guest_port=_int(kv, "guest_port", 23),
        stop_mode=stop_mode, monitor_sock=monitor_sock,
        stop_stages=stop_stages, stop_command=stop_command,
        stop_timeout=_int(kv, "stop_timeout", 300),
        stop_wake=_bool(kv, "stop_wake", True),
        exclusive_with=[p.strip() for p in kv.get("exclusive_with", "").split(",") if p.strip()],
        unknown=sorted(k for k in kv if k not in KNOWN and not k.startswith(STAGED)),
    )


def load_config(path):
    with open(path) as fh:
        cfg = parse_config(fh.read())
    expected = os.path.basename(path)[:-len(".conf")]
    if path.endswith(".conf") and cfg.name != expected:
        raise ConfigError(f"{path}: name = {cfg.name!r} must match the file name")
    return cfg
