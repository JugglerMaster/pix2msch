#!/usr/bin/env python3
"""Parse Mindustry's Blocks.java to extract block metadata (name, size, rotates).

Downloads Blocks.java and relevant block class files from GitHub.
Generates block_catalog.json with size and rotation info for every placeable block.
"""
import re, json, sys, urllib.request
from pathlib import Path

RAW_URL = "https://raw.githubusercontent.com/Anuken/Mindustry/master/core/src/mindustry/content/Blocks.java"
CLASS_BASE = "https://raw.githubusercontent.com/Anuken/Mindustry/master/core/src/mindustry/world/blocks/"
OUT_JSON = Path(__file__).with_name("block_catalog.json")

# Environment-like classes to exclude from the catalog.
ENV_CLASSES = {
    "Floor", "StaticWall", "StaticTree", "TallBlock", "TreeBlock",
    "Prop", "Seaweed", "SeaBush", "OverlayFloor", "OreBlock",
    "AirBlock", "SpawnBlock", "Cliff", "ColoredFloor", "ColoredWall",
    "CharacterOverlay", "RuneOverlay", "ShallowLiquid", "EmptyFloor",
    "SteamVent", "RemoveWall", "RemoveOre", "ConstructBlock",
    "LiquidFloor",
}

# Correct class -> parent class mapping (from source research).
# Only includes cases where the child class is different from the parent.
CLASS_PARENTS = {
    "BufferedItemBridge": "ItemBridge",
    "InvertedSorter": "Sorter",
    "Distributor": "Router",
    "UnderflowGate": "OverflowGate",
    "UnderflowDuct": "OverflowDuct",
    "SurgeConveyor": "StackConveyor",
    "SurgeRouter": "StackRouter",
    "ArmoredConveyor": "Conveyor",
    "ArmoredDuct": "Duct",
    "DuctUnloader": "Duct",
    "PayloadRouter": "PayloadConveyor",
    "PayloadUnloader": "PayloadLoader",
    "LargePayloadMassDriver": "PayloadMassDriver",
    "LargeConstructor": "Constructor",
    "BeamTower": "BeamNode",
    "LongPowerNode": "PowerNode",
}

# Blocks where the Java class in Blocks.java differs from the actual class.
# Maps the constructor class name in Blocks.java to the real class to look up.
CLASS_ALIASES = {
    "AirFactory": "UnitFactory",
    "GroundFactory": "UnitFactory",
    "NavalFactory": "UnitFactory",
    "CliffCrusher": "WallCrafter",
    "LargeCliffCrusher": "WallCrafter",
    "Diode": "PowerDiode",
    "Illuminator": "LightBlock",
    "advancedLaunchPad": "LaunchPad",
    # InvertedSorter -> Sorter, UnderflowGate -> OverflowGate are handled by CLASS_PARENTS
}

# File paths for classes (subdirectory under blocks/)
CLASS_PATHS = {
    "Conveyor": "distribution/Conveyor.java",
    "StackConveyor": "distribution/StackConveyor.java",
    "StackRouter": "distribution/StackRouter.java",
    "Duct": "distribution/Duct.java",
    "DuctRouter": "distribution/DuctRouter.java",
    "OverflowDuct": "distribution/OverflowDuct.java",
    "ArmoredConveyor": "distribution/Conveyor.java",  # inherits
    "ArmoredDuct": "distribution/Duct.java",  # inherits
    "DuctUnloader": "distribution/DirectionalUnloader.java",
    "DirectionalUnloader": "distribution/DirectionalUnloader.java",  # inherits
    "OverflowGate": "distribution/OverflowGate.java",
    "Sorter": "distribution/Sorter.java",
    "Router": "distribution/Router.java",
    "Junction": "distribution/Junction.java",
    "ItemBridge": "distribution/ItemBridge.java",
    "BufferedItemBridge": "distribution/ItemBridge.java",  # inherits
    "MassDriver": "distribution/MassDriver.java",
    "Conduit": "liquid/Conduit.java",
    "LiquidRouter": "liquid/LiquidRouter.java",
    "PowerDiode": "power/PowerDiode.java",
    "PowerNode": "power/PowerNode.java",
    "BeamNode": "power/BeamNode.java",
    "PayloadConveyor": "payloads/PayloadConveyor.java",
    "PayloadRouter": "payloads/PayloadConveyor.java",  # inherits
    "PayloadLoader": "payloads/PayloadLoader.java",
    "PayloadUnloader": "payloads/PayloadLoader.java",  # inherits
    "PayloadMassDriver": "payloads/PayloadMassDriver.java",
    "LargePayloadMassDriver": "payloads/PayloadMassDriver.java",  # inherits
    "PayloadSource": "payloads/PayloadSource.java",
    "PayloadVoid": "payloads/PayloadVoid.java",
    "Constructor": "payloads/Constructor.java",
    "LargeConstructor": "payloads/Constructor.java",  # inherits
    "UnitFactory": "units/UnitFactory.java",
    "Reconstructor": "units/Reconstructor.java",
    "UnitBlock": "units/UnitBlock.java",  # parent of Reconstructor
    "Drill": "production/Drill.java",
    "BeamDrill": "production/BeamDrill.java",
    "BurstDrill": "production/BurstDrill.java",
    "WallCrafter": "production/WallCrafter.java",
    "LaunchPad": "campaign/LaunchPad.java",
    "LightBlock": "sandbox/LightBlock.java",
    # Blocks that don't set rotate - include to avoid fetch errors
    "Unloader": "storage/Unloader.java",
    "CoreBlock": "storage/CoreBlock.java",
    "StorageBlock": "storage/StorageBlock.java",
    "Wall": "defense/Wall.java",
    "Door": "defense/Door.java",
    "LogicBlock": "logic/LogicBlock.java",
    "SwitchBlock": "logic/SwitchBlock.java",
    "MemoryBlock": "logic/MemoryBlock.java",
    "DisplayBlock": "logic/DisplayBlock.java",
}


