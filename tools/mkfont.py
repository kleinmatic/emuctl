#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the console font: VT323's letters plus the line-drawing characters.

    python3 tools/mkfont.py VT323-Regular.ttf src/emuctl/web/static/vendor/fonts/emuctl-vt.woff2

VT323 (SIL Open Font License, after DEC's VT320 type) has no box-drawing,
block or DEC special-graphics glyphs, so the forms old systems draw with
them fall apart in it. This adds them, drawn to VT323's own 40-unit pixel
grid and to the full height of the cell so that lines join: the DEC set
(corners, tees, lines, scan lines 1 to 9, diamond, checkerboard, control
pictures), the double and heavy box sets, and block elements. It also makes
the vertical metrics agree, so every browser draws the same row height. The
result is renamed "emuctl VT", as the OFL asks of a modified font.

Called by tools/vendor.sh. Needs fontTools and brotli (development only).
"""
import sys

from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

PX = 40                 # VT323's pixel, in font units
W = 400                 # cell width (10 pixels)
TOP, BOTTOM = 800, -200  # cell top and bottom (25 pixels)
MID_X = W // 2
MID_Y = 300             # the middle of the cell, where horizontal lines run
T = PX * 2              # a light stroke: 2 pixels, as VT323's own stems
HEAVY = PX * 3
GAP = PX                # between the two strokes of a double line

# Box drawing by (up, down, left, right): 0 none, 1 light, 2 heavy, 3 double.
BOX = {
    0x2500: (0, 0, 1, 1), 0x2501: (0, 0, 2, 2), 0x2502: (1, 1, 0, 0), 0x2503: (2, 2, 0, 0),
    0x250C: (0, 1, 0, 1), 0x250F: (0, 2, 0, 2), 0x2510: (0, 1, 1, 0), 0x2513: (0, 2, 2, 0),
    0x2514: (1, 0, 0, 1), 0x2517: (2, 0, 0, 2), 0x2518: (1, 0, 1, 0), 0x251B: (2, 0, 2, 0),
    0x251C: (1, 1, 0, 1), 0x2523: (2, 2, 0, 2), 0x2524: (1, 1, 1, 0), 0x252B: (2, 2, 2, 0),
    0x252C: (0, 1, 1, 1), 0x2533: (0, 2, 2, 2), 0x2534: (1, 0, 1, 1), 0x253B: (2, 0, 2, 2),
    0x253C: (1, 1, 1, 1), 0x254B: (2, 2, 2, 2),
    0x2550: (0, 0, 3, 3), 0x2551: (3, 3, 0, 0),
    0x2552: (0, 1, 0, 3), 0x2553: (0, 3, 0, 1), 0x2554: (0, 3, 0, 3),
    0x2555: (0, 1, 3, 0), 0x2556: (0, 3, 1, 0), 0x2557: (0, 3, 3, 0),
    0x2558: (1, 0, 0, 3), 0x2559: (3, 0, 0, 1), 0x255A: (3, 0, 0, 3),
    0x255B: (1, 0, 3, 0), 0x255C: (3, 0, 1, 0), 0x255D: (3, 0, 3, 0),
    0x255E: (1, 1, 0, 3), 0x255F: (3, 3, 0, 1), 0x2560: (3, 3, 0, 3),
    0x2561: (1, 1, 3, 0), 0x2562: (3, 3, 1, 0), 0x2563: (3, 3, 3, 0),
    0x2564: (0, 1, 3, 3), 0x2565: (0, 3, 1, 1), 0x2566: (0, 3, 3, 3),
    0x2567: (1, 0, 3, 3), 0x2568: (3, 0, 1, 1), 0x2569: (3, 0, 3, 3),
    0x256A: (1, 1, 3, 3), 0x256B: (3, 3, 1, 1), 0x256C: (3, 3, 3, 3),
    0x256D: (0, 1, 0, 1), 0x256E: (0, 1, 1, 0), 0x256F: (1, 0, 1, 0), 0x2570: (1, 0, 0, 1),
    0x2574: (0, 0, 1, 0), 0x2575: (1, 0, 0, 0), 0x2576: (0, 0, 0, 1), 0x2577: (0, 1, 0, 0),
}

# 3 x 5 pixel letters for the control pictures (HT, FF, CR, LF, VT, NL).
TINY = {
    "H": ["1.1", "1.1", "111", "1.1", "1.1"], "T": ["111", ".1.", ".1.", ".1.", ".1."],
    "F": ["111", "1..", "11.", "1..", "1.."], "C": [".11", "1..", "1..", "1..", ".11"],
    "R": ["11.", "1.1", "11.", "1.1", "1.1"], "L": ["1..", "1..", "1..", "1..", "111"],
    "V": ["1.1", "1.1", "1.1", "1.1", ".1."], "N": ["1.1", "111", "111", "111", "1.1"],
}
PICTURES = {0x2409: "HT", 0x240C: "FF", 0x240D: "CR", 0x240A: "LF", 0x240B: "VT", 0x2424: "NL"}


class Glyph:
    def __init__(self):
        self.pen = TTGlyphPen(None)

    def rect(self, x0, y0, x1, y1):
        if x1 <= x0 or y1 <= y0:
            return
        p = self.pen
        p.moveTo((x0, y0))
        p.lineTo((x0, y1))
        p.lineTo((x1, y1))
        p.lineTo((x1, y0))
        p.closePath()

    def poly(self, points):
        p = self.pen
        p.moveTo(points[0])
        for pt in points[1:]:
            p.lineTo(pt)
        p.closePath()

    def glyph(self):
        return self.pen.glyph()


def strokes(weight, centre):
    """[(low, high)] for a line of `weight` across `centre`."""
    if weight == 1:
        return [(centre - T // 2, centre + T // 2)]
    if weight == 2:
        return [(centre - HEAVY // 2, centre + HEAVY // 2)]
    if weight == 3:
        half = T // 2 + GAP // 2 + PX // 2
        return [(centre - half - PX // 2, centre - half + PX // 2),
                (centre + half - PX // 2, centre + half + PX // 2)]
    return []


def box(up, down, left, right):
    g = Glyph()
    v = max(up, down)
    h = max(left, right)
    # How far a horizontal arm reaches into the middle, and a vertical one:
    # far enough to meet the other direction's outermost stroke.
    hs, vs = strokes(h, MID_Y), strokes(v, MID_X)
    v_reach_lo = min((lo for lo, hi in hs), default=MID_Y)
    v_reach_hi = max((hi for lo, hi in hs), default=MID_Y)
    h_reach_lo = min((lo for lo, hi in vs), default=MID_X)
    h_reach_hi = max((hi for lo, hi in vs), default=MID_X)
    for lo, hi in strokes(up, MID_X):
        g.rect(lo, v_reach_lo if up == 3 or h != 3 else v_reach_hi, hi, TOP)
    for lo, hi in strokes(down, MID_X):
        g.rect(lo, BOTTOM, hi, v_reach_hi if down == 3 or h != 3 else v_reach_lo)
    for lo, hi in strokes(left, MID_Y):
        g.rect(0, lo, h_reach_hi if left == 3 or v != 3 else h_reach_lo, hi)
    for lo, hi in strokes(right, MID_Y):
        g.rect(h_reach_lo if right == 3 or v != 3 else h_reach_hi, lo, W, hi)
    return g.glyph()


def scan(n):
    """DEC scan lines 1, 3, 5, 7 and 9: horizontal lines from the top of the cell down."""
    g = Glyph()
    y = TOP - n * (TOP - BOTTOM) // 10
    g.rect(0, y - PX // 2 - PX // 2, W, y + PX // 2 + PX // 2 - PX)
    return g.glyph()


def block(x0, y0, x1, y1):
    g = Glyph()
    g.rect(x0, y0, x1, y1)
    return g.glyph()


def shade(keep):
    """A dot pattern over the whole cell; keep(col, row) says which pixels are on."""
    g = Glyph()
    rows = (TOP - BOTTOM) // PX
    for r in range(rows):
        for c in range(W // PX):
            if keep(c, r):
                g.rect(c * PX, BOTTOM + r * PX, (c + 1) * PX, BOTTOM + (r + 1) * PX)
    return g.glyph()


def diamond():
    g = Glyph()
    r = 3 * PX
    g.poly([(MID_X, MID_Y + r), (MID_X + r, MID_Y), (MID_X, MID_Y - r), (MID_X - r, MID_Y)])
    return g.glyph()


def picture(letters):
    """Two tiny letters, the first high on the left, the second low on the right, as DEC drew them."""
    g = Glyph()
    for (ch, x0, ytop) in ((letters[0], 0, TOP - 2 * PX), (letters[1], 5 * PX, TOP - 9 * PX)):
        for r, line in enumerate(TINY[ch]):
            for c, bit in enumerate(line):
                if bit == "1":
                    g.rect(x0 + c * PX, ytop - (r + 1) * PX, x0 + (c + 1) * PX, ytop - r * PX)
    return g.glyph()


def build(src, out):
    font = TTFont(src)
    glyphs = {}
    for cp, arms in BOX.items():
        glyphs[cp] = box(*arms)
    for n, cp in ((1, 0x23BA), (3, 0x23BB), (7, 0x23BC), (9, 0x23BD)):
        glyphs[cp] = scan(n)
    glyphs[0x2580] = block(0, MID_Y, W, TOP)                  # upper half
    glyphs[0x2584] = block(0, BOTTOM, W, MID_Y)               # lower half
    glyphs[0x2588] = block(0, BOTTOM, W, TOP)                 # full
    glyphs[0x258C] = block(0, BOTTOM, MID_X, TOP)             # left half
    glyphs[0x2590] = block(MID_X, BOTTOM, W, TOP)             # right half
    glyphs[0x2591] = shade(lambda c, r: (c % 2 == 0) and (r % 2 == 0))
    glyphs[0x2592] = shade(lambda c, r: (c + r) % 2 == 0)     # the DEC checkerboard
    glyphs[0x2593] = shade(lambda c, r: not ((c % 2 == 1) and (r % 2 == 1)))
    glyphs[0x25C6] = diamond()
    for cp, letters in PICTURES.items():
        glyphs[cp] = picture(letters)

    order = font.getGlyphOrder()
    cmap_tables = [t for t in font["cmap"].tables if t.isUnicode()]
    for cp, glyph in glyphs.items():
        name = f"uni{cp:04X}"
        if name not in order:
            order.append(name)
        font["glyf"][name] = glyph
        font["hmtx"][name] = (W, 0)
        for t in cmap_tables:
            t.cmap[cp] = name
    font.setGlyphOrder(order)
    font["maxp"].numGlyphs = len(order)

    # One row height everywhere: hhea, typo and win metrics all 800 / 200.
    font["hhea"].ascent, font["hhea"].descent, font["hhea"].lineGap = TOP, BOTTOM, 0
    os2 = font["OS/2"]
    os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap = TOP, BOTTOM, 0
    os2.usWinAscent, os2.usWinDescent = TOP, -BOTTOM
    os2.fsSelection |= 1 << 7                                  # USE_TYPO_METRICS

    name = font["name"]
    for rec in list(name.names):
        if rec.nameID in (1, 3, 4, 6, 16, 17):
            name.removeNames(nameID=rec.nameID)
    for nid, text in ((1, "emuctl VT"), (4, "emuctl VT"), (6, "emuctlVT-Regular"),
                      (3, "emuctl VT; VT323 2.000 with line drawing")):
        name.setName(text, nid, 3, 1, 0x409)
        name.setName(text, nid, 1, 0, 0)
    name.setName("Copyright 2011 The VT323 Project Authors (peter.hull@oikoi.com). "
                 "Line-drawing glyphs added 2026 by the emuctl authors.", 0, 3, 1, 0x409)

    font.flavor = "woff2"
    font.save(out)
    print(f"mkfont: {out}: {len(glyphs)} glyphs added")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(sys.argv[1], sys.argv[2])
