# SPDX-License-Identifier: GPL-2.0-only
"""Telnet byte handling for the console broker.

Three behaviours, all needed for a terminal client to work against a guest
console:
  - announce WILL ECHO + WILL SGA so clients go character-at-a-time and leave
    echo (and password suppression) to the guest;
  - strip inbound IAC so negotiation bytes never reach the guest;
  - normalise CR-LF, CR-NUL and a lone LF to a single CR, or Enter submits an
    extra empty line.
"""

TN_INIT = b"\xff\xfb\x01\xff\xfb\x03"   # IAC WILL ECHO, IAC WILL SUPPRESS-GO-AHEAD


def strip_telnet(data, st):
    """Drop IAC negotiation. `st` carries parser state across recv() calls:
    0 = normal, 1 = saw IAC, 2 = saw IAC + verb (the option byte follows)."""
    out = bytearray()
    for b in data:
        if st == 0:
            if b == 0xFF:
                st = 1
            else:
                out.append(b)
        elif st == 1:
            if b == 0xFF:
                out.append(0xFF)                    # escaped literal 0xFF
                st = 0
            elif b in (0xFB, 0xFC, 0xFD, 0xFE):
                st = 2                              # WILL / WONT / DO / DONT
            else:
                st = 0                              # other two-byte command
        else:
            st = 0                                  # consumed the option byte
    return bytes(out), st


def crlf_fix(data, pend_cr):
    """Normalise NVT Enter to a single CR. `pend_cr` carries a trailing CR
    across recv() calls."""
    out = bytearray()
    for b in data:
        if b == 0x0D:
            out.append(0x0D)
            pend_cr = True
            continue
        if pend_cr:
            pend_cr = False
            if b in (0x0A, 0x00):                   # LF or NUL after CR: part of Enter
                continue
        if b == 0x0A:
            out.append(0x0D)                        # lone LF -> CR
        else:
            out.append(b)
    return bytes(out), pend_cr
