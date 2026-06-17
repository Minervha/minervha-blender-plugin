"""prop_mapper.py — NormalizedObject -> Wild Life prop dict (UserMesh | Group).

A scene object (the shape produced by scene_introspect.py) -> one `props[]` entry
the game reads: a custom static mesh (`iD:"UserMesh"`, geometry referenced via
stringSettings.MeshPath, materials via CustomMaterial0..N) or an organizational
node (`iD:"Group"`, Blender empty/collection equivalent).

Schema is certified from real saves — see docs/wl-prop-schema.md. Field/casing is
exact (the game silently ignores a wrong-case key): `Color` capital, `emission`
lowercase, `"Texture Tiling"` / `"Material Type"` with spaces.

No bpy import: pure data, runs/tests outside Blender. tests/test_prop_mapper.py is
a regression snapshot (golden via tests/_gen_golden_props.py), like mapper.py.
"""

import hashlib
import math

# --- Calibration item #1 (chunk-06, RESOLVED in-game): Blender material slot i ->
# CustomMaterial{i+OFFSET}. CustomMaterial0 is the prop's built-in/base slot — present in-game
# but NOT usable for a custom material override (confirmed), so the mesh's real materials start
# at CustomMaterial1. Hence OFFSET = 1 and CustomMaterial0 is emitted empty.
OFFSET = 1

# --- Calibration item #2 (chunk-06): Blender -> WL transform convention.
#  * Scale: position is multiplied by `position_scale` (the world scale = 1 / scene Unit Scale),
#    passed in by the caller so geometry (obj_export global_scale) and positions share one factor.
#  * Rotation: per-axis euler->degrees (pitch=rotX, yaw=rotY, roll=rotZ). The exact Blender-axis ->
#    WL pitch/yaw/roll permutation + signs is STILL being calibrated in-game (kept on the prop by
#    decision); a controlled single-axis test fixes it. Changing it affects test_transform + golden.
_ROOT_GUID = "0" * 32


def make_guid(stable_key):
    """Deterministic 32-char uppercase hex guid for a stable key.

    The key is the Blender object name, which Blender guarantees unique within a
    .blend — so re-exporting the same scene reproduces identical guids (the golden
    stays stable). No randomness/uuid/time (those would break the golden)."""
    return hashlib.md5(str(stable_key).encode("utf-8")).hexdigest().upper()


def root_guid():
    """Sentinel parent guid for a root prop (32 zeros) — certified, never ''/'None'."""
    return _ROOT_GUID


def blender_to_wl_transform(location, rotation_euler, rotation_order, scale, position_scale=1.0):
    """Local Blender transform -> WL {position, rotation(deg), scale}.

    location=(x,y,z) metres, rotation_euler=(rx,ry,rz) radians, rotation_order e.g. "XYZ",
    scale=(sx,sy,sz), position_scale = world scale factor (1 / scene Unit Scale). Keys are emitted
    in the game's order (x,y,z / pitch,yaw,roll) for golden stability. This is the SINGLE locus of
    the axis/sign convention (calibration #2); `rotation_order` is threaded for the order-aware
    conversion the calibration will install."""
    lx, ly, lz = location
    rx, ry, rz = rotation_euler
    sx, sy, sz = scale
    return {
        "position": {"x": lx * position_scale, "y": ly * position_scale, "z": lz * position_scale},
        "rotation": {"pitch": math.degrees(rx), "yaw": math.degrees(ry), "roll": math.degrees(rz)},
        "scale": {"x": sx, "y": sy, "z": sz},
    }


def _event(event_id):
    return {"eventId": event_id, "keyboardShortcut": ""}


# customEvents blocks taken verbatim from real saves (UserMesh = 4 inEvents; Group
# adds setVisibilityBelow, whose eventId is lowercase). chunk-06 double-checks these
# against a freshly-created prop in the current game version.
def _usermesh_events():
    return {"inEvents": {
        "setVisibility": _event("SetVisibility"),
        "setCanReceiveEvents": _event("SetCanReceiveEvents"),
        "setCanDispatchEvents": _event("SetCanDispatchEvents"),
        "setOptionValue": _event("SetOptionValue"),
    }}


