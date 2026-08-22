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


# Colors treated as "empty background" during grid detection. Index 9 is the
# flat schematic-editor gray; index 1 is the second color of the editor's
# diagonal stripe pattern (7px idx1 / 9px idx9 bands, 16px period).
BG_COLORS = {core.tuple_array[9], core.tuple_array[1]}


def _is_bg(p):
    return p in BG_COLORS

# RGB background test: palette quantization destroys the anti-aliased edges of
# the diagonal stripes (they snap to arbitrary palette entries and inflate
# empty-cell density), so grid detection runs on the original RGB instead.
# Anti-aliasing interpolates along the segment between the two stripe colors,
# so a pixel is background iff it lies within ~12 RGB units of that segment.
# Tolerances must stay tight: block grays come within ~18 units of it, and a
# loose test counts block interiors as background, inverting the occupancy
# signal.
_BG_A = core.tuple_array[9]
_BG_B = core.tuple_array[1]
_bg_rgb_cache = {}


def _is_bg_rgb(p):
    hit = _bg_rgb_cache.get(p)
    if hit is not None:
        return hit
    ax, ay, az = _BG_A
    bx, by, bz = _BG_B
    dx, dy, dz = bx - ax, by - ay, bz - az
    rx, ry, rz = p[0] - ax, p[1] - ay, p[2] - az
    t = (rx * dx + ry * dy + rz * dz) / float(dx * dx + dy * dy + dz * dz)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    ex, ey, ez = rx - t * dx, ry - t * dy, rz - t * dz
    hit = ex * ex + ey * ey + ez * ez < 144
    _bg_rgb_cache[p] = hit
    return hit


def _fft_ssd(A, T):
    """Per-pixel SSD of template T slid over image A (grayscale numpy)."""
    import numpy as np
    from scipy.signal import fftconvolve
    k = np.ones(T.shape)
    sA2 = fftconvolve(A * A, k, mode="valid")
    sAT = fftconvolve(A, T[::-1, ::-1], mode="valid")
    return sA2 - 2 * sAT + (T * T).sum()


