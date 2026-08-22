"""Download raw Mindustry sprites and generate training exemplars.

Sprites are palette-quantized and composited to match how they appear in
screenshots, bridging the domain gap between clean game assets and
palette-quantized in-game rendering.
"""
import json
import os
import shutil
import urllib.request
import io

from PIL import Image, ImageFilter

import core
import recognize

HERE = os.path.dirname(os.path.abspath(__file__))
SPRITES_CACHE = os.path.join(HERE, "examples", "_sprites")
REMOTE_BASE = ("https://raw.githubusercontent.com/Anuken/Mindustry"
               "/master/core/assets-raw/sprites/blocks")

# Generic sprite discovery: one-time listing of every block sprite in the
# game's raw-asset tree, so any catalog block resolves without a hand-written
# BLOCK_SPRITES entry. Cached to disk; the API is only hit once.
TREE_CACHE = os.path.join(HERE, "training", "_cache", "sprite_tree.json")
TREE_URL = ("https://api.github.com/repos/Anuken/Mindustry"
            "/git/trees/master?recursive=1")
TREE_PREFIX = "core/assets-raw/sprites/blocks/"
# Filename suffixes that mark non-block variants (team recolors, icons,
# heat/glow layers...) and must never be picked as a block's base sprite.
_BAD_SUFFIXES = ("-team", "-preview", "-icon", "-item", "-liquid", "-heat",
                 "-lights", "-rotater", "-schematic-team", "-glow")

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
    "cultivator":            [("production/cultivator.png", "single")],
    "coal-centrifuge":       [("production/coal-centrifuge.png", "single")],
    "oil-extractor":         [("drills/oil-extractor.png", "single")],
    "separator":             [("production/separator.png", "single")],

    # --- Pumps ---
    "mechanical-pump":       [("liquid/mechanical-pump.png", "single")],
    "rotary-pump":           [("liquid/rotary-pump.png", "single")],

    # --- Power (1x1) ---
    "power-node":            [("power/power-node.png", "single")],
    "power-node-large":      [("power/power-node-large.png", "single")],
    "diode":                 [("power/diode.png", "single")],
    "steam-generator": [
        ("power/steam-generator.png", "single"),
        ("power/steam-generator-top.png", "top"),
    ],
    "thermal-generator":     [("power/thermal-generator.png", "single")],
    "combustion-generator":  [("power/combustion-generator.png", "single")],

    # --- Storage ---
    "unloader":              [("storage/unloader.png", "single")],
    "container":             [("storage/container.png", "single")],
    "vault":                 [("storage/vault.png", "single")],

    # --- Extra (1x1) ---
    "incinerator":           [("production/incinerator.png", "single")],
}


def _sprite_index():
    """Map sprite basename -> repo-relative path for every block sprite.

    Fetched once from the GitHub API and cached to TREE_CACHE.
    """
    global _tree_index
    if _tree_index is not None:
        return _tree_index
    if os.path.exists(TREE_CACHE):
        with open(TREE_CACHE) as f:
            _tree_index = json.load(f)
        return _tree_index
    req = urllib.request.Request(TREE_URL,
                                 headers={"User-Agent": "pix2msch"})
    data = json.load(urllib.request.urlopen(req, timeout=30))
    idx = {}
    for entry in data.get("tree", []):
        p = entry.get("path", "")
        if p.startswith(TREE_PREFIX) and p.endswith(".png"):
            # Store relative to the blocks/ dir, same as BLOCK_SPRITES keys.
            idx.setdefault(os.path.basename(p), p[len(TREE_PREFIX):])
    os.makedirs(os.path.dirname(TREE_CACHE), exist_ok=True)
    with open(TREE_CACHE, "w") as f:
        json.dump(idx, f)
    _tree_index = idx
    return _tree_index


_tree_index = None
_resolve_cache = {}


def _resolve_entries(name):
    """Repo paths for a block's sprite: [base, *overlays].

    Resolution order (mirrors how Mindustry names block regions):
      1. <name>.png (+ optional <name>-top.png overlay)
      2. <name>-bottom.png (+ optional -top overlay)
      3. <name>-0-0.png (conveyor family: direction 0, frame 0)
      4. any other file starting with <name>- that isn't a variant
    Returns [] when nothing plausible exists.
    """
    hit = _resolve_cache.get(name)
    if hit is not None:
        return hit
    idx = _sprite_index()
    entries = []
    base = idx.get(name + ".png")
    top = idx.get(name + "-top.png")
    bottom = idx.get(name + "-bottom.png")
    if base:
        entries = [base] + ([top] if top else [])
    elif bottom:
        entries = [bottom] + ([top] if top else [])
    else:
        frame = idx.get(name + "-0-0.png")
        if frame:
            entries = [frame]
        else:
            cands = sorted(
                fn for fn in idx
                if fn.startswith(name + "-")
                and not fn.startswith(name + "-team")
                and not fn.endswith(_BAD_SUFFIXES))
            if cands:
                entries = [idx[cands[0]]]
    _resolve_cache[name] = entries
    return entries


