"""Generalized schematic recognizer for pix2msch.

The tool learns block appearances from a folder of (image, .msch) example
pairs and then detects the same blocks in new screenshots of Mindustry
schematic/base layouts.
"""
import os

import json

import struct

import zlib
try:
    from PIL import Image, ImageFilter
except Exception as e:  # pragma: no cover
    print("You're missing a package!")
    print(e)
    raise

import core

CANON = 24  # canonical pixels per tile used for template matching
BLUR = 3    # Gaussian blur radius applied to the matching feature

# Block footprint sizes in tiles — loaded from block_catalog.json.
def _load_sizes():
    catalog_path = os.path.join(os.path.dirname(__file__), "block_catalog.json")
    if os.path.exists(catalog_path):
        with open(catalog_path) as f:
            catalog = json.load(f)
        return {name: info["size"] for name, info in catalog.items()}
    # Minimal fallback if catalog is missing.
    return {
        "silicon-smelter": 2, "kiln": 2, "graphite-press": 2,
        "pyrolysis-generator": 3,
    }

SIZES = _load_sizes()

# Blocks whose sprites are rotated by their `rotation` field.
DIRECTIONAL = {
    "conveyor",
    "titanium-conveyor",
    "plastanium-conveyor",
    "bridge-conveyor",
    "overflow-gate",
    "sorter",
    "unloader",
    "router",
    "junction",
    "item-source",
    "item-void",
}


def _skip_config(data, i):
    tag = data[i]
    i += 1
    if tag in (0, 10):
        return i
    if tag == 1:
        return i + 4
    if tag == 2:
        return i + 8
    if tag == 3:
        return i + 4
    if tag == 4:
        return i + 2 + struct.unpack_from(">H", data, i)[0]
    if tag == 5:
        return i + 3
    if tag == 6:
        return i + 2 + 4 * struct.unpack_from(">H", data, i)[0]
    if tag == 7:
        return i + 8
    if tag == 8:
        return i + 1 + 4 * data[i]
    if tag == 21:
        return i + 4 + 4 * struct.unpack_from(">i", data, i)[0]
    raise ValueError("unknown config tag {0}".format(tag))


def parse_msch(path):
    """Return (width, height, blocks) where each block is
    (name, x, y, rotation, config, size)."""
    data = zlib.decompress(open(path, "rb").read()[5:])
    i = 0
    width, height = struct.unpack_from(">HH", data, i)
    i += 4
    ntag = data[i]
    i += 1
    for _ in range(ntag):
        n = struct.unpack_from(">H", data, i)[0]
        i += 2
        i += n
        n = struct.unpack_from(">H", data, i)[0]
        i += 2
        i += n
    ndict = data[i]
    i += 1
    names = []
    for _ in range(ndict):
        s = struct.unpack_from(">H", data, i)[0]
        i += 2
        names.append(data[i:i + s].decode())
        i += s
    total = struct.unpack_from(">i", data, i)[0]
    i += 4
    blocks = []
    for _ in range(total):
        idx = data[i]
        i += 1
        packed = struct.unpack_from(">i", data, i)[0]
        i += 4
        tag = data[i]  # i points at the config tag
        config = None
        if tag == 5:
            ctype = data[i + 1]
            cid = struct.unpack_from(">H", data, i + 2)[0]
            config = ("content", ctype, cid)
        elif tag == 8:
            count = data[i + 1]
            pts = tuple(struct.unpack_from(">i", data, i + 2 + 4 * j)[0] for j in range(count))
            config = ("points", pts)
        i = _skip_config(data, i)
        rotation = data[i]
        i += 1
        x = packed >> 16
        y = packed & 65535
        name = names[idx]
        blocks.append((name, x, y, rotation, config, SIZES.get(name, 1)))
    return width, height, blocks


def occ_from_blocks(blocks, width, height):
    occ = [[0] * width for _ in range(height)]
    for (name, x, y, rot, cfg, size) in blocks:
        for dy in range(size):
            for dx in range(size):
                if 0 <= y + dy < height and 0 <= x + dx < width:
                    occ[y + dy][x + dx] = 1
    return occ


def _reduced_exemplars(exemplars):
    seen = set()
    red = []
    for e in exemplars:
        name = e[0]
        if name in seen:
            continue
        seen.add(name)
        red.append(e)
    return red


