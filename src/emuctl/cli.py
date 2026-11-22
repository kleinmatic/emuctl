# SPDX-License-Identifier: GPL-2.0-only
"""The `emuctl` command."""
import argparse
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import time

from . import actions, full_version, hardware, network, service, supervisor, usage
from .config import ConfigError, load_config
from .settings import SettingsError, load_settings
from .state import effective_state, read_state
from .status import format_info, format_table, guest_status
from .telnet import strip_telnet

DETACH = 0x1D       # Ctrl-]


def die(msg, code=1):
    sys.stderr.write(f"emuctl: {msg}\n")
    sys.exit(code)


def need_guest(settings, name):
    if not os.path.exists(settings.conf_path(name)):
        die(f"no such guest: {name}  (looked for {settings.conf_path(name)})", 2)
    try:
        return load_config(settings.conf_path(name))
    except ConfigError as exc:
        die(f"{settings.conf_path(name)}: {exc}", 2)


def run(argv):
    if not service.available(argv):
        die(f"{argv[0]} not found; is `service` in emuctl.conf right for this host?")
    return subprocess.call(argv)


# --- commands ---------------------------------------------------------------

def load_configs(settings, names):
    """{name: GuestConfig} for every guest whose conf parses."""
    out = {}
    for name in names:
        try:
            out[name] = load_config(settings.conf_path(name))
        except (OSError, ConfigError):
            pass
    return out


def with_usage(settings, rows, sample=False):
    configs = load_configs(settings, [r["name"] for r in rows])
    if sample:
        return usage.sample(settings, rows, configs)
    return usage.attach(settings, rows, configs)


def cmd_list(settings, args):
    rows = [guest_status(settings, name) for name in settings.guests()]
    if args.usage or args.json:
        with_usage(settings, rows, sample=args.usage)
    if args.json:
        print(json.dumps(rows, indent=2))
    elif rows:
        print(format_table(rows, usage=args.usage, node=args.node, os=args.os,
                           emulator=args.emulator))
    else:
        print(f"no guests configured in {settings.guest_dir}")
    return 0


def cmd_info(settings, args):
    cfg = need_guest(settings, args.guest)
    if args.json and not args.hardware:
        die("--json goes with --hardware; for the guest's state as JSON use: "
            f"emuctl status {args.guest} --json", 2)
    if args.hardware:
        hw = hardware.describe(cfg)
        print(json.dumps(hw, indent=2) if args.json else hardware.format_hardware(hw))
        return 0
    print(format_info(settings, args.guest))
    return 0


def cmd_status(settings, args):
    need_guest(settings, args.guest)
    row = guest_status(settings, args.guest)
    if args.json:
        with_usage(settings, [row])
        print(json.dumps(row, indent=2))
    else:
        print(row["state"] + (f"  ({row['detail']})" if row["detail"] else ""))
    # Exit status for scripts: 0 ready, 1 not running, 3 running but not ready.
    if row["state"] == "ready":
        return 0
    return 1 if row["state"] in ("down", "crashed", "error") else 3


NO_SIGN_OF_START = 15     # seconds; a supervisor writes its first state well inside this


def current_state(settings, name, since):
    """The guest's state, ignoring a final state left by a run before `since`.

    Right after a start, the state file can still say `crashed` or `halted`
    from the previous run until the new supervisor writes its own. A
    supervisor that refuses or fails early (guard, broker port, missing
    emulator) never writes one, so if nothing new appears within
    NO_SIGN_OF_START seconds the start has failed: that is reported as `down`.
    Until then the state is "pending", which is never shown.
    """
    raw = read_state(settings.run_dir, name)
    state = effective_state(raw)
    if state in ("crashed", "halted") and (raw or {}).get("started_at", 0) < int(since) - 1:
        return "down" if time.time() - since > NO_SIGN_OF_START else "pending"
    return state


def wait_for_boot(settings, name, timeout, since=None):
    """Follow a start until it settles. Returns the state it settled in."""
    since = since or time.time()
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        state = current_state(settings, name, since)
        if state != last and state != "pending":
            print(f"  {state}")
            last = state
        if state in ("ready", "stuck", "crashed", "halted"):
            return state
        if state == "down" and time.time() > deadline - timeout + NO_SIGN_OF_START:
            sys.stderr.write(f"emuctl: {name} did not start; see: emuctl log {name}\n")
            return state
        time.sleep(1)
    return last


