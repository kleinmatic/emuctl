# Contributing

## Running the tests

```sh
python3 -m pip install pytest      # or: uvx --with pytest pytest
python3 -m pytest
```

The suite needs no emulator and no root. `tests/fake_emulator.py` stands in
for one: it serves a firmware prompt, boots, halts, and can be told to drop
its console client, ignore SIGTERM or die, which is how the supervisor's
paths are exercised end to end. Tests run on Linux and FreeBSD.

`ruff check src tests` should pass.

## Rules for changes

- The runtime uses the Python standard library only. It runs as a system
  service on hosts where installing packages is unwelcome.
- Python 3.9 is the oldest supported version.
- A change to supervisor behaviour comes with a test against the fake
  emulator.
- Nothing about a particular host belongs in the code: paths, names and
  addresses come from `emuctl.conf`, the environment or the guest's conf.
- If a real emulator behaves differently from what the docs say, the version
  and the observed behaviour are the useful parts of a bug report.

## License

Contributions are accepted under GPL-2.0-only, the project's license.