def _template_grid_fit(img, blocks, width, height):
    """Fit pitch/origin by matching actual game sprites against the capture.

    Occupancy-agreement scoring is too flat when captures carry UI chrome or
    anti-aliasing noise; sprite templates are decisive (SSD/px ~80 vs 500+ for
    wrong blocks). Requires ground-truth `blocks` so predicted cell centers are
    known — used where we have the .msch (corpus exemplar extraction).

    Returns (tw, th, ox, oy) or None if evidence is weak.
    """
    import numpy as np
    import sprite_train

    A = np.asarray(img.convert("L"), dtype=np.float64)
    H, W = A.shape

    # All occurrences grouped by block name (one sprite per name).
    layout = {}
    for (bname, x, y, rotation, config, size) in blocks:
        layout.setdefault(bname, [size, []])
        layout[bname][1].append((x, y))
    if len(layout) < 2:
        return None

    smax_fit = min(W // width if width else W, H // height if height else H)
    if smax_fit < 10:
        return None
    lo = max(10, int(smax_fit * 0.55))
    tau = 600.0  # SSD/px -> weight decay

    sprites = {}
    for bname in sorted(layout):
        sprites[bname] = sprite_train._load_block_sprite(bname, layout[bname][0])

    # Batched FFT scaffolding: one forward transform of the capture plus an
    # integral image of squared pixels; every template then costs just one
    # small rfft2 + irfft2 instead of two full fftconvolves.
    sides = [s * layout[b][0] for s in range(lo, smax_fit + 3)
             for b in sprites if sprites[b] is not None]
    sides = [sd for sd in sides if 8 <= sd <= min(H, W)]
    if not sides:
        return None
    maxside = max(sides)
    P = (H + maxside, W + maxside)
    FA = np.fft.rfft2(A, P)
    I2 = np.zeros((H + 1, W + 1))
    I2[1:, 1:] = (A * A).cumsum(0).cumsum(1)

    def ssd_map(T, side):
        FT = np.fft.rfft2(T[::-1, ::-1], P)
        corr = np.fft.irfft2(FA * FT)
        # Convolution with a flipped kernel: index k corresponds to a window
        # whose top-left sits at k - side + 1.
        sub = corr[side - 1:H, side - 1:W]
        b = (I2[side:, side:] - I2[:-side, side:]
             - I2[side:, :-side] + I2[:-side, :-side])
        return b - 2.0 * sub + float((T * T).sum())

    best = None
    for s in range(lo, smax_fit + 3):
        pts = []
        min_ssd = None
        ok_types = 0
        for bname in sorted(layout):
            bsz, spots = layout[bname]
            spr = sprites.get(bname)
            if spr is None:
                continue
            side = s * bsz
            if side > H or side > W:
                continue
            T = np.asarray(spr.resize((side, side), Image.LANCZOS).convert("L"),
                           dtype=np.float64)
            m = ssd_map(T, side) / float(side * side)
            flat = np.argpartition(m.ravel(), 3)[:4]
            got = False
            for i in flat:
                yy, xx = divmod(int(i), m.shape[1])
                v = float(m[yy, xx])
                if min_ssd is None or v < min_ssd:
                    min_ssd = v
                pts.append((v, float(xx) + side / 2.0,
                            float(yy) + side / 2.0, bname))
                got = True
            if got:
                ok_types += 1
        if not pts or ok_types < 2:
            continue

        def axis_score(axis):
            span = (W - width * s) if axis == 0 else (H - height * s)
            sigma = max(2.0, s / 4.0)
            best_sc, best_off = None, 0
            for off in range(max(0, int(span)) + 1):
                sc = 0.0
                for ssd, hx, hy, bname in pts:
                    bsz, spots = layout[bname]
                    r_best = None
                    for (bx, by) in spots:
                        cx = bx + bsz / 2.0
                        cy = by + bsz / 2.0
                        p = off + (cx if axis == 0 else cy) * s
                        hv = hx if axis == 0 else hy
                        r = abs(hv - p)
                        if r_best is None or r < r_best:
                            r_best = r
                    sc += np.exp(-ssd / tau) * np.exp(
                        -(r_best * r_best) / (2 * sigma * sigma))
                if best_sc is None or sc > best_sc:
                    best_sc, best_off = sc, off
            return best_sc, best_off

        sx, ox = axis_score(0)
        sy, oy = axis_score(1)
        sc = sx * sy
        if best is None or sc > best[0]:
            best = (sc, s, ox, oy, min_ssd)

    if best is None or best[4] is None or best[4] > 400:
        return None
    return float(best[1]), float(best[1]), float(best[2]), float(best[3])


def _grid_selfssd(img, blocks, tw, th, ox, oy, sheet_h):
    """Mean SSD/px between each block's cell crop and its own game sprite.

    A nearly assumption-free quality measure for a candidate grid: at the
    correct pitch/origin every block lines up with its sprite regardless of
    background noise, so the mean drops an order of magnitude below any wrong
    alignment.
    """
    import numpy as np
    import sprite_train

    A = np.asarray(img.convert("L"), dtype=np.float64)
    H, W = A.shape
    tw_i, th_i = int(round(tw)), int(round(th))
    total = 0.0
    count = 0
    cache = {}
    for (bname, x, y, rotation, config, size) in blocks:
        key = (bname, size)
        spr = cache.get(key)
        if spr is None:
            spr = sprite_train._load_block_sprite(bname, size)
            cache[key] = spr
        if spr is None:
            continue
        side = size * tw_i
        T = np.asarray(spr.resize((side, th_i * size), Image.LANCZOS)
                       .convert("L"), dtype=np.float64)
        top_row = sheet_h - 1 - (y + size - 1)
        x0 = int(round(ox + x * tw_i))
        y0 = int(round(oy + top_row * th_i))
        if x0 < 0 or y0 < 0 or x0 + side > W or y0 + th_i * size > H:
            return float("inf")
        C = A[y0:y0 + th_i * size, x0:x0 + side]
        d = C - T[:C.shape[0], :C.shape[1]]
        total += float((d * d).mean())
        count += 1
    return total / count if count else float("inf")


def detect_grid(img, occ, width, height, exemplars=None):
    """Best grid estimate (see _detect_grid_candidates)."""
    return _detect_grid_candidates(img, occ, width, height, exemplars=exemplars)[0]


def detect_grid_auto(imgfile, occ, width, height, exemplars=None, verbose=False):
    """Name-free grid fit: sprite peaks propose, occupancy validates.

    Scans every catalog sprite over a range of scales with FFT template
    matching (decisive evidence: correct sprites hit SSD/px ~80 vs 500+ for
    wrong ones). Each strong peak is a block center; enumerating which grid
    column/row that center could belong to pins down pitch and origin. The
    surviving hypotheses are ranked by occupancy agreement and finally by how
    well their occupied cells match corpus exemplars.
    """
    import numpy as np
    import sprite_train

    rgb = Image.open(imgfile).convert("RGB") if isinstance(imgfile, str) \
        else imgfile.convert("RGB")
    W, Hh = rgb.size
    A = np.asarray(rgb.convert("L"), dtype=np.float64)
    pxl = rgb.load()

    # Foreground integral image for O(1) occupancy-agreement scoring.
    I = [[0] * (W + 1) for _ in range(Hh + 1)]
    for y in range(Hh):
        rowsum = I[y]
        nxt = I[y + 1]
        run = 0
        for x in range(W):
            if not _is_bg_rgb(pxl[x, y]):
                run += 1
            nxt[x + 1] = rowsum[x + 1] + run

    def agree(tw, th, ox, oy):
        total = 0
        cells = 0
        for by in range(height):
            for bx in range(width):
                x0, y0 = int(round(ox + bx * tw)), int(round(oy + by * th))
                x1, y1 = int(round(x0 + tw)), int(round(y0 + th))
                x0, y0 = max(0, min(W - 1, x0)), max(0, min(Hh - 1, y0))
                x1, y1 = max(1, min(W, x1)), max(1, min(Hh, y1))
                fg = I[y1][x1] - I[y0][x1] - I[y1][x0] + I[y0][x0]
                want = 1 if occ[height - 1 - by][bx] else 0
                total += fg / float((x1 - x0) * (y1 - y0)) if want else 1 - fg / float((x1 - x0) * (y1 - y0))
                cells += 1
        return total / max(1, cells)

    smax_fit = min(W // max(1, width), Hh // max(1, height))
    smax_fit = max(smax_fit, 10)
    scales = list(range(max(10, int(smax_fit * 0.55)), smax_fit + 3))

    panel = []
    for n in sorted(SIZES):
        sz = SIZES[n]
        try:
            spr = sprite_train._load_block_sprite(n, sz)
        except Exception:
            spr = None
        if spr is None:
            continue
        g = np.asarray(spr.convert("L"), dtype=np.float64)
        if float(g.std()) < 14.0:
            continue  # flat walls match any flat region; carry no evidence
        panel.append((n, sz, spr))

    # Drop sprites that are pixel-identical to one already scanned. A duplicate
    # produces the same peaks, so keeping it only wastes FFT work.
    seen = set()
    _dedup = []
    for (n, sz, spr) in panel:
        key = np.asarray(spr.convert("L").resize((24, 24)),
                         dtype=np.uint8).tobytes()
        if key in seen:
            continue
        seen.add(key)
        _dedup.append((n, sz, spr))
    panel = _dedup

    def scan(names_scales):
        out = []
        for s, (n, sz, spr) in names_scales:
            side = int(round(s * sz))
            if side < 8 or side > min(W, Hh):
                continue
            T = np.asarray(spr.resize((side, side), Image.LANCZOS)
                           .convert("L"), dtype=np.float64)
            FT = np.fft.rfft2(T[::-1, ::-1], P)
            corr = np.fft.irfft2(FA * FT)
            # Convolution with a flipped kernel: index k corresponds to a
            # window whose top-left sits at k - side + 1.
            sub = corr[side - 1:Hh, side - 1:W]
            b = (I2[side:, side:] - I2[:-side, side:]
                 - I2[side:, :-side] + I2[:-side, :-side])
            ssd = b - 2.0 * sub + float((T * T).sum())
            v = float(ssd.min())
            if v < 300.0 * side * side:
                yy, xx = divmod(int(ssd.argmin()), ssd.shape[1])
                out.append((v / float(side * side), xx + side / 2.0,
                            yy + side / 2.0, sz, n, s))
        return out

    # Batched FFT scan: one forward transform of the capture total, then a
    # small transform per sprite/scale. SSD(k) = boxSum(A^2) - 2*corr + Sum(T^2).
    sides = [int(round(s * sz)) for s in scales for (_n, sz, _spr) in panel]
    maxside = max([sd for sd in sides if 8 <= sd <= min(W, Hh)] or [0])
    P = (Hh + maxside, W + maxside)
    FA = np.fft.rfft2(A, P)
    I2 = np.zeros((Hh + 1, W + 1))
    I2[1:, 1:] = (A * A).cumsum(0).cumsum(1)

    if panel:
        # Single pass over the whole textured catalog: scale pre-selection via
        # a subset sounds cheap but starves the true pitch whenever the subset
        # happens not to resemble anything in the capture.
        peaks = scan([(s, p) for s in scales for p in panel])
    if not peaks:
        return detect_grid(imgfile, occ, width, height)

    peaks.sort()
    # Collapse near-duplicate hits: the same spot often matches at several
    # neighbouring scales, which would otherwise flood the seed budget.
    clustered = []
    for p in peaks:
        if not any(abs(p[1] - q[1]) < 8 and abs(p[2] - q[2]) < 8
                   for q in clustered):
            clustered.append(p)
    # Seed a few peaks per scale: tiny templates match flat regions easily,
    # so a global top-N would let one noisy scale monopolize the budget and
    # starve the true pitch of hypotheses.
    by_scale = {}
    for p in clustered:
        by_scale.setdefault(p[5], []).append(p)
    seed_peaks = []
    for lst in by_scale.values():
        seed_peaks.extend(lst[:2])
    seed_peaks.sort()

    strong = [p for p in clustered if p[0] < 160]

    def peak_support(tw, th, ox, oy):
        """Fraction of strong sprite hits explained by predicted centers."""
        if not strong:
            return 0.0
        tol = max(4.0, 0.08 * tw)
        hit = 0
        for (_v, cx, cy, _sz, _n, _s) in strong:
            found = False
            for by in range(height):
                for bx in range(width):
                    for szb in (1, 2):
                        px_ = ox + (bx + szb / 2.0) * tw
                        py_ = oy + (by + szb / 2.0) * th
                        if abs(px_ - cx) <= tol and abs(py_ - cy) <= tol:
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                hit += 1
        return hit / len(strong)

    hyps = {}
    for (v, cx, cy, sz, n, s) in seed_peaks[:24]:
        for c1 in range(0, width - sz + 1):
            ox = cx - (c1 + sz / 2.0) * s
            if ox < -2 or ox + width * s > W + 2:
                continue
            for r1 in range(0, height - sz + 1):
                oy = cy - (r1 + sz / 2.0) * s
                if oy < -2 or oy + height * s > Hh + 2:
                    continue
                key = (round(s), round(ox), round(oy))
                if key not in hyps:
                    hyps[key] = (agree(s, s, round(ox), round(oy)),
                                 s, round(ox), round(oy))

    def refine(sc0, s0, ox0, oy0):
        best = (sc0, s0, ox0, oy0)
        for ds in (-0.5, -0.25, 0, 0.25, 0.5):
            for dxo in (-2, -1, 0, 1, 2):
                for dyo in (-2, -1, 0, 1, 2):
                    sc = agree(s0 + ds, s0 + ds,
                               ox0 + dxo, oy0 + dyo)
                    if sc > best[0]:
                        best = (sc, s0 + ds, ox0 + dxo, oy0 + dyo)
        return best[1], best[1], best[2], best[3]

    # Shortlist: overall-best hypotheses by occupancy agreement plus the best
    # representative of every scale, so exemplar scoring sees real competition
    # across pitches. Keep raw and refined variants of each.
    ranked = sorted(hyps.values(), key=lambda t: -t[0])
    if verbose:
        print('--- top-15 hyps by agree:')
        for hv in ranked[:15]:
            print('    agree=%.3f s=%5.2f ox=%4d oy=%4d' % hv)
        true_h = [hv for hv in hyps.values()
                  if abs(hv[1] - 32) < 0.51 and abs(hv[2] - 16) <= 2
                  and abs(hv[3] - 17) <= 2]
        print('    TRUE-ish hyps:', true_h if true_h else 'NONE')
        print('--- seed peaks (v, cx, cy, sz, name, s):')
        for pk in seed_peaks[:24]:
            print('    v=%6.1f c=(%.0f,%.0f) sz=%d %s s=%.2f' % (pk[0], pk[1], pk[2], pk[3], pk[4], pk[5]))
    chosen = list(ranked[:8])
    for s in sorted(by_scale):
        rep = None
        for hval in ranked:
            if abs(hval[1] - s) < 0.51:
                rep = hval
                break
        if rep is not None and all(abs(rep[1] - c[1]) > 0.51 or
                                   abs(rep[2] - c[2]) > 2.5 or
                                   abs(rep[3] - c[3]) > 2.5 for c in chosen):
            chosen.append(rep)

    refined = []
    for (_sc, s0, ox0, oy0) in chosen:
        refined.append((float(s0), float(s0), float(ox0), float(oy0)))
        try:
            refined.append(refine(_sc, s0, ox0, oy0))
        except Exception:
            pass

    if exemplars and refined:
        scored = []
        for c in refined:
            try:
                sc_e = _candidate_exemplar_score(rgb, occ, width, height,
                                                 c[0], c[1], c[2], c[3],
                                                 exemplars)
            except Exception:
                sc_e = float("inf")
            sup = peak_support(c[0], c[1], c[2], c[3])
            scored.append((-sup, sc_e, c))
            if verbose:
                print('    support=%.2f exemplar=%10.0f  tw=%5.2f ox=%5.1f oy=%5.1f'
                      % (sup, sc_e, c[0], c[2], c[3]))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [c for (_ns, _se, c) in scored]
    refined.sort(key=lambda c: -agree(c[0], c[0], c[1], c[2]))
    return refined


def _candidate_exemplar_score(img, occ, width, height, tw, th, ox, oy, exemplars):
    """Rank a candidate grid by how well its occupied cells match any exemplar.

    Cheap alternative to full classification: tight-normalize each occupied
    cell and take its SSD to the nearest exemplar feature. Correct alignments
    minimize this even when per-cell identities are wrong. Skin-invariant: the
    tight-feature normalization is what lets it work on captures whose block
    rendering diverges from the catalog sprite.
    """
    import numpy as np

    stacks = _exstacks(exemplars)
    if not stacks:
        return float("inf")

    total = 0.0
    count = 0
    for by in range(height):
        for bx in range(width):
            if not occ[height - 1 - by][bx]:
                continue
            crop = _crop(img, ox + bx * tw, oy + by * th, tw, th, 1)
            t = _tight(crop)
            if t is None:
                continue
            q = np.asarray(_feat(t).getdata(), dtype=np.float32).reshape(-1)
            best_d = None
            for sz, stack in stacks.items():
                d = float(((stack - q) ** 2).sum(axis=1).min())
                if best_d is None or d < best_d:
                    best_d = d
            total += best_d
            count += 1
    return total / max(1, count)


_EXSTACK_CACHE = {}


def _exstacks(exemplars):
    """Cache tight-feature stacks (per block size) for fast exemplar scoring."""
    import numpy as np

    cached = _EXSTACK_CACHE.get(id(exemplars))
    if cached is not None:
        return cached
    by_size = {}
    for ex in exemplars:
        tf = _tight_ex_feat(ex)
        if tf is None:
            continue
        arr = np.asarray(tf.getdata(), dtype=np.float32).reshape(-1)
        by_size.setdefault(ex[3], []).append(arr)
    stacks = {sz: np.stack(lst) for sz, lst in by_size.items()}
    _EXSTACK_CACHE[id(exemplars)] = stacks
    return stacks


_RAWSTACK_CACHE = {}


def _rawstacks(exemplars):
    """Cache raw (_feat) exemplar vectors grouped by feature length."""
    import numpy as np

    cached = _RAWSTACK_CACHE.get(id(exemplars))
    if cached is not None:
        return cached
    groups = {}
    for ex in exemplars:
        arr = np.asarray(ex[4], dtype=np.float32).reshape(-1)
        groups.setdefault(arr.shape[0], []).append(arr)
    stacks = {k: np.stack(v) for k, v in groups.items()}
    _RAWSTACK_CACHE[id(exemplars)] = stacks
    return stacks


def _lattice_extract(g, y0, x0, hh, ww):
    """Read region of shape (hh, ww[, c]) from `g` starting at (y0, x0); zero outside."""
    import numpy as np
    H, W = g.shape[:2]
    out = np.zeros((hh, ww) + g.shape[2:], dtype=g.dtype)
    sy0 = max(0, y0)
    sy1 = min(H, y0 + hh)
    sx0 = max(0, x0)
    sx1 = min(W, x0 + ww)
    oy0 = sy0 - y0
    ox0 = sx0 - x0
    if sy1 > sy0 and sx1 > sx0:
        out[oy0:oy0 + (sy1 - sy0), ox0:ox0 + (sx1 - sx0)] = g[sy0:sy1, sx0:sx1]
    return out


def _lattice_score_grid(g, occ, width, height, p, E,
                        oxlo, oxhi, oylo, oyhi, step):
    """Score every origin on the (step) mesh over [oxlo,oxhi]x[oylo,oyhi].

    Returns (score_grid (n_oy, n_ox), oxlo, oylo) where score_grid[i, j] is the
    mean raw exemplar distance for origin (oxlo + j*step, oylo + i*step).
    """
    import numpy as np
    from skimage.util import view_as_windows
    from PIL import Image, ImageFilter

    cells = [(bx, by) for by in range(height)
             for bx in range(width) if occ[height - 1 - by][bx]]
    cnt = len(cells)
    if cnt == 0:
        return None, 0, 0
    pi = int(round(p))
    can = CANON
    n_oy = (oyhi - oylo) // step + 1
    n_ox = (oxhi - oxlo) // step + 1
    if n_oy <= 0 or n_ox <= 0:
        return None, 0, 0
    N = n_oy * n_ox
    total = np.zeros(N, dtype=np.float64)
    E = np.asarray(E, dtype=np.float32)
    for (bx, by) in cells:
        y0 = int(round(oylo + by * p))
        x0 = int(round(oxlo + bx * p))
        hh = pi + (n_oy - 1) * step
        ww = pi + (n_ox - 1) * step
        reg = _lattice_extract(g, y0, x0, hh, ww)
        wins = view_as_windows(reg, (pi, pi, 3), step=step)
        wn = np.asarray(wins, dtype=np.uint8).reshape(N, pi, pi, 3)
        # Exact per-window _feat (PIL LANCZOS + Gaussian blur). The 206k tiny
        # SSD calls that dominated the original are now one batched matmul.
        Fb = np.empty((N, can * can), dtype=np.float32)
        for n in range(N):
            im = (Image.fromarray(wn[n]).resize((can, can), Image.LANCZOS)
                  .convert("L").filter(ImageFilter.GaussianBlur(radius=BLUR)))
            v = np.asarray(im, dtype=np.float32).reshape(-1)
            Fb[n] = v - v.mean()
        FF = (Fb * Fb).sum(axis=1)
        cross = E @ Fb.T
        const = (E * E).sum(axis=1)
        d = FF[None, :] + (const[:, None] - 2.0 * cross)
        total += d.min(axis=0)
    total /= cnt
    return total.reshape(n_oy, n_ox), oxlo, oylo


def _lattice_origin(img, occ, width, height, pitch, exemplars, step=4, refine_step=1):
    """Skin-invariant origin search for a known pitch.

    Template matching fails when a capture's block skin diverges from the
    catalog sprite, so grid hypotheses never get seeded. Here we score each
    candidate origin by the *raw* exemplar feature distance (the same metric
    the classifier uses) over the dense foreground region. The tile origin is
    tightly bounded because the lattice must cover the block content, which
    makes the search cheap and lets the true grid win decisively.
    """
    import numpy as np

    W, Hh = img.size
    px = img.load()
    p = float(pitch)

    # Dense-content bounding box (the schematic blocks, ignoring sparse UI).
    colfg = [sum(1 for y in range(Hh) if not _is_bg_rgb(px[x, y])) / Hh
             for x in range(W)]
    rowfg = [sum(1 for x in range(W) if not _is_bg_rgb(px[x, y])) / W
             for y in range(Hh)]
    cx = [x for x in range(W) if colfg[x] > 0.25]
    cy = [y for y in range(Hh) if rowfg[y] > 0.25]
    if not cx or not cy:
        return 0.0, 0.0
    oxlo = max(0, min(cx) - int(p))
    oxhi = min(int(W - width * p), max(cx) - int(width * p) + int(p))
    oylo = max(0, min(cy) - int(p))
    oyhi = min(int(Hh - height * p), max(cy) - int(height * p) + int(p))
    if oxhi < oxlo or oyhi < oylo:
        return 0.0, 0.0

    stacks = _rawstacks(exemplars)
    E = stacks.get(CANON * CANON)
    if E is None:
        return 0.0, 0.0
    g = np.asarray(img.convert("RGB"), dtype=np.float32)

    # Coarse sweep over the (step) mesh.
    sgrid, bx0, by0 = _lattice_score_grid(
        g, occ, width, height, p, E, oxlo, oxhi, oylo, oyhi, step)
    if sgrid is None:
        return 0.0, 0.0
    i, j = np.unravel_index(np.argmin(sgrid), sgrid.shape)
    best_ox = bx0 + j * step
    best_oy = by0 + i * step
    s = float(sgrid[i, j])

    # Refine at unit resolution within +/- step of the coarse best.
    rlo_x = max(oxlo, best_ox - step)
    rhi_x = min(oxhi, best_ox + step)
    rlo_y = max(oylo, best_oy - step)
    rhi_y = min(oyhi, best_oy + step)
    if rhi_x > rlo_x and rhi_y > rlo_y:
        sgrid2, bx1, by1 = _lattice_score_grid(
            g, occ, width, height, p, E, rlo_x, rhi_x, rlo_y, rhi_y, 1)
        if sgrid2 is not None:
            i2, j2 = np.unravel_index(np.argmin(sgrid2), sgrid2.shape)
            cand_ox = bx1 + j2
            cand_oy = by1 + i2
            if float(sgrid2[i2, j2]) < s:
                best_ox, best_oy, s = cand_ox, cand_oy, float(sgrid2[i2, j2])
    return float(best_ox), float(best_oy)


def _detect_grid_candidates(img, occ, width, height, top_k=5, exemplars=None):
    """Find tile size and origin candidates for a known occupancy matrix.

    `img` may be a file path (preferred: keeps pre-quantization RGB for
    accurate background detection) or a PIL image.

    Blocks are rendered as contiguous rectangles with a uniform-margin border,
    so the content bounding box (longest interior run of non-background columns
    and rows) divided by the schematic dimensions gives the tile size, and the
    run start gives the origin. A short occupancy-agreement refinement locks the
    exact pixel alignment.
    """
    if isinstance(img, str):
        rgb = Image.open(img).convert("RGB")
    else:
        rgb = img.convert("RGB")
    px = rgb.load()
    w, h = rgb.size
    I = [[0] * (w + 1) for _ in range(h + 1)]
    for y in range(h):
        row = 0
        for x in range(w):
            row += not _is_bg_rgb(px[x, y])
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

    colbg = [sum(1 for y in range(h) if _is_bg_rgb(px[x, y])) / h for x in range(w)]
    rowbg = [sum(1 for x in range(w) if _is_bg_rgb(px[x, y])) / w for y in range(h)]
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
        return [(tw, th, best[1], best[2])]

    l, r = lr
    t, b = tr
    tw0 = int(round((r - l + 1) / width))
    th0 = int(round((b - t + 1) / height))

    # Pitch hint via autocorrelation of foreground profiles: packed sheets are
    # literally periodic at the tile pitch, and this signal survives UI chrome
    # and shadows that corrupt bounding-box spans.
    import numpy as np
    fgm = np.zeros((h, w), dtype=np.float32)
    for y in range(h):
        for x in range(w):
            if not _is_bg_rgb(px[x, y]):
                fgm[y, x] = 1.0

    def ac_pitch(profile):
        p = profile - profile.mean()
        n = len(p)
        f = np.fft.rfft(p, 2 * n)
        ac = np.fft.irfft(f * np.conj(f))[:n]
        if ac[0] <= 0:
            return None
        ac /= ac[0]
        best_k, best_v = None, 0.25
        for k in range(12, min(n // 2, 200)):
            if ac[k] > best_v:
                best_v, best_k = ac[k], k
        return best_k

    ac_tw = ac_pitch(fgm.sum(axis=0))
    ac_th = ac_pitch(fgm.sum(axis=1))

    # Both bounding boxes are inflated by block drop-shadows and stray UI
    # elements, so neither span nor the run starts are trustworthy beyond a
    # rough hint. The game renders square pixels (prefer tw ~= th), and the
    # integral image makes occupancy scoring O(1) per cell — so search pitches
    # around both spans plus the autocorrelation estimate, scanning every
    # legal offset exhaustively.
    pitch_set = set(range(max(6, tw0 - 6), tw0 + 4))
    pitch_set |= set(range(max(6, th0 - 4), th0 + 5))
    for est in (ac_tw, ac_th):
        if est:
            pitch_set |= {max(6, est - 1), est, est + 1}
    sq_candidates = sorted(pitch_set)
    th_candidates = sq_candidates

    # Vectorized occupancy-agreement: for a fixed pitch pair the score over
    # every offset at once is a correlation of the window-sum image with the
    # signed occupancy mask (occupied cells vote +density, empty cells -density,
    # empties contribute their constant 1). This replaces ~10^9 Python-level
    # pixel probes with a few thousand numpy slice additions.
    Mocc = np.zeros((height, width))
    for by_ in range(height):  # image rows, top to bottom
        for bx_ in range(width):
            Mocc[by_, bx_] = 1.0 if occ[height - 1 - by_][bx_] else 0.0
    Ksign = 2.0 * Mocc - 1.0
    empty_w = float((Mocc == 0).sum())
    II2 = np.zeros((h + 1, w + 1))
    II2[1:, 1:] = fgm.astype(np.float64).cumsum(axis=0).cumsum(axis=1)

    def pitch_scores(tw, th):
        outH = h - height * th + 1
        outW = w - width * tw + 1
        if outH <= 0 or outW <= 0:
            return None
        S = II2[th:, tw:] - II2[:-th, tw:] - II2[th:, :-tw] + II2[:-th, :-tw]
        Sn = S / float(tw * th)
        acc = np.zeros((outH, outW))
        for by_ in range(height):
            for bx_ in range(width):
                blk = Sn[by_ * th:by_ * th + outH, bx_ * tw:bx_ * tw + outW]
                if blk.shape != acc.shape:
                    return None
                if Ksign[by_, bx_]:
                    acc += Ksign[by_, bx_] * blk
        return empty_w + acc

    per_pitch = {}  # (tw, th) -> list of (sc, tw, th, ox, oy), best first
    best = None
    for tw in sq_candidates:
        if width * tw > w:
            continue
        for th in th_candidates:
            if height * th > h:
                continue
            scmap = pitch_scores(tw, th)
            if scmap is None:
                continue
            pen = 0.05 * abs(tw - th)
            order = np.argsort(scmap.ravel())[::-1][:2]
            lst = []
            for fi in order:
                oy_, ox_ = divmod(int(fi), scmap.shape[1])
                sc = float(scmap[oy_, ox_]) - pen
                item = (sc, tw, th, ox_, oy_)
                lst.append(item)
                if best is None or sc > best[0]:
                    best = item
            lst.sort(reverse=True)
            per_pitch[(tw, th)] = lst
    if best is None:
        # No combination fit the pitch candidates (extreme aspect); sweep all
        # legal pitches.
        for tw in range(6, w // width + 1):
            for th in range(6, h // height + 1):
                scmap = pitch_scores(tw, th)
                if scmap is None:
                    continue
                fi = int(np.argmax(scmap))
                oy_, ox_ = divmod(fi, scmap.shape[1])
                sc = float(scmap[oy_, ox_])
                item = (sc, tw, th, ox_, oy_)
                per_pitch.setdefault((tw, th), []).append(item)
                if best is None or sc > best[0]:
                    best = item
    if best is None:
        raise TypeError("'NoneType' object is not subscriptable")

    def frac_score(tw, th, ox, oy):
        s = 0.0
        for by in range(height):
            for bx in range(width):
                x0 = int(round(ox + bx * tw))
                y0 = int(round(oy + (height - 1 - by) * th))
                x1 = int(round(ox + (bx + 1) * tw))
                y1 = int(round(oy + (height - by) * th))
                x0c, y0c = max(0, x0), max(0, y0)
                x1c, y1c = min(w, x1), min(h, y1)
                if x1c <= x0c or y1c <= y0c:
                    dd = 0.0
                else:
                    n = I[y1c][x1c] - I[y0c][x1c] - I[y1c][x0c] + I[y0c][x0c]
                    dd = n / float((x1c - x0c) * (y1c - y0c))
                s += dd if occ[by][bx] else (1 - dd)
        return s

    def refine(tw, th, ox, oy):
        """Hill-climb fractional pitches/offsets from an integer seed."""
        ftw, fth = float(tw), float(th)
        fox, foy = float(ox), float(oy)
        fscore = frac_score(ftw, fth, fox, foy)
        for _ in range(4):
            improved = False
            for dtw in (-0.5, -0.25, 0.25, 0.5):
                for dth in (-0.5, -0.25, 0.25, 0.5):
                    if width * (ftw + dtw) > w or height * (fth + dth) > h:
                        continue
                    sc = frac_score(ftw + dtw, fth + dth, fox, foy)
                    if sc > fscore:
                        fscore, ftw, fth, improved = sc, ftw + dtw, fth + dth, True
            for dox in (-2, -1, 1, 2):
                for doy in (-2, -1, 1, 2):
                    nx, ny = max(0.0, fox + dox), max(0.0, foy + doy)
                    sc = frac_score(ftw, fth, nx, ny)
                    if sc > fscore:
                        fscore, fox, foy, improved = sc, nx, ny, True
            if not improved:
                break
        return ftw, fth, fox, foy

    # Sub-pixel refinement: editor zoom is an arbitrary scale, so the true
    # pitch is rarely an integer and an integer lock accumulates several px of
    # drift across a wide sheet.
    seeds = [item for lst in per_pitch.values() for item in lst]
    seeds.sort(reverse=True)
    picked = []
    seen_pitch = set()
    for item in seeds:
        pk = (item[1], item[2])
        if pk in seen_pitch:
            continue
        seen_pitch.add(pk)
        picked.append(item)
        if len(picked) >= 3 * top_k:
            break

    cands = []
    for (_sc, tw, th, ox, oy) in picked:
        try:
            cands.append(refine(tw, th, ox, oy))
        except Exception:
            cands.append((float(tw), float(th), float(ox), float(oy)))
    if exemplars:
        rgb = img.convert("RGB") if not isinstance(img, str) else Image.open(img).convert("RGB")
        scored = []
        for c in cands:
            try:
                s = _candidate_exemplar_score(rgb, occ, width, height,
                                              c[0], c[1], c[2], c[3], exemplars)
            except Exception:
                s = float("inf")
            scored.append((s, c))
        scored.sort(key=lambda sc: sc[0])
        return [c for (_s, c) in scored]
    return cands


def _crop(img, x0, y0, tw, th, size, can=CANON):
    x0 = int(round(x0))
    y0 = int(round(y0))
    tw = int(round(tw))
    th = int(round(th))
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
        # Two independent grid estimators; pick by direct sprite-vs-cell SSD.
        # Sprite matching is decisive but can lock onto a wrong scale, and
        # occupancy agreement alone is too flat on noisy captures — so always
        # compute both when possible and let cell-vs-sprite SSD arbitrate.
        candidates = []
        fitted = _template_grid_fit(img, blocks, width, height)
        if fitted is not None:
            candidates.append(
                (_grid_selfssd(img, blocks, *fitted, height), fitted))
        geo = detect_grid(png, occ, width, height, None)
        candidates.append((_grid_selfssd(img, blocks, *geo, height), geo))
        candidates.sort(key=lambda c: c[0])
        tw, th, ox, oy = candidates[0][1]
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
                if size == 1:
                    import classifier as _clf_mod
                    ctx_rgb, ctx_feat = _clf_mod.extract_context(
                        img, x, y, tw, th, ox, oy)
                    exemplars.append((bname, rotation, config, size, fex, crop,
                                      ctx_rgb, ctx_feat))
                else:
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


_tight_ex_cache = {}  # id(exemplar) -> tight-normalized feature image


def _tight(imgcrop, can=CANON):
    """Crop away background margins and rescale the content to can x can.

    Screenshots render blocks at arbitrary scales/paddings depending on zoom
    and UI context; comparing content-normalized crops makes matching
    scale-invariant.
    """
    w, h = imgcrop.size
    m = Image.new("L", (w, h), 0)
    mp = m.load()
    ip = imgcrop.load()
    for y in range(h):
        for x in range(w):
            if not _is_bg(ip[x, y]):
                mp[x, y] = 255
    bb = m.getbbox()
    if bb is None:
        return None
    return imgcrop.crop(bb).resize((can, can), Image.LANCZOS)


def _tight_ex_feat(ex, can=CANON):
    hit = _tight_ex_cache.get(id(ex))
    if hit is not None and hit[0] is ex:
        return hit[1] or None
    t = _tight(ex[5], can)
    tf = _feat(t) if t is not None else False
    _tight_ex_cache[id(ex)] = (ex, tf)
    return tf or None


def _classify_cell(img, cx, cy, tw, th, ox, oy, size, exemplars, can=CANON, restrict=None):
    crop = _crop(img, ox + cx * tw, oy + cy * th, tw, th, size, can)

    # Scale-invariant pass: normalize content, then SSD. Only trusted when it
    # is decisively better than the runner-up (raw-cell matching stays the
    # authority otherwise).
    qt = _tight(crop, can)
    if qt is not None:
        qf = _feat(qt)
        tbest = tsecond = None
        for ex in exemplars:
            name, rotation, config, esize = ex[0], ex[1], ex[2], ex[3]
            if esize != size:
                continue
            if restrict is not None and name not in restrict:
                continue
            tf = _tight_ex_feat(ex, can)
            if tf is None:
                continue
            d = _ssd(qf, tf)
            if tbest is None or d < tbest[0]:
                tsecond = tbest
                tbest = (d, name, rotation, config)
            elif tsecond is None or d < tsecond[0]:
                tsecond = (d,)
        if tbest is not None and tsecond is not None and tbest[0] < 0.75 * tsecond[0]:
            return tbest

    fcrop = _feat(crop)

    if size == 1:
        import classifier
        cell_exemplars = [e for e in exemplars if e[3] == size
                          and (restrict is None or e[0] in restrict)]
        if cell_exemplars:
            ctx_rgb, ctx_feat = classifier.extract_context(
                img, cx, cy, tw, th, ox, oy)
            name, rotation, config, src = classifier.classify_with_fallback(
                _clf, crop, fcrop, cell_exemplars,
                ctx_rgb=ctx_rgb, ctx_feat=ctx_feat)
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
    px = img.load()

    def density(x0, y0, tw, th):
        x0 = int(round(x0))
        y0 = int(round(y0))
        tw = int(round(tw))
        th = int(round(th))
        return sum(not _is_bg(px[x, y]) for y in range(y0, y0 + th) for x in range(x0, x0 + tw)) / float(tw * th)

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
                tw, th, ox, oy = detect_grid(imgfile, occ_c, width, height)
            except Exception:
                continue
            blocks, conf, _ssd_total = _recognize_grid(
                img, px, density, tw, th, ox, oy, width, height, exemplars)
            if best is None or conf > best[0]:
                best = (conf, width, height, tw, th, ox, oy, blocks)
        _, width, height, tw, th, ox, oy, blocks = best
    else:
        width, height = dims
        grid_occ = occ if occ is not None else occ_from_blocks([], width, height)
        if exemplars:
            cands = detect_grid_auto(imgfile, grid_occ, width, height,
                                     exemplars)[:8]
        else:
            cands = detect_grid(imgfile, grid_occ, width, height)
            cands = [cands] if isinstance(cands, tuple) else cands
        # Final arbiter: full classification. Average exemplar SSD separates
        # the true alignment decisively where cheaper metrics stay flat.
        best = None
        best_cand = None
        for (tw, th, ox, oy) in cands:
            blocks, conf, ssd_total = _recognize_grid(
                img, px, density, tw, th, ox, oy, width, height, exemplars,
                occ, occ_thresh)
            if not blocks:
                continue
            key = (ssd_total / len(blocks), -conf)
            if best is None or key < best[0]:
                best = (key, blocks)
                best_cand = (tw, th, ox, oy)
        if best is not None and best[0][0] > 40000.0:
            # Low confidence: template seeding likely missed the true grid
            # (e.g. a capture whose block skin diverges from the catalog
            # sprite). Recover the origin with a skin-invariant exemplar sweep
            # over the promising pitches the detector considered.
            try:
                pitches = sorted({int(c[0] + 0.5) for c in cands
                                  if 25.0 <= c[0] <= 41.0})
                for p in pitches[:3]:
                    lox, loy = _lattice_origin(img, grid_occ, width, height,
                                              float(p), exemplars, step=3)
                    if lox or loy:
                        cands = list(cands) + [(p, p, lox, loy)]
                        blocks, conf, ssd_total = _recognize_grid(
                            img, px, density, p, p, lox, loy, width, height,
                            exemplars, occ, occ_thresh)
                        if blocks:
                            key = (ssd_total / len(blocks), -conf)
                            if best is None or key < best[0]:
                                best = (key, blocks)
            except Exception:
                pass
        if best is None:
            tw, th, ox, oy = (cands[0] if cands else
                              (12, 12, 0, 0))
            blocks, _, _ = _recognize_grid(
                img, px, density, tw, th, ox, oy, width, height, exemplars,
                occ, occ_thresh)
        else:
            blocks = best[1]
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
    ssd_total = 0.0
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
            ssd_total += d
    return blocks, confidence / count if count else 0.0, ssd_total


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
            row += not _is_bg(px[x, y])
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
