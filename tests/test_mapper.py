"""Parity test: mapper.py (Python port) must match the Studio's mapMaterial.js.

Runs the shared fixtures (tests/fixtures/normalized_materials.json) through the
Python mapper and asserts the resulting `entry` (the customMaterials object that
lands in the save) and `textures` list are identical to the golden output
generated from mapMaterial.js (tests/golden/expected.json).

Floats are compared with a small tolerance so JS/Python repr differences don't
cause false failures. Run:  python tests/test_mapper.py   (exit 0 = parity OK)
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import mapper  # noqa: E402


def approx_equal(a, b, tol=1e-9):
    # Bools first: True/False must match exactly and not be treated as ints 1/0.
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(approx_equal(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(approx_equal(x, y, tol) for x, y in zip(a, b))
    return a == b


def run():
    fixtures = json.load(open(os.path.join(HERE, "fixtures", "normalized_materials.json"), encoding="utf-8"))
    golden = json.load(open(os.path.join(HERE, "golden", "expected.json"), encoding="utf-8"))
    assert len(fixtures) == len(golden), f"fixture/golden length mismatch: {len(fixtures)} vs {len(golden)}"

    fails = []
    for i, (norm, exp) in enumerate(zip(fixtures, golden)):
        name = norm.get("name")
        got = mapper.map_material(norm)
        if exp is None:
            if got is not None:
                fails.append((i, name, "expected None (skipped), got an entry"))
            continue
        if got is None:
            fails.append((i, name, "got None, expected an entry"))
            continue
        if not approx_equal(got["entry"], exp["entry"]):
            fails.append((i, name, f"entry mismatch\n  got={got['entry']}\n  exp={exp['entry']}"))
        if not approx_equal(got["textures"], exp["textures"]):
            fails.append((i, name, f"textures mismatch\n  got={got['textures']}\n  exp={exp['textures']}"))

    if fails:
        print(f"PARITY FAILED — {len(fails)} mismatch(es):")
        for i, name, msg in fails:
            print(f"  [{i}] {name}: {msg}")
        sys.exit(1)
    print(f"PARITY OK — {len(fixtures)} fixtures match the Studio mapMaterial.js golden")


if __name__ == "__main__":
    run()
