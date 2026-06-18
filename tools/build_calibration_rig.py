"""build_calibration_rig.py — build the coordinate-calibration rig and export it to a .wlsave.

A deliberately ASYMMETRIC + CHIRAL rig so one in-game screenshot reads off the whole
Blender -> Wild Life convention (see docs/.../coordinate-transform/RIG-READOFF.md):

  - AxisX : long pointed spike  (length 3) along Blender +X, red
  - AxisY : medium bar          (length 2) along Blender +Y, green
  - AxisZ : short fat stub       (length 1) along Blender +Z, blue
  - ChiralR : a flat letter "R" in the XY plane facing +Z  (mirror test)
  - OriginParent : empty at Blender (1,2,3) parenting the four  (placement test)
  - RotProbe : a second axis cluster under RotParent at (8,0,0), rotated +30° about Blender X
               (rotation channel/sign test — the DECISIVE check that C_obj·B_geom = B)

Run inside Blender (this is a dev tool, needs bpy):
  exec(open(r"...\\tools\\build_calibration_rig.py").read(), {"__name__": "__main__"})
or  blender --background <file>.blend --python tools/build_calibration_rig.py

Idempotent: removes a prior `CalibRig` collection + its `CalibRig_*` materials first. Leaves the
collection in the scene so you can inspect it; writes dist/CalibRig.wlsave.
"""

import math
import os
import sys

import bpy

# Import the DEV exporter modules (top-level; their relative imports fall back to absolute on sys.path).
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.join(os.path.dirname(_HERE), "minervha_material_exporter")
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)
import introspect          # noqa: E402
import scene_introspect    # noqa: E402
import obj_export          # noqa: E402
import wlsave_export       # noqa: E402

OUT_PATH = os.path.join(os.path.dirname(_HERE), "dist", "CalibRig.wlsave")
COLL = "CalibRig"


_MESH_PREFIXES = ("AxisX", "AxisY", "AxisZ", "ChiralR")


def _clear_previous():
    coll = bpy.data.collections.get(COLL)
    if coll:
        for o in list(coll.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(coll)
    for m in list(bpy.data.materials):
        if m.name.startswith("CalibRig_"):
            bpy.data.materials.remove(m)
    # purge orphan mesh/curve datablocks from a prior run so names don't accrue .001 suffixes.
    for me in list(bpy.data.meshes):
        if me.users == 0 and me.name.startswith(_MESH_PREFIXES):
            bpy.data.meshes.remove(me)
    for cu in list(bpy.data.curves):
        if cu.users == 0 and cu.name.startswith("CalibRig_R"):
            bpy.data.curves.remove(cu)


def _mat(name, rgba):
    m = bpy.data.materials.new("CalibRig_" + name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
    return m


def _mesh_obj(coll, name, verts, faces, mat):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    obj = bpy.data.objects.new(name, me)
    obj.data.materials.append(mat)
    coll.objects.link(obj)
    return obj


def _box(x0, y0, z0, x1, y1, z1):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return v, f


def _spike_x(length, w):
    # square base at x=0, apex at +x -> unmistakable direction.
    v = [(0, -w, -w), (0, w, -w), (0, w, w), (0, -w, w), (length, 0, 0)]
    f = [(0, 1, 2, 3), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    return v, f


def _axis_cluster(coll, suffix, mats):
    sx, sy, sz = mats
    a = _mesh_obj(coll, "AxisX" + suffix, *_spike_x(3.0, 0.15), sx)            # long pointed spike +X
    bv, bf = _box(-0.12, 0.0, -0.12, 0.12, 2.0, 0.12)
    b = _mesh_obj(coll, "AxisY" + suffix, bv, bf, sy)                          # medium bar +Y
    cv, cf = _box(-0.22, -0.22, 0.0, 0.22, 0.22, 1.0)
    c = _mesh_obj(coll, "AxisZ" + suffix, cv, cf, sz)                          # short fat stub +Z
    return [a, b, c]


def _chiral_r(coll, mat):
    cu = bpy.data.curves.new("CalibRig_R", 'FONT')
    cu.body = "R"
    cu.extrude = 0.06
    cu.size = 1.2
    tmp = bpy.data.objects.new("CalibRig_R_tmp", cu)
    coll.objects.link(tmp)
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    me.name = "ChiralR"
    bpy.data.objects.remove(tmp, do_unlink=True)
    obj = bpy.data.objects.new("ChiralR", me)
    obj.data.materials.append(mat)
    obj.location = (-1.6, -1.8, 0.0)        # off to a corner, flat in XY, facing +Z
    coll.objects.link(obj)
    return obj


def build_rig():
    _clear_previous()
    coll = bpy.data.collections.new(COLL)
    bpy.context.scene.collection.children.link(coll)

    red = _mat("Red", (0.8, 0.05, 0.05, 1))
    green = _mat("Green", (0.05, 0.7, 0.05, 1))
    blue = _mat("Blue", (0.05, 0.2, 0.85, 1))
    grey = _mat("Grey", (0.6, 0.6, 0.6, 1))
    mats = (red, green, blue)

    # --- main cluster under an empty at (1,2,3) ---
    origin = bpy.data.objects.new("OriginParent", None)
    origin.empty_display_size = 0.5
    origin.location = (1.0, 2.0, 3.0)
    coll.objects.link(origin)
    main = _axis_cluster(coll, "", mats) + [_chiral_r(coll, grey)]
    for o in main:
        o.parent = origin                  # data-API parent -> matrix_parent_inverse stays identity

    # --- rotation probe: a second cluster, rotated +30° about Blender X, off at (8,0,0) ---
    rot = bpy.data.objects.new("RotParent", None)
    rot.empty_display_size = 0.5
    rot.location = (8.0, 0.0, 0.0)
    rot.rotation_euler = (math.radians(30.0), 0.0, 0.0)
    coll.objects.link(rot)
    for o in _axis_cluster(coll, "_Probe", mats):
        o.parent = rot

    bpy.context.view_layer.update()
    return coll


def export_rig(coll):
    objs = list(coll.objects)
    norms = introspect.collect('COLLECTION', objs)
    norm_objects = scene_introspect.collect('COLLECTION', objs)
    unit = bpy.context.scene.unit_settings.scale_length or 1.0
    world_scale = 100.0 * unit
    exporter = obj_export.make_obj_exporter(scene_introspect.build_mesh_object_map(objs),
                                            global_scale=world_scale)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    report = wlsave_export.build_scene_wlsave(norms, norm_objects, COLL, OUT_PATH, exporter,
                                              position_scale=world_scale, level="")
    return report


def main():
    coll = build_rig()
    report = export_rig(coll)
    print("CalibRig exported -> %s" % OUT_PATH)
    print("  objects: %s" % report.get("objectsExported"))
    print("  meshes : %s" % report.get("meshesWritten"))
    print("  failed : %s" % report.get("meshExportFailed"))
    return report


if __name__ == "__main__":
    main()
