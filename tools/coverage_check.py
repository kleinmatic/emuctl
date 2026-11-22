#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Fail CI if test coverage drops below the floors.

    python3 -m pytest --cov=emuctl --cov-report=json
    python3 tools/coverage_check.py coverage.json

Two rules:
  - The whole project may not fall below TOTAL_FLOOR. Raise it when coverage
    rises; never lower it to make a change pass.
  - Each module listed in NEW_MODULES must reach NEW_FLOOR. New modules are
    added to that list when they are created.
"""
import json
import sys

TOTAL_FLOOR = 88
NEW_FLOOR = 90
# Paths as coverage reports them. A listed module that does not exist yet is skipped.
NEW_MODULES = [
    "src/emuctl/usage.py",
    "src/emuctl/actions.py",
    "src/emuctl/hardware.py",
    "src/emuctl/network.py",
    "src/emuctl/web/",
]


def check(report):
    """A list of failure messages; empty means pass."""
    failures = []
    total = report["totals"]["percent_covered"]
    if total < TOTAL_FLOOR:
        failures.append(f"total coverage {total:.1f}% is below the floor of {TOTAL_FLOOR}%")
    for path, data in sorted(report["files"].items()):
        if any(path == m or (m.endswith("/") and path.startswith(m)) for m in NEW_MODULES):
            pct = data["summary"]["percent_covered"]
            if pct < NEW_FLOOR:
                failures.append(f"{path}: {pct:.1f}% is below {NEW_FLOOR}% for new modules")
    return failures


def main(argv):
    path = argv[1] if len(argv) > 1 else "coverage.json"
    with open(path) as fh:
        report = json.load(fh)
    failures = check(report)
    for f in failures:
        sys.stderr.write(f"coverage: {f}\n")
    if not failures:
        print(f"coverage: total {report['totals']['percent_covered']:.1f}% "
              f"(floor {TOTAL_FLOOR}%); new modules at or above {NEW_FLOOR}%")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
