"""Unit tests for prop_mapper.blender_to_wl_transform.

Pins the Blender -> Wild Life transform convention (calibration item #2, chunk-06).
RESOLVED in-game: position is scaled by `position_scale` (= 100 x Unit Scale, metres
-> WL cm) and passes through UNFLIPPED on every axis (Blender up=+Z -> the game's up,
sign preserved). STILL open: the rotation axis/sign permutation. A calibration change
shows up as a deliberate, reviewed diff here (and in the golden) rather than a silent shift.

Pure Python (no bpy). Run:  python tests/test_transform.py  (or pytest)
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import prop_mapper  # noqa: E402


def _approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_identity():
    t = prop_mapper.blender_to_wl_transform((0, 0, 0), (0, 0, 0), "XYZ", (1, 1, 1))
    assert t == {
        "position": {"x": 0, "y": 0, "z": 0},
        "rotation": {"pitch": 0, "yaw": 0, "roll": 0},
        "scale": {"x": 1, "y": 1, "z": 1},
    }


def test_key_order():
    # Golden stability depends on key order matching real saves.
    t = prop_mapper.blender_to_wl_transform((1, 2, 3), (0, 0, 0), "XYZ", (1, 1, 1))
    assert list(t["position"]) == ["x", "y", "z"]
    assert list(t["rotation"]) == ["pitch", "yaw", "roll"]
    assert list(t["scale"]) == ["x", "y", "z"]


def test_translation():
    # x,y,z all pass through (Blender up=+Z maps to the game's up, sign preserved).
    t = prop_mapper.blender_to_wl_transform((1.5, -2.0, 3.0), (0, 0, 0), "XYZ", (1, 1, 1))
    assert t["position"] == {"x": 1.5, "y": -2.0, "z": 3.0}


def test_z_axis_passthrough():
    up = prop_mapper.blender_to_wl_transform((0, 0, 5.0), (0, 0, 0), "XYZ", (1, 1, 1))
    assert up["position"]["z"] == 5.0
    down = prop_mapper.blender_to_wl_transform((0, 0, -5.0), (0, 0, 0), "XYZ", (1, 1, 1))
    assert down["position"]["z"] == -5.0


def test_zero_z_is_not_negative_zero():
    # A z=0 prop must serialise as "0.0", never "-0.0".
    t = prop_mapper.blender_to_wl_transform((0.0, 0.0, 0.0), (0, 0, 0), "XYZ", (1, 1, 1))
    assert repr(t["position"]["z"]) == "0.0"


def test_rotation_radians_to_degrees():
    t = prop_mapper.blender_to_wl_transform((0, 0, 0), (math.pi / 2, 0, math.pi), "XYZ", (1, 1, 1))
    assert _approx(t["rotation"]["pitch"], 90.0)
    assert _approx(t["rotation"]["yaw"], 0.0)
    assert _approx(t["rotation"]["roll"], 180.0)


def test_scale_passthrough_incl_negative():
    t = prop_mapper.blender_to_wl_transform((0, 0, 0), (0, 0, 0), "XYZ", (2.0, 0.5, -1.0))
    assert t["scale"] == {"x": 2.0, "y": 0.5, "z": -1.0}


def test_position_scale_factor():
    # world scale = 100 x Unit Scale (metres -> WL cm); default Unit Scale 1.0 -> factor 100,
    # applied to position only (all axes pass through, sign preserved).
    t = prop_mapper.blender_to_wl_transform((1.0, -2.0, 3.0), (0, 0, 0), "XYZ", (1, 1, 1), position_scale=100.0)
    assert t["position"] == {"x": 100.0, "y": -200.0, "z": 300.0}
    assert t["scale"] == {"x": 1, "y": 1, "z": 1}  # prop scale is unitless, untouched by world scale


def run():
    tests = [
        test_identity, test_key_order, test_translation,
        test_z_axis_passthrough, test_zero_z_is_not_negative_zero,
        test_rotation_radians_to_degrees, test_scale_passthrough_incl_negative,
        test_position_scale_factor,
    ]
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
