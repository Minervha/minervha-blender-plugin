"""Headless Blender probe for collection-instance expansion (scene_introspect).

Builds: an EXCLUDED collection "Chair" (mesh Seat + mesh Back parented to Seat, instance_offset
non-zero), a collection "Set" holding a mesh + an Empty instancing "Chair" (nested instancing),
then two instancer Empties in the scene (one instancing "Chair" rotated/scaled, one instancing
"Set"). Asserts:
  - every rendered instance (depsgraph.object_instances) has a matching expanded prop whose
    composed transform chain equals Blender's instance world matrix,
  - guids are unique, parents resolve, instanced meshes are offered to the OBJ exporter.

Run:  F:\\Software\\Blender\\5.1\\blender.exe --background --factory-startup --python tests/verify_collection_instances_live.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import bpy                      # noqa: E402
from mathutils import Matrix, Euler, Vector   # noqa: E402
import scene_introspect         # noqa: E402
import prop_mapper              # noqa: E402

FAILS = []


def check(c, m):
    if not c:
        FAILS.append(m)


def mesh(name, coll):
    me = bpy.data.meshes.new(name + "Mesh")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    o = bpy.data.objects.new(name, me)
    coll.objects.link(o)
    return o


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
master = scene.collection

chair = bpy.data.collections.new("Chair")
master.children.link(chair)
chair.instance_offset = (0.5, 0.0, 0.25)
seat = mesh("Seat", chair)
seat.location = (1.0, 2.0, 0.0)
seat.rotation_euler = Euler((0.0, 0.0, 0.3))
back = mesh("Back", chair)
back.parent = seat
back.location = (0.0, 0.5, 1.0)

setc = bpy.data.collections.new("Set")
master.children.link(setc)
table = mesh("Table", setc)
table.location = (3.0, 0.0, 0.0)
inner = bpy.data.objects.new("InnerChair", None)
inner.instance_type = "COLLECTION"
inner.instance_collection = chair
inner.location = (0.0, -2.0, 0.0)
inner.rotation_euler = Euler((0.0, 0.0, 1.0))
setc.objects.link(inner)

# Hide the source collections from the view layer (the usual "asset collection" setup).
for lc in bpy.context.view_layer.layer_collection.children:
    lc.exclude = True

e1 = bpy.data.objects.new("ChairInst", None)
e1.instance_type = "COLLECTION"
e1.instance_collection = chair
e1.location = (10.0, 0.0, 0.0)
e1.rotation_euler = Euler((0.2, 0.0, 0.7))
e1.scale = (2.0, 2.0, 2.0)
master.objects.link(e1)
e2 = bpy.data.objects.new("SetInst", None)
e2.instance_type = "COLLECTION"
e2.instance_collection = setc
e2.location = (0.0, 20.0, 1.0)
master.objects.link(e2)
bpy.context.view_layer.update()

objs = scene_introspect.exportable_objects("SCENE", list(scene.objects), None, True)
check({o.name for o in objs} == {"ChairInst", "SetInst"}, "exportable objs = the 2 instancers, got %s"
      % sorted(o.name for o in objs))
extra = scene_introspect.instanced_source_objects(objs)
check({o.name for o in extra} == {"Seat", "Back", "Table"}, "instanced sources %s" % sorted(o.name for o in extra))

for hier in (True, False):
    norms = scene_introspect.collect("SCENE", objs, collection_hierarchy=hier)
    keys = [n.get("guid_key") or n["name"] for n in norms]
    check(len(keys) == len(set(keys)), "unique guid sources (hier=%s)" % hier)
    by_key = dict(zip(keys, norms))
    for n in norms:
        p = n["parent_name"]
        check(p is None or p in by_key, "parent %r of %r resolves" % (p, n["name"]))

    def world(n):
        t = n["transform"]
        m = Matrix.LocRotScale(Vector(t["location"]), Euler(t["rotation_euler"], "XYZ"), Vector(t["scale"]))
        p = n["parent_name"]
        return world(by_key[p]) @ m if p else m

    meshes = [n for n in norms if n["kind"] == "mesh"]
    check(len(meshes) == 5, "5 expanded meshes (2 chair + set table + set chair x2), got %d" % len(meshes))
    dg = bpy.context.evaluated_depsgraph_get()
    inst_mats = [(i.object.original.name, i.matrix_world.copy()) for i in dg.object_instances
                 if i.is_instance and i.object.type == "MESH"]
    check(len(inst_mats) == 5, "depsgraph has 5 mesh instances, got %d" % len(inst_mats))
    for name, mw in inst_mats:
        cands = [world(n) for n in meshes if n["name"] == name]
        best = min((max(abs(a - b) for ra, rb in zip(c, mw) for a, b in zip(ra, rb)) for c in cands),
                   default=99)
        check(best < 1e-4, "instance %s world matrix matches (err %.6f, hier=%s)" % (name, best, hier))
    # prop_mapper accepts them and guids are unique
    props = [prop_mapper.map_object(n) for n in norms]
    guids = [p["guid"] for p in props]
    check(len(guids) == len(set(guids)), "unique prop guids")

if FAILS:
    print("FAIL:\n  " + "\n  ".join(FAILS))
    sys.exit(1)
print("OK collection instances")