def cmd_start(settings, args):
    cfg = need_guest(settings, args.guest)
    # The supervisor runs the guard again, under its locks. Checking here first
    # means a refusal is reported to whoever typed the command, rather than
    # only to the service manager's log after it has accepted the start.
    plan = actions.plan_start(settings, args.guest, cfg)
    if not plan.allowed:
        if plan.code:
            sys.stderr.write(f"emuctl: {plan.message}\n")
        else:
            print(plan.message)
        return plan.code
    since = time.time()
    for argv in plan.steps:
        rc = run(argv)
        if rc != 0:
            sys.stderr.write(f"emuctl: start failed; see: emuctl log {args.guest}\n")
            return rc
    if not args.wait:
        # A supervisor that refuses or fails does so within a second or two,
        # but may not write a state at all; wait for this run's own.
        time.sleep(3)
        state = current_state(settings, args.guest, since)
        while state == "pending" and time.time() - since <= NO_SIGN_OF_START + 1:
            time.sleep(1)
            state = current_state(settings, args.guest, since)
        if state in ("down", "crashed"):
            sys.stderr.write(f"emuctl: {args.guest} did not start; see: emuctl log {args.guest}\n")
            return 1
        print(f"{args.guest}: {state}; follow it with: emuctl status {args.guest}")
        return 0
    state = wait_for_boot(settings, args.guest, cfg.boot_timeout, since)
    return 0 if state == "ready" else 3


def cmd_stop(settings, args):
    cfg = need_guest(settings, args.guest)
    mode = "now" if args.now else "force" if args.force else "clean"
    plan = actions.plan_stop(settings, args.guest, mode, cfg)
    if plan.message:
        out = sys.stderr if (plan.code or plan.power_cut) else sys.stdout
        out.write(f"emuctl: {plan.message}\n" if out is sys.stderr else f"{plan.message}\n")
    if not plan.allowed and plan.power_cut:
        sys.stderr.write("        Shut it down from inside, or repeat with --force.\n")
    if not plan.allowed:
        return plan.code
    rc = 0
    for i, argv in enumerate(plan.steps):
        rc = run(argv)
        if rc != 0 and i < len(plan.steps) - 1:
            # --now's signal failed. Going on would run the full shutdown the
            # user asked to skip, so stop here and say so.
            sys.stderr.write(f"emuctl: could not signal {args.guest}'s supervisor; nothing was "
                             f"stopped. `emuctl stop {args.guest}` stops it normally.\n")
            return rc
    return rc


def cmd_log(settings, args):
    need_guest(settings, args.guest)
    return run(service.log_argv(settings, args.guest, args.lines, args.follow))


def cmd_console(settings, args):
    cfg = need_guest(settings, args.guest)
    if args.telnet:
        if not shutil.which("telnet"):
            die("no telnet client installed; omit --telnet to use the built-in one")
        os.execvp("telnet", ["telnet", cfg.bind, str(cfg.broker)])
    try:
        sock = socket.create_connection((cfg.bind, cfg.broker), timeout=5)
    except OSError as exc:
        die(f"cannot reach the {args.guest} console on {cfg.bind}:{cfg.broker} ({exc}); "
            f"is it started?")
    return console_client(sock)


