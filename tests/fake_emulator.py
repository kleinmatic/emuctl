#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""A stand-in emulator for the tests.

Listens on one or more "serial" TCP ports and behaves like firmware plus a
tiny OS on the first one:

    >>>                 firmware prompt, printed once every serial has a client
    boot        ->      boot messages, then `login: `   (or the --stuck text)
    shutdown    ->      `system halted`, then `>>> ` again
    drop        ->      closes the console connection, then accepts a new one

Options change how it misbehaves, so each supervisor path can be exercised
without a real emulator.
"""
import argparse
import os
import signal
import socket
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", type=int, action="append", required=True)
    ap.add_argument("--disk", action="append", default=[], help="file to hold open")
    ap.add_argument("--stuck", help="print this instead of reaching login")
    ap.add_argument("--self-boot", action="store_true", help="do not wait for `boot`")
    ap.add_argument("--exit-on-shutdown", action="store_true",
                    help="exit 0 after `shutdown`, as SIMH does")
    ap.add_argument("--ignore-term", action="store_true")
    ap.add_argument("--die-after", type=float, help="exit 3 after this many seconds")
    args = ap.parse_args()

    if args.ignore_term:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    held = [open(d, "r+b") for d in args.disk]     # noqa: F841 -- kept open on purpose

    listeners = []
    for port in args.serial:
        ls = socket.socket()
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind(("127.0.0.1", port))
        ls.listen(2)
        listeners.append(ls)
    # Like es40 before 0.95: nothing happens until every serial has a client.
    conns = [ls.accept()[0] for ls in listeners]
    con = conns[0]
    started = time.time()

    def say(text):
        con.sendall(text.encode())

    say("\r\nFake Firmware V1.0\r\n")
    if args.self_boot:
        say("booting...\r\n" + (args.stuck or "\r\nlogin: "))
    else:
        say(">>> ")
    line = b""
    con.settimeout(0.2)
    while True:
        if args.die_after and time.time() - started > args.die_after:
            os._exit(3)
        try:
            data = con.recv(1024)
        except socket.timeout:
            continue
        if not data:                                # client went away: take a new one
            con.close()
            con = listeners[0].accept()[0]
            con.settimeout(0.2)
            continue
        for ch in data:
            if ch not in (0x0D, 0x0A):
                line += bytes([ch])
                continue
            cmd, line = line.decode(errors="replace").strip(), b""
            if cmd == "boot":
                say("\r\nbooting...\r\n" + (args.stuck or "\r\nlogin: "))
            elif cmd == "shutdown":
                say("\r\nsystem halted\r\n")
                if args.exit_on_shutdown:
                    os._exit(0)
                say(">>> ")
            elif cmd == "drop":
                con.close()
                con = listeners[0].accept()[0]
                con.settimeout(0.2)
                say("\r\nlogin: ")
                break
            elif cmd == "":
                say("\r\nlogin: " if not args.stuck else "\r\n" + args.stuck)
            else:
                say(f"\r\n?{cmd}\r\nlogin: ")


if __name__ == "__main__":
    main()
