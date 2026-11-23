#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the console font: VT323's letters plus the line-drawing characters.

    python3 tools/mkfont.py VT323-Regular.ttf src/emuctl/web/static/vendor/fonts/emuctl-vt.woff2

VT323 (SIL Open Font License, after DEC's VT320 type) has no box-drawing,
block or DEC special-graphics glyphs, so the forms old systems draw with
them fall apart in it. This adds them, drawn on VT323's own grid (a pixel
is 40 units; a cell is 10 by 25 pixels): the box-drawing set (light, heavy,
double, rounded corners), block elements and shades, and the rest of the
DEC special graphics set (scan lines 1, 3, 7 and 9, diamond, degree, plus or
minus, the control pictures). Lines reach a little past the cell, so that
neighbouring cells overlap and no gap shows when a browser rounds a row
height. It also makes the vertical metrics agree, so every browser draws
the same row height. The result is renamed "emuctl VT", as the OFL asks of
a modified font.

Called by tools/vendor.sh. Needs fontTools and brotli (development only).
"""
import sys

from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

PX = 40                     # VT323's pixel, in font units
W = 400                     # cell width (10 pixels)
TOP, BOTTOM = 800, -200     # cell top and bottom (25 pixels)
MID_X = W // 2
MID_Y = 300                 # the middle of the cell, where horizontal lines run
LIGHT = 2 * PX              # stroke widths, as VT323's own stems
HEAVY = 3 * PX
GAP = 2 * PX                # between the centres of a double line's two strokes, each side
OVER = PX                   # how far a line reaches into the next cell

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
    # Rounded corners, drawn square: at this size the difference is a pixel.
    0x256D: (0, 1, 0, 1), 0x256E: (0, 1, 1, 0), 0x256F: (1, 0, 1, 0), 0x2570: (1, 0, 0, 1),
    0x2574: (0, 0, 1, 0), 0x2575: (1, 0, 0, 0), 0x2576: (0, 0, 0, 1), 0x2577: (0, 1, 0, 0),
}

# 3 x 5 pixel letters for the control pictures, as the VT100 drew them.
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
        self.empty = True

    def rect(self, x0, y0, x1, y1):
        if x1 <= x0 or y1 <= y0:
            return
        p = self.pen
        p.moveTo((x0, y0))
        p.lineTo((x0, y1))
        p.lineTo((x1, y1))
        p.lineTo((x1, y0))
        p.closePath()
        self.empty = False

    def poly(self, points):
        p = self.pen
        p.moveTo(points[0])
        for pt in points[1:]:
            p.lineTo(pt)
        p.closePath()
        self.empty = False

    def glyph(self):
        return self.pen.glyph()


def strokes(weight, centre):
    """[(low, high)] across a line of `weight` running through `centre`."""
    if weight == 1:
        return [(centre - LIGHT // 2, centre + LIGHT // 2)]
    if weight == 2:
        return [(centre - HEAVY // 2, centre + HEAVY // 2)]
    if weight == 3:
        return [(centre - GAP - PX // 2, centre - GAP + PX // 2),
                (centre + GAP - PX // 2, centre + GAP + PX // 2)]
    return []


def box(up, down, left, right):
    """A box-drawing glyph. Each arm runs from the cell's edge (and a little
    past it) to the middle, far enough to meet the lines across it.

    An arm meeting a double line stops at the double's near stroke if the
    double runs straight through (a tee), and crosses to its far stroke if
    the double turns (a corner), so the corner is closed."""
    if 3 in (up, down) and 3 in (left, right):
        return double_box(up, down, left, right)
    g = Glyph()
    vs = strokes(max(up, down), MID_X)          # x ranges of the vertical strokes
    hs = strokes(max(left, right), MID_Y)       # y ranges of the horizontal strokes
    through_h = bool(left and right)
    through_v = bool(up and down)

    for going_up, weight in ((True, up), (False, down)):
        if not hs:
            lo_end, hi_end = MID_Y - LIGHT // 2, MID_Y + LIGHT // 2
        elif len(hs) == 2 and through_h:        # meets a double horizontal: near stroke
            lo_end, hi_end = (hs[1][0], hs[0][1])
        else:
            lo_end, hi_end = hs[0][0], hs[-1][1]
        for x0, x1 in strokes(weight, MID_X):
            if going_up:
                g.rect(x0, lo_end, x1, TOP + OVER)
            else:
                g.rect(x0, BOTTOM - OVER, x1, hi_end)
    for going_left, weight in ((True, left), (False, right)):
        if not vs:
            lo_end, hi_end = MID_X - LIGHT // 2, MID_X + LIGHT // 2
        elif len(vs) == 2 and through_v:        # meets a double vertical: near stroke
            lo_end, hi_end = (vs[1][0], vs[0][1])
        else:
            lo_end, hi_end = vs[0][0], vs[-1][1]
        for y0, y1 in strokes(weight, MID_Y):
            if going_left:
                g.rect(-OVER, y0, hi_end, y1)
            else:
                g.rect(lo_end, y0, W + OVER, y1)
    return g.glyph()


def double_box(up, down, left, right):
    """Glyphs where a double vertical meets a double horizontal: ╔ ╗ ╚ ╝ ╠ ╣
    ╦ ╩ ╬. Drawn as the four quarter-corners they are made of: each quarter
    of the cell that lies between two arms gets an inner corner, and each
    side without an arm gets a straight outer stroke."""
    g = Glyph()
    (xl0, xl1), (xr0, xr1) = strokes(3, MID_X)      # left and right vertical strokes
    (yb0, yb1), (yt0, yt1) = strokes(3, MID_Y)      # bottom and top horizontal strokes
    t, b, lft, rt = TOP + OVER, BOTTOM - OVER, -OVER, W + OVER
    # Quarters: (vertical arm, horizontal arm) -> corner drawn in that quarter.
    if up and left:     # top-left quarter: └ shape from the inner strokes
        g.rect(xl0, yt0, xl1, t)
        g.rect(lft, yt0, xl1, yt1)
    if up and right:
        g.rect(xr0, yt0, xr1, t)
        g.rect(xr0, yt0, rt, yt1)
    if down and left:
        g.rect(xl0, b, xl1, yb1)
        g.rect(lft, yb0, xl1, yb1)
    if down and right:
        g.rect(xr0, b, xr1, yb1)
        g.rect(xr0, yb0, rt, yb1)
    # Sides with no arm: one straight stroke along that side, from arm to arm.
    if not up:
        g.rect(xl0 if left else xr0, yt0, xr1 if right else xl1, yt1)
        if left and not right:
            g.rect(lft, yt0, xr1, yt1)
        if right and not left:
            g.rect(xl0, yt0, rt, yt1)
        if left and right:
            g.rect(lft, yt0, rt, yt1)
    if not down:
        if left and right:
            g.rect(lft, yb0, rt, yb1)
        elif left:
            g.rect(lft, yb0, xr1, yb1)
        elif right:
            g.rect(xl0, yb0, rt, yb1)
    if not left:
        if up and down:
            g.rect(xl0, b, xl1, t)
        elif up:
            g.rect(xl0, yb0, xl1, t)
        elif down:
            g.rect(xl0, b, xl1, yt1)
    if not right:
        if up and down:
            g.rect(xr0, b, xr1, t)
        elif up:
            g.rect(xr0, yb0, xr1, t)
        elif down:
            g.rect(xr0, b, xr1, yt1)
    return g.glyph()


def scan(n):
    """DEC scan line n (1, 3, 7 or 9) of the nine from the top of the cell to
    the bottom. Scan line 5 is the middle, where ─ runs."""
    g = Glyph()
    centre = TOP - LIGHT // 2 - (n - 1) * (TOP - BOTTOM - LIGHT) // 8
    g.rect(-OVER, centre - LIGHT // 2, W + OVER, centre + LIGHT // 2)
    return g.glyph()


def block(x0, y0, x1, y1):
    """A solid block; edges on the cell's border reach past it."""
    g = Glyph()
    g.rect(x0 - (OVER if x0 == 0 else 0), y0 - (OVER if y0 == BOTTOM else 0),
           x1 + (OVER if x1 == W else 0), y1 + (OVER if y1 == TOP else 0))
    return g.glyph()