def console_client(sock, stdin=None, stdout=None):
    """A minimal terminal client for the broker. Ctrl-] detaches."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    fd = stdin.fileno()
    saved = None
    if os.isatty(fd):
        import termios
        import tty
        saved = termios.tcgetattr(fd)
        tty.setraw(fd)
        sys.stderr.write("emuctl: attached; Ctrl-] detaches\r\n")
    sock.settimeout(None)
    st = 0
    try:
        while True:
            readable, _, _ = select.select([sock, fd], [], [])
            if sock in readable:
                data = sock.recv(4096)
                if not data:
                    break
                data, st = strip_telnet(data, st)
                stdout.write(data)
                stdout.flush()
            if fd in readable:
                keys = os.read(fd, 1024)
                if not keys:
                    break
                if DETACH in keys:
                    sock.sendall(keys[:keys.index(DETACH)])
                    break
                sock.sendall(keys.replace(b"\xff", b"\xff\xff"))
    finally:
        if saved is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            sys.stderr.write("\r\nemuctl: detached\n")
        sock.close()
    return 0


def port_free(bind, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((bind, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


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
    if not (shutil.which(exe) if os.sep not in exe else os.access(exe, os.X_OK)):
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
    if cfg.stop_command and not (shutil.which(cfg.stop_command[0])
                                 or os.access(cfg.stop_command[0], os.X_OK)):
        errors.append(f"stop_command {cfg.stop_command[0]} is not an executable")
    if cfg.bind not in ("127.0.0.1", "::1", "localhost"):
        warnings.append(f"bind = {cfg.bind}: the broker is an unauthenticated console; "
                        f"anyone who can reach that address can type at the guest")
    if not cfg.clean_stop:
        warnings.append("no stop.<n> stages, stop_command or stop_mode = freeze: "
                        "stopping this guest is a power cut for its OS")

    if effective_state(read_state(settings.run_dir, name)) == "down":
        for port in [cfg.broker] + cfg.serials:
            if not port_free(cfg.bind, port):
                errors.append(f"port {port} on {cfg.bind} is already in use")
    for other in settings.guests():
        if other == name:
            continue
        try:
            o = load_config(settings.conf_path(other))
        except (OSError, ConfigError):
            continue
        clash = sorted(set([cfg.broker] + cfg.serials) & set([o.broker] + o.serials))
        wild = ("0.0.0.0", "::")
        if clash and (o.bind == cfg.bind or o.bind in wild or cfg.bind in wild):
            errors.append(f"port(s) {', '.join(map(str, clash))} also used by guest '{other}'")
        shared = sorted(set(cfg.disks) & set(o.disks))
        if shared and other not in cfg.exclusive_with:
            warnings.append(f"shares {', '.join(shared)} with '{other}' but is not "
                            f"exclusive_with it (the disk lock will still refuse a double start)")
    return errors, warnings


def cmd_check(settings, args):
    names = [args.guest] if args.guest else settings.guests()
    if args.guest and not os.path.exists(settings.conf_path(args.guest)):
        die(f"no such guest: {args.guest}", 2)
    if not names:
        print(f"no guests configured in {settings.guest_dir}")
    try:
        net = network.host_network(settings)["warnings"]
    except Exception as exc:
        net = [f"network check failed: {exc}"]
    bad = 0
    for name in names:
        errors, warnings = check_guest(settings, name)
        warnings = warnings + [w for w in net if w.startswith(f"{name}: ")
                               or w.startswith("network check failed")]
        print(f"{name}: " + ("ok" if not errors else f"{len(errors)} error(s)"))
        for e in errors:
            print(f"  error: {e}")
        for w in warnings:
            print(f"  warning: {w}")
        bad += bool(errors)
    return 1 if bad else 0


def cmd_net(settings, args):
    if args.guest:
        need_guest(settings, args.guest)
        rep = network.guest_network(settings, args.guest)
        rep["warnings"] += [w for w in network.host_network(settings)["warnings"]
                            if w.startswith(f"{args.guest}: uses ")]
        print(json.dumps(rep, indent=2) if args.json else network.format_guest(rep))
        return 0
    rep = network.host_network(settings)
    print(json.dumps(rep, indent=2) if args.json else network.format_host(rep))
    return 0


def cmd_supervise(settings, args):
    cfg = need_guest(settings, args.guest)
    return supervisor.main(cfg, settings)


def build_parser():
    p = argparse.ArgumentParser(
        prog="emuctl", description="Run emulated machines as services.")
    p.add_argument("--version", action="version", version=f"emuctl {full_version()}")
    sub = p.add_subparsers(dest="command", metavar="command")

    s = sub.add_parser("list", help="every guest and what it is doing")
    s.add_argument("--json", action="store_true")
    s.add_argument("--usage", action="store_true",
                   help="add CPU and memory use (takes a one-second sample)")
    s.add_argument("--node", action="store_true", help="add the machine's own name")
    s.add_argument("--os", action="store_true", help="add the OS label")
    s.add_argument("--emulator", action="store_true", help="add the emulator label")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("info", help="how to reach a guest and where its files live")
    s.add_argument("guest")
    s.add_argument("--hardware", action="store_true",
                   help="the emulated hardware, read from the emulator's config")
    s.add_argument("--json", action="store_true", help="with --hardware: as JSON")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("status", help="one guest's state; exit 0 only when ready")
    s.add_argument("guest")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("start", help="start a guest")
    s.add_argument("guest")
    s.add_argument("--wait", action="store_true",
                   help="follow the boot until it is ready, stuck or has failed")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("stop", help="stop a guest")
    s.add_argument("guest")
    s.add_argument("--force", action="store_true",
                   help="stop even though the guest OS will not be shut down first")
    s.add_argument("--now", action="store_true",
                   help="skip the guest's shutdown, or end one under way, and stop the "
                        "emulator now: a power cut for the guest")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("console", help="attach to a guest's console (Ctrl-] detaches)")
    s.add_argument("guest")
    s.add_argument("--telnet", action="store_true", help="use the system telnet client")
    s.set_defaults(func=cmd_console)

    s = sub.add_parser("log", help="the supervisor's log for a guest")
    s.add_argument("guest")
    s.add_argument("-n", "--lines", type=int, default=60)
    s.add_argument("-f", "--follow", action="store_true")
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("net", help="bridges, taps and which guest is on which")
    s.add_argument("guest", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_net)

    s = sub.add_parser("check", help="validate a guest's conf against this host")
    s.add_argument("guest", nargs="?")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("supervise", help="run a guest's supervisor in the foreground "
                                         "(what the service manager runs)")
    s.add_argument("guest")
    s.set_defaults(func=cmd_supervise)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        settings = load_settings()
    except SettingsError as exc:
        die(str(exc), 2)
    return args.func(settings, args)


if __name__ == "__main__":
    sys.exit(main())
