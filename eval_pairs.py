#!/usr/bin/env python3
"""Score .png + .msch pairs: detect with the reference layout, compare to truth.

For every <name>.png sitting next to <name>.msch in DIR, runs the same
reference-based recognition the GUI uses, then reports per-sheet accuracy,
missed/extra blocks, rotation errors and the most common misclassifications.

Usage:
    python eval_pairs.py [DIR]           # default: training/generated
"""
import os
import sys
import argparse
from collections import Counter

import core
import recognize


def score_pair(png, msch, corpus):
    w, h, truth = recognize.parse_msch(msch)
    occ = recognize.occ_from_blocks(truth, w, h)
    _, _, det = recognize.recognize(png, corpus, dims=(w, h), occ=occ)

    tmap = {(b[1], b[2]): b for b in truth}
    dmap = {(b[1], b[2]): b for b in det}
    ok = wrong = missed = 0
    rot_wrong = 0
    confusions = Counter()
    for pos, tb in tmap.items():
        db = dmap.get(pos)
        if db is None:
            missed += 1
        elif db[0] != tb[0]:
            wrong += 1
            confusions[(tb[0], db[0])] += 1
        else:
            ok += 1
            if db[3] != tb[3]:
                rot_wrong += 1
    extra = sum(1 for p in dmap if p not in tmap)
    total = len(tmap)
    return w, h, total, ok, wrong, missed, extra, rot_wrong, confusions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", default=os.path.join("training", "generated"))
    args = ap.parse_args()

    corpus = recognize.build_corpus("examples")

    pairs = []
    for fn in sorted(os.listdir(args.dir)):
        if fn.endswith(".png"):
            msch = os.path.join(args.dir, fn[:-4] + ".msch")
            if os.path.exists(msch):
                pairs.append((os.path.join(args.dir, fn), msch))
    if not pairs:
        sys.exit("no .png/.msch pairs found in %s" % args.dir)

    tot = Counter()
    all_conf = Counter()
    print("%-28s %11s %5s %5s %6s %6s %6s %4s  %s" %
          ("sheet", "size px/tile", "cells", "ok", "wrong", "missed",
           "extra", "rot", "note"))
    for png, msch in pairs:
        name = os.path.basename(png[:-4])
        try:
            iw, ih, total, ok, wrong, missed, extra, rot_wrong, conf = \
                score_pair(png, msch, corpus)
        except Exception as e:
            print("%-28s %11s %5s %5s %6s %6s %6s %4s  %s"
                  % (name, "?", "-", "-", "-", "-", "-", "-",
                     "detection failed: %s" % type(e).__name__))
            continue
        from PIL import Image
        pw, ph = Image.open(png).size
        sw, sh = recognize.parse_msch(msch)[:2]
        ppt = min(pw / sw if sw else 0, ph / sh if sh else 0)
        note = ""
        if ppt < 8:
            note = "TOO SMALL (%.1fpx/tile)" % ppt
        elif extra > total * 0.3:
            note = "background not plain?"
        print("%-28s %4dx%-4d %5.1f %5d %5d %6d %6d %6d %4d  %s"
              % (name, pw, ph, ppt, total, ok, wrong, missed, extra,
                 rot_wrong, note))
        tot["cells"] += total
        tot["ok"] += ok
        tot["wrong"] += wrong
        tot["missed"] += missed
        tot["extra"] += extra
        tot["rot"] += rot_wrong
        all_conf.update(conf)

    print("-" * 66)
    acc = tot["ok"] / max(1, tot["cells"])
    print("TOTAL: %d/%d cells correct (%.1f%%), %d wrong, %d missed, "
          "%d extra, %d rotation errors"
          % (tot["ok"], tot["cells"], acc * 100, tot["wrong"],
             tot["missed"], tot["extra"], tot["rot"]))

    if all_conf:
        print("\nTop confusions (truth -> guessed):")
        for (t, d), n in all_conf.most_common(15):
            print("  %-24s -> %-24s x%d" % (t, d, n))


if __name__ == "__main__":
    main()
