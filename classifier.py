"""Classifiers for Mindustry block recognition.

Requires scikit-learn (``pip install scikit-learn``).  Two models are
trained at startup:
  1. Block classifier: predicts which block type + rotation a cell contains.
  2. Occupancy classifier: predicts whether a cell contains any block at all
     (foreground vs background), replacing the fragile density-threshold check.

Features: grayscale (576d) + palette color histogram (16d) + spatial
color (64d) + Sobel edge magnitude (576d) + LBP texture (256d) + edge
histogram (16d) = 1504 dimensions total.

Key design choices:
  - If a block's center region is a solid color (>=90% same palette index),
    it is masked during feature extraction — this catches filter-type
    indicators (sorter, overflow-gate) without losing structural detail
    in blocks that have multi-colored centers.
  - Screenshot exemplars are weighted 5x higher than sprite exemplars
    during training, since they match the actual in-game appearance.
  - All exemplars get data augmentation (color jitter, noise) to
    improve generalization.
  - A confidence threshold gates RF usage: below it, SSD is used instead.
"""
import os
import hashlib

import numpy as np
from scipy import ndimage
from skimage.feature import local_binary_pattern
from sklearn.ensemble import RandomForestClassifier
import joblib

import core
import recognize
from PIL import Image


# Confidence threshold: if the RF's top prediction is below this,
# fall back to SSD template matching instead.
CONFIDENCE_THRESHOLD = 0.20

# Sprite-only block names: these appear only in the sprite download,
# never in screenshot exemplars. Used to assign lower training weight.
_SPRITE_ONLY = {
    "power-node-large", "diode", "container", "vault", "incinerator",
}

# Fraction of the tile radius that constitutes the "center" to mask.
CENTER_MASK_FRAC = 0.30

# LBP parameters
LBP_RADIUS = 2
LBP_N_POINTS = 8 * LBP_RADIUS

# Augmentation parameters for sprite exemplars
AUG_JITTER = 0.08       # max relative RGB jitter
AUG_NOISE_STD = 3.0     # Gaussian noise std in palette-indexed space
AUG_AUGMENT_PER_EX = 3  # extra augmented copies per sprite exemplar

# Context: how many tiles in each direction around the cell to include.
# 1 means the cell + 1 tile ring = 3x3 tile area.
CTX_RADIUS = 1
# Context features require enough training data to avoid overfitting.
# Enable once more exemplars are available.
USE_CONTEXT = False


def _model_path(examples_dir):
    return os.path.join(examples_dir, "_classifier.joblib")


def _corpus_hash(examples_dir):
    """Hash all exemplar sources so we retrain when data changes."""
    h = hashlib.md5()
    for name in sorted(os.listdir(examples_dir)):
        if name.endswith(".png"):
            h.update(open(os.path.join(examples_dir, name), "rb").read())
        if name.endswith(".msch"):
            h.update(open(os.path.join(examples_dir, name), "rb").read())
    tdir = os.path.join(examples_dir, "training")
    manifest = os.path.join(tdir, "manifest.jsonl")
    if os.path.exists(manifest):
        h.update(open(manifest, "rb").read())
        imgdir = os.path.join(tdir, "images")
        if os.path.isdir(imgdir):
            for fn in sorted(os.listdir(imgdir)):
                h.update(open(os.path.join(imgdir, fn), "rb").read())
    return h.hexdigest()


def _closest_palette(p):
    """Find the closest palette index for an RGB pixel."""
    best = 0
    best_d = 1 << 30
    for ci, cp in enumerate(core.tuple_array):
        d = (p[0] - cp[0]) ** 2 + (p[1] - cp[1]) ** 2 + (p[2] - cp[2]) ** 2
        if d < best_d:
            best_d = d
            best = ci
    return best


