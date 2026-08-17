"""Generalized schematic recognizer for pix2msch.

The tool learns block appearances from a folder of (image, .msch) example
pairs and then detects the same blocks in new screenshots of Mindustry
schematic/base layouts.
"""

import os
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

# Block footprint sizes in tiles.
SIZES = {
    "silicon-smelter": 2,
    "kiln": 2,
    "graphite-press": 2,
    "pyrolysis-generator": 3,
}

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


def build_corpus(examples_dir):
    """Build exemplars from every (image, .msch) pair in examples_dir."""
    exemplars = []  # (name, rotation, config, size, image)
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
                    fex = _feat(crop.rotate(-k * 90))
                    exemplars.append((bname, (rotation + k) % 4, config, size, fex))
            else:
                fex = _feat(crop)
                exemplars.append((bname, rotation, config, size, fex))
    return exemplars


def _classify_cell(img, cx, cy, tw, th, ox, oy, size, exemplars, can=CANON):
    crop = _crop(img, ox + cx * tw, oy + cy * th, tw, th, size, can)
    fcrop = _feat(crop)
    best = None
    for (name, rotation, config, esize, fex) in exemplars:
        if esize != size:
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


def _recognize_grid(img, px, density, tw, th, ox, oy, width, height, exemplars, occ=None, occ_thresh=0.5):
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
                res = _classify_cell(img, cx, cy, tw, th, ox, oy, size, exemplars)
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
                 bg=None, tol=40, thresh=None, gate=1000000):
    """Detect blocks from a known grid (no occupancy reference).

    Occupancy is decided from the background: a cell is occupied when the
    fraction of pixels that differ from `bg` (by more than `tol`) clears the
    threshold. `bg` defaults to the schematic-editor background color; passing a
    color sampled from an empty area lets running/live screenshots (whose ground
    differs from the editor background) be detected without a reference .msch.
    When `thresh` is None it is auto-calibrated to the valley between the empty
    and block clusters.
    """
    if bg is None:
        bg = core.tuple_array[9]
    br, bgc, bb = bg[0], bg[1], bg[2]

    def occ(x0, y0, ww, hh):
        cnt = 0
        tot = 0
        for y in range(y0, y0 + hh):
            for x in range(x0, x0 + ww):
                p = px[x, y]
                if abs(p[0] - br) > tol or abs(p[1] - bgc) > tol or abs(p[2] - bb) > tol:
                    cnt += 1
                tot += 1
        return cnt / float(tot)

    if thresh is None:
        thresh = 0.4

    blocks = []
    for cy in range(height):
        for cx in range(width):
            best = None
            for size in (3, 2, 1):
                if cx + size > width or cy + size > height:
                    continue
                if occ(ox + cx * tw, oy + cy * th, tw * size, th * size) <= thresh:
                    continue
                res = _classify_cell(img, cx, cy, tw, th, ox, oy, size, exemplars)
                if res is None:
                    continue
                d, name, rotation, config = res
                if best is None or d < best[0]:
                    best = (d, name, rotation, config, size)
            if best is None or best[0] >= gate:
                continue
            d, name, rotation, config, size = best
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
    x0, y0, x1, y1 = box
    tw = max(1, (x1 - x0) // width)
    th = max(1, (y1 - y0) // height)
    ox, oy = x0, y0
    ox, oy = _refine_origin(px, w, h, bg, width, height, tw, th, ox, oy, span=20)

    ex = exemplars
    if block_counts:
        ex = exemplars + _mine_2x2_exemplars(img, px, tw, th, ox, oy, width, height, bg, tol, block_counts)

    blocks, used_thresh = detect_cells(img, px, tw, th, ox, oy, width, height,
                                       ex, bg=bg, tol=tol, thresh=thresh, gate=gate)
    return width, height, blocks, (tw, th, ox, oy), used_thresh


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
