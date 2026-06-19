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
    import numpy as np
except ImportError:                # numpy ships with Blender; absent only in pure-Python lint
    np = None

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


# Direct OBJ writer (default ON): writes the .obj/.mtl from the evaluated mesh data instead of
# calling bpy.ops.wm.obj_export per mesh. The operator pays heavy per-call overhead on a big scene
# (a select_all(DESELECT) over every object ≈ 41 ms, plus a whole-scene view_layer.update ×2) that
# dwarfs the tiny meshes here — direct writing drops the geometry phase from minutes to seconds.
# Geometry is validated equivalent to the operator (vertex positions within ~0.005 mm, identical UVs,
# matching normal values, same material-section order). make_obj_exporter falls back to the operator
# on any failure, so the validated path is always available.
USE_DIRECT_OBJ = True


def format_obj_text(arrays, mtl_name=None):
    """Build OBJ text from the arrays read by `_read_obj_arrays`. Pure (no bpy/numpy required —
    sequences of tuples work), so it is unit-testable. `vt`/`vn` are emitted per LOOP (no dedup —
    still valid OBJ, and WL is agnostic); each face references its vt/vn by global loop index. Per
    face the winding is reversed when `mirrored` (a reflected geometry basis), matching the operator
    path's `reverse_obj_winding`; normals are then absent (a reflected vn points inward → WL
    recomputes from the corrected winding)."""
    verts = arrays["verts"]; uvs = arrays.get("uvs"); normals = arrays.get("normals")
    lvi = arrays["loop_verts"]; ls = arrays["loop_start"]; lt = arrays["loop_total"]
    mi = arrays["mat_index"]; slots = arrays["slots"]; mirrored = arrays["mirrored"]
    out = []
    if mtl_name:
        out.append("mtllib " + mtl_name)
    out.extend("v %.6f %.6f %.6f" % (v[0], v[1], v[2]) for v in verts)
    if uvs is not None:
        out.extend("vt %.6f %.6f" % (u[0], u[1]) for u in uvs)
    if normals is not None:
        out.extend("vn %.6f %.6f %.6f" % (n[0], n[1], n[2]) for n in normals)
    has_uv = uvs is not None
    has_n = normals is not None
    cur = None
    for f in range(len(ls)):
        m = int(mi[f])
        if m != cur:
            cur = m
            out.append("usemtl " + (slots[m] if 0 <= m < len(slots) and slots[m] else "None"))
        s = int(ls[f]); c = int(lt[f])
        seq = range(s + c - 1, s - 1, -1) if mirrored else range(s, s + c)
        refs = []
        for loop in seq:
            vi = int(lvi[loop]) + 1
            if has_uv and has_n:
                refs.append("%d/%d/%d" % (vi, loop + 1, loop + 1))
            elif has_uv:
                refs.append("%d/%d" % (vi, loop + 1))
            elif has_n:
                refs.append("%d//%d" % (vi, loop + 1))
            else:
                refs.append("%d" % vi)
        out.append("f " + " ".join(refs))
    return "\n".join(out) + "\n"


