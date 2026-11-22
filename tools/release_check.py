#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check a release tag before publishing it, and print its release notes.

    python3 tools/release_check.py v0.1.0 > notes.md

Fails, with the reason on stderr, unless:
  - the tag is vX.Y.Z,
  - it matches emuctl.__version__ exactly,
  - CHANGELOG.md has a dated heading for that version, "## X.Y.Z (YYYY-MM-DD)".
On success prints that heading's section, which becomes the release notes.
The release workflow runs this; run it yourself before pushing a tag.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TAG_RE = re.compile(r"v(\d+\.\d+\.\d+)")
HEADING_RE = re.compile(r"^## (\S+) \((\d{4}-\d{2}-\d{2})\)\s*$")


class ReleaseError(Exception):
    pass


def check_tag(tag, version):
    """The version a tag names, if it is a proper release tag for `version`."""
    m = TAG_RE.fullmatch(tag)
    if not m:
        raise ReleaseError(f"tag {tag!r} is not vX.Y.Z")
    if m.group(1) != version:
        raise ReleaseError(f"tag {tag} does not match emuctl.__version__ {version!r}")
    return m.group(1)


def changelog_section(text, version):
    """The body under '## <version> (<date>)', without the heading."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            if start is not None:
                return "\n".join(lines[start:i]).strip() + "\n"
            m = HEADING_RE.match(line)
            if m and m.group(1) == version:
                start = i + 1
            elif line.split()[1:2] == [version]:
                raise ReleaseError(f"CHANGELOG.md heading for {version} has no date: {line!r}")
    if start is None:
        raise ReleaseError(f"CHANGELOG.md has no '## {version} (YYYY-MM-DD)' heading")
    return "\n".join(lines[start:]).strip() + "\n"


def read_version(root=ROOT):
    with open(os.path.join(root, "src", "emuctl", "__init__.py")) as fh:
        m = re.search(r'^__version__ = "([^"]+)"', fh.read(), re.M)
    if not m:
        raise ReleaseError("no __version__ in src/emuctl/__init__.py")
    return m.group(1)


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: release_check.py vX.Y.Z\n")
        return 2
    try:
        version = check_tag(argv[1], read_version())
        with open(os.path.join(ROOT, "CHANGELOG.md")) as fh:
            notes = changelog_section(fh.read(), version)
    except (OSError, ReleaseError) as exc:
        sys.stderr.write(f"release_check: {exc}\n")
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
