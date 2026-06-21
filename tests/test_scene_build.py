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


def _build(level="", **kwargs):
    objs = _fixtures()
    norms = _material_norms(objs)
    tmp = tempfile.mkdtemp(prefix="scene_test_")
    dest = os.path.join(tmp, "out.wlsave")
    report = wlsave_export.build_scene_wlsave(
        norms, objs, NAME, dest, _fake_obj_exporter,
        skeleton_path=os.path.join(PKG, "skeleton.json"), level=level, **kwargs)
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


import struct  # noqa: E402
import zlib  # noqa: E402

import prop_mapper  # noqa: E402


def _tiny_png(w=2, h=2):
    """A minimal valid RGBA PNG (no bpy) for thumbnail tests."""
    def chunk(typ, data):
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))  # filter byte + red row
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def test_master_group_wraps_every_root():
    base_objs, base_report, _, base_data = _build()
    objs, report, names, data = _build(master_group=True)
    props = data["props"]
    # exactly one extra prop (the synthetic master group).
    assert len(props) == len(base_data["props"]) + 1
    root = prop_mapper.root_guid()
    roots = [p for p in props if p["parent"] == root]
    # after wrapping, the master group is the ONLY prop hanging off the scene root.
    assert len(roots) == 1
    mg = roots[0]
    assert mg["iD"] == "Group"
    assert mg["label"] == NAME
    assert mg["guid"] == prop_mapper.master_group(NAME)["guid"]
    assert report["masterGroup"] == NAME
    # every other prop is now parented to the master group (or to its own non-root parent).
    assert all(p["parent"] != root for p in props if p["guid"] != mg["guid"])


def test_master_group_off_by_default():
    objs, report, names, data = _build()
    assert report["masterGroup"] is None
    # without the toggle, at least one prop sits at the scene root (the fixture's "Root" group).
    root = prop_mapper.root_guid()
    assert any(p["parent"] == root for p in data["props"])


def test_enable_collision_propagates_to_all_usermesh():
    objs, report, names, data = _build(enable_collision=True)
    meshes = [p for p in data["props"] if p["iD"] == "UserMesh"]
    assert meshes and all(p["boolSettings"]["EnableCollision"] is True for p in meshes)
    assert report["enableCollision"] is True
    # default stays off.
    _, _, _, data_off = _build()
    assert all(p["boolSettings"]["EnableCollision"] is False
               for p in data_off["props"] if p["iD"] == "UserMesh")


def test_thumbnail_bundled_as_first_png_and_flagged():
    tmp = tempfile.mkdtemp(prefix="scene_thumb_")
    try:
        png = os.path.join(tmp, "thumb.png")
        with open(png, "wb") as f:
            f.write(_tiny_png())
        objs, report, names, data = _build(thumbnail=png)
        assert f"{NAME}/{NAME}.png" in names
        assert data["bHasDedicatedIcon"] is True
        assert report["thumbnail"] is True
        # icon precedes any Textures/ entry (Studio reader takes the first .png).
        icon_i = names.index(f"{NAME}/{NAME}.png")
        tex_is = [i for i, n in enumerate(names) if n.startswith(f"{NAME}/Textures/")]
        assert all(icon_i < i for i in tex_is)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_thumbnail_leaves_flag_false():
    objs, report, names, data = _build()
    assert data["bHasDedicatedIcon"] is False
    assert report["thumbnail"] is False
    assert not any(n.endswith(".png") for n in names)


_PHASES = {"bake", "textures", "map", "read", "meshes", "props", "zip"}


