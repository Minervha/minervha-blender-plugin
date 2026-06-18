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

try:
    from . import wl_transform      # the single coordinate-convention locus
except ImportError:
    import wl_transform

# Orientation is NO LONGER hardcoded here. The geometry matrix baked into the OBJ is
# wl_transform.geom_matrix() (= B_geom = C_objᵀ·B, the OBJ-import-aware change of basis), and the
# operator's own forward/up are left at their identity ('Y'/'Z') so wl_transform stays the single locus.
# A reflective basis (geom_is_mirrored) means the in-world geometry is mirrored -> reverse face winding
# and skip writing normals (the reflected vn would point inward; WL recomputes from the corrected winding).
_OBJ_FORWARD = "Y"   # operator identity — MUST stay 'Y'/'Z' (a non-identity value composes with B_geom)
_OBJ_UP = "Z"
# Scale is NOT hardcoded here: the caller passes one "world" scale as `global_scale` (= 100 x scene Unit
# Scale, the Blender-metres -> WL-centimetres factor) and it is applied UNIFORMLY to geometry here AND to
# prop positions in prop_mapper (never per-object). wm.obj_export does not honour scene unit scale itself
# (no use_scene_unit option), so the caller passes the factor explicitly.


def reverse_obj_winding(obj_text):
    """Reverse the vertex order of every `f ` face line (keeping each v/vt/vn triplet intact), so a
    mirrored (det < 0) bake keeps faces CCW / outward. Non-face lines are untouched. Pure text."""
    out = []
    for line in obj_text.splitlines(keepends=True):
        if line.startswith("f "):
            body = line.rstrip("\n")
            nl = line[len(body):]
            verts = body.split()[1:]
            out.append("f " + " ".join(reversed(verts)) + nl)
        else:
            out.append(line)
    return "".join(out)


def _reverse_winding_file(obj_path):
    """Rewrite an OBJ in place with reversed face winding. Best-effort (an IO error must not fail export)."""
    try:
        with open(obj_path, encoding="utf-8") as f:
            text = f.read()
        new = reverse_obj_winding(text)
        if new != text:
            with open(obj_path, "w", encoding="utf-8") as f:
                f.write(new)
    except Exception:
        pass


def _usemtl_order(obj_text):
    """Material names in first-`usemtl`-appearance order in an OBJ. For wm.obj_export this
    equals the Blender material-slot order. Deduplicated, order-preserving."""
    order = []
    for line in obj_text.splitlines():
        if line.startswith("usemtl "):
            name = line[7:].strip()
            if name and name not in order:
                order.append(name)
    return order


def reorder_mtl_blocks(obj_text, mtl_text):
    """Rewrite an .mtl so its `newmtl` blocks follow the .obj's `usemtl` (= slot) order.

    wm.obj_export writes `usemtl` in Blender slot order but `newmtl` ALPHABETICALLY. Wild
    Life indexes a mesh's material sections by the .mtl's `newmtl` order and overrides them
    with the prop's CustomMaterial{i} (also slot order), so an alphabetical .mtl SWAPS the
    materials on any multi-material mesh whose slot order isn't already alphabetical (the
    reported wood<->fabric bug). Reordering the .mtl to the .obj's order realigns the two.
    Materials not referenced by any `usemtl` keep their original relative order, appended last.
    """
    header, blocks, order, cur = [], {}, [], None
    for line in mtl_text.splitlines(keepends=True):
        if line.startswith("newmtl "):
            cur = line[7:].strip()
            blocks[cur] = [line]
            order.append(cur)
        elif cur is None:
            header.append(line)
        else:
            blocks[cur].append(line)

    want = _usemtl_order(obj_text)
    seq = [n for n in want if n in blocks] + [n for n in order if n not in want]
    if seq == order:
        return mtl_text                       # already aligned -> leave the file untouched
    out = list(header)
    for n in seq:
        out.extend(blocks[n])
    return "".join(out)


def _reorder_sidecar_mtl(obj_path):
    """Reorder the .obj's sibling .mtl in place so material order matches usemtl/slot order.
    Best-effort: a parse/IO failure must not fail the export (the OBJ is already written)."""
    mtl_path = os.path.splitext(obj_path)[0] + ".mtl"
    if not os.path.isfile(mtl_path):
        return
    try:
        with open(obj_path, encoding="utf-8") as f:
            obj_text = f.read()
        with open(mtl_path, encoding="utf-8") as f:
            mtl_text = f.read()
        new_text = reorder_mtl_blocks(obj_text, mtl_text)
        if new_text != mtl_text:
            with open(mtl_path, "w", encoding="utf-8") as f:
                f.write(new_text)
    except Exception:
        pass


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
        # The exporter bakes the world matrix (no "skip object transform" option), so set world =
        # geom_matrix() = B_geom: the geometry change of basis, in mesh-LOCAL space (no translation —
        # placement stays on the prop). Flush the depsgraph so the eval sees it.
        mirrored = wl_transform.geom_is_mirrored()
        src_object.matrix_world = mathutils.Matrix(wl_transform.geom_matrix())
        view_layer.update()
        bpy.ops.wm.obj_export(
            filepath=dest,
            export_selected_objects=True,
            apply_modifiers=True,
            export_uv=True,
            export_normals=not mirrored,   # a reflected vn points inward; let WL recompute from winding
            export_materials=write_mtl,
            forward_axis=_OBJ_FORWARD,     # identity — orientation lives entirely in geom_matrix()
            up_axis=_OBJ_UP,
            global_scale=global_scale,
        )
        if mirrored:
            # det(B) < 0 -> the bake mirrored the geometry; restore CCW/outward faces.
            _reverse_winding_file(dest)
        if write_mtl:
            # wm.obj_export writes the .mtl alphabetically; realign it to slot order so
            # WL maps each section to the right CustomMaterial (see reorder_mtl_blocks).
            _reorder_sidecar_mtl(dest)
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
