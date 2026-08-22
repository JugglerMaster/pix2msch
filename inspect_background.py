#!/usr/bin/env python3
"""Measure the background of a screenshot: palette histogram + stripe pattern.

Drop any raw capture (e.g. of the schematic editor) here and run:
    python inspect_background.py PATH.png

Prints which of the 16 game palette colors dominate, and the horizontal
run-length pattern of the top-left corner rows -- enough to identify the
editor's stripe colors and period so exemplars can be composited to match.
"""
import sys
from collections import Counter

import core
import recognize


def main(path):
    img = core._palette_image(path)
    px = img.load()
    w, h = img.size
    print("size: %dx%d" % (w, h))

    hist = Counter()
    for y in range(h):
        for x in range(w):
            hist[px[x, y]] += 1
    total = w * h
    print("\npalette histogram (color -> share):")
    for c, n in hist.most_common(8):
        idx = core.tuple_array.index(c) if c in core.tuple_array else "?"
        print("  %-15s idx=%-3s %5.1f%%" % (str(c), idx, 100.0 * n / total))

    # Run-length pattern along the top rows (likely pure background).
    print("\ntop-row run-length pattern (first 120 px):")
    for y in range(min(4, h)):
        runs = []
        cur = px[0, y]
        n = 1
        for x in range(1, min(120, w)):
            if px[x, y] == cur:
                n += 1
            else:
                runs.append((cur, n))
                cur = px[x, y]
                n = 1
        runs.append((cur, n))
        print("  y=%d: %s" % (y, " ".join("%s x%d" % (c, n) for c, n in runs[:14])))

    # Vertical pattern too (stripes may be diagonal/checkerboard).
    print("\nleft-column run-length pattern (first 60 px):")
    for x in range(min(2, w)):
        runs = []
        cur = px[x, 0]
        n = 1
        for y in range(1, min(60, h)):
            if px[x, y] == cur:
                n += 1
            else:
                runs.append((cur, n))
                cur = px[x, y]
                n = 1
        runs.append((cur, n))
        print("  x=%d: %s" % (x, " ".join("%s x%d" % (c, n) for c, n in runs[:14])))


if __name__ == "__main__":
    main(sys.argv[1])
