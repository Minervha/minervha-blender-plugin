"""wl_transform.py — the single locus of the Blender -> Wild Life coordinate convention.

The WHOLE axis / sign / handedness / rotator convention lives in one declarative spec
(`WL_BASIS`); position, rotation, scale and the geometry matrix all derive from it by
linear algebra. Calibration (the rig + corpus, see the coordinate-transform plan) edits
only `WL_BASIS`.

Why a single change of basis: Blender is right-handed, Z-up, metres; the game is
left-handed and (per in-game observation) Y-up, centimetres. A correct conversion is one
change of basis `B` applied consistently — position `B·p`, rotation `B·R·Bᵀ`, scale by the
axis permutation — never three ad-hoc fixes. Geometry travels a second path (the game's OBJ
importer, convention `C_obj`), so the matrix baked into the OBJ is *derived*:
`B_geom = C_objᵀ·B` (so `C_obj·B_geom = B`). The geometry mirror is therefore `det(B)`.

Pure: no `bpy`, no numpy — runs/tests outside Blender, like `mapper.py` / `prop_mapper.py`.
Blender's euler convention is reproduced exactly: `Euler(e, "XYZ").to_matrix() == Rz·Ry·Rx`
(verified against the live Blender 5.1).
"""

import math

# --- The convention. THEORY SEED — calibrated in-game via the rig (see plan chunk-04). ---
# B: rows = game (x,y,z), cols = Blender (x,y,z); B[g][b] = ±1 means game axis g = ±Blender axis b.
# Seed: game X = +Blender X, game Y(up) = +Blender Z, game Z(depth) = +Blender Y  ->  det(B) = -1
# (right-handed Blender -> left-handed game; matches the observed "+Y is up").
WL_BASIS = {
    "B": ((1, 0, 0),
          (0, 0, 1),
          (0, 1, 0)),
    # C_obj: the game's OBJ-importer convention (MEASURED; seed = identity, verts used as-is).
    # B_geom is DERIVED as C_objᵀ·B — never edited directly.
    "C_obj": ((1, 0, 0),
              (0, 1, 0),
              (0, 0, 1)),
    # Game euler rotator: which game axis each channel rotates about, the extraction order, per-channel sign.
    # Seed: yaw about the up axis (game Y), pitch about X, roll about Z. Pinned by the rig.
    "rotator_axis": {"yaw": "y", "pitch": "x", "roll": "z"},
    "rotator_order": "XYZ",
    "rotator_sign": {"yaw": 1, "pitch": 1, "roll": 1},
}

_IDENTITY3 = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2, "X": 0, "Y": 1, "Z": 2}


# --- tiny 3x3 linear algebra (no numpy) ---

def det3(M):
    a, b, c = M[0]
    d, e, f = M[1]
    g, h, i = M[2]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def transpose3(M):
    return tuple(tuple(M[r][c] for r in range(3)) for c in range(3))


def mat3_mul(A, B):
    return tuple(tuple(sum(A[r][k] * B[k][c] for k in range(3)) for c in range(3)) for r in range(3))


def mat3_vec(M, v):
    return tuple(sum(M[r][k] * v[k] for k in range(3)) for r in range(3))


def _axis_rot(axis, a):
    """Right-handed elementary rotation matrix (column-vector convention; matches mathutils)."""
    ca, sa = math.cos(a), math.sin(a)
    if axis in ("X", "x"):
        return ((1, 0, 0), (0, ca, -sa), (0, sa, ca))
    if axis in ("Y", "y"):
        return ((ca, 0, sa), (0, 1, 0), (-sa, 0, ca))
    return ((ca, -sa, 0), (sa, ca, 0), (0, 0, 1))


def euler_to_mat3(euler, order):
    """Blender euler (rad) + order -> rotation 3x3. Matches Blender: the first-listed axis is applied
    first (innermost / rightmost factor), e.g. order "XYZ" -> Rz·Ry·Rx."""
    ang = {"X": euler[0], "Y": euler[1], "Z": euler[2]}
    M = _IDENTITY3
    for axis in order:                 # left-to-right: first axis ends up rightmost (left-multiply each)
        M = mat3_mul(_axis_rot(axis, ang[axis]), M)
    return M


