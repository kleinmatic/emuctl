# SPDX-License-Identifier: GPL-2.0-only
"""tools/coverage_check.py: the coverage floors CI enforces."""
from coverage_check import NEW_FLOOR, TOTAL_FLOOR, check


def report(total, files):
    return {"totals": {"percent_covered": total},
            "files": {p: {"summary": {"percent_covered": pct}} for p, pct in files.items()}}


def test_passes_at_the_floors():
    assert check(report(TOTAL_FLOOR, {"src/emuctl/usage.py": NEW_FLOOR,
                                      "src/emuctl/cli.py": 10})) == []


def test_total_below_floor():
    assert "total coverage" in check(report(TOTAL_FLOOR - 1, {}))[0]


def test_new_module_below_floor():
    out = check(report(95, {"src/emuctl/network.py": NEW_FLOOR - 1}))
    assert len(out) == 1 and "network.py" in out[0]


def test_web_package_counts_as_new():
    out = check(report(95, {"src/emuctl/web/app.py": 50}))
    assert "web/app.py" in out[0]


def test_old_modules_only_count_toward_the_total():
    assert check(report(95, {"src/emuctl/supervisor.py": 40})) == []
