"""Download raw Mindustry sprites and generate training exemplars.

Sprites are palette-quantized and composited to match how they appear in
screenshots, bridging the domain gap between clean game assets and
palette-quantized in-game rendering.
"""
import os
import shutil
import urllib.request
import io

from PIL import Image, ImageFilter

import core
import recognize

SPRITES_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "examples", "_sprites")
REMOTE_BASE = ("https://raw.githubusercontent.com/Anuken/Mindustry"
               "/master/core/assets-raw/sprites/blocks")

# Map Mindustry block names → list of (remote_path, composite_mode).
# composite_mode:
#   "single"  – one sprite fills the whole tile
#   "top"     – multi-tile top half (bottom = background)
#   "stack"   – stack both halves vertically (2x2 blocks)
BLOCK_SPRITES = {
    # --- Distribution (1x1) ---
    "conveyor":              [("distribution/conveyors/conveyor-0-0.png", "single")],
    "titanium-conveyor":     [("distribution/conveyors/titanium-conveyor-0-0.png", "single")],
    "plastanium-conveyor":   [("distribution/stack-conveyors/plastanium-conveyor.png", "single")],
    "bridge-conveyor":       [("distribution/bridge-conveyor.png", "single")],
    "overflow-gate":         [("distribution/overflow-gate.png", "single")],
    "sorter":                [("distribution/sorter.png", "single")],
    "router":                [("distribution/router.png", "single")],
    "junction":              [("distribution/junction.png", "single")],
    "item-source":           [("distribution/distributor.png", "single")],
    "item-void":             [("distribution/cross.png", "single")],

    # --- Production (2x2) ---
    "silicon-smelter": [
        ("production/silicon-smelter.png", "single"),
        ("production/silicon-smelter-top.png", "top"),
    ],
    "graphite-press": [
        ("production/graphite-press.png", "single"),
    ],
    "kiln": [
        ("production/kiln.png", "single"),
        ("production/kiln-top.png", "top"),
    ],
    "pyratite-mixer":        [("production/pyratite-mixer.png", "single")],

    # --- Power (1x1) ---
    "power-node":            [("power/power-node.png", "single")],
    "power-node-large":      [("power/power-node-large.png", "single")],
    "diode":                 [("power/diode.png", "single")],

    # --- Storage ---
    "unloader":              [("storage/unloader.png", "single")],
    "container":             [("storage/container.png", "single")],
    "vault":                 [("storage/vault.png", "single")],

    # --- Extra (1x1) ---
    "incinerator":           [("production/incinerator.png", "single")],
}


def _download_sprites():
    """Download all raw sprites to SPRITES_CACHE if not already present."""
    if os.path.exists(SPRITES_CACHE) and os.listdir(SPRITES_CACHE):
        return
    os.makedirs(SPRITES_CACHE, exist_ok=True)
    seen = set()
    for paths in BLOCK_SPRITES.values():
        for remote, _ in paths:
            if remote in seen:
                continue
            seen.add(remote)
            local = os.path.join(SPRITES_CACHE, os.path.basename(remote))
            if os.path.exists(local):
                continue
            url = REMOTE_BASE + "/" + remote
            try:
                data = urllib.request.urlopen(url, timeout=15).read()
                with open(local, "wb") as f:
                    f.write(data)
            except Exception as e:
                print("  [sprite_train] WARN: could not download %s: %s" % (remote, e))


def _palette_quantize(img):
    """Quantize an RGBA sprite to the game's 16-color palette on the
    editor background, matching how _palette_image processes screenshots."""
    bg = core.tuple_array[9]  # editor background (83, 86, 92)
    bg_img = Image.new("RGB", img.size, bg)
    if img.mode == "RGBA":
        bg_img.paste(img, mask=img.split()[3])
    else:
        bg_img.paste(img)
    return bg_img._new(bg_img.im.convert("P", 0, core.palette.im)).convert("RGB")


def _composite_sprite(sprites):
    """Composite multi-part sprites (top + bottom) into a single image.

    For 'top' sprites, the top portion is placed and the rest is background.
    For 'stack', both halves are drawn vertically.
    Returns a single PIL Image.
    """
    if len(sprites) == 1:
        return sprites[0]
    # Stack the parts: bottom first, then top on top
    base = sprites[0]  # bottom part
    top = sprites[1]   # top part
    w, h = base.size
    comp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # Place bottom at bottom, top at top
    comp.paste(base, (0, h - base.size[1]), base if base.mode == "RGBA" else None)
    comp.paste(top, (0, 0), top if top.mode == "RGBA" else None)
    return comp


def _load_block_sprite(block_name, size_tiles=1):
    """Load and process a block's sprite, composited and palette-quantized.

    Returns a PIL Image (RGB, CANON x CANON for size=1).
    """
    entries = BLOCK_SPRITES.get(block_name)
    if not entries:
        return None
    parts = []
    for remote, mode in entries:
        local = os.path.join(SPRITES_CACHE, os.path.basename(remote))
        if not os.path.exists(local):
            continue
        img = Image.open(local).convert("RGBA")
        parts.append(img)
    if not parts:
        return None
    comp = _composite_sprite(parts) if len(parts) > 1 else parts[0]
    quantized = _palette_quantize(comp)
    can = recognize.CANON
    tile_px = can * size_tiles
    quantized = quantized.resize((tile_px, tile_px), Image.LANCZOS)
    return quantized


def build_sprite_exemplars(include_empty=True):
    """Generate exemplars from raw game sprites.

    Returns a list of (name, rotation, config, size, feature_image, rgb_image)
    tuples compatible with the exemplar format used by build_corpus / classifier.
    """
    _download_sprites()
    exemplars = []
    can = recognize.CANON

    for block_name, entries in BLOCK_SPRITES.items():
        size_tiles = 2 if block_name in recognize.SIZES else 1
        sprite = _load_block_sprite(block_name, size_tiles)
        if sprite is None:
            continue

        is_directional = block_name in recognize.DIRECTIONAL
        rotations = range(4) if is_directional else [0]

        for rot in rotations:
            if rot == 0:
                rotated = sprite
            else:
                rotated = sprite.rotate(-rot * 90, expand=False)
            fex = recognize._feat(rotated)
            exemplars.append((block_name, rot, None, size_tiles, fex, rotated))

    if include_empty:
        bg = core.tuple_array[9]
        empty = Image.new("RGB", (can, can), bg)
        fex = recognize._feat(empty)
        exemplars.append(("__empty__", 0, None, 1, fex, empty))

    return exemplars


if __name__ == "__main__":
    exs = build_sprite_exemplars()
    print("Generated %d sprite exemplars" % len(exs))
    by_name = {}
    for (name, rot, cfg, size, fex) in exs:
        by_name.setdefault(name, []).append(rot)
    for name in sorted(by_name):
        print("  %-25s  %d rotations" % (name, len(by_name[name])))
