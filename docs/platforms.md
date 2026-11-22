# Supported platforms

**emuctl is built and tested for Linux with systemd. Debian 13 is the
reference system.** Other systemd distributions should work; problems on them
are bugs worth reporting.

systemd 252 or newer is needed for `emuctl stop --now` (Debian 12 has 252,
Debian 13 has 257).

Python 3.10 or newer is needed for the command and the supervisor. The web
interface, when it arrives, will need Python 3.11 or newer and the `aiohttp`
package (`python3-aiohttp` on Debian).

## FreeBSD

emuctl started on FreeBSD, and the core still runs there: `list`, `start`,
`stop`, `console`, the supervisor and the rc.d script, with tests that keep
passing. New features are written and tested for Linux only. Where one needs
something FreeBSD does not offer in the same way (cgroup resource numbers,
`/sys/class/net`, polkit, the web interface), it reports "not available"
instead of guessing. Patches that make a feature work on FreeBSD are welcome
if they come with tests.

## Why

Most people who would run emuctl run Linux, and choosing one platform to test
properly beats claiming two and testing neither well. systemd also does work
emuctl would otherwise have to do: it counts each guest's CPU and memory,
keeps its log, and decides who may start and stop it.