def shade(keep):
    """A dot pattern over the whole cell; keep(col, row) says which pixels are on."""
    g = Glyph()
    for r in range((TOP - BOTTOM) // PX):
        for c in range(W // PX):
            if keep(c, r):
                g.rect(c * PX, BOTTOM + r * PX, (c + 1) * PX, BOTTOM + (r + 1) * PX)
    return g.glyph()


def diamond():
    g = Glyph()
    r = 4 * PX
    g.poly([(MID_X, MID_Y + r), (MID_X + r, MID_Y), (MID_X, MID_Y - r), (MID_X - r, MID_Y)])
    return g.glyph()


def picture(letters):
    """Two tiny letters, the first high on the left, the second low on the right."""
    g = Glyph()
    for ch, x0, ytop in ((letters[0], PX, TOP - 3 * PX), (letters[1], 5 * PX, TOP - 10 * PX)):
        for r, line in enumerate(TINY[ch]):
            for c, bit in enumerate(line):
                if bit == "1":
                    g.rect(x0 + c * PX, ytop - (r + 1) * PX, x0 + (c + 1) * PX, ytop - r * PX)
    return g.glyph()


def glyphs():
    out = {cp: box(*arms) for cp, arms in BOX.items()}
    for n, cp in ((1, 0x23BA), (3, 0x23BB), (7, 0x23BC), (9, 0x23BD)):
        out[cp] = scan(n)
    out[0x2580] = block(0, MID_Y, W, TOP)                  # upper half
    out[0x2584] = block(0, BOTTOM, W, MID_Y)               # lower half
    out[0x2588] = block(0, BOTTOM, W, TOP)                 # full
    out[0x258C] = block(0, BOTTOM, MID_X, TOP)             # left half
    out[0x2590] = block(MID_X, BOTTOM, W, TOP)             # right half
    out[0x2591] = shade(lambda c, r: c % 2 == 0 and r % 2 == 0)
    out[0x2592] = shade(lambda c, r: (c + r) % 2 == 0)     # the DEC checkerboard
    out[0x2593] = shade(lambda c, r: not (c % 2 == 1 and r % 2 == 1))
    out[0x25C6] = diamond()
    for cp, letters in PICTURES.items():
        out[cp] = picture(letters)
    return out


def build(src, out):
    font = TTFont(src, recalcTimestamp=False)   # the same bytes every build
    added = glyphs()
    order = font.getGlyphOrder()
    cmap_tables = [t for t in font["cmap"].tables if t.isUnicode()]
    for cp, glyph in added.items():
        name = f"uni{cp:04X}"
        if name not in order:
            order.append(name)
        font["glyf"][name] = glyph
        glyph.recalcBounds(font["glyf"])
        # The side bearing must be the outline's real left edge, even where
        # that is left of the cell: renderers move the outline to match it.
        font["hmtx"][name] = (W, glyph.xMin if glyph.numberOfContours else 0)
        for t in cmap_tables:
            t.cmap[cp] = name
    font.setGlyphOrder(order)
    font["maxp"].numGlyphs = len(order)

    # A terminal places every character in its cell: no kerning, no
    # ligatures, nothing that moves one glyph for its neighbour.
    for table in ("GPOS", "GSUB", "GDEF", "kern", "DSIG"):
        if table in font:
            del font[table]
    font["post"].isFixedPitch = 1

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
    print(f"mkfont: {out}: {len(added)} glyphs added")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(sys.argv[1], sys.argv[2])
