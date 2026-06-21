"""Headless Blender probe for the collection-hierarchy bpy readers.

Builds a synthetic scene (nested collections + a view-layer-EXCLUDED collection + an
object-parented empty), then exercises scene_introspect.collect / exportable_objects with
collection_hierarchy=True and asserts the resulting prop tree matches the outliner.

Run:  F:\\Blender\\5.1\\blender.exe --background --python tests/verify_collection_hierarchy_live.py
Exits non-zero on any failure (so it can gate a release).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import bpy                       # noqa: E402
import scene_introspect          # noqa: E402

KEY = scene_introspect._collection_guid_key
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def _mesh(name):
    return bpy.data.objects.new(name, bpy.data.meshes.new(name + "Mesh"))


def _build_scene():
    """Empty file, then:
        Master(scene root)
          Furniture/                      (collection)
            Chairs/  -> ChairA            (sub-collection + mesh)
            Table                         (mesh directly in Furniture)
          Hidden/  -> Ghost               (EXCLUDED collection + mesh)
          Rig (empty)  -> RigChild        (object-parented mesh, both in Master)
          FreeObj                         (mesh directly in Master)
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    master = scene.collection

    furniture = bpy.data.collections.new("Furniture")
    chairs = bpy.data.collections.new("Chairs")
    hidden = bpy.data.collections.new("Hidden")
    master.children.link(furniture)
    furniture.children.link(chairs)
    master.children.link(hidden)

    chairs.objects.link(_mesh("ChairA"))
    furniture.objects.link(_mesh("Table"))
    hidden.objects.link(_mesh("Ghost"))

    rig = bpy.data.objects.new("Rig", None)          # empty
    rigchild = _mesh("RigChild")
    rigchild.parent = rig
    master.objects.link(rig)
    master.objects.link(rigchild)
    master.objects.link(_mesh("FreeObj"))

    # Exclude "Hidden" from the active view layer (eye/screen stay; this is .exclude).
    vl = bpy.context.view_layer
    vl.layer_collection.children["Hidden"].exclude = True
    vl.update()
    return scene, furniture


def _by_name(norms):
    return {n["name"]: n for n in norms}


def test_scene_scope():
    scene, _ = _build_scene()
    objs = list(scene.objects)
    norms = scene_introspect.collect("FILE", objs, root_collection=None, collection_hierarchy=True)
    by = _by_name(norms)

    # Excluded collection + its object are gone; Master is the implicit (unemitted) root.
    check("Ghost" not in by, "excluded 'Ghost' should be dropped from props")
    check("Master" not in by, "scene master collection must not be emitted as a group")
    check("Hidden" not in by, "excluded 'Hidden' collection must not be emitted")

    # Emitted collection groups (kind group, identity, namespaced guid_key).
    for cname in ("Furniture", "Chairs"):
        g = by.get(cname)
        check(g is not None and g["kind"] == "group", f"'{cname}' should be an emitted Group")
        if g:
            check(g.get("guid_key") == KEY(cname), f"'{cname}' guid_key namespaced")
            check(g["transform"]["location"] == (0.0, 0.0, 0.0)
                  and g["transform"]["scale"] == (1.0, 1.0, 1.0), f"'{cname}' identity transform")

    # Nesting + re-homing.
    check(by["Furniture"]["parent_name"] is None, "Furniture is a top-level group (root)")
    check(by["Chairs"]["parent_name"] == KEY("Furniture"), "Chairs nested under Furniture")
    check(by["ChairA"]["parent_name"] == KEY("Chairs"), "ChairA re-homed under Chairs")
    check(by["Table"]["parent_name"] == KEY("Furniture"), "Table re-homed under Furniture")
    check(by["FreeObj"]["parent_name"] is None, "FreeObj stays at root (Master not emitted)")

    # Object parenting wins over collection: RigChild keeps its empty parent, NOT re-homed.
    check(by["RigChild"]["parent_name"] == "Rig", "RigChild keeps its object-parent 'Rig'")
    check(by["Rig"]["parent_name"] is None, "Rig (root-direct empty) stays at root")


def test_collection_scope_root_emitted():
    scene, furniture = _build_scene()
    objs = list(furniture.all_objects)
    norms = scene_introspect.collect("COLLECTION", objs, root_collection=furniture,
                                     collection_hierarchy=True)
    by = _by_name(norms)
    # The chosen collection IS the emitted root group.
    check("Furniture" in by and by["Furniture"]["parent_name"] is None,
          "COLLECTION scope: chosen collection is the root group")
    check(by["Chairs"]["parent_name"] == KEY("Furniture"), "Chairs nested under Furniture root")
    check(by["ChairA"]["parent_name"] == KEY("Chairs"), "ChairA under Chairs")
    check("Ghost" not in by, "Hidden/Ghost not part of the Furniture subtree")


def test_exportable_objects_drops_excluded():
    scene, _ = _build_scene()
    objs = list(scene.objects)
    kept = scene_introspect.exportable_objects("FILE", objs, None, collection_hierarchy=True)
    names = {o.name for o in kept}
    check("Ghost" not in names, "exportable_objects must drop the excluded 'Ghost'")
    check({"ChairA", "Table", "FreeObj", "RigChild", "Rig"} <= names,
          "exportable_objects keeps every non-excluded object")
    # Off -> unchanged (no exclusion filtering).
    same = scene_introspect.exportable_objects("FILE", objs, None, collection_hierarchy=False)
    check(same is objs, "hierarchy off: exportable_objects returns the list unchanged")


def main():
    for t in (test_scene_scope, test_collection_scope_root_emitted, test_exportable_objects_drops_excluded):
        try:
            t()
        except Exception as e:                       # noqa: BLE001
            FAILS.append(f"{t.__name__} raised: {e!r}")
    if FAILS:
        print("LIVE COLLECTION HIERARCHY FAILED:")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("LIVE COLLECTION HIERARCHY OK — scene + collection scopes + exclusion validated")


if __name__ == "__main__":
    main()
