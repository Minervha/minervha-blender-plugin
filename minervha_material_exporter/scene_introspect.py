"""scene_introspect.py — Blender scene -> NormalizedObject[] (scene-export input).

Reads the objects in scope and produces the plain-dict shape prop_mapper consumes:
mesh objects -> kind "mesh", empties -> kind "group", with local (parent-relative)
transforms, material-slot order, mesh-datablock dedup keys, hierarchy, and best-effort
validation flags for the report (UVs, procedural-texture materials, risky transforms).

Reads only — no mapping/coordinate logic (that is prop_mapper). Needs bpy. The
transform values feed prop_mapper.blender_to_wl_transform (calibration item #2).
"""

try:
    import bpy
    import mathutils
except ImportError:                 # importable for syntax check without Blender
    bpy = None
    mathutils = None

# Generator texture nodes whose output is procedural -> cannot be exported as an image;
# their materials are flagged so the report can tell the user to bake them manually.
_PROCEDURAL_NODE_TYPES = {
    "TEX_NOISE", "TEX_VORONOI", "TEX_MUSGRAVE", "TEX_WAVE",
    "TEX_MAGIC", "TEX_GRADIENT", "TEX_CHECKER", "TEX_BRICK",
}


def _tree_has_procedural(nt, seen):
    """Recurse a node tree (and its group sub-trees) for a procedural texture node."""
    if nt is None or nt.name in seen:   # Blender forbids recursive groups; seen-set is defensive
        return False
    seen.add(nt.name)
    for n in nt.nodes:
        if n.type in _PROCEDURAL_NODE_TYPES:
            return True
        if n.type == "GROUP" and _tree_has_procedural(getattr(n, "node_tree", None), seen):
            return True
    return False


def _has_procedural_textures(mat):
    """Best-effort: does `mat` use a procedural texture-generator node (at any group depth)?
    Such textures can't be exported — the user must bake them."""
    return _tree_has_procedural(getattr(mat, "node_tree", None), set())


def _risky_transform(scale, mirrored):
    """Report-only flag. `mirrored` (matrix determinant < 0) is detected on the source
    matrix because decompose() folds a negative scale into the rotation, so the
    decomposed `scale` is always non-negative and can't reveal a mirror by itself."""
    if mirrored:
        return "mirrored (negative scale) — apply scale before export for a faithful result"
    sx, sy, sz = scale
    if not (abs(sx - sy) < 1e-6 and abs(sy - sz) < 1e-6):
        return "non-uniform scale"
    return None


def collect_objects(scope, objects):
    """Ordered, de-duplicated MESH+EMPTY object list for the scope.

    `objects` is the list resolved by the UI (ui._objects_for_scope): selected objects,
    a collection's `all_objects`, or None for the whole scene."""
    if objects is None:
        if bpy is not None and scope == "SELECTED":
            objects = list(bpy.context.selected_objects)
        elif bpy is not None:
            objects = list(bpy.context.scene.objects)
        else:
            objects = []
    seen, out = set(), []
    for o in objects:
        if o.type in ("MESH", "EMPTY") and o.name not in seen:
            seen.add(o.name)
            out.append(o)
    return out


def _local_transform(obj, parent_in_scope):
    """Parent-relative transform for an in-scope-parented object, else world transform
    (a root gets its world placement). Decomposed from the matrix so it is robust to
    rotation_mode and Blender's matrix_parent_inverse. Returns (transform_dict, mirrored).

    The euler is intentionally normalized to XYZ order via the matrix -> quaternion ->
    euler path (independent of the object's rotation_mode); rotation_order is reported as
    "XYZ" to match. `mirrored` = matrix determinant < 0 (decompose() hides a negative
    scale by folding it into the rotation)."""
    mat = obj.matrix_local if parent_in_scope else obj.matrix_world
    loc, quat, scale = mat.decompose()
    eul = quat.to_euler("XYZ")
    transform = {
        "location": (loc.x, loc.y, loc.z),
        "rotation_euler": (eul.x, eul.y, eul.z),
        "rotation_order": "XYZ",
        "scale": (scale.x, scale.y, scale.z),
    }
    return transform, mat.determinant() < 0


def normalize_object(obj, in_scope_names):
    is_mesh = obj.type == "MESH"
    parent = obj.parent
    parent_in_scope = parent is not None and parent.name in in_scope_names
    parent_name = parent.name if parent_in_scope else None

    transform, mirrored = _local_transform(obj, parent_in_scope)

    material_slots = []
    procedural = []
    has_uv = False
    if is_mesh:
        material_slots = [(s.material.name if s.material else None) for s in obj.material_slots]
        has_uv = len(obj.data.uv_layers) > 0
        for s in obj.material_slots:
            if s.material and _has_procedural_textures(s.material) and s.material.name not in procedural:
                procedural.append(s.material.name)

    return {
        "name": obj.name,
        "kind": "mesh" if is_mesh else "group",
        "mesh_key": obj.data.name if is_mesh else None,
        "parent_name": parent_name,
        "child_index": 0,                     # assigned in collect()
        "visible": not obj.hide_render,
        "transform": transform,
        "material_slots": material_slots,
        "validation": {
            "has_uv": has_uv,
            "procedural_materials": procedural,
            "risky_transform": _risky_transform(transform["scale"], mirrored) if is_mesh else None,
        },
    }


def _assign_child_indices(norms):
    """Deterministic childIndex: per parent (None = roots), sort siblings by name."""
    by_parent = {}
    for n in norms:
        by_parent.setdefault(n["parent_name"], []).append(n)
    for siblings in by_parent.values():
        for i, n in enumerate(sorted(siblings, key=lambda x: x["name"])):
            n["child_index"] = i


def collect(scope="SCENE", objects=None):
    """Return NormalizedObject[] for the scope (UI passes the resolved object list)."""
    objs = collect_objects(scope, objects)
    in_scope = {o.name for o in objs}
    norms = [normalize_object(o, in_scope) for o in objs]
    _assign_child_indices(norms)
    return norms


def build_mesh_object_map(objects):
    """{mesh datablock name -> a representative bpy object using it} for obj_export.
    First in-scope object per datablock wins (instances share one OBJ)."""
    out = {}
    for o in collect_objects(None, objects):
        if o.type == "MESH" and o.data.name not in out:
            out[o.data.name] = o
    return out
