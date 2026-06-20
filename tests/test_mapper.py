"""Regression test: mapper.py must still match the committed golden snapshot.

Runs the fixtures (tests/fixtures/normalized_materials.json) through mapper.py and
asserts the resulting `entry` (the customMaterials object that lands in the save)
and `textures` list are identical to tests/golden/expected.json. The golden is a
snapshot of mapper.py itself (regenerate with tests/_gen_golden.py) — the Studio's
mapMaterial.js it once mirrored has been removed, so this guards against accidental
drift in the mapping, not cross-language parity.

Floats are compared with a small tolerance. Run:  python tests/test_mapper.py
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


# Phase-1 shading-compatibility signals — the golden compares only entry/textures, so the
# new report-level signals (bIsTriplanar inference, per-loss notes + bakeCandidates) are
# asserted explicitly here. Each value: bIsTriplanar expectation, required (channel,reason)
# bakeCandidates, and an optional note substring.
_SEMANTIC = {
    "TriplanarProjection":   {"triplanar": True},
    "TriplanarNoUV":         {"triplanar": True, "note_sub": "without UVs"},
    "MultiTexBaseColor":     {"triplanar": False, "bake": [("diffuse", "multi-texture")],
                              "needs_bake": ["diffuse"]},
    "DivergentNormalTiling": {"bake": [("normal", "divergent-uv")]},
    "RotatedMapping":        {"bake": [("diffuse", "rotation")]},
    "PackedORM":             {"bake": [("roughness", "orm-packed")]},
    "UdimDiffuse":           {"bake": [("diffuse", "udim")]},
    "ProceduralDiffuse":     {"bake": [("diffuse", "procedural")], "needs_bake": ["diffuse"]},
    "BakedDiffuse":          {"tiling_identity": True},
    "FlipGreenOff":          {"flip_green": False},
    "LossyAniso":            {"note_sub": "anisotropy"},
    # chunk-03: only DIRECT textures ship; a slot reached through a transforming node graph is left
    # to baking (Bake off -> empty + reported), never shipped as a wrong guess.
    "TransformedDiffuse":    {"needs_bake": ["diffuse"], "note_sub": "node graph"},
    "DirectDiffuse":         {"needs_bake_not": ["diffuse"]},
}


def run_semantic():
    fixtures = json.load(open(os.path.join(HERE, "fixtures", "normalized_materials.json"), encoding="utf-8"))
    by_name = {m.get("name"): m for m in fixtures}
    fails = []
    for name, checks in _SEMANTIC.items():
        norm = by_name.get(name)
        if norm is None:
            fails.append((name, "fixture missing")); continue
        r = mapper.map_material(norm)
        if r is None:
            fails.append((name, "mapper returned None")); continue
        entry, report = r["entry"], r["report"]
        if "triplanar" in checks and entry["bIsTriplanar"] is not checks["triplanar"]:
            fails.append((name, f"bIsTriplanar={entry['bIsTriplanar']!r} expected {checks['triplanar']!r}"))
        if checks.get("tiling_identity") and entry["textureTiling"] != {"x": 1, "y": 1, "z": 1}:
            fails.append((name, f"textureTiling={entry['textureTiling']} expected identity (baked diffuse)"))
        if "flip_green" in checks and entry["bFlipGreenChannel"] is not checks["flip_green"]:
            fails.append((name, f"bFlipGreenChannel={entry['bFlipGreenChannel']!r} expected {checks['flip_green']!r}"))
        for ch, reason in checks.get("bake", []):
            if {"channel": ch, "reason": reason} not in report["bakeCandidates"]:
                fails.append((name, f"missing bakeCandidate ({ch},{reason}); have {report['bakeCandidates']}"))
        for ch in checks.get("needs_bake", []):
            if ch not in report.get("needsBake", []):
                fails.append((name, f"expected '{ch}' in needsBake; have {report.get('needsBake')}"))
        for ch in checks.get("needs_bake_not", []):
            if ch in report.get("needsBake", []):
                fails.append((name, f"'{ch}' should NOT be in needsBake; have {report.get('needsBake')}"))
        sub = checks.get("note_sub")
        if sub and not any(sub in n for n in report["notes"]):
            fails.append((name, f"missing note ~ '{sub}'; have {report['notes']}"))
    if fails:
        print(f"SEMANTIC FAILED — {len(fails)} issue(s):")
        for n, m in fails:
            print(f"  {n}: {m}")
        sys.exit(1)
    print(f"SEMANTIC OK — {len(_SEMANTIC)} shading-compat signals verified")


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
    print(f"GOLDEN OK — {len(fixtures)} fixtures match the mapper.py snapshot")


def run_surface_type():
    """The `surface_type` param drives entry['surfaceType']; default stays SurfaceType_Default."""
    fixtures = json.load(open(os.path.join(HERE, "fixtures", "normalized_materials.json"), encoding="utf-8"))
    norm = next(m for m in fixtures if mapper.map_material(m) is not None)
    fails = []
    default = mapper.map_material(norm)["entry"]["surfaceType"]
    if default != "SurfaceType_Default":
        fails.append(f"default surfaceType={default!r} expected 'SurfaceType_Default'")
    for st in ("SurfaceType3", "SurfaceType2", "SurfaceType_Default"):
        got = mapper.map_material(norm, surface_type=st)["entry"]["surfaceType"]
        if got != st:
            fails.append(f"surface_type={st!r} -> entry {got!r}")
    if fails:
        print(f"SURFACE_TYPE FAILED — {len(fails)} issue(s):")
        for m in fails:
            print(f"  {m}")
        sys.exit(1)
    print("SURFACE_TYPE OK — surface_type param drives entry['surfaceType']")


if __name__ == "__main__":
    run()
    run_semantic()
    run_surface_type()
