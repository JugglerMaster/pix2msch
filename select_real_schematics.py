"""Scan a folder of .msch files and pick a diverse subset for screenshotting.

Real schematics are heavily biased toward common blocks (conveyors, routers,
smelters). This tool parses every schematic, reports block-type coverage, then
greedily selects schematics that add the rarest unseen block types first, so a
few hundred screenshots cover as many block types as possible.

Usage:
    python select_real_schematics.py SRC_DIR [--out DIR] [--max N] [--max-blocks N]
"""
import argparse
import json
import os
import re
import shutil

import recognize
import planets

HERE = os.path.dirname(os.path.abspath(__file__))


def scan(src, valid):
    """Parse every .msch under src. Returns list of (path, nblocks, names).

    Skips schematics referencing blocks outside the current catalog (cursed /
    legacy / environment-only entries) since they won't import cleanly."""
    entries = []
    for dirpath, _, fnames in os.walk(src):
        for fn in fnames:
            if not fn.endswith(".msch"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                _, _, blocks = recognize.parse_msch(p)
            except Exception:
                continue
            names = {b[0] for b in blocks}
            if not names <= valid:
                continue
            entries.append((p, len(blocks), names))
    return entries


def select(entries, max_pick, max_blocks):
    """Greedy: prefer schematics adding the rarest unseen types, small ones."""
    freq = {}
    for _, _, names in entries:
        for n in names:
            freq[n] = freq.get(n, 0) + 1

    seen = set()
    picked = []
    pool = [e for e in entries if e[1] <= max_blocks]
    # Planet-bound schematics beat sandbox-only ones for volume.
    on_planet = [planets.schematic_planet(e[2]) != "unknown" for e in pool]
    while len(picked) < max_pick:
        best, best_key = None, None
        for i, e in enumerate(pool):
            if e in picked:
                continue
            gain = {n for n in e[2] if n not in seen}
            # No-gain candidates still rank (rarity 0), so --max keeps
            # adding volume after coverage saturates.
            rarity = sum(1.0 / freq[n] for n in gain)
            key = (rarity, on_planet[i], -e[1])
            if best_key is None or key > best_key:
                best, best_key = e, key
        if best is None:
            break
        picked.append(best)
        seen |= best[2]
    return picked, freq, seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default=os.path.join(HERE, "training", "selected_real"))
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--max-blocks", type=int, default=400)
    args = ap.parse_args()

    print("Parsing %s ..." % args.src)
    catalog = json.load(open(os.path.join(HERE, "block_catalog.json")))
    valid = {n for n in catalog
             if not n.endswith("boulder") and n != "boulder"
             and re.fullmatch(r"[a-z0-9-]+", n)}
    entries = scan(args.src, valid)
    print("Parsed %d schematics" % len(entries))

    picked, freq, seen = select(entries, args.max, args.max_blocks)

    missing = sorted(set(catalog) - seen)

    os.makedirs(args.out, exist_ok=True)
    manifest = {}
    for i, (p, nb, names) in enumerate(picked):
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_",
                      "%03d_%s" % (i, os.path.splitext(os.path.basename(p))[0]))
        dest = os.path.join(args.out, stem + ".msch")
        shutil.copyfile(p, dest)
        manifest[stem] = {"source": p, "blocks": nb,
                          "planet": planets.schematic_planet(names),
                          "types": sorted(names)}

    with open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    print("\nSelected %d schematics covering %d block types"
          % (len(picked), len(seen)))
    print("%d catalog blocks never appear in the corpus (use generated sheets):"
          % len(missing))
    for m in missing:
        print("  " + m)


if __name__ == "__main__":
    main()
