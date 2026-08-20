"""Random Forest classifiers for Mindustry block recognition.

Requires scikit-learn (``pip install scikit-learn``).  Two models are
trained at startup:
  1. Block classifier: predicts which block type + rotation a cell contains.
  2. Occupancy classifier: predicts whether a cell contains any block at all
     (foreground vs background), replacing the fragile density-threshold check.

Features: grayscale (576d) + palette color histogram (16d) + spatial
color (64d) = 656 dimensions total.

Key design choices:
  - If a block's center region is a solid color (>=90% same palette index),
    it is masked during feature extraction — this catches filter-type
    indicators (sorter, overflow-gate) without losing structural detail
    in blocks that have multi-colored centers.
  - Screenshot exemplars are weighted 5x higher than sprite exemplars
    during training, since they match the actual in-game appearance.
  - A confidence threshold gates RF usage: below it, SSD is used instead.
"""
import os
import hashlib

import numpy as np
from sklearn.ensemble import RandomForestClassifier
import joblib

import core
import recognize


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


def feat_vector(rgb_img, feat_img, can=recognize.CANON):
    """Extract a combined feature vector from an RGB crop and its _feat() output.

    Features:
      - Grayscale (flatten _feat output): 576 dims
      - Palette color histogram:           16 dims
      - Spatial color (4 quadrants):       64 dims
    Total: 656 dims.

    If the center region is a solid color it is masked out so the
    classifier focuses on the border/frame.
    """
    gray = np.array(feat_img, dtype=np.float64).flatten()

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

    return np.concatenate([gray, hist, spatial])


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
    """Train the block-type Random Forest and save the model."""
    if not exemplars:
        return False

    X, y, w = [], [], []
    for ex in exemplars:
        if len(ex) == 5:
            name, rotation, config, size, fex = ex
            rgb = None
        elif len(ex) == 6:
            name, rotation, config, size, fex, rgb = ex
        else:
            continue
        if size != 1:
            continue
        if name.startswith("__"):
            continue
        if rgb is not None:
            X.append(feat_vector(rgb, fex))
        else:
            continue  # need RGB crop for feature extraction
        y.append(_encode_class(name, rotation))
        w.append(_exemplar_weight(name))

    classes = set(y)
    if len(classes) < 2:
        return False

    X = np.array(X)
    y = np.array(y)
    w = np.array(w, dtype=np.float64)

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


def classify(clf, rgb_img, feat_img):
    """Predict (name, rotation, confidence) from an RGB crop + _feat output."""
    vec = feat_vector(rgb_img, feat_img).reshape(1, -1)
    cls = clf.predict(vec)[0]
    proba = clf.predict_proba(vec)
    classes = clf.classes_
    idx = list(classes).index(cls)
    confidence = proba[0][idx]
    name, rotation = _decode_class(cls)
    return name, rotation, confidence


def classify_with_fallback(clf, rgb_img, feat_img, exemplars):
    """Classify using the RF model; fall back to SSD if the RF confidence
    is below CONFIDENCE_THRESHOLD.

    Returns (name, rotation, config, source) where source is 'rf' or 'ssd'.
    """
    name, rotation, confidence = classify(clf, rgb_img, feat_img)
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
    """Train a binary occupancy RF and save it.

    ``occ_data`` is a list of ``(feature_vector, is_occupied)`` tuples.
    """
    if len(occ_data) < 20:
        return False
    X = np.array([d[0] for d in occ_data])
    y = np.array([1 if d[1] else 0 for d in occ_data], dtype=np.int32)

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
