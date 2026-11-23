#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""A stand-in for an emulator with no serial console, such as 86Box.

    fake_screen.py [--hup-only] [--needs PATH] [--die-after S]

Runs until signalled. --hup-only ignores SIGTERM and SIGINT and exits on
SIGHUP, as 86Box does. --needs exits 3 at once unless PATH exists (the X
server it would draw on). --die-after exits 3 after that many seconds.
"""
import argparse
import os
import signal
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--hup-only", action="store_true")
ap.add_argument("--needs")
ap.add_argument("--die-after", type=float)
args = ap.parse_args()
if args.needs and not os.path.exists(args.needs):
    print(f"fake_screen: cannot open display {args.needs}", flush=True)
    sys.exit(3)
quit_ = lambda *_: sys.exit(0)                       # noqa: E731
if args.hup_only:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
else:
    signal.signal(signal.SIGTERM, quit_)
signal.signal(signal.SIGHUP, quit_)
started = time.time()
while True:
    time.sleep(0.05)
    if args.die_after and time.time() - started > args.die_after:
        sys.exit(3)