def fetch(url):
    """Fetch a URL and return decoded text."""
    req = urllib.request.Request(url, headers={"User-Agent": "block_catalog"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_blocks_java(source):
    """Extract block definitions from Blocks.java source."""
    results = []
    constructor_re = re.compile(
        r'(\w+)\s*=\s*new\s+(\w+)\s*\(\s*"([^"]+)"'
    )

    for m in constructor_re.finditer(source):
        var_name = m.group(1)
        class_name = m.group(2)
        sprite_id = m.group(3)

        start = m.end()
        chunk = source[start:start + 5000]

        # Find the matching {{ }} pair, handling nesting.
        dd_start = chunk.find("{{")
        if dd_start == -1:
            continue
        depth = 0
        dd_end = -1
        for i in range(dd_start, len(chunk) - 1):
            if chunk[i:i+2] == "{{":
                depth += 1
            elif chunk[i:i+2] == "}}":
                depth -= 1
                if depth == 0:
                    dd_end = i
                    break
        if dd_end == -1:
            continue
        body = chunk[dd_start + 2:dd_end]

        size_match = re.search(r'\bsize\s*=\s*(\d+)', body)
        size = int(size_match.group(1)) if size_match else 1

        rotate_match = re.search(r'\brotate\s*=\s*(true|false)', body)
        rotates_here = rotate_match.group(1) == "true" if rotate_match else None

        results.append({
            "var": var_name,
            "sprite": sprite_id,
            "class": class_name,
            "size": size,
            "rotates_here": rotates_here,
        })

    return results


def check_class_rotate(class_name, cache):
    """Check if a block class (or its parent chain) sets rotate = true."""
    if class_name in cache:
        return cache[class_name]

    # Resolve alias
    real_class = CLASS_ALIASES.get(class_name, class_name)

    # Look up file path
    path = CLASS_PATHS.get(real_class)
    if path is None:
        # Unknown class - check parent
        parent = CLASS_PARENTS.get(class_name) or CLASS_PARENTS.get(real_class)
        if parent:
            result = check_class_rotate(parent, cache)
            cache[class_name] = result
            return result
        cache[class_name] = False
        return False

    try:
        source = fetch(CLASS_BASE + path)
    except Exception:
        # File not found - check parent
        parent = CLASS_PARENTS.get(class_name) or CLASS_PARENTS.get(real_class)
        if parent:
            result = check_class_rotate(parent, cache)
            cache[class_name] = result
            return result
        cache[class_name] = False
        return False

    has_rotate = bool(re.search(r'\brotate\s*=\s*true\b', source))

    if not has_rotate:
        # Check parent class from hierarchy
        parent = CLASS_PARENTS.get(class_name) or CLASS_PARENTS.get(real_class)
        if parent:
            has_rotate = check_class_rotate(parent, cache)

    cache[class_name] = has_rotate
    return has_rotate


def main():
    print("Downloading Blocks.java ...")
    source = fetch(RAW_URL)
    blocks = parse_blocks_java(source)
    print(f"Found {len(blocks)} blocks with string IDs")

    blocks = [b for b in blocks if b["class"] not in ENV_CLASSES]
    print(f"After filtering environment: {len(blocks)} placeable blocks")

    print("Checking class hierarchy for rotate flags ...")
    class_cache = {}
    for b in blocks:
        if b["rotates_here"] is not None:
            b["rotates"] = b["rotates_here"]
        else:
            b["rotates"] = check_class_rotate(b["class"], class_cache)

    sizes = {}
    directional = set()
    catalog = {}

    for b in blocks:
        name = b["sprite"]
        sizes[name] = b["size"]
        if b["rotates"]:
            directional.add(name)
        catalog[name] = {
            "size": b["size"],
            "rotates": b["rotates"],
            "class": b["class"],
            "var": b["var"],
        }

    with open(OUT_JSON, "w") as f:
        json.dump(catalog, f, indent=2, sort_keys=True)
    print(f"Wrote {len(catalog)} blocks to {OUT_JSON}")

    if "--python" in sys.argv:
        print("\n# --- Paste into recognize.py ---")
        print("SIZES = {")
        for name in sorted(sizes.keys()):
            print(f'    "{name}": {sizes[name]},')
        print("}")
        print()
        print("DIRECTIONAL = {")
        for name in sorted(directional):
            print(f'    "{name}",')
        print("}")
    else:
        print(f"\nSIZES: {len(sizes)} entries")
        print(f"DIRECTIONAL: {len(directional)} entries")
        print(f"\nDirectional blocks:")
        for name in sorted(directional):
            cls = catalog[name]["class"]
            print(f"  {name} ({cls})")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
