# SPDX-License-Identifier: GPL-2.0-only
"""The version string and the release checks."""
import pytest
import release_check
from release_check import ReleaseError, changelog_section, check_tag

import emuctl

CHANGELOG = """# Changelog

## Unreleased

- Something new.

## 0.2.0 (2026-12-01)

- Second.

### Breaking

- A key was renamed.

## 0.1.0 (2026-11-22)

First public version.
"""


def test_tag_matches():
    assert check_tag("v0.2.0", "0.2.0") == "0.2.0"


@pytest.mark.parametrize("tag,version", [
    ("v0.2.0", "0.1.0"),            # wrong version
    ("v0.2.0", "0.2.0.dev0"),       # main between releases
    ("0.2.0", "0.2.0"),             # no v
    ("v0.2", "0.2"),                # not X.Y.Z
    ("v0.2.0-rc1", "0.2.0-rc1"),
])
def test_tag_mismatch(tag, version):
    with pytest.raises(ReleaseError):
        check_tag(tag, version)


def test_section_in_the_middle():
    notes = changelog_section(CHANGELOG, "0.2.0")
    assert notes.startswith("- Second.")
    assert "### Breaking" in notes          # subheadings stay in the section
    assert "First public" not in notes


def test_section_at_the_end():
    assert changelog_section(CHANGELOG, "0.1.0") == "First public version.\n"


def test_section_missing():
    with pytest.raises(ReleaseError, match="no '## 0.3.0"):
        changelog_section(CHANGELOG, "0.3.0")


def test_section_without_a_date():
    with pytest.raises(ReleaseError, match="no date"):
        changelog_section("## 0.3.0\n\n- x\n", "0.3.0")


def test_main_against_this_repo(capsys):
    """The real __version__ and CHANGELOG.md agree when main is at a release."""
    version = release_check.read_version()
    if ".dev" in version:
        assert release_check.main(["x", f"v{version}"]) == 1
    else:
        assert release_check.main(["x", f"v{version}"]) == 0
        assert capsys.readouterr().out.strip()


def test_full_version_without_build_file(tmp_path):
    assert emuctl.full_version(str(tmp_path)) == emuctl.__version__


def test_full_version_with_build_file(tmp_path):
    (tmp_path / "BUILD").write_text("2d687ac\n")
    assert emuctl.full_version(str(tmp_path)) == f"{emuctl.__version__} (2d687ac)"


def test_cli_version(capsys):
    from emuctl.cli import main
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.startswith(f"emuctl {emuctl.__version__}")