def _center_mask(w, h):
    """Return a boolean mask that is True for pixels NOT in the center."""
    cx, cy = w / 2.0, h / 2.0
    r = max(w, h) * CENTER_MASK_FRAC
    mask = np.ones((h, w), dtype=bool)
    for py in range(h):
        for pxx in range(w):
            if (pxx - cx) ** 2 + (py - cy) ** 2 < r ** 2:
                mask[py, pxx] = False
    return mask


def _center_is_solid(rgb_img):
    """Check if the center region of the image is a single solid color.

    Returns True if >=90% of center pixels map to the same palette index.
    """
    px = rgb_img.load()
    w, h = rgb_img.size
    cx, cy = w / 2.0, h / 2.0
    r = max(w, h) * CENTER_MASK_FRAC
    counts = {}
    total = 0
    for py in range(h):
        for pxx in range(w):
            if (pxx - cx) ** 2 + (py - cy) ** 2 < r ** 2:
                pi = _closest_palette(px[pxx, py])
                counts[pi] = counts.get(pi, 0) + 1
                total += 1
    if total == 0:
        return False
    dominant = max(counts.values())
    return dominant / total >= 0.90


def extract_context(full_img, cx, cy, tw, th, ox, oy, can=recognize.CANON):
    """Extract a context crop around cell (cx, cy) covering CTX_RADIUS neighbors.

    Returns (ctx_rgb, ctx_feat) — a CANON×CANON RGB image and its _feat()
    output, or (None, None) if the context area is fully out of bounds.
    """
    r = CTX_RADIUS
    img_w, img_h = full_img.size
    x0 = ox + (cx - r) * tw
    y0 = oy + (cy - r) * th
    size = 1 + 2 * r  # e.g. 3

    # Clamp to image bounds.
    x1 = x0 + size * tw
    y1 = y0 + size * th
    if x1 <= 0 or y1 <= 0 or x0 >= img_w or y0 >= img_h:
        return None, None
    x0c = max(x0, 0)
    y0c = max(y0, 0)
    x1c = min(x1, img_w)
    y1c = min(y1, img_h)

    crop = full_img.crop((x0c, y0c, x1c, y1c)).resize(
        (can, can), Image.LANCZOS)
    ctx_feat = recognize._feat(crop)
    return crop, ctx_feat


def _sobel_features(gray_arr):
    """Compute Sobel edge features from a 2D grayscale array.

    Returns a flattened vector of horizontal + vertical Sobel magnitudes,
    normalized to [0, 1].
    """
    sx = ndimage.sobel(gray_arr, axis=1)
    sy = ndimage.sobel(gray_arr, axis=0)
    mag = np.sqrt(sx ** 2 + sy ** 2)
    if mag.max() > 0:
        mag /= mag.max()
    return mag.flatten()


def _lbp_features(gray_arr):
    """Compute LBP (Local Binary Pattern) histogram features.

    Returns a 256-dim normalized histogram of LBP codes.
    """
    lbp = local_binary_pattern(gray_arr, LBP_N_POINTS, LBP_RADIUS, method="uniform")
    # Use max code for histogram bins
    n_bins = int(lbp.max()) + 1
    hist, _ = np.histogram(lbp, bins=n_bins, range=(0, n_bins), density=True)
    # Pad to固定 size if needed
    if len(hist) < 256:
        hist = np.pad(hist, (0, 256 - len(hist)))
    else:
        hist = hist[:256]
    return hist