def _ensure_downloaded(remote):
    """Download one sprite into SPRITES_CACHE if missing; return local path."""
    local = os.path.join(SPRITES_CACHE, os.path.basename(remote))
    if not os.path.exists(local):
        os.makedirs(SPRITES_CACHE, exist_ok=True)
        url = REMOTE_BASE + "/" + remote
        try:
            data = urllib.request.urlopen(url, timeout=15).read()
            with open(local, "wb") as f:
                f.write(data)
        except Exception as e:
            print("  [sprite_train] WARN: could not download %s: %s"
                  % (remote, e))
            return None
    return local


def _download_sprites():
    """Download any missing raw sprites to SPRITES_CACHE."""
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


def _editor_grid_bg(size):
    """Recreate the in-game schematic editor background: 45-degree diagonal
    stripes, 7px palette idx1 / 9px idx9, 16px period (measured from a real
    editor capture; phase chosen to match)."""
    c1 = core.tuple_array[1]
    c9 = core.tuple_array[9]
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = c1 if ((x - y + 4) % 16) < 7 else c9
    return img


def _palette_quantize(img, bg_idx=9):
    """Quantize an RGBA sprite to the game's 16-color palette on the given
    background color, matching how _palette_image processes screenshots."""
    bg_img = _editor_grid_bg(img.size) if bg_idx == "editor-grid" \
        else Image.new("RGB", img.size, core.tuple_array[bg_idx])
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


def _entries_for(block_name):
    """Sprite entries for a block: hardcoded map first, then generic
    discovery over the game's raw-asset tree (downloading as needed)."""
    entries = BLOCK_SPRITES.get(block_name)
    if entries:
        return [(remote, mode) for remote, mode in entries]
    return [(remote, "single") for remote in _resolve_entries(block_name)]


_raw_cache = {}


def _raw_sprite(block_name):
    """Composited RGBA sprite at native resolution — no background, no
    palette quantization. None when no sprite is available."""
    hit = _raw_cache.get(block_name)
    if hit is not None:
        return hit
    parts = []
    for remote, _mode in _entries_for(block_name):
        local = _ensure_downloaded(remote)
        if local is None:
            continue
        parts.append(Image.open(local).convert("RGBA"))
    out = _composite_sprite(parts) if parts else None
    _raw_cache[block_name] = out
    return out


def _load_block_sprite(block_name, size_tiles=1, bg_idx=9):
    """Load and process a block's sprite, composited and palette-quantized.

    Returns a PIL Image (RGB, CANON x CANON for size=1), or None when no
    sprite can be found/downloaded.
    """
    comp = _raw_sprite(block_name)
    if comp is None:
        return None
    quantized = _palette_quantize(comp, bg_idx)
    can = recognize.CANON
    tile_px = can * size_tiles
    quantized = quantized.resize((tile_px, tile_px), Image.LANCZOS)
    return quantized


# Backgrounds blocks are composited onto. 9 is the flat schematic-editor
# gray; the others stand in for common floors (sand, dark basalt, water,
# grass); "editor-grid" is the editor's diagonal stripe pattern so editor
# captures match exemplars pixel-for-pixel.
BG_VARIANTS = (9, "editor-grid", 0, 5, 6, 10)


def build_sprite_exemplars(include_empty=True):
    """Generate exemplars from raw game sprites.

    Returns a list of (name, rotation, config, size, feature_image, rgb_image)
    tuples compatible with the exemplar format used by build_corpus / classifier.
    Each block is emitted once per background variant in BG_VARIANTS.
    """
    _download_sprites()
    exemplars = []
    can = recognize.CANON

    for block_name, entries in BLOCK_SPRITES.items():
        # True footprint from the catalog (SIZES holds every block, so
        # membership alone doesn't mean multi-tile).
        size_tiles = recognize.SIZES.get(block_name, 1)

        is_directional = block_name in recognize.DIRECTIONAL
        rotations = range(4) if is_directional else [0]

        for bg_idx in BG_VARIANTS:
            sprite = _load_block_sprite(block_name, size_tiles, bg_idx)
            if sprite is None:
                continue
            for rot in rotations:
                if rot == 0:
                    rotated = sprite
                else:
                    rotated = sprite.rotate(-rot * 90, expand=False)
                fex = recognize._feat(rotated)
                exemplars.append((block_name, rot, None, size_tiles, fex, rotated))

    if include_empty:
        for bg_idx in BG_VARIANTS:
            if bg_idx == "editor-grid":
                empty = _editor_grid_bg((can, can))
            else:
                empty = Image.new("RGB", (can, can), core.tuple_array[bg_idx])
            fex = recognize._feat(empty)
            exemplars.append(("__empty__", 0, None, 1, fex, empty))

    return exemplars


if __name__ == "__main__":
    exs = build_sprite_exemplars()
    print("Generated %d sprite exemplars" % len(exs))
    by_name = {}
    for (name, rot, cfg, size, fex, rgb) in exs:
        by_name.setdefault(name, []).append(rot)
    for name in sorted(by_name):
        print("  %-25s  %d rotations" % (name, len(by_name[name])))
