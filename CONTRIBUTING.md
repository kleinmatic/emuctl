# Contributing

Thanks for looking. The full guide, for people and AI agents alike, is
[AGENTS.md](AGENTS.md); this page is the short version.

## Try it and run the tests

```sh
python3 -m pip install pytest pytest-cov ruff     # or use uvx
python3 -m pytest -q
ruff check src tests tools tools/devhost
eval "$(tools/devhost up)" && emuctl list         # four fake guests, no root
tools/devhost down
```

The suite needs no emulator and no root. `tests/fake_emulator.py` stands in
for one: it serves a firmware prompt, boots, halts, and can be told to drop
its console client, ignore SIGTERM or die, which is how the supervisor's
paths are exercised end to end. CI also checks coverage
(`tools/coverage_check.py`).

## Rules for changes

- The core uses the Python standard library only and runs on Python 3.10.
- Linux with systemd is the supported platform; keep the FreeBSD paths
  working ([docs/platforms.md](docs/platforms.md)).
- A change to supervisor behaviour comes with a test against the fake
  emulator.
- Nothing about a particular host belongs in the code or the tests: paths,
  names and addresses come from `emuctl.conf`, the environment or the guest's
  conf. Fixtures use invented names and addresses.
- Docs are plain English, and a change a user can see updates them.
- If a real emulator behaves differently from what the docs say, the version
  and the observed behaviour are the useful parts of a bug report.

`.claude/skills/` has checklists for common jobs: trying a change on a
throwaway host, supporting a new emulator, adding a web API route, checking a
web change against the security rules, and preparing a release.

## The changelog and versions

A change a user could notice adds a line to the `## Unreleased` section of
`CHANGELOG.md` in the same commit. A change that could break someone's setup
(a renamed command, conf key or `--json` field) also goes under
`### Breaking`. Version numbers and releases are explained in
[docs/releasing.md](docs/releasing.md); only the maintainer tags a release.

## License

Contributions are accepted under GPL-2.0-only, the project's license.
