"""Tests for prop_mapper.blender_to_wl_transform — now a thin caller of wl_transform.

The coordinate math (change of basis, rotation conjugation, scale permutation) is pinned in
tests/test_wl_transform.py. Here we only pin the prop_mapper contract: it delegates to
wl_transform with WL_BASIS, threads position_scale as the scale_factor, and emits the golden
key order.

Pure Python (no bpy). Run:  python tests/test_transform.py  (or pytest)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import prop_mapper      # noqa: E402
import wl_transform     # noqa: E402


def test_delegates_to_wl_transform():
    for loc, eul, scl, ps in [
        ((1.5, -2.0, 3.0), (0, 0, 0), (1, 1, 1), 1.0),
        ((0, 0, 5), (0.3, 0.0, 1.2), (2, 0.5, 9), 100.0),
    ]:
        got = prop_mapper.blender_to_wl_transform(loc, eul, "XYZ", scl, position_scale=ps)
        exp = wl_transform.object_transform(loc, eul, "XYZ", scl, basis=wl_transform.WL_BASIS, scale_factor=ps)
        assert got == exp, (loc, eul, scl, ps)


def test_key_order():
    # Golden stability depends on key order matching real saves.
    t = prop_mapper.blender_to_wl_transform((1, 2, 3), (0, 0, 0), "XYZ", (1, 1, 1))
    assert list(t["position"]) == ["x", "y", "z"]
    assert list(t["rotation"]) == ["pitch", "yaw", "roll"]
    assert list(t["scale"]) == ["x", "y", "z"]


def test_seed_up_axis_and_scale_factor():
    # The headline behavior, tied to WL_BASIS (corpus: Z-up): Blender up (+Z) -> game up (+Z), x100.
    t = prop_mapper.blender_to_wl_transform((0, 0, 1), (0, 0, 0), "XYZ", (1, 1, 1), position_scale=100.0)
    assert t["position"] == {"x": 0, "y": 0, "z": 100.0}


def test_identity_rotation_is_zero():
    t = prop_mapper.blender_to_wl_transform((0, 0, 0), (0, 0, 0), "XYZ", (1, 1, 1))
    assert t["rotation"] == {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"TRANSFORM FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"TRANSFORM OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
