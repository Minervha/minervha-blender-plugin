"""End-to-end test for wlsave_export.build_scene_wlsave (pure — no bpy).

Feeds the NormalizedObject fixtures + minimal NormalizedMaterials and a FAKE
obj_exporter (writes stub .obj files), then asserts the ZIP carries Models/ +
Textures-free JSON whose props[] and customMaterials[] cross-reference by the exact
namespaced material name, instances share one Models/ file, and groups carry no mesh.

Run:  python tests/test_scene_build.py  (or pytest)
"""

import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import wlsave_export  # noqa: E402

NAME = "Scene"


def _fixtures():
    return json.load(open(os.path.join(HERE, "fixtures", "normalized_objects.json"), encoding="utf-8"))


def _material_norms(objs):
    """Minimal NormalizedMaterial[] for every material name used by the objects."""
    used = []
    for o in objs:
        for slot in (o.get("material_slots") or []):
            if slot and slot not in used:
                used.append(slot)
    return [{"name": n, "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1}, "textures": []} for n in used]


def _fake_obj_exporter(mesh_key, dest_dir, used_basenames):
    """Stand-in for obj_export: writes a stub .obj named after the mesh datablock."""
    basename = f"{mesh_key}.obj"
    with open(os.path.join(dest_dir, basename), "w", encoding="utf-8") as f:
        f.write(f"# stub obj for {mesh_key}\n")
    return basename


def _build(level=""):
    objs = _fixtures()
    norms = _material_norms(objs)
    tmp = tempfile.mkdtemp(prefix="scene_test_")
    dest = os.path.join(tmp, "out.wlsave")
    report = wlsave_export.build_scene_wlsave(
        norms, objs, NAME, dest, _fake_obj_exporter,
        skeleton_path=os.path.join(PKG, "skeleton.json"), level=level)
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        data = json.loads(z.read(f"{NAME}/{NAME}.json"))
    shutil.rmtree(tmp, ignore_errors=True)
    return objs, report, names, data


def test_zip_layout_and_models_dedup():
    objs, report, names, data = _build()
    assert f"{NAME}/{NAME}.json" in names
    # 4 unique mesh datablocks across the fixtures (CrateMesh + LinkMesh each appear twice).
    models = sorted(n for n in names if n.startswith(f"{NAME}/Models/"))
    assert models == [f"{NAME}/Models/CrateMesh.obj", f"{NAME}/Models/FloorMesh.obj",
                      f"{NAME}/Models/LampMesh.obj", f"{NAME}/Models/LinkMesh.obj"], models
    assert len(report["meshesWritten"]) == 4


def test_props_count_and_types():
    objs, report, names, data = _build()
    props = data["props"]
    assert len(props) == len(objs)
    groups = [p for p in props if p["iD"] == "Group"]
    meshes = [p for p in props if p["iD"] == "UserMesh"]
    assert len(groups) == 1 and len(meshes) == 6
    root = groups[0]
    assert root["label"] == "Root"
    assert root["stringSettings"] == {}          # group carries no mesh / materials


def test_instances_share_meshpath_distinct_guid():
    objs, report, names, data = _build()
    props = {p["label"]: p for p in data["props"]}
    crate, inst = props["Crate"], props["Crate.001"]
    assert crate["stringSettings"]["MeshPath"] == inst["stringSettings"]["MeshPath"] == f"{NAME}/Models/CrateMesh.obj"
    assert crate["guid"] != inst["guid"]


def test_material_cross_reference_exact():
    objs, report, names, data = _build()
    mat_names = {m["name"] for m in data["customMaterials"]}
    assert mat_names, "expected customMaterials"
    assert all(n.startswith(f"{NAME}/") for n in mat_names), mat_names
    # every non-empty CustomMaterialN on every prop must resolve to a real customMaterials name
    for p in data["props"]:
        for k, v in (p.get("stringSettings") or {}).items():
            if k.startswith("CustomMaterial") and v:
                assert v in mat_names, f"{p['label']}.{k}={v!r} not in customMaterials"


def test_report_counters():
    objs, report, names, data = _build()
    assert set(report["objectsExported"]) == {"Floor", "Crate", "Crate.001", "GlassLamp", "ChainParent", "ChainLeaf"}
    assert report["noUv"] == ["GlassLamp"]
    assert "GlassMat" in report["proceduralMaterials"]
    assert report["materialNamespaced"] is True


def test_level_default_is_collection():
    # No level given -> a portable collection (level "").
    objs, report, names, data = _build()
    assert data["level"] == ""
    assert report["level"] == ""


def test_level_map_target():
    # A fixed map name flows verbatim into the save's `level` field (and the report).
    objs, report, names, data = _build(level="Showroom")
    assert data["level"] == "Showroom"
    assert report["level"] == "Showroom"
    # Only the level field changes — the ZIP layout (Models/, props, customMaterials) is unchanged.
    assert f"{NAME}/{NAME}.json" in names
    assert len(data["props"]) == len(objs)


def test_materials_only_path_still_namespaces():
    # build_wlsave (materials-only) must keep working and now namespace names too.
    tmp = tempfile.mkdtemp(prefix="mat_test_")
    try:
        dest = os.path.join(tmp, "mats.wlsave")
        norms = [{"name": "Wood", "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1}, "textures": []}]
        wlsave_export.build_wlsave(norms, "Pack", dest, skeleton_path=os.path.join(PKG, "skeleton.json"))
        with zipfile.ZipFile(dest) as z:
            data = json.loads(z.read("Pack/Pack.json"))
        assert data["props"] == []
        assert data["customMaterials"][0]["name"] == "Pack/Wood"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"SCENE BUILD FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"SCENE BUILD OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
