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
# A guest with `console = none` (86Box: a screen, no serial console) needs none of these.
CONSOLE_KEYS = ("serials", "broker", "ready")
# Keys that only mean something with a serial console.
NEEDS_CONSOLE = ("halted", "stop_wake", "console_timeout", "bind")
CONSOLES = ("serial", "none")
STOP_SIGNALS = ("TERM", "HUP", "INT", "QUIT")
VALID_STOP_MODES = ("signal", "freeze", "monitor")
# Keys whose value may contain '#', so an inline comment cannot be stripped.
PATTERN_KEYS = ("ready", "halted", "command", "stop_command")
STAGED = ("boot.", "stop.", "stuck.")
HW_PREFIX = "hw."
KINDS = ("simh", "es40", "qemu", "gxemul", "86box", "other")
DISPLAY_AUTH = ("none", "vnc", "x509vnc")
# What a guest name may be. The web interface checks names from URLs against it.
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
KNOWN = set(REQUIRED) | {
    "node", "os", "emulator", "env", "diskless", "bind", "halted",
    "boot_timeout", "console_timeout", "guest_addr", "guest_port",
    "stop_mode", "monitor", "stop_command", "stop_timeout", "stop_wake", "halt_grace",
    "exclusive_with", "kind", "config", "nics", "display", "display_auth",
    "display_password_file", "display_ca", "notes", "locked",
    "console", "ready_after", "stop_signal", "display_command", "display_wait",
}
# Program names that are SIMH simulators, for guessing `kind`.
SIMH_PREFIXES = ("vax", "microvax", "rtvax", "infoserver", "pdp", "altair", "nova", "eclipse",
                 "i1401", "i1620", "i650", "i7", "ibm1130", "sigma", "b5500", "id16", "id32",
                 "h316", "hp2100", "hp3000", "lgp", "s3", "sds", "swtp", "tx-0", "simh")


def guess_kind(command):
    """The emulator kind, from the program name in `command`."""
    prog = os.path.basename(command[0]).lower() if command else ""
    for kind in ("es40", "qemu", "gxemul", "86box"):
        if kind in prog:
            return kind
    return "simh" if prog.startswith(SIMH_PREFIXES) else "other"