def test_iter_progress_events_wellformed_and_clean_cancel():
    objs = _fixtures()
    norms = _material_norms(objs)
    tmp = tempfile.mkdtemp(prefix="scene_iter_")
    try:
        dest = os.path.join(tmp, "out.wlsave")
        gen = wlsave_export._iter_build_scene_wlsave(
            norms, objs, NAME, dest, _fake_obj_exporter,
            skeleton_path=os.path.join(PKG, "skeleton.json"))
        events = []
        for _ in range(2):                     # advance a few steps, then cancel mid-run
            try:
                events.append(next(gen))
            except StopIteration:
                break
        gen.close()                            # cancel — must not raise (GeneratorExit handled by finally)
        assert not os.path.exists(dest), "a cancelled export must not leave a .wlsave"
        for ev in events:
            phase, done, total = ev
            assert phase in _PHASES, phase
            assert 0 <= done <= total
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_iter_full_run_matches_wrapper():
    objs = _fixtures()
    norms = _material_norms(objs)
    tmp = tempfile.mkdtemp(prefix="scene_iter2_")
    try:
        gen = wlsave_export._iter_build_scene_wlsave(
            norms, objs, NAME, os.path.join(tmp, "a.wlsave"), _fake_obj_exporter,
            skeleton_path=os.path.join(PKG, "skeleton.json"))
        events, rep_gen = [], None
        try:
            while True:
                events.append(next(gen))
        except StopIteration as e:
            rep_gen = e.value
        rep_wrap = wlsave_export.build_scene_wlsave(
            norms, objs, NAME, os.path.join(tmp, "b.wlsave"), _fake_obj_exporter,
            skeleton_path=os.path.join(PKG, "skeleton.json"))
        phases = [p for (p, d, t) in events]
        # the mesh long-pole reached its total (4 unique datablocks) and the final zip phase fired.
        assert ("meshes", 4, 4) in events, events
        assert "zip" in phases
        # generator (drained) and wrapper produce the same logical report.
        for k in ("created", "meshesWritten", "objectsExported", "noUv", "masterGroup"):
            assert rep_gen[k] == rep_wrap[k], k
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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


def test_collection_group_parent_resolves_end_to_end():
    # A collection Group (namespaced guid_key) + a mesh re-homed under it: the whole prop tree
    # must resolve, and the group's guid must come from the key, not its label.
    key = "\x00minervha-collection\x00Furniture"
    ident = {"location": [0, 0, 0], "rotation_euler": [0, 0, 0], "rotation_order": "XYZ", "scale": [1, 1, 1]}
    objs = [
        {"name": "Furniture", "kind": "group", "mesh_key": None, "guid_key": key,
         "parent_name": None, "child_index": 0, "visible": True, "transform": ident,
         "material_slots": [], "validation": {"has_uv": False, "procedural_materials": [], "risky_transform": None}},
        {"name": "Chair", "kind": "mesh", "mesh_key": "ChairMesh", "parent_name": key,
         "child_index": 0, "visible": True,
         "transform": {**ident, "location": [1, 0, 0]}, "material_slots": ["WoodMat"],
         "validation": {"has_uv": True, "procedural_materials": [], "risky_transform": None}},
    ]
    norms = _material_norms(objs)
    tmp = tempfile.mkdtemp(prefix="scene_coll_")
    try:
        dest = os.path.join(tmp, "out.wlsave")
        wlsave_export.build_scene_wlsave(norms, objs, NAME, dest, _fake_obj_exporter,
                                         skeleton_path=os.path.join(PKG, "skeleton.json"))
        with zipfile.ZipFile(dest) as z:
            data = json.loads(z.read(f"{NAME}/{NAME}.json"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    props = {p["label"]: p for p in data["props"]}
    grp, chair = props["Furniture"], props["Chair"]
    assert grp["iD"] == "Group"
    assert grp["guid"] == prop_mapper.make_guid(key)           # guid from key, not label
    assert grp["guid"] != prop_mapper.make_guid("Furniture")
    assert chair["parent"] == grp["guid"]                      # child resolves to the group
    guids = {p["guid"] for p in data["props"]}
    root = prop_mapper.root_guid()
    assert all(p["parent"] == root or p["parent"] in guids for p in data["props"])


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
