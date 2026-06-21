"""Unit tests for wl_transform — the single-locus Blender -> Wild Life coordinate convention.

Pure Python (no bpy). Run:  python tests/test_wl_transform.py  (or pytest)

Pins the math (change of basis B·R·Bᵀ, position B·p, scale permutation, derived B_geom) and the
THEORY-SEED values in WL_BASIS. The seed/anchor assertions are deliberately calibration-sensitive:
chunk-04 edits WL_BASIS and these expectations together as a reviewed diff.
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import wl_transform as T  # noqa: E402

ID_BASIS = {
    "B": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "C_obj": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "rotator_axis": {"yaw": "z", "pitch": "y", "roll": "x"},
    "rotator_order": "XYZ",
    "rotator_sign": {"yaw": 1, "pitch": 1, "roll": 1},
}


def _approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def _mat_close(A, B, tol=1e-6):
    return all(abs(A[r][c] - B[r][c]) <= tol for r in range(3) for c in range(3))


def test_euler_matches_blender():
    # Captured live from Blender 5.1: Euler((0.1,0.2,0.3),'XYZ').to_matrix().
    ref = ((0.93629336, -0.27509585, 0.21835066),
           (0.28962949, 0.95642507, -0.03695701),
           (-0.19866933, 0.09784339, 0.97517031))
    assert _mat_close(T.euler_to_mat3((0.1, 0.2, 0.3), "XYZ"), ref)


def test_euler_roundtrip():
    for e in [(0.1, 0.2, 0.3), (0.5, -0.3, 1.0), (-1.2, 0.4, -0.7), (0.0, 0.0, 0.0)]:
        got = T.mat3_to_euler(T.euler_to_mat3(e, "XYZ"), "XYZ")
        assert all(_approx(got[i], e[i]) for i in range(3)), (e, got)


def test_identity_basis_is_passthrough():
    t = T.object_transform((1, 2, 3), (0, 0, 0), "XYZ", (1, 1, 1), basis=ID_BASIS)
    assert t["position"] == {"x": 1, "y": 2, "z": 3}
    assert t["rotation"] == {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
    assert t["scale"] == {"x": 1, "y": 1, "z": 1}


def test_scale_factor_on_position_only():
    t = T.object_transform((1, -2, 3), (0, 0, 0), "XYZ", (1, 1, 1), basis=ID_BASIS, scale_factor=100)
    assert t["position"] == {"x": 100, "y": -200, "z": 300}
    assert t["scale"] == {"x": 1, "y": 1, "z": 1}
    assert t["rotation"] == {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}


def test_seed_basis_negates_y():
    # Corpus convention: Z stays up (Z->Z), Blender Y is negated (handedness flip), X passes through.
    assert T.object_transform((0, 0, 5), (0, 0, 0), "XYZ", (1, 1, 1))["position"] == {"x": 0, "y": 0, "z": 5}
    assert T.object_transform((0, 7, 0), (0, 0, 0), "XYZ", (1, 1, 1))["position"] == {"x": 0, "y": -7, "z": 0}
    assert T.object_transform((3, 0, 0), (0, 0, 0), "XYZ", (1, 1, 1))["position"] == {"x": 3, "y": 0, "z": 0}


def test_scale_unaffected_by_sign_flip():
    # |B| = identity for negate-Y, so scale magnitudes pass straight through.
    assert T.object_transform((0, 0, 0), (0, 0, 0), "XYZ", (2, 3, 9))["scale"] == {"x": 2, "y": 3, "z": 9}


def test_det_and_orthonormality():
    B = T.WL_BASIS["B"]
    identity = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert T.det3(B) == -1                       # right-handed Blender -> left-handed game
    assert _mat_close(T.mat3_mul(B, T.transpose3(B)), identity)


def test_single_axis_rotation_conjugation():
    # A +90° Blender-Z rotation, conjugated by the seed B (negate-Y, det -1), is a -90° rotation about
    # game up (Z) — the negate-Y flips the yaw sign.
    B = T.WL_BASIS["B"]
    R = T.euler_to_mat3((0, 0, math.pi / 2), "XYZ")
    R_game = T.mat3_mul(T.mat3_mul(B, R), T.transpose3(B))
    assert _mat_close(R_game, T.euler_to_mat3((0, 0, -math.pi / 2), "XYZ"))
    assert _approx(T.det3(R_game), 1.0)         # conjugation of a proper rotation stays proper


def test_seed_rotation_about_blender_z():
    # End-to-end: a +90° Blender-Z rotation -> game yaw -90 about the up axis (Z), pitch/roll 0.
    t = T.object_transform((0, 0, 0), (0, 0, math.pi / 2), "XYZ", (1, 1, 1))
    assert t["rotation"] == {"pitch": 0.0, "yaw": -90.0, "roll": 0.0}


def test_seed_rotation_about_blender_x():
    # +90° Blender-X -> game roll +90 (rotator_sign roll = -1, PINNED in-game via the CalibLeaf rig:
    # Unreal FRotator roll = atan2(-M21, M22), opposite to this module's bare XYZ-euler extraction).
    t = T.object_transform((0, 0, 0), (math.pi / 2, 0, 0), "XYZ", (1, 1, 1))
    assert t["rotation"] == {"pitch": 0.0, "yaw": 0.0, "roll": 90.0}


def test_seed_rotation_about_blender_y():
    # +90° Blender-Y -> game pitch -90 (rotator_sign pitch = -1, PINNED via the rig: Unreal FRotator
    # pitch = asin(M20), opposite sign to this module's bare extraction). Seed +1 mirrored roll/pitch
    # and exploded any non-yaw-rotated scene.
    t = T.object_transform((0, 0, 0), (0, math.pi / 2, 0), "XYZ", (1, 1, 1))
    assert t["rotation"] == {"pitch": -90.0, "yaw": 0.0, "roll": 0.0}


def test_geom_constraint():
    Bg4 = T.geom_matrix()
    Bg = tuple(tuple(Bg4[r][c] for c in range(3)) for r in range(3))
    assert T.mat3_mul(T.WL_BASIS["C_obj"], Bg) == T.WL_BASIS["B"]     # C_obj · B_geom == B (rotated props ok)
    assert Bg == ((1, 0, 0), (0, -1, 0), (0, 0, -1))                   # the known-good (x,-y,-z) geometry
    assert T.det3(Bg) == 1                                             # proper rotation -> exporter handles normals
    assert T.geom_is_mirrored() is False                              # so no winding reversal / normal skip
    assert Bg4[0][3] == 0.0 and Bg4[3][3] == 1.0                       # no translation in the bake


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"WL TRANSFORM FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"WL TRANSFORM OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
