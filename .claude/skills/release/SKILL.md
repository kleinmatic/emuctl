---
name: release
description: Prepare an emuctl release (changelog, version, checks) following docs/releasing.md, and stop before tagging. Use when asked to prepare, cut or plan a release or a version bump.
---

# Prepare an emuctl release

Read `docs/releasing.md` first; it is the rule. This skill walks it and stops
before anything is published.

1. Confirm the working tree is clean and on `main`, and that CI passed on the
   latest commit (`gh run list --branch main --limit 1`).
2. Propose the version. Read `## Unreleased` in `CHANGELOG.md` and the commits
   since the last tag (`git log $(git describe --tags --abbrev=0)..HEAD --oneline`).
   Fixes only → PATCH. Anything new → MINOR. Anything under `### Breaking`, or
   a change to a command, conf key, `--json` field or API route that could
   break a user's setup → MINOR while in 0.x, and it must be listed under
   Breaking. Say which commits drove the choice.
3. Check the Unreleased section covers every user-visible change in those
   commits. Add missing lines, in plain English.
4. Rename `## Unreleased` to `## X.Y.Z (YYYY-MM-DD)` and set `__version__`.
5. Run `python3 -m pytest -q`, `ruff check src tests tools`, and
   `python3 tools/release_check.py vX.Y.Z`. All three must pass.
6. Commit `Release X.Y.Z`. **Stop.** Do not tag and do not push. Report the
   version, the reason for it, and the release notes, and ask the maintainer
   to publish it with:
   ```sh
   git tag -a vX.Y.Z -m "emuctl X.Y.Z" && git push origin main vX.Y.Z
   ```
7. Once the maintainer has published and the Release workflow has passed,
   open the next version: an empty `## Unreleased` at the top and
   `__version__` set to the next MINOR with `.dev0`.
