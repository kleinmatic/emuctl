# Versions and releases

## Version numbers

emuctl uses [semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`.

What counts as emuctl's public interface, the things a version number makes
promises about:

- the commands, their options and their exit codes;
- the keys in a guest conf and in `emuctl.conf`;
- the fields in `--json` output;
- the web API, once there is one.

How each part of the number changes:

- **PATCH** (0.1.0 → 0.1.1): fixes only. Nothing a user wrote stops working.
- **MINOR** (0.1.1 → 0.2.0): new commands, keys, fields or routes. While the
  version starts with 0, a MINOR release may also break something. When it
  does, the changelog lists it under a `### Breaking` heading in that
  release's section.
- **MAJOR** (→ 1.0.0): 1.0.0 comes once the web API has gone one full
  release without a breaking change. After 1.0.0, any breaking change needs a
  new MAJOR.

## Where the version lives

In one place: `__version__` in `src/emuctl/__init__.py`. `pyproject.toml`
reads it from there.

Between releases, `main` carries the next version with `.dev0` on the end,
such as `0.2.0.dev0`. A build from `main` is never mistaken for a release.

`install.sh`, run from a git checkout, also records the commit, so
`emuctl --version` shows exactly which build a host runs:

```
emuctl 0.2.0.dev0 (3f1c2ab)
```

`-dirty` after the commit means the checkout had uncommitted changes. An
install from a release archive or with `pip` shows the version alone.

## The changelog

`CHANGELOG.md` starts with an `## Unreleased` section. Every change a user
could notice adds a line there, in the same commit as the change. Internal
changes (tests, refactoring) do not need one.

## Making a release

Only the maintainer decides to release. A contributor, or an agent, can
prepare the release commit but does not push a tag.

1. Check that CI passes on `main`.
2. Decide the new version from the rules above. Read the Unreleased section:
   anything under Breaking rules out a PATCH.
3. In `CHANGELOG.md`, rename `## Unreleased` to `## X.Y.Z (YYYY-MM-DD)`.
4. Set `__version__ = "X.Y.Z"` in `src/emuctl/__init__.py`.
5. Check them against each other:
   ```sh
   python3 tools/release_check.py vX.Y.Z
   ```
   It prints the release notes, or says what is wrong.
6. Commit (`Release X.Y.Z`), tag and push:
   ```sh
   git commit -am "Release X.Y.Z"
   git tag -a vX.Y.Z -m "emuctl X.Y.Z"
   git push origin main vX.Y.Z
   ```
7. The **Release** workflow runs on the tag. It runs the tests, runs
   `release_check.py`, builds the source archive and wheel, and publishes the
   GitHub release with that changelog section as its notes. Nobody creates a
   release by hand on GitHub.
8. Open the next version: add an empty `## Unreleased` section at the top of
   the changelog, set `__version__` to the next MINOR with `.dev0`
   (`0.3.0.dev0` after `0.2.0`), and commit.

If the workflow fails, fix the cause on `main`, delete the tag
(`git push origin :refs/tags/vX.Y.Z` and `git tag -d vX.Y.Z`), and tag again.
Never move a tag after its release has been published; make a new PATCH
release instead.

emuctl is not on PyPI. Install it from a git checkout with `install.sh`, or
from a release archive.
