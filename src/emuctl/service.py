# SPDX-License-Identifier: GPL-2.0-only
"""Start, stop and read logs through the host's service manager.

emuctl does not daemonise anything itself. On Linux each guest is an instance
of a systemd template unit; on FreeBSD the rc.d script takes guest names.
"""
import os
import shutil


def _elevate(settings):
    if os.geteuid() == 0 or settings.elevate == "none":
        return []
    return [settings.elevate]


def start_argv(settings, name):
    if settings.service == "systemd":
        return _elevate(settings) + ["systemctl", "start", settings.unit.format(name=name)]
    return _elevate(settings) + ["service", settings.rc_script, "onestart", name]


def stop_argv(settings, name):
    if settings.service == "systemd":
        return _elevate(settings) + ["systemctl", "stop", settings.unit.format(name=name)]
    return _elevate(settings) + ["service", settings.rc_script, "onestop", name]


def kill_argv(settings, name, sig, pid=None):
    """Send `sig` (a name such as "USR2") to the guest's supervisor only.

    systemd knows the supervisor as the unit's main process. Under rc.d the
    supervisor's pid comes from the state file; without one, returns None.
    """
    if settings.service == "systemd":
        return _elevate(settings) + ["systemctl", "kill", "--kill-whom=main", "-s", sig,
                                     settings.unit.format(name=name)]
    if not pid:
        return None
    return _elevate(settings) + ["kill", f"-{sig}", str(pid)]


def log_argv(settings, name, lines=60, follow=False):
    if settings.service == "systemd":
        argv = _elevate(settings) + ["journalctl", "-u", settings.unit.format(name=name),
                                     "-n", str(lines), "--no-pager"]
        return argv + (["-f"] if follow else [])
    return ["tail", "-n", str(lines)] + (["-f"] if follow else []) + [f"/var/log/emuctl-{name}.log"]


def available(argv):
    return shutil.which(argv[0]) is not None