def parse_display(value):
    """(host, port) from 'vnc://host:port'."""
    m = re.fullmatch(r"vnc://([^:/\s]+|\[[0-9a-fA-F:]+\]):(\d{1,5})", value)
    if not m or not 0 < int(m.group(2)) < 65536:
        raise ConfigError(f"display must be vnc://host:port, got {value!r}")
    return m.group(1).strip("[]"), int(m.group(2))


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
    halt_grace: int
    stop_wake: bool
    exclusive_with: list
    unknown: list = field(default_factory=list)
    kind: str = "other"
    config_files: list = field(default_factory=list)   # absolute paths
    nics: list = field(default_factory=list)
    display: object = None                              # (host, port) or None
    display_auth: str = "none"
    display_password_file: object = None
    display_ca: object = None                           # x509vnc: the certificate to trust
    notes: object = None                                # absolute path or None
    hw: dict = field(default_factory=dict)              # label -> text, in conf order
    locked: str = ""                                    # why it must not be started, or ""
    console: str = "serial"                             # "serial", or "none" (no console)
    ready_after: int = 10                               # console = none: ready after this
    stop_signal: str = "TERM"                           # how stop_mode = signal stops it
    display_command: list = field(default_factory=list)  # a helper run beside the emulator
    display_wait: object = None                         # path that must answer first

    @property
    def has_console(self):
        return self.console != "none"

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
    console = kv.get("console", "serial")
    if console not in CONSOLES:
        raise ConfigError(f"console must be one of {CONSOLES}, got {console!r}")
    for key in REQUIRED:
        if key not in kv and not (console == "none" and key in CONSOLE_KEYS):
            raise ConfigError(f"missing required key: {key}")
    if console == "none":
        used = [k for k in kv if k in CONSOLE_KEYS + NEEDS_CONSOLE
                or k.startswith(STAGED)]
        if used:
            raise ConfigError(f"console = none: {', '.join(sorted(used))} need a serial "
                              f"console; remove them")
        if kv.get("stop_mode") == "freeze":
            raise ConfigError("console = none: stop_mode = freeze needs a serial console")

    name = kv["name"]
    if not NAME_PATTERN.fullmatch(name):
        raise ConfigError(f"name must be letters, digits, '.', '_' or '-', got {name!r}")

    workdir = kv["workdir"]
    if not os.path.isabs(workdir):
        raise ConfigError("workdir must be an absolute path")

    serials, broker = [], None
    if console == "serial":
        try:
            serials = [int(p) for p in kv["serials"].split(",") if p.strip()]
        except ValueError:
            raise ConfigError(f"serials must be a comma-separated list of ports, "
                              f"got {kv['serials']!r}")
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

    def rel(p):
        return p if os.path.isabs(p) else os.path.join(workdir, p)

    kind = kv.get("kind") or guess_kind(command)
    if kind not in KINDS:
        raise ConfigError(f"kind must be one of {KINDS}, got {kind!r}")
    display = parse_display(kv["display"]) if kv.get("display") else None
    display_auth = kv.get("display_auth", "vnc" if kv.get("display_password_file") else "none")
    if display_auth not in DISPLAY_AUTH:
        raise ConfigError(f"display_auth must be one of {DISPLAY_AUTH}, got {display_auth!r}")
    if display_auth in ("vnc", "x509vnc") and not kv.get("display_password_file"):
        raise ConfigError(f"display_auth = {display_auth} needs display_password_file")
    if display_auth == "x509vnc" and not kv.get("display_ca"):
        raise ConfigError("display_auth = x509vnc needs display_ca: the VNC server's "
                          "certificate, or the CA that signed it")
    if kv.get("display_ca") and display_auth != "x509vnc":
        raise ConfigError("display_ca is only for display_auth = x509vnc")
    if (display_auth != "none" or kv.get("display_password_file")) and not display:
        raise ConfigError("display_auth and display_password_file need display")
    if display_auth == "none" and kv.get("display_password_file"):
        raise ConfigError("display_password_file is set but display_auth = none")
    nics = [n.strip() for n in kv.get("nics", "").split(",") if n.strip()]
    for n in nics:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,14}", n):
            raise ConfigError(f"nics: {n!r} is not an interface name")
    hw = {k[len(HW_PREFIX):]: v for k, v in kv.items() if k.startswith(HW_PREFIX)}
    if any(not label for label in hw):
        raise ConfigError("hw. keys need a label, such as hw.memory")

    stop_signal = kv.get("stop_signal", "TERM").upper().removeprefix("SIG")
    if stop_signal not in STOP_SIGNALS:
        raise ConfigError(f"stop_signal must be one of {STOP_SIGNALS}, got {kv['stop_signal']!r}")
    try:
        display_command = shlex.split(kv.get("display_command", ""))
    except ValueError as exc:
        raise ConfigError(f"display_command: {exc}")
    display_wait = kv.get("display_wait") or None
    if display_wait and not os.path.isabs(display_wait):
        raise ConfigError("display_wait must be an absolute path")
    if display_wait and not display_command:
        raise ConfigError("display_wait needs display_command")
    ready_after = _int(kv, "ready_after", 10)
    if ready_after < 0:
        raise ConfigError("ready_after must not be negative")
    locked = kv.get("locked", "")
    if locked.lower() in ("no", "false", "off", "0", "none"):
        raise ConfigError(f"locked = {locked} would lock the guest: locked takes a reason; "
                          f"remove the line to allow starts")
    halted = kv.get("halted")
    return GuestConfig(
        name=name, node=kv.get("node", name), os=kv.get("os", ""),
        emulator=kv.get("emulator", ""), workdir=workdir, command=command, env=env,
        disks=disks, serials=serials, broker=broker,
        bind=kv.get("bind", "127.0.0.1"),
        boot_stages=_stages(kv, "boot."),
        ready=_regex("ready", kv["ready"]) if console == "serial" else "",
        halted=_regex("halted", halted) if halted else None,
        stuck=[_regex(k, v) for k, v in _family(kv, "stuck.")],
        boot_timeout=_int(kv, "boot_timeout", 900),
        console_timeout=_int(kv, "console_timeout", 180),
        guest_addr=kv.get("guest_addr"), guest_port=_int(kv, "guest_port", 23),
        stop_mode=stop_mode, monitor_sock=monitor_sock,
        stop_stages=stop_stages, stop_command=stop_command,
        stop_timeout=_int(kv, "stop_timeout", 300),
        halt_grace=_int(kv, "halt_grace", 0),
        stop_wake=_bool(kv, "stop_wake", True),
        exclusive_with=[p.strip() for p in kv.get("exclusive_with", "").split(",") if p.strip()],
        unknown=sorted(k for k in kv if k not in KNOWN and not k.startswith(STAGED)
                       and not k.startswith(HW_PREFIX)),
        kind=kind,
        config_files=[rel(p.strip()) for p in kv.get("config", "").split(",") if p.strip()],
        nics=nics, display=display, display_auth=display_auth,
        display_password_file=rel(kv["display_password_file"])
        if kv.get("display_password_file") else None,
        display_ca=rel(kv["display_ca"]) if kv.get("display_ca") else None,
        notes=rel(kv["notes"]) if kv.get("notes") else None,
        hw=hw,
        locked=locked,
        console=console, ready_after=ready_after, stop_signal=stop_signal,
        display_command=display_command, display_wait=display_wait,
    )


def load_config(path):
    with open(path) as fh:
        cfg = parse_config(fh.read())
    expected = os.path.basename(path)[:-len(".conf")]
    if path.endswith(".conf") and cfg.name != expected:
        raise ConfigError(f"{path}: name = {cfg.name!r} must match the file name")
    return cfg
