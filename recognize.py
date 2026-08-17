"""Generalized schematic recognizer for pix2msch.

The tool learns block appearances from a folder of (image, .msch) example
pairs and then detects the same blocks in new screenshots of Mindustry
schematic/base layouts.
"""

import os
import struct
import zlib

try:
    from PIL import Image
except Exception as e:  # pragma: no cover
    print("You're missing a package!")
    print(e)
    raise

import core

CANON = 24  # canonical pixels per tile used for template matching

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


def _ssd(a, b):
    pa = a.getdata()
    pb = b.getdata()
    total = 0
    for (r1, g1, b1), (r2, g2, b2) in zip(pa, pb):
        total += (r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2
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
                    exemplars.append((bname, (rotation + k) % 4, config, size, crop.rotate(-k * 90)))
            else:
                exemplars.append((bname, rotation, config, size, crop))
    return exemplars


def _classify_cell(img, cx, cy, tw, th, ox, oy, size, exemplars, can=CANON):
    crop = _crop(img, ox + cx * tw, oy + cy * th, tw, th, size, can)
    best = None
    for (name, rotation, config, esize, ex) in exemplars:
        if esize != size:
            continue
        d = _ssd(crop, ex)
        if best is None or d < best[0]:
            best = (d, name, rotation, config)
    return best


def recognize(imgfile, exemplars, dims=None, occ=None):
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
        if occ is None:
            occ = occ_from_blocks([], width, height)
        tw, th, ox, oy = detect_grid(img, occ, width, height, None)
        blocks, _ = _recognize_grid(img, px, density, tw, th, ox, oy, width, height, exemplars, occ)
    return width, height, blocks


def _recognize_grid(img, px, density, tw, th, ox, oy, width, height, exemplars, occ=None):
    if occ is not None:
        occupied = [[occ[height - 1 - cy][cx] for cx in range(width)] for cy in range(height)]
    else:
        occupied = [[density(ox + cx * tw, oy + cy * th, tw, th) > 0.5
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