def mat3_to_euler(M, order):
    """Rotation 3x3 -> euler (rad) as (ex, ey, ez) by axis component, inverse of euler_to_mat3.

    Implemented for order "XYZ" (the seed rotator order). Closed form derived from M = Rz·Ry·Rx:
      M[2][0] = -sin(y);  M[2][1]/M[2][2] = cos(y)·(sin x, cos x);  M[1][0]/M[0][0] = cos(y)·(sin z, cos z).
    """
    if order != "XYZ":
        raise NotImplementedError("mat3_to_euler only implements 'XYZ' (seed rotator order); add the order "
                                  "alongside its calibrated value if the rig pins a different one.")
    cy = math.hypot(M[0][0], M[1][0])
    if cy > 1e-9:
        ex = math.atan2(M[2][1], M[2][2])
        ey = math.atan2(-M[2][0], cy)
        ez = math.atan2(M[1][0], M[0][0])
    else:                              # gimbal lock (cos y ~ 0): fold ex into ez
        ex = math.atan2(-M[1][2], M[1][1])
        ey = math.atan2(-M[2][0], cy)
        ez = 0.0
    return (ex, ey, ez)


# --- the conversion ---

def _clean(x):
    """Snap FP noise from the matrix path (1e-15) to a clean 6-dp value; +0.0 not -0.0."""
    return round(x, 6) + 0.0


def object_transform(location, rotation_euler, rotation_order, scale, basis=WL_BASIS, scale_factor=1.0):
    """Local Blender transform -> WL {position, rotation(deg), scale}, via one change of basis.

    position = scale_factor · (B·location)   (Blender up=+Z lands on the game up axis, sign per B)
    rotation = euler of (B·R·Bᵀ) in the game's rotator convention (channels/order/sign from `basis`)
    scale    = axis-permuted magnitudes (|B|·scale) — follows the up-axis change; signs irrelevant
    Keys are emitted in the game's order (x,y,z / pitch,yaw,roll) for golden stability.
    """
    B = basis["B"]
    # position
    px, py, pz = mat3_vec(B, location)
    position = {"x": px * scale_factor or 0.0, "y": py * scale_factor or 0.0, "z": pz * scale_factor or 0.0}
    # scale: |B| is a permutation matrix -> permutes the scale magnitudes
    absB = tuple(tuple(abs(v) for v in row) for row in B)
    sx, sy, sz = mat3_vec(absB, scale)
    out_scale = {"x": sx, "y": sy, "z": sz}
    # rotation: change of basis of the rotation matrix, then extract in the game convention
    R = euler_to_mat3(rotation_euler, rotation_order)
    R_game = mat3_mul(mat3_mul(B, R), transpose3(B))
    eul = mat3_to_euler(R_game, basis["rotator_order"])
    by_axis = {"x": eul[0], "y": eul[1], "z": eul[2]}
    ax, sg = basis["rotator_axis"], basis["rotator_sign"]
    rotation = {
        "pitch": _clean(math.degrees(by_axis[ax["pitch"]] * sg["pitch"])),
        "yaw": _clean(math.degrees(by_axis[ax["yaw"]] * sg["yaw"])),
        "roll": _clean(math.degrees(by_axis[ax["roll"]] * sg["roll"])),
    }
    return {"position": position, "rotation": rotation, "scale": out_scale}


def geom_matrix(basis=WL_BASIS):
    """The 4x4 (rotation/reflection only, no translation) to bake into the OBJ export.
    DERIVED: B_geom = C_objᵀ·B, so that C_obj·B_geom = B (geometry agrees with placement in world)."""
    Bg = mat3_mul(transpose3(basis["C_obj"]), basis["B"])
    return ((Bg[0][0], Bg[0][1], Bg[0][2], 0.0),
            (Bg[1][0], Bg[1][1], Bg[1][2], 0.0),
            (Bg[2][0], Bg[2][1], Bg[2][2], 0.0),
            (0.0, 0.0, 0.0, 1.0))


def geom_is_mirrored(basis=WL_BASIS):
    """True if the in-world geometry is mirrored -> obj_export must reverse face winding.
    Keyed on det(B) (== det(C_obj·B_geom)), NOT det(B_geom) in isolation."""
    return det3(basis["B"]) < 0
