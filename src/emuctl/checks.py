# SPDX-License-Identifier: GPL-2.0-only
"""`emuctl check`: a guest's conf checked against this host.

Used by the command and by the web page's summary. Reads files and binds
ports for a moment; changes nothing.
"""
import os
import shutil
import socket

from .config import ConfigError, load_config
from .state import effective_state, read_state


def port_free(bind, port):
    s = socket.socket(socket.AF_INET6 if ":" in bind else socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((bind, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _executable(path):
    """True for a program on PATH, or a path to an executable file."""
    if os.sep not in path:
        return bool(shutil.which(path))
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _addr(bind):
    return "127.0.0.1" if bind == "localhost" else bind


def check_guest(settings, name):
    """(errors, warnings) for one guest's conf, checked against this host."""
    errors, warnings = [], []
    try:
        cfg = load_config(settings.conf_path(name))
    except (OSError, ConfigError) as exc:
        return [str(exc)], warnings

    for key in cfg.unknown:
        warnings.append(f"unknown key '{key}' (ignored)")
    if not os.path.isdir(cfg.workdir):
        errors.append(f"workdir {cfg.workdir} does not exist")
    exe = cfg.command[0]
    if not _executable(exe):
        errors.append(f"command {exe} is not an executable")
    for disk in cfg.disks:
        if not os.path.exists(disk):
            errors.append(f"disk {disk} does not exist")
    for peer in cfg.exclusive_with:
        if not os.path.exists(settings.conf_path(peer)):
            errors.append(f"exclusive_with names '{peer}', which has no conf")
    for path in cfg.config_files:
        if not os.path.exists(path):
            errors.append(f"config file {path} does not exist")
    if cfg.locked:
        warnings.append(f"locked: {cfg.locked} (starts are refused)")
    if cfg.notes and not os.path.exists(cfg.notes):
        warnings.append(f"notes file {cfg.notes} does not exist")
    if cfg.display_password_file:
        try:
            os.stat(cfg.display_password_file)
        except PermissionError:
            warnings.append(f"display_password_file {cfg.display_password_file}: this user "
                            f"cannot see it; check as the account that needs it")
        except OSError:
            errors.append(f"display_password_file {cfg.display_password_file} does not exist")
    if cfg.display_ca:
        try:
            with open(cfg.display_ca, "rb"):
                pass
        except PermissionError:
            warnings.append(f"display_ca {cfg.display_ca}: this user cannot read it; the web "
                            f"daemon's account must (it is a certificate, not a secret)")
        except OSError:
            errors.append(f"display_ca {cfg.display_ca} does not exist")
    if cfg.stop_command and not _executable(cfg.stop_command[0]):
        errors.append(f"stop_command {cfg.stop_command[0]} is not an executable")
    if cfg.kind == "86box" and cfg.stop_signal == "TERM" and cfg.stop_mode == "signal":
        warnings.append("86Box ignores SIGTERM; set stop_signal = HUP, or every stop waits "
                        "out the grace period and ends in SIGKILL")
    if cfg.display_command and not _executable(cfg.display_command[0]):
        errors.append(f"display_command {cfg.display_command[0]} is not an executable")
    if cfg.has_console and cfg.bind not in ("127.0.0.1", "::1", "localhost"):
        warnings.append(f"bind = {cfg.bind}: the broker is an unauthenticated console; "
                        f"anyone who can reach that address can type at the guest")
    if not cfg.clean_stop:
        warnings.append("no stop.<n> stages, stop_command or stop_mode = freeze: "
                        "stopping this guest is a power cut for its OS"
                        if cfg.has_console else
                        "no stop_command: stopping this guest is a power cut for its OS")

    if effective_state(read_state(settings.run_dir, name)) == "down":
        for port in ([cfg.broker] + cfg.serials) if cfg.has_console else []:
            if not port_free(cfg.bind, port):
                errors.append(f"port {port} on {cfg.bind} is already in use")
    for other in settings.guests():
        if other == name:
            continue
        try:
            o = load_config(settings.conf_path(other))
        except (OSError, ConfigError):
            continue
        mine = set([cfg.broker] + cfg.serials) if cfg.has_console else set()
        theirs = set([o.broker] + o.serials) if o.has_console else set()
        clash = sorted(mine & theirs)
        wild = ("0.0.0.0", "::")
        if clash and (_addr(o.bind) == _addr(cfg.bind) or o.bind in wild or cfg.bind in wild):
            errors.append(f"port(s) {', '.join(map(str, clash))} also used by guest '{other}'")
        if cfg.display_wait and cfg.display_wait == o.display_wait:
            errors.append(f"display_wait {cfg.display_wait} is also guest '{other}''s: the two "
                          f"would share one display")
        shared = sorted(set(cfg.disks) & set(o.disks))
        if shared and other not in cfg.exclusive_with:
            warnings.append(f"shares {', '.join(shared)} with '{other}' but is not "
                            f"exclusive_with it (the disk lock will still refuse a double start)")
    return errors, warnings
