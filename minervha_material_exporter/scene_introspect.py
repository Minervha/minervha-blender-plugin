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
# (TEX_MUSGRAVE removed in Blender 4.1 — folded into TEX_NOISE; no node carries that type.)
_PROCEDURAL_NODE_TYPES = {
    "TEX_NOISE", "TEX_VORONOI", "TEX_WAVE",
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
    """Deterministic childIndex: per parent (None = roots), sort siblings by name.

    Works on a mixed list (objects + collection Groups): an object re-homed under a collection
    and a sub-collection of that collection share the same parent_name (the collection's
    guid_key), so they are siblings under one deterministic name-sort."""
    by_parent = {}
    for n in norms:
        by_parent.setdefault(n["parent_name"], []).append(n)
    for siblings in by_parent.values():
        for i, n in enumerate(sorted(siblings, key=lambda x: x["name"])):
            n["child_index"] = i


# ── Collection hierarchy ────────────────────────────────────────────────────
# A Blender Collection -> an identity-transform Group prop, nested under its parent
# collection's Group; objects with no in-scope object-parent are re-homed under the Group of the
# collection that directly contains them. The guid is seeded from a namespaced key (mirrors
# prop_mapper.master_group) so a collection never collides with an object of the same name.
_COLLECTION_GUID_PREFIX = "\x00minervha-collection\x00"


def _collection_guid_key(name):
    return _COLLECTION_GUID_PREFIX + str(name)


def _identity_transform():
    return {"location": (0.0, 0.0, 0.0), "rotation_euler": (0.0, 0.0, 0.0),
            "rotation_order": "XYZ", "scale": (1.0, 1.0, 1.0)}


def build_collection_groups(object_norms, tree, emit_root_as_group):
    """Pure (bpy-free): collection `tree` + object norms -> (group_norms, reparent).

    `tree`: plain nested dict {"name", "objects": [obj names], "children": [tree, ...]}, already
    exclusion-filtered (excluded children omitted). `object_norms`: NormalizedObject[] whose
    parent_name is the in-scope object-parent or None. `emit_root_as_group`: emit the root
    collection itself as a Group (COLLECTION scope) vs treat it as the implicit root (SCENE/SELECTED).

    Returns:
      group_norms — NormalizedObject[] (kind "group", identity transform, `guid_key`, parent_name
        = parent kept-collection guid_key or None) for the collections kept after pruning.
      reparent — {obj_name -> new parent_name (a collection guid_key, or None for the implicit
        root's direct objects)} for the objects re-homed under a collection.

    An object is claimed by the FIRST collection (DFS pre-order, children name-sorted) that
    directly lists it, has it in scope, hasn't claimed it yet, and where it is a would-be-root
    (parent_name None — an object already parented to an in-scope object keeps that parent). A
    collection is kept iff it claims >=1 object or a kept descendant survives (empty branches pruned)."""
    in_scope = {n["name"] for n in object_norms}
    by_name = {n["name"]: n for n in object_norms}
    claimed = set()
    group_norms = []
    reparent = {}

    def walk(node, parent_key, is_root):
        key = _collection_guid_key(node.get("name"))
        emit_self = emit_root_as_group or not is_root
        home_key = key if emit_self else parent_key   # where this node's direct objects parent
        mine = []
        for obj_name in sorted(node.get("objects") or []):
            if obj_name in in_scope and obj_name not in claimed:
                n = by_name.get(obj_name)
                if n is not None and n.get("parent_name") is None:
                    claimed.add(obj_name)
                    mine.append(obj_name)
        kept_children = False
        for child in sorted(node.get("children") or [], key=lambda c: c.get("name") or ""):
            kept_children = walk(child, home_key, False) or kept_children
        if not (mine or kept_children):
            return False                              # prune: nothing exportable in this subtree
        for obj_name in mine:
            reparent[obj_name] = home_key
        if emit_self:
            group_norms.append({
                "name": node.get("name"), "kind": "group", "mesh_key": None,
                "guid_key": key, "parent_name": parent_key, "child_index": 0,
                "visible": True, "transform": _identity_transform(), "material_slots": [],
                "validation": {"has_uv": False, "procedural_materials": [], "risky_transform": None},
            })
        return True

    walk(tree, None, True)
    return group_norms, reparent


def objects_in_tree(tree):
    """Set of every object name anywhere in a (non-excluded) collection `tree` dict."""
    out = set(tree.get("objects") or [])
    for child in tree.get("children") or []:
        out |= objects_in_tree(child)
    return out


def _read_layer_tree(layer_coll):
    """LayerCollection -> plain tree dict, skipping view-layer-EXCLUDED children. The given root's
    own `.exclude` is ignored (an explicitly chosen root collection is always honored). Reads
    DIRECT `collection.objects` only (direct membership, not recursive)."""
    coll = layer_coll.collection
    node = {"name": coll.name, "objects": [o.name for o in coll.objects], "children": []}
    for child in layer_coll.children:
        if getattr(child, "exclude", False):
            continue
        node["children"].append(_read_layer_tree(child))
    return node


def _read_collection_tree(coll):
    """Collection -> plain tree dict with NO exclusion info (fallback when the chosen collection
    isn't present in the active view layer's LayerCollection tree)."""
    return {"name": coll.name, "objects": [o.name for o in coll.objects],
            "children": [_read_collection_tree(c) for c in coll.children]}


def _find_layer_collection(layer_coll, target):
    """Depth-first search for the LayerCollection wrapping `target` collection, or None."""
    if layer_coll.collection == target:
        return layer_coll
    for child in layer_coll.children:
        found = _find_layer_collection(child, target)
        if found is not None:
            return found
    return None


def _collection_tree(scope, root_collection):
    """Plain collection tree for the scope (bpy). `root_collection` (COLLECTION scope) is the
    emitted root, honored even if excluded; else the scene master collection (SCENE/SELECTED) is
    the implicit root."""
    master_lc = bpy.context.view_layer.layer_collection
    if root_collection is not None:
        lc = _find_layer_collection(master_lc, root_collection)
        return _read_layer_tree(lc) if lc is not None else _read_collection_tree(root_collection)
    return _read_layer_tree(master_lc)


def collect(scope="SCENE", objects=None, root_collection=None, collection_hierarchy=False):
    """Return NormalizedObject[] for the scope (UI passes the resolved object list).

    With `collection_hierarchy` True, every non-excluded Blender Collection in scope is also
    emitted as an identity-transform Group and objects with no in-scope object-parent are re-homed
    under the Group of the collection that directly contains them (build_collection_groups).
    `root_collection` (COLLECTION scope) is the emitted root group, honored even if excluded;
    SCENE/SELECTED use the scene master collection as the implicit root. With it False (or without
    bpy) the behaviour is unchanged."""
    if not collection_hierarchy or bpy is None:
        objs = collect_objects(scope, objects)
        in_scope = {o.name for o in objs}
        norms = [normalize_object(o, in_scope) for o in objs]
        _assign_child_indices(norms)
        return norms

    tree = _collection_tree(scope, root_collection)
    reachable = objects_in_tree(tree)               # drop excluded-only objects (props ~ exclusion)
    objs = [o for o in collect_objects(scope, objects) if o.name in reachable]
    in_scope = {o.name for o in objs}
    object_norms = [normalize_object(o, in_scope) for o in objs]
    group_norms, reparent = build_collection_groups(
        object_norms, tree, emit_root_as_group=root_collection is not None)
    for n in object_norms:
        if n["name"] in reparent:
            n["parent_name"] = reparent[n["name"]]
    norms = object_norms + group_norms
    _assign_child_indices(norms)
    return norms


def exportable_objects(scope, objects, root_collection=None, collection_hierarchy=False):
    """The object list to feed materials + obj_export so they stay consistent with the props.
    With `collection_hierarchy` True, view-layer-excluded objects are dropped (the props don't
    reference them, so their materials/OBJs shouldn't ship). False (or without bpy) -> `objects`
    unchanged."""
    if not collection_hierarchy or bpy is None:
        return objects
    reachable = objects_in_tree(_collection_tree(scope, root_collection))
    base = objects if objects is not None else list(bpy.context.scene.objects)
    return [o for o in base if o.name in reachable]


def build_mesh_object_map(objects):
    """{mesh datablock name -> a representative bpy object using it} for obj_export.
    First in-scope object per datablock wins (instances share one OBJ)."""
    out = {}
    for o in collect_objects(None, objects):
        if o.type == "MESH" and o.data.name not in out:
            out[o.data.name] = o
    return out
