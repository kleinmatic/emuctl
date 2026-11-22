# SPDX-License-Identifier: GPL-2.0-only
"""`emuctl web serve` and `emuctl web passwd`."""
import dataclasses
import getpass
import logging
import sys

from . import auth
from .conf import WebConfError, load_web_conf

log = logging.getLogger("emuctl.web")


def cmd_passwd(args):
    try:
        conf = load_web_conf()
    except WebConfError as exc:
        sys.stderr.write(f"emuctl: {exc}\n")
        return 2
    path = conf.users_path
    try:
        if args.delete:
            auth.delete_user(path, args.user)
            print(f"removed {args.user} from {path}")
            return 0
        if args.stdin or not sys.stdin.isatty():
            password = sys.stdin.readline().rstrip("\n")
        else:
            password = getpass.getpass(f"new password for {args.user}: ")
            if getpass.getpass("again: ") != password:
                sys.stderr.write("emuctl: the passwords do not match\n")
                return 1
        auth.set_password(path, args.user, password)
    except auth.AuthError as exc:
        sys.stderr.write(f"emuctl: {exc}\n")
        return 1
    except PermissionError as exc:
        sys.stderr.write(f"emuctl: {exc}; run as root or as the web daemon's account\n")
        return 1
    print(f"set the password for {args.user} in {path}")
    return 0


def cmd_serve(settings, args):
    if sys.version_info < (3, 11):
        sys.stderr.write("emuctl: the web interface needs Python 3.11 or newer\n")
        return 2
    try:
        from aiohttp import web
    except ImportError:
        sys.stderr.write("emuctl: the web interface needs aiohttp: "
                         "apt install python3-aiohttp (or pip install aiohttp)\n")
        return 2
    from . import tls
    from .app import make_app

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    try:
        conf = load_web_conf()
    except WebConfError as exc:
        sys.stderr.write(f"emuctl: {exc}\n")
        return 2
    # The daemon acts through polkit (docs/web.md), never through sudo.
    settings = dataclasses.replace(settings, elevate="none")
    ctx = None
    if conf.tls:
        try:
            if tls.ensure_cert(conf):
                log.info("made a self-signed certificate: %s", conf.cert_path)
            ctx = tls.server_context(conf.cert_path, conf.key_path)
            log.info("certificate SHA-256 fingerprint: %s", tls.fingerprint(conf.cert_path))
        except (tls.CertError, OSError) as exc:
            sys.stderr.write(f"emuctl: {exc}\n")
            return 1
    else:
        log.warning("tls = off: passwords, session cookies and console traffic cross the "
                    "network in clear")
    if not auth.read_users(conf.users_path):
        log.warning("no users in %s; add one with: emuctl web passwd <name>", conf.users_path)
    scheme = "https" if ctx else "http"
    log.info("emuctl web listening on %s://%s:%d/ (guests from %s)", scheme, conf.listen,
             conf.port, settings.guest_dir)
    app = make_app(settings, conf)
    web.run_app(app, host=conf.listen, port=conf.port, ssl_context=ctx, print=None,
                access_log=None, handle_signals=True, shutdown_timeout=5)
    return 0


def add_parser(sub):
    p = sub.add_parser("web", help="the web interface (needs aiohttp)")
    wsub = p.add_subparsers(dest="web_command", metavar="web-command")
    s = wsub.add_parser("serve", help="run the web daemon in the foreground "
                                      "(what emuctl-web.service runs)")
    s.set_defaults(web_func="serve")
    s = wsub.add_parser("passwd", help="set or remove a web user's password")
    s.add_argument("user")
    s.add_argument("--delete", action="store_true", help="remove the user")
    s.add_argument("--stdin", action="store_true",
                   help="read the password from one line of standard input")
    s.set_defaults(web_func="passwd")
    return p


def run(settings, args, parser):
    which = getattr(args, "web_func", None)
    if which == "serve":
        return cmd_serve(settings, args)
    if which == "passwd":
        return cmd_passwd(args)
    parser.print_help()
    return 2

