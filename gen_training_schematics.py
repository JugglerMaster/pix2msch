"""Generate .msch training sheets covering every placeable block x rotation.

Each output sheet is a normal Mindustry schematic that can be imported in-game
(sandbox for free placement), built, and screenshotted. Because the .msch is
written next to nothing else, it doubles as the ground-truth reference for the
same-name detection flow: screenshot called <sheet>.png next to <sheet>.msch
gives pixel-exact labels for every cell -> training exemplars for free.

Blocks are planet-gated in Mindustry, so sheets are split:
  coverage-serpulo-XX.msch         -> import on a Serpulo sandbox map
  coverage-erekir-XX.msch          -> import on an Erekir sandbox map
  coverage-editor-XX.msch          -> sandbox/source blocks; place from the map editor
  coverage-special-<planet>-XX.msch -> blocks needing water/heat/vents/cliffs;
                                      place near those tiles or skip

Usage:
    python gen_training_schematics.py [--out DIR] [--per-sheet N] [--gap N]
"""
import argparse
import json
import os
import re

import core
import planets

# Blocks that only place on specific tiles (pumps need liquid, thermal
# generators heat, condensers steam vents, cliff crushers cliff walls).
# Kept out of the normal sheets so they never block schematic placement.
SPECIAL_TILE = {
    "mechanical-pump", "rotary-pump", "impulse-pump", "reinforced-pump",
    "water-extractor", "thermal-generator", "turbine-condenser",
    "vent-condenser", "cliff-crusher", "large-cliff-crusher",
}
SPECIAL_WHY = {
    "mechanical-pump": "water", "rotary-pump": "water",
    "impulse-pump": "water", "reinforced-pump": "water",
    "water-extractor": "groundwater",
    "thermal-generator": "heat",
    "turbine-condenser": "steam vents", "vent-condenser": "steam vents",
    "cliff-crusher": "cliffs", "large-cliff-crusher": "cliffs",
}

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "block_catalog.json")
MAX_SHEET = 120  # stay under Mindustry's 128x128 limit with margin


def load_placeable():
    """Sorted {name: catalog entry}, excluding environment boulders and
    malformed catalog entries."""
    with open(CATALOG) as f:
        cat = json.load(f)
    return {n: v for n, v in sorted(cat.items())
            if not n.endswith("boulder") and n != "boulder"
            and re.fullmatch(r"[a-z0-9-]+", n)}


def expand_rotations(blocks):
    """Flatten to (name, rot, size) placements, all 4 rotations when rotating."""
    out = []
    for name, info in blocks:
        rots = [0, 1, 2, 3] if info["rotates"] else [0]
        for rot in rots:
            out.append((name, rot, info["size"]))
    return out


def pack(placements, width):
    """Greedy row packing. Returns (height, [(name, rot, size, x, y)])."""
    x = y = 0
    row_h = 0
    placed = []
    for name, rot, size in placements:
        if x + size > width:
            x = 0
            y += row_h + GAP
            row_h = 0
        placed.append((name, rot, size, x, y))
        x += size + GAP
        row_h = max(row_h, size)
    return y + row_h, placed


def realistic_cluster():
    """A small base fragment: production fed by conveyors, power, storage.

    Real schematics provide plenty of context like this, but one hand-built
    example is useful as a template for running-state screenshots.
    """
    cat = load_placeable()
    size = lambda n: cat[n]["size"]

    blocks = []  # (name, x, y, rot)

    def put(name, x, y, rot=0):
        s = size(name)
        blocks.append((name, x, y, rot))
        return s

    # Production column: Serpulo-only producers stacked with gaps.
    producers = ["silicon-smelter", "kiln", "graphite-press",
                 "pulverizer", "coal-centrifuge", "pyratite-mixer"]
    y = 2
    rows = []
    for p in producers:
        s = put(p, 8, y)
        rows.append((y, s))
        y += s + 3

    # Conveyor feed line on the left, snaking into each producer.
    for ry, rs in rows:
        cy = ry + rs // 2
        put("conveyor", 4, cy, 0)
        put("conveyor", 5, cy, 0)
        put("sorter", 6, cy, 0)
        put("junction", 7, cy)

    # Power spine down the right side.
    py = 2
    while py < y:
        put("power-node-large", 16, py)
        py += size("power-node-large") + 4

    # Storage + unloading at the bottom.
    by = y + 1
    put("vault", 4, by)
    put("container", 10, by)
    put("unloader", 9, by + 1)
    put("router", 12, by + 1)
    put("overflow-gate", 13, by + 1)
    put("bridge-conveyor", 14, by + 1, 0)

    w = max(x + size(n) for n, x, _, _ in blocks) + 2
    h = max(yy + size(n) for n, _, yy, _ in blocks) + 2
    return min(w, MAX_SHEET), min(h, MAX_SHEET), blocks


