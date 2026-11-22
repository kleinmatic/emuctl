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

## 4. Hardware parser (once `src/emuctl/hardware.py` exists)

From phase 1 of the web work, emuctl reads each emulator's config to show the
emulated hardware. For a new kind:

1. Add the kind's name to the allowed values of the `kind` key in
   `config.py`, and to the guess from the program name, with a test.
2. Write a pure function `parse_<kind>(text)` in `hardware.py` returning the
   common shape: `machine`, `cpu`, `memory`, `disks`, `nics` (each with
   `model`, `mac`, `host_if`), `other`. A line it does not understand is left
   out; the parser never raises.
3. Add an invented fixture under `tests/fixtures/hardware/<kind>/` (no real
   hostnames, MACs or addresses) and tests: the fixture parses to the
   expected result, and a nonsense file returns an empty result without an
   exception.
4. Document the kind in `docs/config.md` under `kind` and in the emulator's
   page.
5. `hardware.py` is in `NEW_MODULES` in `tools/coverage_check.py`; keep it at
   90% or more.

Until `hardware.py` exists, skip this step. Update this skill when it lands.
