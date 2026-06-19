"""Tests for prop_mapper.map_object (+ golden regression).

Targeted assertions pin the certified prop schema (see docs/wl-prop-schema.md):
exact key casing, hardcoded defaults, per-slot material linkage, deterministic
guids, root sentinel, and the UserMesh/Group customEvents blocks. The golden
(tests/golden/expected_props.json) is a self-snapshot of this mapper, regenerated
via tests/_gen_golden_props.py — it catches any accidental drift.

Pure Python (no bpy). Run:  python tests/test_prop_mapper.py  (or pytest)
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import prop_mapper  # noqa: E402

HEX32 = re.compile(r"^[0-9A-F]{32}$")


def _mesh_obj(name="Cube", parent=None, slots=("MatA", "MatB"), visible=True):
    return {
        "name": name, "kind": "mesh", "mesh_key": "CubeMesh",
        "parent_name": parent, "child_index": 0, "visible": visible,
        "transform": {"location": (0, 0, 0), "rotation_euler": (0, 0, 0),
                      "rotation_order": "XYZ", "scale": (1, 1, 1)},
        "material_slots": list(slots),
        "validation": {"has_uv": True, "procedural_materials": [], "risky_transform": None},
    }


def _group(name="Empty", parent=None, visible=True):
    return {
        "name": name, "kind": "group", "mesh_key": None,
        "parent_name": parent, "child_index": 0, "visible": visible,
        "transform": {"location": (0, 0, 0), "rotation_euler": (0, 0, 0),
                      "rotation_order": "XYZ", "scale": (1, 1, 1)},
        "material_slots": [], "validation": {"has_uv": False, "procedural_materials": [], "risky_transform": None},
    }


MAT_NAMES = {"MatA": "Scene/MatA", "MatB": "Scene/MatB"}


def test_usermesh_core_fields():
    p = prop_mapper.map_object(_mesh_obj(), mesh_path="Scene/Models/CubeMesh.obj", material_names=MAT_NAMES)
    assert p["iD"] == "UserMesh"
    assert p["label"] == "Cube"
    assert HEX32.match(p["guid"]), p["guid"]
    assert p["guid"] == prop_mapper.make_guid("Cube")
    assert p["attachment"] == "None"
    assert p["bIsInteractable"] is False
    assert p["stringSettings"]["MeshPath"] == "Scene/Models/CubeMesh.obj"


def test_usermesh_material_linkage_and_offset():
    p = prop_mapper.map_object(_mesh_obj(slots=("MatA", "MatB")),
                               mesh_path="Scene/Models/CubeMesh.obj", material_names=MAT_NAMES)
    ss = p["stringSettings"]
    assert ss["CustomMaterial%d" % (0 + prop_mapper.OFFSET)] == "Scene/MatA"
    assert ss["CustomMaterial%d" % (1 + prop_mapper.OFFSET)] == "Scene/MatB"
    assert ss["Texture Override URL"] == ""


def test_usermesh_empty_slot_is_blank():
    p = prop_mapper.map_object(_mesh_obj(slots=("MatA", None)),
                               mesh_path="Scene/Models/CubeMesh.obj", material_names=MAT_NAMES)
    assert p["stringSettings"]["CustomMaterial%d" % (1 + prop_mapper.OFFSET)] == ""


def test_usermesh_exact_casing_and_defaults():
    p = prop_mapper.map_object(_mesh_obj(), mesh_path="Scene/Models/CubeMesh.obj", material_names=MAT_NAMES)
    # colorSettings: capital Color, lowercase emission (certified casing).
    assert "Color" in p["colorSettings"] and "emission" in p["colorSettings"]
    assert "Emission" not in p["colorSettings"]
    # floatSettings / intSettings keys with spaces.
    assert p["floatSettings"]["Texture Tiling"] == 1.0
    assert p["floatSettings"]["Mass"] == 1000
    assert p["intSettings"]["Material Type"] == 0
    # deliberate UV override of the game default (true).
    assert p["boolSettings"]["UseTriplanarMapping"] is False
    # optional pack fields omitted.
    assert "bIsPacked" not in p and "packedFlags" not in p


def test_root_vs_parented_guid():
    root = prop_mapper.map_object(_mesh_obj(name="Root", parent=None),
                                  mesh_path="Scene/Models/CubeMesh.obj", material_names=MAT_NAMES)
    assert root["parent"] == prop_mapper.root_guid() == "0" * 32
    child = prop_mapper.map_object(_mesh_obj(name="Child", parent="Root"),
                                   mesh_path="Scene/Models/CubeMesh.obj", material_names=MAT_NAMES)
    assert child["parent"] == prop_mapper.make_guid("Root")


def test_bisvisible_per_object():
    vis = prop_mapper.map_object(_mesh_obj(visible=True), mesh_path="x", material_names=MAT_NAMES)
    hid = prop_mapper.map_object(_mesh_obj(visible=False), mesh_path="x", material_names=MAT_NAMES)
    assert vis["bIsVisible"] is True and hid["bIsVisible"] is False


def test_enable_collision_toggle():
    off = prop_mapper.map_object(_mesh_obj(), mesh_path="x", material_names=MAT_NAMES)
    on = prop_mapper.map_object(_mesh_obj(), mesh_path="x", material_names=MAT_NAMES, enable_collision=True)
    assert off["boolSettings"]["EnableCollision"] is False          # default unchanged (golden stable)
    assert on["boolSettings"]["EnableCollision"] is True
    # only the one flag flips — the rest of boolSettings is untouched.
    assert on["boolSettings"]["UseTriplanarMapping"] is False
    assert on["boolSettings"]["SimulatePhysics"] is False


def test_master_group_shape_and_guid():
    mg = prop_mapper.master_group("MySave")
    assert mg["iD"] == "Group"
    assert mg["label"] == "MySave"
    assert mg["parent"] == prop_mapper.root_guid()
    assert mg["position"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert mg["rotation"] == {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
    assert mg["scale"] == {"x": 1.0, "y": 1.0, "z": 1.0}
    assert HEX32.match(mg["guid"]), mg["guid"]
    # Group customEvents (5 inEvents incl. setVisibilityBelow); no mesh/materials.
    assert "setVisibilityBelow" in mg["customEvents"]["inEvents"]
    assert mg["stringSettings"] == {}
    # guid is reserved-key-derived, so it never collides with an object sharing the save name.
    assert mg["guid"] != prop_mapper.make_guid("MySave")
    assert prop_mapper.master_group("A")["guid"] != prop_mapper.master_group("B")["guid"]


def test_group_shape():
    g = prop_mapper.map_object(_group(name="Empty", parent=None), mesh_path=None, material_names={})
    assert g["iD"] == "Group"
    assert g["boolSettings"] == {"ShowIcon": True}
    assert g["stringSettings"] == {}          # no MeshPath, no materials
    assert "MeshPath" not in g["stringSettings"]
    # Group customEvents has the extra setVisibilityBelow (5 inEvents); UserMesh has 4.
    assert "setVisibilityBelow" in g["customEvents"]["inEvents"]


def test_usermesh_events_has_no_visibility_below():
    p = prop_mapper.map_object(_mesh_obj(), mesh_path="x", material_names=MAT_NAMES)
    keys = p["customEvents"]["inEvents"]
    assert set(keys) == {"setVisibility", "setCanReceiveEvents", "setCanDispatchEvents", "setOptionValue"}


def test_guid_deterministic_and_unique():
    assert prop_mapper.make_guid("A") == prop_mapper.make_guid("A")
    assert prop_mapper.make_guid("A") != prop_mapper.make_guid("B")
    assert HEX32.match(prop_mapper.make_guid("anything"))


def test_golden_matches():
    fx = os.path.join(HERE, "fixtures", "normalized_objects.json")
    gd = os.path.join(HERE, "golden", "expected_props.json")
    if not (os.path.isfile(fx) and os.path.isfile(gd)):
        return  # golden not generated yet (first TDD pass); _gen_golden_props.py creates it
    import _gen_golden_props  # noqa: E402
    got = _gen_golden_props.build()
    exp = json.load(open(gd, encoding="utf-8"))
    assert got == exp, "prop_mapper output drifted from golden — regenerate with tests/_gen_golden_props.py and review the diff"


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"PROP MAPPER FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"PROP MAPPER OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