def _read_obj_arrays(obj, global_scale):
    """Read `obj`'s EVALUATED mesh (modifiers applied) into plain arrays for `format_obj_text`,
    pre-transformed to WL space: vertices and loop normals through `wl_transform.geom_matrix()`
    (the same change of basis the operator bakes via matrix_world) and scaled by `global_scale`.
    Normals are dropped when the basis is mirrored (parity with the operator). Returns None for an
    empty mesh or without bpy/numpy. Fast: a few `foreach_get` calls (~0.2 ms for a small mesh)."""
    if bpy is None or np is None:
        return None
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        npoly = len(me.polygons)
        nv = len(me.vertices)
        if npoly == 0 or nv == 0:
            return None
        M = np.array(wl_transform.geom_matrix(), dtype=np.float64)[:3, :3]
        mirrored = wl_transform.geom_is_mirrored()
        co = np.empty(nv * 3, dtype=np.float64); me.vertices.foreach_get("co", co)
        verts = (co.reshape(nv, 3) @ M.T) * global_scale
        nl = len(me.loops)
        loop_verts = np.empty(nl, dtype=np.int64); me.loops.foreach_get("vertex_index", loop_verts)
        uvs = None
        uvlayer = me.uv_layers.active
        if uvlayer is not None:
            u = np.empty(nl * 2, dtype=np.float64); uvlayer.data.foreach_get("uv", u)
            uvs = u.reshape(nl, 2)
        normals = None
        if not mirrored:
            n = np.empty(nl * 3, dtype=np.float64)
            try:
                me.corner_normals.foreach_get("vector", n)   # split/custom loop normals (4.1+)
            except Exception:
                me.loops.foreach_get("normal", n)
            normals = n.reshape(nl, 3) @ M.T
        ls = np.empty(npoly, dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
        lt = np.empty(npoly, dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
        mat_index = np.empty(npoly, dtype=np.int64); me.polygons.foreach_get("material_index", mat_index)
        slots = [(s.material.name if s.material else "") for s in obj.material_slots]
        return {"verts": verts, "loop_verts": loop_verts, "uvs": uvs, "normals": normals,
                "loop_start": ls, "loop_total": lt, "mat_index": mat_index,
                "slots": slots, "mirrored": mirrored}
    finally:
        ev.to_mesh_clear()


def write_obj_direct(src_object, dest_dir, used_basenames, write_mtl=True, global_scale=1.0):
    """Write `src_object`'s mesh to `dest_dir/<datablock>.obj` (+ sibling .mtl) directly from the
    evaluated mesh — no `wm.obj_export`, no selection/transform/depsgraph churn (so it is also
    side-effect-free on the scene). Returns the .obj basename, or None on any failure so the caller
    falls back to the validated operator path. The .mtl is built then run through the SAME
    `reorder_mtl_blocks` the operator path uses, so the material-section order WL maps to
    CustomMaterial{i} is identical."""
    if bpy is None or np is None:
        return None
    try:
        base = wlsave_export._sanitize_basename((src_object.data.name or "mesh") + ".obj", "mesh")
        stem, ext = os.path.splitext(base)
        candidate, i = base, 2
        while candidate in used_basenames:           # bundle-unique basename (caller adds it after)
            candidate = "%s_%d%s" % (stem, i, ext)
            i += 1
        base = candidate
        dest = os.path.join(dest_dir, base)
        arrays = _read_obj_arrays(src_object, global_scale)
        if arrays is None:
            return None
        mtl_name = (os.path.splitext(base)[0] + ".mtl") if write_mtl else None
        obj_text = format_obj_text(arrays, mtl_name)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(obj_text)
        if write_mtl:
            # newmtl for every slot (like wm.obj_export), then reorder to usemtl/slot order.
            names = sorted(dict.fromkeys((n or "None") for n in arrays["slots"]))
            if names:
                raw = "".join("newmtl %s\nKd 0.800000 0.800000 0.800000\n" % n for n in names)
                with open(os.path.join(dest_dir, mtl_name), "w", encoding="utf-8") as f:
                    f.write(reorder_mtl_blocks(obj_text, raw))
        return base if os.path.isfile(dest) and os.path.getsize(dest) > 0 else None
    except Exception:
        return None


def make_obj_exporter(mesh_object_map, write_mtl=True, global_scale=1.0):
    """Adapt export_mesh_obj to wlsave_export's (mesh_key, dest_dir, used) -> basename seam.

    `mesh_object_map` = {mesh datablock name -> a representative bpy object using it}
    (build via scene_introspect.build_mesh_object_map). `global_scale` = the world scale factor
    (1 / scene Unit Scale), applied to the geometry; the same factor scales prop positions.

    Uses the fast direct writer (`write_obj_direct`) when `USE_DIRECT_OBJ`, falling back to the
    `wm.obj_export` operator path on any failure (so the validated exporter is always available)."""
    def _exporter(mesh_key, dest_dir, used_basenames):
        obj = mesh_object_map.get(mesh_key)
        if obj is None:
            return None
        if USE_DIRECT_OBJ:
            base = write_obj_direct(obj, dest_dir, used_basenames, write_mtl=write_mtl, global_scale=global_scale)
            if base:
                return base                          # else: fall back to the operator below
        return export_mesh_obj(obj, dest_dir, used_basenames, write_mtl=write_mtl, global_scale=global_scale)
    return _exporter
