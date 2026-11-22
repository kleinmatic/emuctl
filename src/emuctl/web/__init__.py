# SPDX-License-Identifier: GPL-2.0-only
"""The web interface, `emuctl web`. See docs/web.md.

Needs Python 3.11 and aiohttp. The rest of emuctl needs neither: the command
line imports only main.py, and main.py, conf.py, auth.py and tls.py use the
standard library alone, so `emuctl web passwd` works before aiohttp is
installed. aiohttp is imported only when the daemon starts.
"""
