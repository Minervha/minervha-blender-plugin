"""obj_export.py — write one Blender mesh datablock to an OBJ for the .wlsave bundle.

Geometry is exported in **object-local space** (so all instances of a datablock reuse
one file and the placement stays on the prop). bpy.ops.wm.obj_export has no
"don't apply object transform" option — it bakes the object's world matrix — so we
temporarily set matrix_world = Identity for the export and restore it after.

`make_obj_exporter` adapts this to the (mesh_key, dest_dir, used_basenames) -> basename
seam that wlsave_export.build_scene_wlsave expects (so that module stays Blender-free
and unit-testable; production injects this).

Needs bpy. The axis convention here is co-calibrated with
prop_mapper.blender_to_wl_transform (calibration item #2, chunk-06).
"""

import os

try:
    import bpy
    import mathutils
except ImportError:                # importable for syntax/lint without Blender
    bpy = None
    mathutils = None

try:
    from . import wlsave_export     # reuse the name-sanitiser
except ImportError:
    import wlsave_export

# Calibrated in-game (chunk-06): the ORIENTATION fix lives here (on the OBJ), not on the prop
# rotation. forward='Y'/up='Z' = Blender's native axes (no remap) so geometry stays Z-up like WL —
# the operator's default (NEGATIVE_Z/Y) was remapping to Y-up and laying meshes down.
FORWARD_AXIS = "Y"
UP_AXIS = "Z"
# Scale is NOT hardcoded here: it is driven by the scene's Unit Scale (one "world" scale), passed in as
# `global_scale` by the caller and applied UNIFORMLY to geometry here AND to prop positions in prop_mapper
# (never per-object). wm.obj_export does not honour scene unit scale itself (no use_scene_unit option), so
# the caller passes the factor explicitly.


def export_mesh_obj(src_object, dest_dir, used_basenames, write_mtl=True, global_scale=1.0):
    """Export `src_object`'s mesh to `dest_dir/<sanitized datablock name>.obj` in local
    space. Returns the .obj basename, or None on failure. Restores selection, the active
    object, and the object's world matrix even on error."""
    if bpy is None:
        return None
    base = wlsave_export._sanitize_basename((src_object.data.name or "mesh") + ".obj", "mesh")
    # dedup basename within the bundle
    stem, ext = os.path.splitext(base)
    candidate, i = base, 2
    while candidate in used_basenames:
        candidate = f"{stem}_{i}{ext}"
        i += 1
    base = candidate
    dest = os.path.join(dest_dir, base)

    view_layer = bpy.context.view_layer
    prev_active = view_layer.objects.active
    prev_selected = list(bpy.context.selected_objects)
    # Save the BASIS matrix (the object's own loc/rot/scale channels), not world — restoring
    # it returns the user's transform exactly, even for parented objects.
    saved_basis = src_object.matrix_basis.copy()
    # bpy.ops.object.* require OBJECT mode; bail out of Edit/Sculpt/etc. and restore after.
    prev_mode = None
    active = bpy.context.object
    if active is not None and active.mode != "OBJECT":
        prev_mode = active.mode
    try:
        if prev_mode is not None:
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        src_object.select_set(True)
        view_layer.objects.active = src_object
        # world == Identity -> the exporter writes mesh-local coords (it bakes the world matrix,
        # and has no "skip object transform" option). Flush the depsgraph so the eval sees it.
        src_object.matrix_world = mathutils.Matrix.Identity(4)
        view_layer.update()
        bpy.ops.wm.obj_export(
            filepath=dest,
            export_selected_objects=True,
            apply_modifiers=True,
            export_uv=True,
            export_normals=True,
            export_materials=write_mtl,
            forward_axis=FORWARD_AXIS,
            up_axis=UP_AXIS,
            global_scale=global_scale,
        )
    except Exception:
        # Broad on purpose: one bad mesh must not abort the whole scene export. The caller
        # (build_scene_wlsave) records the failed mesh in the report so it isn't silent.
        return None
    finally:
        src_object.matrix_basis = saved_basis
        view_layer.update()
        try:
            bpy.ops.object.select_all(action="DESELECT")
            for o in prev_selected:
                o.select_set(True)
            view_layer.objects.active = prev_active
            if prev_mode is not None and prev_active is not None:
                bpy.ops.object.mode_set(mode=prev_mode)
        except Exception:
            pass
    return base if os.path.isfile(dest) and os.path.getsize(dest) > 0 else None


def make_obj_exporter(mesh_object_map, write_mtl=True, global_scale=1.0):
    """Adapt export_mesh_obj to wlsave_export's (mesh_key, dest_dir, used) -> basename seam.

    `mesh_object_map` = {mesh datablock name -> a representative bpy object using it}
    (build via scene_introspect.build_mesh_object_map). `global_scale` = the world scale factor
    (1 / scene Unit Scale), applied to the geometry; the same factor scales prop positions."""
    def _exporter(mesh_key, dest_dir, used_basenames):
        obj = mesh_object_map.get(mesh_key)
        if obj is None:
            return None
        return export_mesh_obj(obj, dest_dir, used_basenames, write_mtl=write_mtl, global_scale=global_scale)
    return _exporter
