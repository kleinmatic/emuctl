#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""A stand-in for Xvnc: listens on a Unix socket after a delay, as an X
server does once it is up, and writes MARKER when it is told to stop.

    fake_display.py SOCKET MARKER [--delay S] [--never]
"""
import argparse
import signal
import socket
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("socket")
ap.add_argument("marker")
ap.add_argument("--delay", type=float, default=0.3)
ap.add_argument("--never", action="store_true", help="make the socket but never listen")


def stopped(*_):
    with open(args.marker, "w") as fh:
        fh.write("stopped\n")
    sys.exit(0)


args = ap.parse_args()
signal.signal(signal.SIGTERM, stopped)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(args.socket)                 # the file exists from here, like X's
time.sleep(args.delay)
if not args.never:
    s.listen(4)
while True:
    time.sleep(0.05)