def _longest_run(flags):
    best = (0, -1)
    cur = (0, -1)
    for i, f in enumerate(flags):
        if f:
            if cur[1] < cur[0]:
                cur = (i, i)
            else:
                cur = (cur[0], i)
            if cur[1] - cur[0] > best[1] - best[0]:
                best = cur
        else:
            cur = (i + 1, i + 1)
    if best[1] < best[0]:
        return None
    return best


def detect_grid(img, occ, width, height, exemplars=None):
    """Find tile size and origin for a known schematic occupancy matrix.

    Blocks are rendered as contiguous rectangles with a uniform-margin border,
    so the content bounding box (longest interior run of non-background columns
    and rows) divided by the schematic dimensions gives the tile size, and the
    run start gives the origin. A short occupancy-agreement refinement locks the
    exact pixel alignment.
    """
    bg = core.tuple_array[9]
    px = img.load()
    w, h = img.size
    I = [[0] * (w + 1) for _ in range(h + 1)]
    for y in range(h):
        row = 0
        for x in range(w):
            row += px[x, y] != bg
            I[y + 1][x + 1] = I[y][x + 1] + row

    def density(x0, y0, tw, th):
        return (I[y0 + th][x0 + tw] - I[y0][x0 + tw] - I[y0 + th][x0] + I[y0][x0]) / float(tw * th)

    def occ_score(tw, th, ox, oy):
        s = 0.0
        for by in range(height):
            for bx in range(width):
                dd = density(ox + bx * tw, oy + (height - 1 - by) * th, tw, th)
                s += dd if occ[by][bx] else (1 - dd)
        return s

    colbg = [sum(1 for y in range(h) if px[x, y] == bg) / h for x in range(w)]
    rowbg = [sum(1 for x in range(w) if px[x, y] == bg) / w for y in range(h)]
    cols = [colbg[x] < 0.98 for x in range(w)]
    rows = [rowbg[y] < 0.98 for y in range(h)]
    lr = _longest_run(cols)
    tr = _longest_run(rows)
    if lr is None or tr is None:
        # Fall back to occupancy-agreement search.
        tw_lo = max(8, w // (width + 3))
        tw_hi = min(w // width + 3, w // 2)
        th_lo = max(8, h // (height + 3))
        th_hi = min(h // height + 3, h // 2)
        best_twth = None
        for tw in range(tw_lo, tw_hi):
            for th in range(th_lo, th_hi):
                if width * tw > w or height * th > h:
                    continue
                spanx = w - width * tw
                spany = h - height * th
                sc = max(occ_score(tw, th, ox, oy)
                         for ox in (0, spanx // 2, spanx)
                         for oy in (0, spany // 2, spany))
                if best_twth is None or sc > best_twth[0]:
                    best_twth = (sc, tw, th)
        tw, th = best_twth[1], best_twth[2]
        best = None
        for ox in range(0, w - width * tw + 1):
            for oy in range(0, h - height * th + 1):
                sc = occ_score(tw, th, ox, oy)
                if best is None or sc > best[0]:
                    best = (sc, ox, oy)
        return tw, th, best[1], best[2]

    l, r = lr
    t, b = tr
    tw0 = int(round((r - l + 1) / width))
    th0 = int(round((b - t + 1) / height))

    best = None
    for tw in range(max(6, tw0 - 4), tw0 + 5):
        for th in range(max(6, th0 - 4), th0 + 5):
            if width * tw > w or height * th > h:
                continue
            spanx = w - width * tw
            spany = h - height * th
            for ox in range(max(0, l - 12), min(spanx, l + 13)):
                for oy in range(max(0, t - 12), min(spany, t + 13)):
                    sc = occ_score(tw, th, ox, oy)
                    if best is None or sc > best[0]:
                        best = (sc, tw, th, ox, oy)
    return best[1], best[2], best[3], best[4]


def _crop(img, x0, y0, tw, th, size, can=CANON):
    return img.crop((x0, y0, x0 + size * tw, y0 + size * th)).resize(
        (size * can, size * can), Image.LANCZOS
    )


def _feat(img, blur=BLUR):
    """State-invariant matching feature.

    Convert to grayscale, blur away thin power lines / belt items, then subtract
    the mean so a uniformly glowing running block is comparable to its idle
    exemplar. The result is a single-channel (float) image.
    """
    g = img.convert("L").filter(ImageFilter.GaussianBlur(radius=blur))
    f = g.convert("F")
    data = list(f.getdata())
    mean = sum(data) / len(data) if data else 0.0
    return f.point(lambda v: v - mean)


def _ssd(a, b):
    pa = a.getdata()
    pb = b.getdata()
    total = 0
    for va, vb in zip(pa, pb):
        if isinstance(va, (tuple, list)):
            total += sum((x - y) ** 2 for x, y in zip(va, vb))
        else:
            total += (va - vb) ** 2
    return total


_clf = None  # trained RandomForestClassifier (loaded once per session)
_occ_clf = None  # trained occupancy RandomForestClassifier
_corpus_hash = None  # hash of the corpus used to train _clf
_cached_exemplars = None  # exemplars from last build_corpus call


def build_corpus(examples_dir):
    """Build exemplars from every (image, .msch) pair in examples_dir.

    Also trains (or loads a cached) Random Forest classifier so future
    detections generalise better than brute-force SSD.  Trains a second
    RF for occupancy detection (foreground vs background).
    """
    global _clf, _corpus_hash, _occ_clf, _cached_exemplars

    # Fast path: if classifiers are already loaded and corpus hasn't changed,
    # skip the expensive exemplar/feature reconstruction.
    import classifier
    ch = classifier._corpus_hash(examples_dir)
    if (_clf is not None and _occ_clf is not None and _corpus_hash == ch
            and _cached_exemplars is not None):
        return _cached_exemplars

    exemplars = []  # (name, rotation, config, size, feat_image [, rgb_image])
    occ_data = []   # (feature_vector, is_occupied) for occupancy training
    for name in sorted(os.listdir(examples_dir)):
        if not name.endswith(".png"):
            continue
        png = os.path.join(examples_dir, name)
        msch = os.path.join(examples_dir, name[:-4] + ".msch")
        if not os.path.exists(msch):
            continue
        width, height, blocks = parse_msch(msch)
        img = core._palette_image(png)
        occ = occ_from_blocks(blocks, width, height)
        tw, th, ox, oy = detect_grid(img, occ, width, height, None)
        for (bname, x, y, rotation, config, size) in blocks:
            top_row = height - 1 - (y + size - 1)
            crop = _crop(img, ox + x * tw, oy + top_row * th, tw, th, size)
            if bname in DIRECTIONAL:
                for k in range(4):
                    rot_crop = crop.rotate(-k * 90) if k else crop
                    fex = _feat(rot_crop)
                    exemplars.append((bname, (rotation + k) % 4, config, size, fex, rot_crop))
            else:
                fex = _feat(crop)
                exemplars.append((bname, rotation, config, size, fex, crop))
        # Collect occupancy training data: every cell in the grid.
        import classifier as _clf_mod
        for cy in range(height):
            for cx in range(width):
                rgb_crop = _crop(img, ox + cx * tw, oy + cy * th, tw, th, 1)
                feat_crop = _feat(rgb_crop)
                vec = _clf_mod.feat_vector(rgb_crop, feat_crop)
                is_occ = bool(occ[height - 1 - cy][cx])
                occ_data.append((vec, is_occ))
    # Fold in any hand-corrected exemplars collected through the UI.
    exemplars += _training_exemplars(examples_dir)

    # Fold in raw game-sprite exemplars (palette-quantized to match screenshots).
    import sprite_train
    exemplars += sprite_train.build_sprite_exemplars(include_empty=False)

    # Train or load the RF classifiers.
    import classifier
    ch = classifier._corpus_hash(examples_dir)
    old_clf, old_hash = classifier.load(examples_dir)
    if old_clf is not None and old_hash == ch:
        _clf = old_clf
        _corpus_hash = ch
    else:
        classifier.train(examples_dir, exemplars)
        _clf, _corpus_hash = classifier.load(examples_dir)

    # Train or load the occupancy classifier.
    old_occ, occ_hash = classifier.load_occupancy(examples_dir)
    if old_occ is not None and occ_hash == ch:
        _occ_clf = old_occ
    else:
        classifier.train_occupancy(examples_dir, occ_data)
        _occ_clf, _ = classifier.load_occupancy(examples_dir)

    _cached_exemplars = exemplars
    return exemplars


def _training_exemplars(examples_dir):
    """Load exemplars previously exported from the Review window.

    Stored as <examples_dir>/training/images/<uuid>.png (a cell crop) with a
    <examples_dir>/training/manifest.jsonl describing each one. This lets
    corrections of running/live screenshots improve future detections.
    """
    tdir = os.path.join(examples_dir, "training")
    manifest = os.path.join(tdir, "manifest.jsonl")
    if not os.path.exists(manifest):
        return []
    out = []
    for line in open(manifest):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        imgpath = os.path.join(tdir, "images", rec["uuid"] + ".png")
        if not os.path.exists(imgpath):
            continue
        size = int(rec.get("size", 1))
        crop = Image.open(imgpath).convert("RGB").resize((size * CANON, size * CANON), Image.LANCZOS)
        name = rec["name"]
        rotation = int(rec.get("rotation", 0))
        if name in DIRECTIONAL:
            for k in range(4):
                rot_crop = crop.rotate(-k * 90) if k else crop
                out.append((name, (rotation + k) % 4, None, size, _feat(rot_crop), rot_crop))
        else:
            out.append((name, rotation, None, size, _feat(crop), crop))
    return out


def _classify_cell(img, cx, cy, tw, th, ox, oy, size, exemplars, can=CANON, restrict=None):
    crop = _crop(img, ox + cx * tw, oy + cy * th, tw, th, size, can)
    fcrop = _feat(crop)

    if size == 1:
        import classifier
        cell_exemplars = [e for e in exemplars if e[3] == size
                          and (restrict is None or e[0] in restrict)]
        if cell_exemplars:
            name, rotation, config, src = classifier.classify_with_fallback(
                _clf, crop, fcrop, cell_exemplars)
            if src == "rf" and name is not None:
                return (0, name, rotation, config)

    # SSD template matching for multi-tile blocks and low-confidence 1x1.
    best = None
    for ex in exemplars:
        name, rotation, config, esize, fex = ex[0], ex[1], ex[2], ex[3], ex[4]
        if esize != size:
            continue
        if restrict is not None and name not in restrict:
            continue
        d = _ssd(fcrop, fex)
        if best is None or d < best[0]:
            best = (d, name, rotation, config)
    return best


def recognize(imgfile, exemplars, dims=None, occ=None, occ_thresh=0.5):
    """Detect the schematic in `imgfile`.

    Returns (width, height, blocks) with blocks = (name, x, y, rotation, config, size).
    """
    img = core._palette_image(imgfile)
    w, h = img.size
    bg = core.tuple_array[9]
    px = img.load()

    def density(x0, y0, tw, th):
        return sum(px[x, y] != bg for y in range(y0, y0 + th) for x in range(x0, x0 + tw)) / float(tw * th)

    if dims is None:
        # Try plausible grid sizes derived from the image aspect ratio.
        candidates = []
        for width in range(1, w // 12 + 1):
            for height in range(1, h // 12 + 1):
                candidates.append((width, height))
        best = None
        for (width, height) in candidates:
            try:
                occ_c = occ_from_blocks([], width, height)
                tw, th, ox, oy = detect_grid(img, occ_c, width, height)
            except Exception:
                continue
            blocks, conf = _recognize_grid(img, px, density, tw, th, ox, oy, width, height, exemplars)
            if best is None or conf > best[0]:
                best = (conf, width, height, tw, th, ox, oy, blocks)
        _, width, height, tw, th, ox, oy, blocks = best
    else:
        width, height = dims
        grid_occ = occ if occ is not None else occ_from_blocks([], width, height)
        tw, th, ox, oy = detect_grid(img, grid_occ, width, height, None)
        blocks, _ = _recognize_grid(img, px, density, tw, th, ox, oy, width, height, exemplars, occ, occ_thresh)
    return width, height, blocks


def _recognize_grid(img, px, density, tw, th, ox, oy, width, height, exemplars, occ=None, occ_thresh=0.5, restrict=None):
    if occ is not None:
        occupied = [[occ[height - 1 - cy][cx] for cx in range(width)] for cy in range(height)]
    else:
        occupied = [[density(ox + cx * tw, oy + cy * th, tw, th) > occ_thresh
                     for cx in range(width)] for cy in range(height)]
    blocks = []
    confidence = 0.0
    count = 0
    for cy in range(height):
        for cx in range(width):
            if not occupied[cy][cx]:
                continue
            # Among the sizes whose footprint is fully occupied, pick the one
            # whose crop best matches an exemplar (lowest SSD). This avoids
            # treating a 1x1 block next to a larger one as a false large block.
            best = None
            for size in (3, 2, 1):
                if cx + size > width or cy + size > height:
                    continue
                if not all(occupied[cy + dy][cx + dx] for dy in range(size) for dx in range(size)):
                    continue
                res = _classify_cell(img, cx, cy, tw, th, ox, oy, size, exemplars, restrict=restrict)
                if res is None:
                    continue
                d, name, rotation, config = res
                if best is None or d < best[0]:
                    best = (d, name, rotation, config, size)
            if best is None:
                continue
            d, name, rotation, config, size = best
            for dy in range(size):
                for dx in range(size):
                    occupied[cy + dy][cx + dx] = False
            msch_x = cx
            msch_y = height - 1 - (cy + size - 1)
            blocks.append((name, msch_x, msch_y, rotation, config, size))
            confidence += 1.0
            count += 1
    return blocks, confidence / count if count else 0.0


def detect_cells(img, px, tw, th, ox, oy, width, height, exemplars,
                 bg=None, tol=40, thresh=None, gate=1000000, restrict=None):
    """Detect blocks from a known grid (no occupancy reference).

    Uses the occupancy RF to decide which cells contain blocks.  `thresh`
    controls the occupancy probability threshold (default 0.4).
    """
    import classifier as _clf_mod

    # Precompute per-cell occupancy score.
    occ_grid = []  # occ_grid[cy][cx] = score
    for cy in range(height):
        row = []
        for cx in range(width):
            rgb_crop = _crop(img, ox + cx * tw, oy + cy * th, tw, th, 1)
            feat_crop = _feat(rgb_crop)
            row.append(_clf_mod.occ_probability(_occ_clf, rgb_crop, feat_crop))
        occ_grid.append(row)

    def occ_check(cx, cy, size):
        """Return True if the cell block at (cx, cy) of given size is occupied."""
        for dy in range(size):
            for dx in range(size):
                if occ_grid[cy + dy][cx + dx] <= thresh:
                    return False
        return True

    if thresh is None:
        thresh = 0.4

    occupied = [[True] * width for _ in range(height)]
    blocks = []
    for cy in range(height):
        for cx in range(width):
            if not occupied[cy][cx]:
                continue
            best = None
            for size in (3, 2, 1):
                if cx + size > width or cy + size > height:
                    continue
                if not all(occupied[cy + dy][cx + dx]
                           for dy in range(size) for dx in range(size)):
                    continue
                if not occ_check(cx, cy, size):
                    continue
                res = _classify_cell(img, cx, cy, tw, th, ox, oy, size, exemplars, restrict=restrict)
                if res is None:
                    continue

                d, name, rotation, config = res
                if best is None or d < best[0]:
                    best = (d, name, rotation, config, size)
            if best is None or best[0] >= gate:
                continue
            d, name, rotation, config, size = best
            for dy in range(size):
                for dx in range(size):
                    occupied[cy + dy][cx + dx] = False
            msch_x = cx
            msch_y = height - 1 - (cy + size - 1)
            blocks.append((name, msch_x, msch_y, rotation, config, size))
    return blocks, thresh


def _refine_origin(px, w, h, bg, width, height, tw, th, ox0, oy0, span=20):
    """Snap the grid origin to the actual block lattice.

    Uses a summed-area table of non-background pixels and maximizes the number
    of cells that are clearly occupied (density > 0.6) or clearly empty
    (density < 0.25). This locks the grid onto the blocks even when the
    user's box is offset by a few pixels, which matters most for multi-tile
    blocks whose footprint must align exactly.
    """
    I = [[0] * (w + 1) for _ in range(h + 1)]
    for y in range(h):
        row = 0
        for x in range(w):
            row += px[x, y] != bg
            I[y + 1][x + 1] = I[y][x + 1] + row

    def dens(x0, y0, tw, th):
        x0 = max(0, min(x0, w)); y0 = max(0, min(y0, h))
        x1 = max(0, min(x0 + tw, w)); y1 = max(0, min(y0 + th, h))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        s = I[y1][x1] - I[y0][x1] - I[y1][x0] + I[y0][x0]
        return s / float(tw * th)

    best = None
    for ox in range(ox0 - span, ox0 + span + 1):
        if ox < 0 or ox + width * tw > w:
            continue
        for oy in range(oy0 - span, oy0 + span + 1):
            if oy < 0 or oy + height * th > h:
                continue
            score = 0
            for cy in range(height):
                for cx in range(width):
                    d = dens(ox + cx * tw, oy + cy * th, tw, th)
                    if d > 0.6 or d < 0.25:
                        score += 1
            if best is None or score > best[0]:
                best = (score, ox, oy)
    return best[1], best[2] if best else (ox0, oy0)


def recognize_box(imgfile, exemplars, box, width, height, bg=None, tol=20, thresh=0.35,
                 gate=1000000, block_counts=None):
    """Detect blocks given a user-drawn grid box (image pixels) and dimensions.

    `bg` is the background color (sampled from an empty area, or the
    schematic-editor background by default). `block_counts` is an optional dict
    of block-name -> count; when given, running-state exemplars are mined from
    the screenshot itself (for multi-tile blocks that don't match idle
    exemplars), enabling detection without a reference .msch.
    Returns (width, height, blocks, grid, threshold) where grid = (tw, th, ox, oy).
    """
    img = core._palette_image(imgfile)
    px = img.load()
    w, h = img.size
    if bg is None:
        bg = core.tuple_array[9]
    if not isinstance(thresh, (int, float)):
        thresh = 0.2
    x0, y0, x1, y1 = box
    tw = max(1, (x1 - x0) // width)
    th = max(1, (y1 - y0) // height)
    ox, oy = x0, y0
    ox, oy = _refine_origin(px, w, h, bg, width, height, tw, th, ox, oy, span=20)

    ex = exemplars
    restrict = None
    if block_counts:
        ex = exemplars + _mine_2x2_exemplars(img, px, tw, th, ox, oy, width, height, bg, tol, block_counts)
        # In screenshot-only mode the user knows the block inventory, so only
        # allow those types (plus mined ones). This kills 1x1 false positives
        # (e.g. stray junction/overflow-gate) that otherwise clutter the panel.
        restrict = set(block_counts.keys())

    # When the user supplies exact block counts, resolve 1x1 typing globally:
    # assign precisely the requested counts to the occupied 1x1 cells (greedy
    # min-SSD). This turns noisy per-cell matching into an exact result, and we
    # auto-pick the occupancy threshold that makes the counts line up, so no
    # manual threshold tuning is needed for running/live screenshots.
    if block_counts:
        blocks, used_thresh = _auto_threshold(img, px, tw, th, ox, oy, width, height,
                                             ex, bg, tol, gate, thresh, block_counts)
        return width, height, blocks, (tw, th, ox, oy), used_thresh

    blocks, used_thresh = detect_cells(img, px, tw, th, ox, oy, width, height,
                                       ex, bg=bg, tol=tol, thresh=thresh, gate=gate,
                                       restrict=restrict)
    return width, height, blocks, (tw, th, ox, oy), used_thresh


def _auto_threshold(img, px, tw, th, ox, oy, width, height, exemplars, bg, tol,
                    gate, thresh, block_counts):
    """Pick the occupancy threshold whose detection matches the given counts.

    Uses the occupancy RF for all occupancy decisions.  Features are extracted
    per cell ONCE; the threshold sweep then only toggles occupancy (cheap) and
    re-runs the count assignment.  Returns the result at the highest threshold
    where every requested count is met exactly (most conservative).
    """
    from collections import Counter
    need_multi = {n: c for n, c in block_counts.items() if SIZES.get(n, 1) > 1}
    need1_total = sum(c for n, c in block_counts.items() if SIZES.get(n, 1) == 1)
    restrict = set(block_counts.keys())

    # Precompute per-1x1-cell best match + occupancy score (cached once).
    import classifier as _clf_mod
    by_name = {}
    for ex in exemplars:
        name, rotation, config, size, fex = ex[0], ex[1], ex[2], ex[3], ex[4]
        if size == 1:
            by_name.setdefault(name, []).append((rotation, config, fex))
    cell_bf = []   # (cx, cy, {name: (d, rot, cfg)})
    cell_occ = []  # occupancy RF probability per 1x1 cell
    for cy in range(height):
        for cx in range(width):
            rgb_crop = _crop(img, ox + cx * tw, oy + cy * th, tw, th, 1)
            crop = _feat(rgb_crop)
            cell_occ.append(_clf_mod.occ_probability(_occ_clf, rgb_crop, crop))
            bf = {}
            for n in restrict:
                lst = by_name.get(n)
                if not lst:
                    continue
                best = None
                for (rot, cfg, fex) in lst:
                    d = _ssd(crop, fex)
                    if best is None or d < best[0]:
                        best = (d, rot, cfg)
                bf[n] = best
            cell_bf.append((cx, cy, bf))

    # Precompute per-2x2-cell best match (cached once), restricted to multi types.
    multi_names = set(need_multi.keys())
    cell_match2 = {}
    if multi_names:
        for cy in range(height - 1):
            for cx in range(width - 1):
                res = _classify_cell(img, cx, cy, tw, th, ox, oy, 2, exemplars, restrict=multi_names)
                if res is not None:
                    d, name, rot, cfg = res
                    cell_match2[(cx, cy)] = (name, rot, cfg, d)

    types1 = [n for n in restrict if n in by_name]
    total_need = need1_total

    # Build a map from (cx, cy) to the precomputed occupancy score.
    occ_lookup = {}
    for i, (cx, cy, _) in enumerate(cell_bf):
        occ_lookup[(cx, cy)] = cell_occ[i]

    def detect_at(T):
        # multi-tile (2x2) blocks present at this threshold
        fixed = []
        exclude = set()
        for cy in range(height - 1):
            for cx in range(width - 1):
                # For 2x2: require all four 1x1 cells above threshold.
                if not all(occ_lookup.get((cx + dx, cy + dy), 0) > T
                           for dy in range(2) for dx in range(2)):
                    continue
                m = cell_match2.get((cx, cy))
                if not m or m[3] >= gate:
                    continue
                if any((cx + dx, cy + dy) in exclude for dy in range(2) for dx in range(2)):
                    continue
                name, rot, cfg, _ = m
                msch_x = cx
                msch_y = height - 1 - (cy + 1)
                fixed.append((name, msch_x, msch_y, rot, cfg, 2))
                for dy in range(2):
                    for dx in range(2):
                        exclude.add((cx + dx, cy + dy))
        # 1x1 cells occupied at this threshold and not under a multi-tile block
        cells = [(cx, cy, bf) for (cx, cy, bf) in cell_bf
                 if (cx, cy) not in exclude and occ_lookup[(cx, cy)] > T]
        # greedy count assignment (min-SSD) over cached per-cell matches
        pool = []
        for i, (cx, cy, bf) in enumerate(cells):
            for n in types1:
                if bf.get(n) is not None:
                    pool.append((bf[n][0], i, n, bf[n][1], bf[n][2]))
        pool.sort()
        used = set()
        type_count = {n: 0 for n in types1}
        out = list(fixed)
        for cost, i, n, rot, cfg in pool:
            if type_count[n] >= block_counts[n] or i in used:
                continue
            used.add(i)
            type_count[n] += 1
            cx, cy = cells[i][0], cells[i][1]
            out.append((n, cx, height - 1 - cy, rot, cfg, 1))
            if sum(type_count.values()) >= total_need:
                break
        return out

    hits = []
    for T in (round(0.05 + 0.01 * i, 2) for i in range(56)):  # 0.05 .. 0.60
        bl = detect_at(T)
        c = Counter(b[0] for b in bl)
        multi_ok = all(c.get(n, 0) == cnt for n, cnt in need_multi.items())
        ones = sum(c.get(n, 0) for n in block_counts if SIZES.get(n, 1) == 1)
        if multi_ok and ones == need1_total:
            hits.append((T, bl))
    if hits:
        hits.sort(key=lambda x: -x[0])  # most conservative threshold
        return hits[0][1], hits[0][0]
    # Fall back to the user-supplied threshold.
    return detect_at(thresh if isinstance(thresh, (int, float)) else 0.2), (thresh if isinstance(thresh, (int, float)) else 0.2)


def _occupied_1x1(img, tw, th, ox, oy, width, height, thresh, exclude):
    """Find occupied 1x1 cells using the occupancy RF."""
    import classifier as _clf_mod
    cells = []
    for cy in range(height):
        for cx in range(width):
            if (cx, cy) in exclude:
                continue
            rgb_crop = _crop(img, ox + cx * tw, oy + cy * th, tw, th, 1)
            feat_crop = _feat(rgb_crop)
            score = _clf_mod.occ_probability(_occ_clf, rgb_crop, feat_crop)
            if score > thresh:
                cells.append((cx, cy))
    return cells


def _assign_counts(blocks, img, px, tw, th, ox, oy, width, height, exemplars,
                   bg, tol, thresh, counts):
    fixed = [b for b in blocks if b[5] > 1]
    # Cells already covered by a multi-tile block cannot be 1x1 blocks.
    exclude = set()
    for b in fixed:
        bx, by, bsize = b[1], b[2], b[5]
        cy_top = height - 1 - (by + bsize - 1)
        for dy in range(bsize):
            for dx in range(bsize):
                exclude.add((bx + dx, cy_top + dy))
    cells = _occupied_1x1(img, tw, th, ox, oy, width, height, thresh, exclude)

    by_name = {}
    for ex in exemplars:
        name, rotation, config, size, fex = ex[0], ex[1], ex[2], ex[3], ex[4]
        if size != 1:
            continue
        by_name.setdefault(name, []).append((rotation, config, fex))

    types = [n for n in counts if n in by_name]
    need = {n: counts[n] for n in types}
    total_need = sum(need.values())
    if not cells or not types:
        return fixed

    cand = []
    for (cx, cy) in cells:
        crop = _feat(_crop(img, ox + cx * tw, oy + cy * th, tw, th, 1))
        bf = {}
        for n in types:
            best = None
            for (rot, cfg, fex) in by_name[n]:
                d = _ssd(crop, fex)
                if best is None or d < best[0]:
                    best = (d, rot, cfg)
            bf[n] = best
        cand.append((cx, cy, bf))

    pool = []
    for i, (cx, cy, bf) in enumerate(cand):
        for n in types:
            if bf[n] is not None:
                pool.append((bf[n][0], i, n, bf[n][1], bf[n][2]))
    pool.sort()
    used = set()
    type_count = {n: 0 for n in types}
    out = list(fixed)
    # Assign exact counts; any surplus occupied cells are dropped (false hits).
    for cost, i, n, rot, cfg in pool:
        if type_count[n] >= need[n] or i in used:
            continue
        used.add(i)
        type_count[n] += 1
        cx, cy = cand[i][0], cand[i][1]
        msch_x = cx
        msch_y = height - 1 - cy
        out.append((n, msch_x, msch_y, rot, cfg, 1))
        if sum(type_count.values()) >= total_need:
            break
    return out


def _mine_2x2_exemplars(img, px, tw, th, ox, oy, width, height, bg, tol, counts):
    """Mine running-state 2x2 exemplars from the screenshot itself.

    For each 2x2 block type in `counts`, take the `counts[name]` most
    block-like 2x2 footprints (high occupancy + coherent interior) and store
    them as running exemplars. This lets running multi-tile blocks (e.g. a
    silicon-smelter that looks nothing like its idle exemplar) be recognized
    without a reference .msch.
    """
    import statistics
    br, bgc, bb = bg[0], bg[1], bg[2]

    def occ2(cx, cy):
        cnt = 0; tot = 0
        for y in range(oy + cy * th, oy + cy * th + 2 * th):
            for x in range(ox + cx * tw, ox + cx * tw + 2 * tw):
                p = px[x, y]
                if abs(p[0] - br) > tol or abs(p[1] - bgc) > tol or abs(p[2] - bb) > tol:
                    cnt += 1
                tot += 1
        return cnt / float(tot)

    cands = []
    for cy in range(height - 1):
        for cx in range(width - 1):
            o = occ2(cx, cy)
            if o < 0.5:
                continue
            means = []
            for sx in (0, 1):
                for sy in (0, 1):
                    vals = [sum(px[ox + cx * tw + sx * tw + i, oy + cy * th + sy * th + j]) / 3.0
                            for i in range(0, tw, max(1, tw // 10))
                            for j in range(0, th, max(1, th // 10))]
                    if vals:
                        means.append(sum(vals) / len(vals))
            coh = 1.0 / (1.0 + (statistics.pstdev(means) if len(means) > 1 else 0.0))
            cands.append((o * coh, cx, cy))
    cands.sort(reverse=True)

    mined = []
    for name, n in counts.items():
        if SIZES.get(name, 1) != 2:
            continue
        picked = 0
        used = set()
        for _sc, cx, cy in cands:
            if any((cx + dx, cy + dy) in used for dx in range(2) for dy in range(2)):
                continue
            crop = _crop(img, ox + cx * tw, oy + cy * th, tw, th, 2)
            mined.append((name, 0, None, 2, _feat(crop)))
            for dx in range(2):
                for dy in range(2):
                    used.add((cx + dx, cy + dy))
            picked += 1
            if picked >= n:
                break
    return mined


def render_preview(imgfile, blocks, grid, tile=44):
    """Render the detected schematic the way it appears in-game.

    Each block is composited from its screenshot crop into a clean grid so the
    user can verify the detection before saving.
    """
    from PIL import Image
    img = core._palette_image(imgfile)
    px = img.load()
    tw, th, ox, oy = grid
    maxx = max(b[1] + b[5] - 1 for b in blocks)
    maxy = max(b[2] + b[5] - 1 for b in blocks)
    w = maxx + 1
    h = maxy + 1
    out = Image.new("RGB", (w * tile, h * tile), (83, 86, 92))
    if not blocks:
        return out
    for (name, x, y, rot, cfg, size) in blocks:
        crop = _crop(img, ox + x * tw, oy + (h - 1 - (y + size - 1)) * th, tw, th, size)
        crop = crop.resize((tile * size, tile * size))
        out.paste(crop, (x * tile, (h - 1 - y) * tile))
    return out