def _edge_histogram(flat_gray, w, h):
    """Compute a 16-bin histogram of edge magnitudes across 4 quadrants."""
    gray_arr = flat_gray.reshape(h, w)
    sx = ndimage.sobel(gray_arr, axis=1)
    sy = ndimage.sobel(gray_arr, axis=0)
    mag = np.sqrt(sx ** 2 + sy ** 2)
    edge_hist = np.zeros(16, dtype=np.float64)
    for qy in range(2):
        for qx in range(2):
            region = mag[qy * h // 2:(qy + 1) * h // 2,
                         qx * w // 2:(qx + 1) * w // 2]
            # Quantize edge magnitudes into 4 bins
            for b in range(4):
                lo = b / 4.0
                hi = (b + 1) / 4.0
                count = np.sum((region >= lo) & (region < hi))
                edge_hist[(qy * 2 + qx) * 4 + b] = count
    total = edge_hist.sum()
    if total > 0:
        edge_hist /= total
    return edge_hist


def feat_vector(rgb_img, feat_img, can=recognize.CANON,
                ctx_rgb=None, ctx_feat=None):
    """Extract a combined feature vector from an RGB crop and its _feat() output.

    Cell features (1504d):
      - Grayscale (flatten _feat output):     576 dims
      - Palette color histogram:               16 dims
      - Spatial color (4 quadrants):           64 dims
      - Sobel edge magnitude:                 576 dims
      - LBP texture histogram:                256 dims
      - Edge magnitude histogram:              16 dims

    Context features (128d): lightweight summary of the 3×3-tile neighborhood.
      - Grayscale downscaled:                  64 dims  (8×8)
      - Spatial color (4 quadrants):           64 dims
    Total: 1632 dims (1504 cell + 128 context) when USE_CONTEXT is True,
    else 1504 dims.

    If the center region is a solid color it is masked out so the
    classifier focuses on the border/frame.
    """
    cell_vec = _crop_features(rgb_img, feat_img)

    if USE_CONTEXT:
        if ctx_rgb is not None and ctx_feat is not None:
            ctx_vec = _ctx_features(ctx_rgb, ctx_feat)
        else:
            ctx_vec = np.zeros(_CTX_DIMS, dtype=np.float64)
        return np.concatenate([cell_vec, ctx_vec])

    return cell_vec


def _crop_features(rgb_img, feat_img):
    """Compute 1504-dim feature vector from a single RGB crop + _feat output."""
    gray = np.array(feat_img, dtype=np.float64)
    flat_gray = gray.flatten()

    px = rgb_img.load()
    w, h = rgb_img.size
    apply_mask = _center_is_solid(rgb_img)
    mask = _center_mask(w, h) if apply_mask else None

    hist = np.zeros(16, dtype=np.float64)
    hist_total = 0
    for py in range(h):
        for pxx in range(w):
            if mask is not None and not mask[py, pxx]:
                continue
            hist[_closest_palette(px[pxx, py])] += 1
            hist_total += 1
    if hist_total > 0:
        hist /= hist_total

    spatial = np.zeros(64, dtype=np.float64)
    for qy in range(2):
        for qx in range(2):
            qhist = np.zeros(16, dtype=np.float64)
            qt = 0
            for py in range(qy * h // 2, (qy + 1) * h // 2):
                for pxx in range(qx * w // 2, (qx + 1) * w // 2):
                    if mask is not None and not mask[py, pxx]:
                        continue
                    qhist[_closest_palette(px[pxx, py])] += 1
                    qt += 1
            if qt > 0:
                qhist /= qt
            spatial[(qy * 2 + qx) * 16:(qy * 2 + qx + 1) * 16] = qhist

    sobel = _sobel_features(gray)
    lbp = _lbp_features(gray)
    edge_hist = _edge_histogram(flat_gray, w, h)

    return np.concatenate([flat_gray, hist, spatial, sobel, lbp, edge_hist])


_CTX_DIMS = 128  # 64 grayscale + 64 spatial color


def _ctx_features(ctx_rgb, ctx_feat):
    """Lightweight 128-dim context features from the neighborhood crop.

    Uses a smaller representation than _crop_features to avoid
    overfitting when training data is limited.
    """
    # Downscale grayscale to 8x8 = 64 dims
    gray_small = np.array(ctx_feat, dtype=np.float64)
    from PIL import Image as _Img
    small = _Img.fromarray(gray_small.astype(np.float32)).resize(
        (8, 8), _Img.LANCZOS)
    gray64 = np.array(small, dtype=np.float64).flatten()
    gmax = np.abs(gray64).max()
    if gmax > 0:
        gray64 /= gmax

    # Spatial color histogram (4 quadrants x 16 palette bins = 64 dims)
    px = ctx_rgb.load()
    w, h = ctx_rgb.size
    spatial = np.zeros(64, dtype=np.float64)
    for qy in range(2):
        for qx in range(2):
            qhist = np.zeros(16, dtype=np.float64)
            qt = 0
            for py in range(qy * h // 2, (qy + 1) * h // 2):
                for pxx in range(qx * w // 2, (qx + 1) * w // 2):
                    qhist[_closest_palette(px[pxx, py])] += 1
                    qt += 1
            if qt > 0:
                qhist /= qt
            spatial[(qy * 2 + qx) * 16:(qy * 2 + qx + 1) * 16] = qhist

    return np.concatenate([gray64, spatial])


def augment_rgb(rgb_img, rng):
    """Create an augmented copy of an RGB image.

    Applies color jitter and Gaussian noise to simulate variation.
    """
    from PIL import Image
    arr = np.array(rgb_img, dtype=np.float64)

    # Color jitter: scale each channel independently
    for c in range(3):
        factor = 1.0 + rng.uniform(-AUG_JITTER, AUG_JITTER)
        arr[:, :, c] *= factor

    # Gaussian noise
    noise = rng.normal(0, AUG_NOISE_STD, arr.shape)
    arr += noise

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _encode_class(name, rotation):
    return "%s__r%d" % (name, rotation)


def _decode_class(cls):
    parts = cls.rsplit("__r", 1)
    return parts[0], int(parts[1])


def _exemplar_weight(name):
    if name in _SPRITE_ONLY:
        return 1.0
    return 5.0


def train(examples_dir, exemplars):
    """Train the block-type classifier and save the model."""
    if not exemplars:
        return False

    X, y, w = [], [], []
    rng = np.random.RandomState(42)

    for ex in exemplars:
        if len(ex) >= 8:
            name, rotation, config, size, fex, rgb, ctx_rgb, ctx_feat = ex[:8]
        elif len(ex) == 6:
            name, rotation, config, size, fex, rgb = ex
            ctx_rgb, ctx_feat = None, None
        elif len(ex) == 5:
            name, rotation, config, size, fex = ex
            rgb = None
            ctx_rgb, ctx_feat = None, None
        else:
            continue
        if size != 1:
            continue
        if name.startswith("__"):
            continue
        if rgb is None:
            continue

        wt = _exemplar_weight(name)
        X.append(feat_vector(rgb, fex, ctx_rgb=ctx_rgb, ctx_feat=ctx_feat))
        y.append(_encode_class(name, rotation))
        w.append(wt)

        # Augment all exemplars to improve generalization
        for _ in range(AUG_AUGMENT_PER_EX):
            aug = augment_rgb(rgb, rng)
            from PIL import ImageFilter
            aug_feat = aug.convert("L").filter(
                ImageFilter.GaussianBlur(radius=recognize.BLUR))
            # Context doesn't change with augmentation — reuse original
            X.append(feat_vector(aug, aug_feat, ctx_rgb=ctx_rgb, ctx_feat=ctx_feat))
            y.append(_encode_class(name, rotation))
            w.append(wt)

    classes = set(y)
    if len(classes) < 2:
        return False

    X = np.array(X)
    y = np.array(y)
    w = np.array(w, dtype=np.float64)

    # Try XGBoost first, fall back to Random Forest.
    try:
        from xgboost import XGBClassifier
        clf = XGBClassifier(
            n_estimators=300,
            max_depth=8,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=2,
            eval_metric="mlogloss",
            n_jobs=-1,
            random_state=42,
        )
        # XGBoost uses sample_weight in fit
        clf.fit(X, y, sample_weight=w)
    except Exception:
        clf = RandomForestClassifier(
            n_estimators=200,
            max_depth=None,
            min_samples_split=2,
            min_samples_leaf=1,
            class_weight="balanced",
            n_jobs=-1,
            random_state=42,
        )
        clf.fit(X, y, sample_weight=w)

    ch = _corpus_hash(examples_dir)
    joblib.dump((clf, ch), _model_path(examples_dir))
    return True


def load(examples_dir):
    """Load the trained classifier. Returns (clf, hash) or (None, None)."""
    mp = _model_path(examples_dir)
    if not os.path.exists(mp):
        return None, None
    clf, ch = joblib.load(mp)
    return clf, ch


def classify(clf, rgb_img, feat_img, ctx_rgb=None, ctx_feat=None):
    """Predict (name, rotation, confidence) from an RGB crop + _feat output."""
    vec = feat_vector(rgb_img, feat_img, ctx_rgb=ctx_rgb, ctx_feat=ctx_feat).reshape(1, -1)
    cls = clf.predict(vec)[0]
    proba = clf.predict_proba(vec)
    classes = clf.classes_
    idx = list(classes).index(cls)
    confidence = proba[0][idx]
    name, rotation = _decode_class(cls)
    return name, rotation, confidence


def classify_with_fallback(clf, rgb_img, feat_img, exemplars,
                           ctx_rgb=None, ctx_feat=None):
    """Classify using the RF model; fall back to SSD if the RF confidence
    is below CONFIDENCE_THRESHOLD.

    Returns (name, rotation, config, source) where source is 'rf' or 'ssd'.
    """
    name, rotation, confidence = classify(
        clf, rgb_img, feat_img, ctx_rgb=ctx_rgb, ctx_feat=ctx_feat)
    if confidence >= CONFIDENCE_THRESHOLD:
        config = None
        for ex in exemplars:
            ename, erot, ecfg = ex[0], ex[1], ex[2]
            if ename == name and erot == rotation:
                config = ecfg
                break
        return name, rotation, config, "rf"

    best = None
    for ex in exemplars:
        name, rotation, config, size, fex = ex[0], ex[1], ex[2], ex[3], ex[4]
        d = recognize._ssd(feat_img, fex)
        if best is None or d < best[0]:
            best = (d, name, rotation, config)
    if best is None:
        return None, 0, None, "ssd"
    return best[1], best[2], best[3], "ssd"


# ---------------------------------------------------------------------------
# Occupancy classifier (foreground vs background)
# ---------------------------------------------------------------------------

def _occ_model_path(examples_dir):
    return os.path.join(examples_dir, "_occ_classifier.joblib")


def train_occupancy(examples_dir, occ_data):
    """Train a binary occupancy classifier and save it.

    ``occ_data`` is a list of ``(feature_vector, is_occupied)`` tuples.
    """
    if len(occ_data) < 20:
        return False
    X = np.array([d[0] for d in occ_data])
    y = np.array([1 if d[1] else 0 for d in occ_data], dtype=np.int32)

    # Try XGBoost first, fall back to Random Forest.
    try:
        from xgboost import XGBClassifier
        clf = XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            n_jobs=-1,
            random_state=42,
        )
        clf.fit(X, y)
    except Exception:
        clf = RandomForestClassifier(
            n_estimators=150,
            max_depth=None,
            min_samples_split=2,
            min_samples_leaf=1,
            class_weight="balanced",
            n_jobs=-1,
            random_state=42,
        )
        clf.fit(X, y)

    ch = _corpus_hash(examples_dir)
    joblib.dump((clf, ch), _occ_model_path(examples_dir))
    return True


def load_occupancy(examples_dir):
    """Load the occupancy classifier. Returns (clf, hash) or (None, None)."""
    mp = _occ_model_path(examples_dir)
    if not os.path.exists(mp):
        return None, None
    clf, ch = joblib.load(mp)
    return clf, ch


def occ_probability(clf, rgb_img, feat_img):
    """Return the probability (0..1) that a cell is occupied."""
    vec = feat_vector(rgb_img, feat_img).reshape(1, -1)
    proba = clf.predict_proba(vec)
    classes = clf.classes_
    if 1 in classes:
        idx = list(classes).index(1)
        return float(proba[0][idx])
    return 0.0
