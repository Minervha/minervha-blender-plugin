"""textureTiling reciprocal test.

Blender's Mapping node Scale multiplies the UV *coordinate* (larger = more
repeats / smaller texture); Wild Life's textureTiling multiplies the texture
*size* (larger = zoomed in). They are reciprocals, so mapper.py must invert the
Scale per axis. This locks that contract explicitly (the golden snapshots it too,
but this states the intent). Pure Python (no bpy). Run:

    python tests/test_tiling.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import mapper  # noqa: E402


def _tiling_for(scale):
    """map_material a one-diffuse-texture material whose Mapping has `scale`."""
    norm = {
        "name": "M",
        "textures": [{
            "name": "t", "slots": ["Base Color"], "fileKind": "path",
            "path": "C:/tex/t.png", "basename": "t.png",
            "mapping": {"loc": {"x": 0, "y": 0, "z": 0}, "scale": scale} if scale else None,
        }],
    }
    return mapper.map_material(norm)["entry"]["textureTiling"]


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_reciprocal():
    cases = [
        ({"x": 2, "y": 2, "z": 1},      {"x": 0.5, "y": 0.5, "z": 1}),      # 2x repeat -> 0.5
        ({"x": 0.5, "y": 0.5, "z": 1},  {"x": 2, "y": 2, "z": 1}),          # 0.5 -> 2
        ({"x": 1, "y": 1, "z": 1},      {"x": 1, "y": 1, "z": 1}),          # default unchanged
        ({"x": 4, "y": 4, "z": 1},      {"x": 0.25, "y": 0.25, "z": 1}),
        ({"x": -2, "y": 2, "z": 1},     {"x": -0.5, "y": 0.5, "z": 1}),     # sign (mirror) preserved
        ({"x": 0, "y": 2, "z": 1},      {"x": 1, "y": 0.5, "z": 1}),        # 0 guarded -> 1
    ]
    fails = []
    for scale, exp in cases:
        got = _tiling_for(scale)
        if not all(approx(got[k], exp[k]) for k in ("x", "y", "z")):
            fails.append(f"scale {scale} -> {got}, expected {exp}")
    # No mapping at all -> game default {1,1,1}
    no_map = _tiling_for(None)
    if no_map != {"x": 1, "y": 1, "z": 1}:
        fails.append(f"no mapping -> {no_map}, expected {{1,1,1}}")
    return fails


def run():
    fails = test_reciprocal()
    if fails:
        print(f"TILING FAILED — {len(fails)} case(s):")
        for f in fails:
            print(f"  {f}")
        sys.exit(1)
    print("TILING OK — textureTiling is the reciprocal of Blender's Mapping Scale")


if __name__ == "__main__":
    run()
