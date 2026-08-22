"""Render synthetic screenshot sheets with pixel-exact ground truth.

Every sheet is a random schematic drawn offline: real game sprites composited
onto plausible backgrounds (editor stripes / flat floors / noisy floors) with
drop shadows, then brightness/noise/blur augmented and palette-quantized like
an actual capture. A matching .msch is written next to each PNG, so pairs
dropped into examples/ flow through build_corpus unchanged — grid fitting,
cell-crop exemplars, occupancy data and classifier training all treat them
like real screenshots.

This removes the manual bottleneck (import in-game -> build -> screenshot):
one run yields hundreds of labeled cells across every block type x rotation.

Usage:
    python gen_synth_sheets.py [--sheets 40] [--out examples] [--seed 0]
                               [--min-scale 12] [--max-scale 34]
                               [--start 0] [--planet serpulo|erekir|editor]
"""
import argparse
import contextlib
import io as _io
import json
import os
import random

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

import core
import planets
import recognize
import sprite_train
from gen_training_schematics import load_placeable

HERE = os.path.dirname(os.path.abspath(__file__))

# Flat-floor background candidates (palette indices): editor gray, dark,
# light steel, sand, water blue, grass green.
FLAT_BGS = (9, 5, 3, 11, 6, 10)

_stripe_cache = None
_sym_cache = {}


def _striped_bg(w, h):
    """Editor diagonal stripes, cropped from a cached pattern."""
    global _stripe_cache
    if _stripe_cache is None or _stripe_cache.size[0] < w \
            or _stripe_cache.size[1] < h:
        _stripe_cache = sprite_train._editor_grid_bg(
            (max(w, 1024), max(h, 1024)))
    return _stripe_cache.crop((0, 0, w, h))


