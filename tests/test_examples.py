"""Test suite for pix2msch, exercised on the bundled example screenshots.

Run directly:   python3 tests/test_examples.py
Or with pytest: pytest tests/test_examples.py
"""
import os
import sys
import glob
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import recognize
from collections import Counter

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def _blocks_of(path):
    w, h, blocks = recognize.parse_msch(path)
    return w, h, blocks


def _parse_names(path):
    w, h, blocks = _blocks_of(path)
    return Counter(b[0] for b in blocks)


# --------------------------------------------------------------------------
# Reference-msch mode: detecting from a screenshot + its reference .msch
# should reproduce that reference exactly (byte-identical block multiset).
# --------------------------------------------------------------------------
def test_reference_examples():
    failures = []
    for png in sorted(glob.glob(os.path.join(EXAMPLES, "*.png"))):
        msch = png[:-4] + ".msch"
        if not os.path.exists(msch):
            continue
        d = tempfile.mkdtemp()
        core.pix2msch(imgfile=png, reference=msch, save_location=d, name="out")
        out = os.path.join(d, "out.msch")
        ref = _parse_names(msch)
        got = _parse_names(out)
        if ref != got:
            failures.append((os.path.basename(png), dict(ref), dict(got)))
    assert not failures, "Reference mismatches: %r" % (failures,)


# --------------------------------------------------------------------------
# No-reference (screenshot-only) mode with exact block counts. The 6xsil
# screenshot must detect every block exactly, with no reference .msch.
# --------------------------------------------------------------------------
SIXSIL_COUNTS = {
    "silicon-smelter": 6, "unloader": 5, "sorter": 5, "bridge-conveyor": 4,
    "titanium-conveyor": 1, "item-source": 3, "item-void": 1,
}


def test_6xsil_no_reference_exact():
    w, h, blocks, grid, thr = core.detect_structure(
        os.path.join(EXAMPLES, "6xsil.png"), (8, 8, 394, 612), 6, 11,
        block_counts=SIXSIL_COUNTS)
    got = Counter(b[0] for b in blocks)
    assert got == Counter(SIXSIL_COUNTS), "6xsil mismatch: %r" % (dict(got),)
    assert isinstance(thr, (int, float)), "threshold should be numeric, got %r" % (thr,)


def test_6xsil_roundtrip():
    w, h, blocks, grid, thr = core.detect_structure(
        os.path.join(EXAMPLES, "6xsil.png"), (8, 8, 394, 612), 6, 11,
        block_counts=SIXSIL_COUNTS)
    d = tempfile.mkdtemp()
    out = os.path.join(d, "rt.msch")
    tags = {"contentMap": "{0:{sand:4,coal:5}}", "labels": "[]",
            "name": "rt", "description": ""}
    wb = [(b[0], b[1], b[2], b[4], b[3]) for b in blocks]
    core._write_schematic(w, h, tags, wb, out, "path")
    got = _parse_names(out)
    assert got == Counter(SIXSIL_COUNTS), "roundtrip mismatch: %r" % (dict(got),)


# --------------------------------------------------------------------------
# Regression: a None threshold (core.detect_structure's default) must not
# propagate as None into the result / comparisons.
# --------------------------------------------------------------------------
def test_threshold_none_returns_numeric():
    w, h, blocks, grid, thr = core.detect_structure(
        os.path.join(EXAMPLES, "6xsil.png"), (8, 8, 394, 612), 6, 11,
        block_counts=SIXSIL_COUNTS)
    assert isinstance(thr, (int, float)), "thr was %r" % (thr,)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
            ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "->", repr(e))
    print("\n%d/%d passed" % (ok, len(tests)))
    return ok == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
