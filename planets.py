"""Classify Mindustry blocks by planet (Serpulo / Erekir / editor-only).

Placement is planet-gated: Erekir blocks cannot be placed on Serpulo maps and
vice versa. Membership is derived from the game's own tech trees, plus a small
set of classic blocks that are placeable but absent from every tree, plus
sandbox/editor-only blocks.
"""
import json
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "training", "_cache")
RAW = ("https://raw.githubusercontent.com/Anuken/Mindustry/master"
       "/core/src/mindustry/content/{0}TechTree.java")

# Tech-tree var name -> catalog sprite name mismatches.
ALIASES = {
    "switch-block": "switch",
}

# Placeable classic Serpulo blocks that appear in no tech tree.
SERPULO_EXTRA = {
    "bridge-conveyor",
    "launch-pad",
    "solar-panel-large",
    "shield-projector",
    "large-shield-projector",
}

# Not player-placeable in a normal game (unit parts, payloads, sandbox/debug).
EDITOR_ONLY = {
    "item-source", "item-void", "liquid-source", "liquid-void",
    "power-source", "power-void", "payload-source", "payload-void",
    "heat-source", "world-cell", "world-message", "world-processor",
    "world-switch", "scathe-missile", "scathe-missile-phase",
    "scathe-missile-surge-split", "thruster",
}


def _dash(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).lower()


def _fetch(planet):
    os.makedirs(CACHE, exist_ok=True)
    local = os.path.join(CACHE, "%sTechTree.java" % planet)
    if not os.path.exists(local):
        urllib.request.urlretrieve(RAW.format(planet), local)
    with open(local) as f:
        return f.read()


_planets_cache = None


def load_planets():
    """Return {'serpulo': set(sprite names), 'erekir': set(sprite names)}."""
    global _planets_cache
    if _planets_cache is not None:
        return _planets_cache
    out = {}
    for planet in ("Serpulo", "Erekir"):
        src = _fetch(planet)
        ids = set(re.findall(
            r"\bnode(?:Produce)?\(\s*([a-z][A-Za-z0-9_]*)\s*[,)]", src))
        roots = re.findall(r'nodeRoot\("[^"]+",\s*([a-z][A-Za-z0-9_]*)', src)
        names = {_dash(i) for i in list(ids) + roots}
        names = {ALIASES.get(n, n) for n in names}
        out[planet.lower()] = names
    _planets_cache = out
    return out


def classify(catalog_names=None):
    """Split catalog block names into serpulo/erekir/editor groups."""
    if catalog_names is None:
        with open(os.path.join(HERE, "block_catalog.json")) as f:
            catalog_names = json.load(f)
    planets = load_planets()
    serpulo = (planets["serpulo"] | SERPULO_EXTRA) & set(catalog_names)
    erekir = planets["erekir"] & set(catalog_names)
    placed = serpulo | erekir
    editor = {n for n in catalog_names if n not in placed}
    return {"serpulo": sorted(serpulo),
            "erekir": sorted(erekir),
            "editor": sorted(editor)}


def schematic_planet(block_names):
    """Guess which planet a schematic belongs to from its block types."""
    planets = load_planets()
    names = set(block_names)
    if names & planets["erekir"]:
        return "erekir"
    if names & planets["serpulo"]:
        return "serpulo"
    return "unknown"


if __name__ == "__main__":
    g = classify()
    for k, v in g.items():
        print("%-8s %3d  %s" % (k, len(v), v[:6]))