def _noisy_floor(w, h, idx, nprng):
    """Flat floor with per-pixel speckle, mimicking ore/floor textures."""
    arr = np.empty((h, w, 3), dtype=np.float64)
    arr[:, :] = core.tuple_array[idx]
    arr += nprng.normal(0, 7.0, (h, w, 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _rotations_for(name, info):
    """Rotations worth rendering: symmetric sprites only get rot 0 so the
    ground truth always matches the pixels."""
    if not info.get("rotates") and name not in recognize.DIRECTIONAL:
        return [0]
    hit = _sym_cache.get(name)
    if hit is None:
        spr = sprite_train._raw_sprite(name)
        if spr is None:
            hit = True
        else:
            diff = ImageChops.difference(spr, spr.rotate(90, expand=False))
            hit = diff.convert("RGB").getbbox() is None
        _sym_cache[name] = hit
    return [0] if hit else [0, 1, 2, 3]


def sample_sheet(groups, cat, rng):
    """Random non-overlapping placement on a random grid.

    Returns (planet, width, height, [(name, x, y, rot, size)]).
    """
    planet = rng.choice(sorted(groups))
    names = [n for n in groups[planet] if n in cat]
    w = rng.randint(14, 24)
    h = rng.randint(14, 24)
    occ = [[False] * w for _ in range(h)]
    cand = []
    for n in names:
        for r in _rotations_for(n, cat[n]):
            cand.append((n, r))
    target = rng.randint(10, min(26, len(cand)))
    placements = []
    for _ in range(target * 60):
        if len(placements) >= target:
            break
        name, rot = cand[rng.randrange(len(cand))]
        s = cat[name]["size"]
        x = rng.randint(0, w - s)
        y = rng.randint(0, h - s)
        if any(occ[y + dy][x + dx] for dy in range(s) for dx in range(s)):
            continue
        for dy in range(s):
            for dx in range(s):
                occ[y + dy][x + dx] = True
        placements.append((name, x, y, rot, s))
    return planet, w, h, placements


def render_sheet(w, h, placements, scale, rng, nprng):
    """Composite one sheet at `scale` px/tile; returns a palette-quantized RGB."""
    W, H = w * scale, h * scale
    if rng.random() < 0.6:
        # Editor gray or the diagonal-stripe editor grid — what real
        # schematic screenshots actually show.
        if rng.random() < 0.5:
            img = Image.new("RGB", (W, H), core.tuple_array[9])
        else:
            img = _striped_bg(W, H).convert("RGB")
    else:
        idx = rng.choice(FLAT_BGS)
        if rng.random() < 0.4:
            img = _noisy_floor(W, H, idx, nprng)
        else:
            img = Image.new("RGB", (W, H), core.tuple_array[idx])

    # Drop shadows first (blocks cast a soft dark offset in-game).
    off = max(1, scale // 8)
    shadow = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(shadow)
    for (name, x, y, rot, s) in placements:
        d.rectangle([x * scale + off, y * scale + off,
                     (x + s) * scale - 1, (y + s) * scale - 1], fill=110)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, scale // 12)))
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img, shadow)

    # Sprites on top.
    for (name, x, y, rot, s) in placements:
        spr = sprite_train._raw_sprite(name)
        if spr is None:
            continue
        if rot:
            spr = spr.rotate(-rot * 90, expand=False)
        side = s * scale
        spr = spr.resize((side, side), Image.LANCZOS)
        img.paste(spr, (x * scale, y * scale), spr.split()[3])

    # Capture-like augmentation, then whole-image palette quantization
    # (same conversion core._palette_image applies to real screenshots).
    # Kept mild so synthetic exemplars stay near clean sprites and don't
    # dominate the 5x-weighted screenshot pool in the classifier.
    arr = np.asarray(img, dtype=np.float64)
    arr *= nprng.uniform(0.95, 1.05)
    arr += nprng.normal(0, nprng.uniform(0, 3), arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    if scale >= 22 and rng.random() < 0.25:
        img = img.filter(ImageFilter.GaussianBlur(rng.choice([0.4, 0.8])))
    return img._new(img.im.convert("P", 0, core.palette.im)).convert("RGB")


def write_pair(stem, w, h, placements):
    tags = {"labels": "[]", "name": os.path.basename(stem),
            "description": "pix2msch synthetic training sheet"}
    payload = [(n, x, y, None, rot) for (n, x, y, rot, s) in placements]
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        core._write_schematic(w, h, tags, payload, stem + ".msch", "path")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheets", type=int, default=40)
    ap.add_argument("--out", default=os.path.join(HERE, "examples"))
    ap.add_argument("--prefix", default="synth-")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--start", type=int, default=0,
                    help="first sheet index (append batches without clobber)")
    ap.add_argument("--min-scale", type=int, default=12)
    ap.add_argument("--max-scale", type=int, default=34)
    ap.add_argument("--planet", choices=("serpulo", "erekir", "editor", "mix"),
                    default="mix")
    args = ap.parse_args()

    cat = load_placeable()
    groups = planets.classify(list(cat))
    if args.planet != "mix":
        groups = {args.planet: groups[args.planet]}

    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "synth-manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)

    total_cells = 0
    written = 0
    types_seen = set()
    rots_seen = set()
    skipped = 0
    for i in range(args.start, args.start + args.sheets):
        rng = random.Random(args.seed * 100003 + i)
        nprng = np.random.RandomState(args.seed * 100003 + i)
        planet, w, h, placements = sample_sheet(groups, cat, rng)
        placements = [p for p in placements
                      if sprite_train._raw_sprite(p[0]) is not None]
        if len(placements) < 3:
            skipped += 1
            continue
        scale = rng.randint(args.min_scale, args.max_scale)
        img = render_sheet(w, h, placements, scale, rng, nprng)
        stem = os.path.join(args.out, "%s%s-%03d" % (args.prefix, planet, i))
        img.save(stem + ".png")
        write_pair(stem, w, h, placements)
        manifest["%s%s-%03d" % (args.prefix, planet, i)] = [
            {"name": n, "x": x, "y": y, "rot": r, "size": s}
            for (n, x, y, r, s) in placements]
        total_cells += len(placements)
        written += 1
        types_seen.update(p[0] for p in placements)
        rots_seen.update((p[0], p[3]) for p in placements)
        print("  %s-%03d  %dx%d @%dpx  %d blocks"
              % (args.prefix + planet, i, w, h, scale, len(placements)))

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=1)

    all_rots = sum(len(_rotations_for(n, cat[n])) for n in cat)
    print("\n%d sheets (%d skipped), %d labeled cells, %d/%d block types, "
          "%d/%d name+rotation combos covered"
          % (written, skipped, total_cells, len(types_seen), len(cat),
             len(rots_seen), all_rots))
    print("corpus dir:", args.out)


if __name__ == "__main__":
    main()
