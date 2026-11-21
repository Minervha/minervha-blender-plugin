"""Headless Blender probe: Geometry Nodes / particle / vertex instancing is exported (scene_introspect).

Scene: a GN object scattering a hidden "Tree" cube on a 3x3 grid (Instance on Points), a particle
emitter instancing a hidden "Rock" (hair, render as object), and a 4-vertex mesh instancing its child
"Post" on its vertices (instance_type VERTS). Asserts that every mesh instance Blender renders has an
exported prop whose composed transform equals the instance's world matrix, that the VERTS template
child gets no prop of its own, that sources are offered to the OBJ exporter, and that the instance
limit skips everything (reported) instead of shipping a partial scatter.

Run:  F:\\Software\\Blender\\5.1\\blender.exe --background --factory-startup --python tests/verify_depsgraph_instances_live.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "minervha_material_exporter"))

import bpy                                  # noqa: E402
from mathutils import Euler, Matrix, Vector  # noqa: E402
import scene_introspect                     # noqa: E402

FAILS = []


def check(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)


bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
assets = bpy.data.collections.new("Assets")
sc.collection.children.link(assets)

bpy.ops.mesh.primitive_cube_add(size=0.4)
tree = bpy.context.active_object; tree.name = "Tree"
bpy.ops.mesh.primitive_ico_sphere_add(radius=0.2)
rock = bpy.context.active_object; rock.name = "Rock"
for o in (tree, rock):
    for c in list(o.users_collection):
        c.objects.unlink(o)
    assets.objects.link(o)
bpy.context.view_layer.layer_collection.children["Assets"].exclude = True

# Geometry Nodes scatter
bpy.ops.mesh.primitive_plane_add(size=2, location=(5, 0, 0))
gn = bpy.context.active_object; gn.name = "Scatter"
gn.rotation_euler = Euler((0, 0, 0.5))
ng = bpy.data.node_groups.new("ScatterGN", "GeometryNodeTree")
ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
grid = ng.nodes.new("GeometryNodeMeshGrid")
grid.inputs["Vertices X"].default_value = 3; grid.inputs["Vertices Y"].default_value = 3
iop = ng.nodes.new("GeometryNodeInstanceOnPoints")
oi = ng.nodes.new("GeometryNodeObjectInfo"); oi.inputs["Object"].default_value = tree
ng.links.new(grid.outputs["Mesh"], iop.inputs["Points"])
ng.links.new(oi.outputs["Geometry"], iop.inputs["Instance"])
ng.links.new(iop.outputs["Instances"], go.inputs[0])
mod = gn.modifiers.new("GN", "NODES"); mod.node_group = ng

# Particle emitter (hair, rendered as object)
bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 5, 0))
em = bpy.context.active_object; em.name = "Emitter"
em.modifiers.new("PS", "PARTICLE_SYSTEM")
ps = em.particle_systems[0].settings
ps.type = "HAIR"; ps.count = 5; ps.render_type = "OBJECT"; ps.instance_object = rock
ps.use_advanced_hair = True; ps.particle_size = 1.0
em.show_instancer_for_render = False

# Vertex instancing
me = bpy.data.meshes.new("Square")
me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [])
sq = bpy.data.objects.new("Square", me); sq.location = (-5, 0, 0)
sc.collection.objects.link(sq)
bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=1.0)
post = bpy.context.active_object; post.name = "Post"
post.parent = sq
sq.instance_type = "VERTS"
bpy.context.view_layer.update()

objs = scene_introspect.exportable_objects("SCENE", list(sc.objects), None, True)
extra_objs = scene_introspect.instanced_source_objects(objs)
extra = {o.name for o in extra_objs}
geo = [o for o in extra_objs if o.get(scene_introspect.TEMP_GEO_PROP)]
check("Rock" in extra and len(geo) == 1,
      "sources offered to export: Rock + ONE geometry copy for the 9 GN instances (%s)" % sorted(extra))
import tempfile                              # noqa: E402
import obj_export                           # noqa: E402
tmpd = tempfile.mkdtemp(prefix="geo_inst_")
base = obj_export.write_obj_direct(geo[0], tmpd, set()) if geo else None
nf = sum(1 for l in open(os.path.join(tmpd, base)) if l.startswith("f ")) if base else 0
check(nf == 6, "geometry-instance copy writes its OBJ (cube: %d faces)" % nf)

norms = scene_introspect.collect("SCENE", objs, collection_hierarchy=True)
stats = dict(scene_introspect.LAST_INSTANCE_STATS)
keys = [n.get("guid_key") or n["name"] for n in norms]
by_key = dict(zip(keys, norms))
check(len(keys) == len(set(keys)), "unique guid sources")


def world(n):
    t = n["transform"]
    m = Matrix.LocRotScale(Vector(t["location"]), Euler(t["rotation_euler"], "XYZ"), Vector(t["scale"]))
    p = n["parent_name"]
    return world(by_key[p]) @ m if p else m


dg = bpy.context.evaluated_depsgraph_get()
# Keyed by INSTANCER: a GN geometry instance has no source object of its own (it ships as a copy
# named "<instancer>_instanceN"), so match each rendered instance among its instancer's props.
want = [(i.parent.original.name, i.matrix_world.copy()) for i in dg.object_instances
        if i.is_instance and i.object.type == "MESH"]
check(len(want) == 9 + 5 + 4, "Blender renders 18 mesh instances (got %d)" % len(want))
check(stats.get("instances") == len(want), "18 instances exported (stats %s)" % stats)
used = set()
bad = 0
for name, mw in want:
    best, bk = 99, None
    for k, n in by_key.items():
        if n.get("instanced_from") == name and k not in used:
            e = max(abs(a - b) for ra, rb in zip(world(n), mw) for a, b in zip(ra, rb))
            if e < best:
                best, bk = e, k
    used.add(bk)
    bad += best > 1e-4
check(bad == 0, "every instance placed at Blender's world matrix (%d off)" % bad)
check(not any(n["name"] == "Post" and not n.get("instanced_from") for n in norms),
      "VERTS template child 'Post' not exported as a prop of its own")
emn = [n for n in norms if n["name"] == "Emitter"]
check(emn and emn[0]["visible"] is False, "emitter hidden (Blender does not render it)")

n_obj = len(bpy.data.objects)
scene_introspect.cleanup_instance_cache()
check(len(bpy.data.objects) == n_obj - 1 and not any(o.get(scene_introspect.TEMP_GEO_PROP)
                                                     for o in bpy.data.objects),
      "cleanup removes the temporary geometry copy")
norms2 = [scene_introspect.normalize_object(o, {x.name for x in objs}) for o in objs]
s2 = scene_introspect.expand_depsgraph_instances(norms2, objs, limit=3)
check(s2["instancesSkipped"] == 18 and s2["instances"] == 0
      and not any(n.get("instanced_from") for n in norms2), "over the limit: none exported, all reported")

if FAILS:
    print("DEPSGRAPH INSTANCES: FAIL (%d)" % len(FAILS))
    sys.exit(1)
print("DEPSGRAPH INSTANCES: OK")
