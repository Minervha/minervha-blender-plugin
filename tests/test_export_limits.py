"""Pure-logic tests for export_limits (no bpy).

Covers the two functions that gate the export — evaluate (soft/hard breaches, scene + per-mesh,
experimental bypass) and clamp_max_res (the fixed 8192 px texture rule). The bpy counters
(gather_scene_budget / gather_texture_inventory) need a live scene and are checked via the
headless Blender path, not here. Run:

    python tests/test_export_limits.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import export_limits  # noqa: E402

T = export_limits.DEFAULT_THRESHOLDS


def _budget(faces_total=0, faces_by_mesh=None, geometries_unique=0):
    return {
        "faces_total": faces_total,
        "faces_by_mesh": faces_by_mesh or {},
        "geometries_unique": geometries_unique,
    }


def test_under_all_limits_is_clean():
    b = _budget(faces_total=100_000, geometries_unique=10,
                faces_by_mesh={"A": 50_000, "B": 50_000})
    assert export_limits.evaluate(b, T, experimental=False) == []


def test_scene_faces_soft_and_hard():
    soft = export_limits.evaluate(_budget(faces_total=600_000), T, False)
    assert len(soft) == 1 and soft[0] == {
        "resource": "faces", "scope": "scene", "value": 600_000, "limit": 500_000, "level": "soft"}

    hard = export_limits.evaluate(_budget(faces_total=2_500_000), T, False)
    assert hard[0]["level"] == "hard" and hard[0]["limit"] == 2_000_000
    assert export_limits.has_hard(hard)


def test_geometry_cap():
    soft = export_limits.evaluate(_budget(geometries_unique=3_500), T, False)
    assert soft[0] == {"resource": "objects", "scope": "scene",
                       "value": 3_500, "limit": 3_200, "level": "soft"}
    hard = export_limits.evaluate(_budget(geometries_unique=4_001), T, False)
    assert hard[0]["level"] == "hard" and hard[0]["limit"] == 4_000


def test_per_mesh_breaches():
    b = _budget(faces_by_mesh={"big": 600_000, "warn": 150_000, "ok": 10_000})
    out = export_limits.evaluate(b, T, False)
    by_mesh = {v["mesh"]: v for v in out if v["scope"] == "mesh"}
    assert by_mesh["big"]["level"] == "hard" and by_mesh["big"]["limit"] == 500_000
    assert by_mesh["warn"]["level"] == "soft" and by_mesh["warn"]["limit"] == 100_000
    assert "ok" not in by_mesh
    assert export_limits.has_hard(out)


def test_boundary_is_inclusive():
    # value exactly at a threshold trips that level (>=)
    assert export_limits.evaluate(_budget(faces_total=500_000), T, False)[0]["level"] == "soft"
    assert export_limits.evaluate(_budget(faces_total=2_000_000), T, False)[0]["level"] == "hard"


def test_experimental_disables_everything():
    b = _budget(faces_total=9_000_000, geometries_unique=99_999,
                faces_by_mesh={"huge": 9_000_000})
    assert export_limits.evaluate(b, T, experimental=True) == []


def test_clamp_max_res():
    c = export_limits.clamp_max_res
    assert c(None, False) == 8192          # no user cap -> the rule
    assert c(16384, False) == 8192         # above the rule -> clamped
    assert c(8192, False) == 8192          # at the rule -> unchanged
    assert c(4096, False) == 4096          # below the rule -> honoured
    # experimental honours the user's choice verbatim, including no cap
    assert c(16384, True) == 16384
    assert c(None, True) is None


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"EXPORT_LIMITS FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"EXPORT_LIMITS OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
