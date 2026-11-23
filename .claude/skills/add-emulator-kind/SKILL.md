---
name: add-emulator-kind
description: Add support for a new emulator to emuctl - docs page, example conf, tests for any special handling, and (once hardware.py exists) a hardware parser with an invented fixture. Use when asked to support, document or add an emulator such as a new SIMH machine, MAME, Hercules or similar.
---

# Support a new emulator

emuctl does not need code to run most emulators: a guest conf names the
command, the console ports and the prompts. "Supporting" an emulator means
finding out how it behaves under a supervisor and writing that down, plus code
only where it misbehaves in a way the conf cannot express.

## 1. Find out how it behaves

Answer these from the emulator's documentation and by running it, and record
the emulator's version with each answer:

- How does it expose the console? A TCP port it listens on is what emuctl
  needs. Does it wait for a client before running the machine?
- Does it have more than one serial port, and must each have a client?
- What does it print when the guest is at its firmware prompt, booting, at a
  login prompt, and halted? These become `boot.<n>`, `ready` and `halted`.
- How does it stop? Does it honour SIGTERM? Does it have a monitor command to
  quit (`stop_mode = monitor`) or a way to save state (`stop_mode = freeze`)?
- Does it drop the console client and reconnect, echo input, need CR or LF?
- What config files does it read, and what do they say about the machine?

## 2. Write it down

- `docs/emulators/<name>.md`, following the existing pages: what it is, a
  minimal working conf, how to stop a guest cleanly, and the gotchas with the
  version they were seen on. Plain English.
- `examples/<name>-<os>.conf`, a commented working conf with invented paths.
- Link the page from `README.md` (the Emulators line) and from
  `docs/quickstart.md` if it is a common choice.
- A `CHANGELOG.md` line under `## Unreleased`.

## 3. Code, only if needed

If the emulator needs behaviour the conf cannot express, change the
supervisor (read `AGENTS.md` rule 3 first) and teach `tests/fake_emulator.py`
to misbehave the same way, behind a new option. Then write an end-to-end test
in `tests/test_end_to_end.py` that fails without the change.

## 4. Hardware parser

emuctl reads each emulator's config to show the emulated hardware
(`emuctl info <guest> --hardware`). For a new kind:

1. Add the kind's name to `KINDS` in `config.py`, and teach `guess_kind()`
   its program name, with a case in `tests/test_config_keys.py`.
2. Write a pure function `parse_<kind>(text)` in `hardware.py` that starts
   from `empty()` and fills the common shape: `machine`, `cpu`, `memory`,
   `disks` (`unit`, `model`, `file`, `kind`, `readonly`), `nics` (`unit`,
   `model`, `mac` lower-case with colons, `host_if`), `other`. A line it does
   not understand is left out; the parser never raises. Register it in
   `PARSERS` and its usual file suffix in `CONFIG_SUFFIX`. `host_if` matters:
   the network report (`network.py`) uses it to find the guest's tap.
3. Add an invented fixture under `tests/fixtures/hardware/<kind>/` (no real
   hostnames or addresses; MACs locally administered, `02:00:00:...`;
   `test_fixtures_hold_nothing_real` checks) and tests in
   `tests/test_hardware.py`: the fixture parses to the expected result, and
   add the parser to `test_parsers_never_raise_on_nonsense`.
4. Document the kind in `docs/config.md` under `kind` and in the emulator's
   page.
5. `hardware.py` is in `NEW_MODULES` in `tools/coverage_check.py`; keep it at
   85% or more.