GAP = 1
SHEET_W = 24      # narrow enough to fit any editor window at readable zoom
PER_SHEET = 12
MAX_H = 22        # split rather than grow taller


def build_sheets(placements, per_sheet):
    """Incrementally pack placements; start a new sheet when it exceeds
    PER_SHEET items or MAX_H rows."""
    sheets = []
    cur = []
    for p in placements:
        cur.append(p)
        h, _ = pack(cur, SHEET_W)
        if len(cur) >= per_sheet or h > MAX_H:
            sheets.append(cur)
            cur = []
    if cur:
        sheets.append(cur)
    return sheets


def main():
    global GAP, SHEET_W, PER_SHEET
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "training", "generated"))
    ap.add_argument("--per-sheet", type=int, default=24)
    ap.add_argument("--gap", type=int, default=1)
    args = ap.parse_args()
    PER_SHEET, GAP = args.per_sheet, args.gap

    os.makedirs(args.out, exist_ok=True)
    blocks = load_placeable()
    groups = planets.classify(list(blocks))
    manifest = {}
    n_sheets = 0

    for planet in ("serpulo", "erekir", "editor"):
        names = [n for n in groups[planet] if n in blocks]
        land = [n for n in names if n not in SPECIAL_TILE]
        special = [n for n in names if n in SPECIAL_TILE]
        for prefix, group_names, why in (
                ("coverage-%s" % planet, land, None),
                ("coverage-special-%s" % planet, special,
                 sorted({SPECIAL_WHY[n] for n in special if n in SPECIAL_WHY}))):
            flat = expand_rotations([(n, blocks[n]) for n in group_names])
            sheets = build_sheets(flat, PER_SHEET)
            for idx, chunk in enumerate(sheets, 1):
                name = "%s-%02d" % (prefix, idx)
                h, placed = pack(chunk, SHEET_W)
                desc = "pix2msch %s %d/%d" % (prefix, idx, len(sheets))
                if why:
                    desc += " (needs %s)" % ", ".join(why)
                seen = []
                for n, _, _, _, _ in placed:
                    if n not in seen:
                        seen.append(n)
                desc += ": " + ", ".join(seen[:8]) + \
                        ("..." if len(seen) > 8 else "")
                tags = {"labels": "[]", "name": name, "description": desc}
                payload = [(n, x, y, None, rot) for n, rot, s, x, y in placed]
                path = os.path.join(args.out, name + ".msch")
                hh = max((y + s for _, _, s, _, y in placed), default=8)
                core._write_schematic(SHEET_W, max(hh, 8), tags, payload,
                                      path, "path")
                manifest[name] = [
                    {"name": n, "rot": r, "x": x, "y": y, "size": s}
                    for n, r, s, x, y in placed
                ]
                n_sheets += 1

    # One realistic-context sheet (Serpulo).
    w, h, cblocks = realistic_cluster()
    payload = [(n, x, y, None, r) for n, x, y, r in cblocks]
    tags = {"labels": "[]", "name": "cluster-demo",
            "description": "pix2msch training: realistic base fragment"}
    path = os.path.join(args.out, "cluster-demo.msch")
    core._write_schematic(w, h, tags, payload, path, "path")
    manifest["cluster-demo"] = [
        {"name": n, "x": x, "y": y, "rot": r, "size": blocks[n]["size"]}
        for n, x, y, r in cblocks
    ]
    n_sheets += 1

    with open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    total = sum(len(v) for v in manifest.values())
    print("\n%d sheets, %d placements, %d block types"
          % (n_sheets, total, len(blocks)))
    for planet in ("serpulo", "erekir", "editor"):
        print("  %-7s %3d types" % (planet, len(groups[planet])))


if __name__ == "__main__":
    main()