def _group_events():
    return {"inEvents": {
        "setVisibility": _event("SetVisibility"),
        "setCanReceiveEvents": _event("SetCanReceiveEvents"),
        "setCanDispatchEvents": _event("SetCanDispatchEvents"),
        "setOptionValue": _event("SetOptionValue"),
        "setVisibilityBelow": _event("setVisibilityBelow"),
    }}


def _common(norm, transform):
    """Fields shared by UserMesh and Group, in canonical key order."""
    parent = norm.get("parent_name")
    return {
        "label": norm.get("name"),
        "labelColor": {"r": 0, "g": 0, "b": 0, "a": 0},
        "position": transform["position"],
        "rotation": transform["rotation"],
        "scale": transform["scale"],
        "guid": make_guid(norm.get("name")),
        "parent": make_guid(parent) if parent else _ROOT_GUID,
        "childIndex": int(norm.get("child_index") or 0),
        "attachment": "None",
        "bIsVisible": bool(norm.get("visible", True)),
        "bIsCompletelyLocked": False,
        "bCanReceiveEvents": True,
        "bCanDispatchEvents": True,
    }


def _custom_materials(material_slots, material_names):
    """slot i -> CustomMaterial{i+OFFSET}. CustomMaterial0..OFFSET-1 are the game's dead/base
    slots (unusable in-game) -> emitted empty, so the prop matches real saves and the mesh's
    real materials start at the first usable index."""
    names = material_names or {}
    out = {}
    for j in range(OFFSET):
        out["CustomMaterial%d" % j] = ""
    for i, slot in enumerate(material_slots or []):
        out["CustomMaterial%d" % (i + OFFSET)] = names.get(slot, "") if slot else ""
    return out


def _map_usermesh(norm, transform, mesh_path, material_names):
    p = dict(_common(norm, transform))
    p["iD"] = "UserMesh"
    p["bIsInteractable"] = False
    p["bIsFoldedOut"] = False
    p["floatSettings"] = {"Specular": 0.05, "Roughness": 1.0, "Metallic": 0.0,
                          "Texture Tiling": 1.0, "Mass": 1000}
    p["intSettings"] = {"Material Type": 0}
    p["colorSettings"] = {"Color": {"r": 0.25, "g": 0.25, "b": 0.25, "a": 1},
                          "emission": {"r": 0, "g": 0, "b": 0, "a": 1}}
    p["boolSettings"] = {"EnableCollision": False, "UseTriplanarMapping": False,
                         "SimulatePhysics": False, "ShowIcon": True}
    p["vectorSettings"] = {}
    string_settings = {"Texture Override URL": "", "MeshPath": mesh_path or ""}
    string_settings.update(_custom_materials(norm.get("material_slots"), material_names))
    p["stringSettings"] = string_settings
    p["customEvents"] = _usermesh_events()
    return p


def _map_group(norm, transform):
    p = dict(_common(norm, transform))
    p["iD"] = "Group"
    p["bIsInteractable"] = True
    p["bIsFoldedOut"] = False
    p["boolSettings"] = {"ShowIcon": True}
    p["floatSettings"] = {}
    p["intSettings"] = {}
    p["colorSettings"] = {}
    p["stringSettings"] = {}
    p["vectorSettings"] = {}
    p["customEvents"] = _group_events()
    return p


def map_object(norm, mesh_path=None, material_names=None, position_scale=1.0):
    """NormalizedObject -> WL prop dict.

    mesh_path: full relative MeshPath "<Name>/Models/<file>.obj" for a mesh object
      (built by wlsave_export after OBJ export); ignored for groups.
    material_names: {blender_mat_name -> namespaced customMaterials name
      "<Collection>/<Mat>"}; the POST-sanitisation/dedup names. Unknown/empty slot -> "".
    position_scale: world scale factor (1 / scene Unit Scale) applied to the prop position.
    """
    t = norm.get("transform") or {}
    transform = blender_to_wl_transform(
        t.get("location", (0, 0, 0)),
        t.get("rotation_euler", (0, 0, 0)),
        t.get("rotation_order", "XYZ"),
        t.get("scale", (1, 1, 1)),
        position_scale=position_scale,
    )
    if norm.get("kind") == "group":
        return _map_group(norm, transform)
    return _map_usermesh(norm, transform, mesh_path, material_names)
