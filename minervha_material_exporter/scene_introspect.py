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


# ── Collection instances ────────────────────────────────────────────────────
# An Empty with instance_type == 'COLLECTION' renders the whole instanced collection at its own
# transform (minus the collection's instance_offset). Wild Life has no instancing, so each instance
# is EXPANDED: the Empty stays a Group (its transform), and every MESH/EMPTY of the instanced
# collection becomes a child prop placed in collection space. Meshes keep their datablock as
# mesh_key, so N instances still share ONE OBJ. Each expanded node gets a namespaced guid_key
# (instancer path + object name) so the same source object can appear under many instancers.
_INSTANCE_GUID_PREFIX = "\x00minervha-instance\x00"
_MAX_INSTANCE_DEPTH = 8


def _is_collection_instancer(obj):
    return (getattr(obj, "type", None) == "EMPTY" and getattr(obj, "instance_type", None) == "COLLECTION"
            and getattr(obj, "instance_collection", None) is not None)


def _matrix_to_transform(mat):
    loc, quat, scale = mat.decompose()
    eul = quat.to_euler("XYZ")
    return ({"location": (loc.x, loc.y, loc.z), "rotation_euler": (eul.x, eul.y, eul.z),
             "rotation_order": "XYZ", "scale": (scale.x, scale.y, scale.z)}, mat.determinant() < 0)


def _expand_instancer(empty, empty_key, depth=0, chain=()):
    """NormalizedObject[] for the content of the collection instanced by `empty` (bpy).
    `empty_key` = the guid source of the instancer node (its guid_key or its name)."""
    coll = empty.instance_collection
    if depth >= _MAX_INSTANCE_DEPTH or coll.name in chain:
        return []                                   # recursive instancing guard
    chain = chain + (coll.name,)
    members = [o for o in coll.all_objects if o.type in ("MESH", "EMPTY")]
    names = {o.name for o in members}
    offset = mathutils.Matrix.Translation(-mathutils.Vector(coll.instance_offset))
    out = []
    for o in members:
        key = _INSTANCE_GUID_PREFIX + empty_key + "/" + o.name
        parent_in = o.parent is not None and o.parent.name in names
        n = normalize_object(o, names)
        mat = o.matrix_local if parent_in else offset @ o.matrix_world
        n["transform"], mirrored = _matrix_to_transform(mat)
        if n["kind"] == "mesh":
            n["validation"]["risky_transform"] = _risky_transform(n["transform"]["scale"], mirrored)
        n["guid_key"] = key
        n["parent_name"] = (_INSTANCE_GUID_PREFIX + empty_key + "/" + o.parent.name) if parent_in else empty_key
        n["instanced_from"] = coll.name
        out.append(n)
        if _is_collection_instancer(o):
            out.extend(_expand_instancer(o, key, depth + 1, chain))
    return out


def expand_collection_instances(norms, objs):
    """Append the expansion of every collection-instancer Empty in `objs` to `norms` (in place).
    Returns the number of instancers expanded."""
    by_name = {o.name: o for o in objs}
    count = 0
    for n in list(norms):
        o = by_name.get(n["name"]) if not n.get("guid_key") else None
        if o is not None and _is_collection_instancer(o):
            norms.extend(_expand_instancer(o, n["name"]))
            count += 1
    return count


# ── Other instancing (Geometry Nodes / particles / instancing on vertices or faces) ─────────────
# Read from the evaluated depsgraph: Blender lists every rendered instance with its exact world matrix,
# whatever produced it. Each one becomes a mesh prop parented to its instancer (local = instancer⁻¹ ·
# instance). Collection-instancer Empties are left to `expand_collection_instances` (it keeps the
# instanced collection's own hierarchy). Above MAX_DEPSGRAPH_INSTANCES nothing is expanded and the
# count is reported — a scattered forest can hold far more props than the game holds.
MAX_DEPSGRAPH_INSTANCES = 10000
LAST_INSTANCE_STATS = {}


def _may_instance(obj):
    if _is_collection_instancer(obj):
        return False
    if getattr(obj, "instance_type", "NONE") in ("VERTS", "FACES"):
        return True
    for ps in getattr(obj, "particle_systems", []) or []:
        if getattr(ps.settings, "render_type", "") in ("OBJECT", "COLLECTION"):
            return True
    return any(m.type == "NODES" for m in getattr(obj, "modifiers", []) or [])


# One depsgraph pass per export (cached), and the throwaway objects made for GEOMETRY instances.
# A Geometry Nodes "Instance on Points" fed by Object Info (As Instance OFF — the default) instances
# a copy of the geometry, not the object: there is no datablock to ship. Each distinct instanced
# geometry is copied once into a temporary mesh + object (never linked to any scene), shared by all
# its instances, and removed by `cleanup_instance_cache()` at the end of the export.
TEMP_GEO_PROP = "_wl_temp_geo"
_INST_CACHE = {"key": None, "found": [], "unsupported": 0, "total": 0, "temps": []}


def cleanup_instance_cache():
    for obj, me in _INST_CACHE["temps"]:
        try:
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
            if me.name in bpy.data.meshes and me.users == 0:
                bpy.data.meshes.remove(me)
        except ReferenceError:
            pass
    _INST_CACHE.update(key=None, found=[], unsupported=0, total=0, temps=[])


def _instance_mesh_of(inst, par):
    """The object to ship for one instance: the real source object, or None for a geometry instance."""
    src = inst.object.original
    if src.type == "MESH" and src != par:
        return src
    return None


def _depsgraph_instances(objs):
    """([(instancer_name, source_object, world_matrix, persistent_id)], unsupported, total) for the
    in-scope non-collection instancers. `total` = every mesh instance seen (the limit applies to it);
    over the limit nothing is materialized. Cached until `cleanup_instance_cache()`."""
    key = frozenset(o.name for o in objs if _may_instance(o))
    if _INST_CACHE["key"] == key:
        return _INST_CACHE["found"], _INST_CACHE["unsupported"], _INST_CACHE["total"]
    cleanup_instance_cache()
    _INST_CACHE["key"] = key
    if not key:
        return [], 0, 0
    dg = bpy.context.evaluated_depsgraph_get()
    total = sum(1 for i in dg.object_instances if i.is_instance and i.parent is not None
                and i.parent.original.name in key and i.object.type == "MESH")
    _INST_CACHE["total"] = total
    if total > MAX_DEPSGRAPH_INSTANCES:
        return [], 0, total
    found, unsupported, geo = [], 0, {}
    for inst in dg.object_instances:
        if not inst.is_instance or inst.parent is None:
            continue
        par = inst.parent.original
        if par.name not in key:
            continue
        if inst.object.type != "MESH":
            unsupported += 1
            continue
        src = _instance_mesh_of(inst, par)
        if src is None:
            ptr = inst.object.data.as_pointer()
            src = geo.get(ptr)
            if src is None:
                try:
                    # Copy the INSTANCE's geometry (inst.object.data). new_from_object() would rebuild
                    # the instancer's own (empty, instances-only) mesh instead — measured: 0 faces.
                    me = inst.object.data.copy()
                except Exception:
                    unsupported += 1
                    continue
                me.name = "%s_instance%d" % (par.name, len(geo) + 1)
                src = bpy.data.objects.new(me.name, me)
                src[TEMP_GEO_PROP] = True
                geo[ptr] = src
                _INST_CACHE["temps"].append((src, me))
        found.append((par.name, src, inst.matrix_world.copy(), tuple(inst.persistent_id)))
    _INST_CACHE["found"], _INST_CACHE["unsupported"] = found, unsupported
    return found, unsupported, total


def expand_depsgraph_instances(norms, objs, limit=None):
    """Append a mesh prop per GN / particle / vertex-face instance of the in-scope objects (in place).
    Also hides an instancer Blender does not render itself, and drops the template children of a
    VERTS/FACES instancer (Blender does not render those either). Returns the stats dict."""
    limit = MAX_DEPSGRAPH_INSTANCES if limit is None else limit
    stats = {"instances": 0, "instancesSkipped": 0, "unsupported": 0, "limit": limit}
    found, stats["unsupported"], total = _depsgraph_instances(objs)
    by_name = {o.name: o for o in objs}
    # Template children of a vertex/face instancer are not rendered by Blender -> no prop of their own.
    templates = {o.name for o in objs if o.parent is not None
                 and getattr(o.parent, "instance_type", "NONE") in ("VERTS", "FACES")
                 and o.parent.name in by_name}
    if templates:
        norms[:] = [n for n in norms if n.get("guid_key") or n["name"] not in templates]
    if total > limit:
        stats["instancesSkipped"] = total
        return stats
    instancers = {f[0] for f in found}
    for n in norms:
        o = by_name.get(n["name"]) if not n.get("guid_key") else None
        if o is not None and o.name in instancers and not getattr(o, "show_instancer_for_render", True):
            n["visible"] = False
    for par_name, src, mw, pid in found:
        par = by_name[par_name]
        n = normalize_object(src, set())
        n["transform"], mirrored = _matrix_to_transform(par.matrix_world.inverted_safe() @ mw)
        n["validation"]["risky_transform"] = _risky_transform(n["transform"]["scale"], mirrored)
        n["guid_key"] = _INSTANCE_GUID_PREFIX + par_name + "#" + "-".join(str(i) for i in pid)
        n["parent_name"] = par_name
        n["visible"] = True
        n["instanced_from"] = par_name
        norms.append(n)
    stats["instances"] = len(found)
    return stats


def instanced_source_objects(objs):
    """The real bpy MESH objects reachable through collection instancing from `objs` (recursive),
    not already in `objs` — fed to material introspection and the OBJ exporter so instanced
    geometry and its materials ship even when the source collection is hidden/excluded."""
    have = {o.name for o in objs}
    out, seen_colls = [], set()
    stack = [o for o in objs if _is_collection_instancer(o)]
    while stack:
        e = stack.pop()
        coll = e.instance_collection
        if coll.name in seen_colls:
            continue
        seen_colls.add(coll.name)
        for o in coll.all_objects:
            if o.type == "MESH" and o.name not in have:
                have.add(o.name)
                out.append(o)
            if _is_collection_instancer(o):
                stack.append(o)
    # Sources of GN / particle / vertex-face instances (often hidden objects or in another collection).
    found, _, _ = _depsgraph_instances(objs)   # empty when over the limit (not expanded, not shipped)
    for _, src, _, _ in found:
        if src.name not in have:
            have.add(src.name)
            out.append(src)
    return out


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
        if bpy is not None:
            expand_collection_instances(norms, objs)
            LAST_INSTANCE_STATS.clear()
            LAST_INSTANCE_STATS.update(expand_depsgraph_instances(norms, objs))
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
    expand_collection_instances(norms, objs)
    LAST_INSTANCE_STATS.clear()
    LAST_INSTANCE_STATS.update(expand_depsgraph_instances(norms, objs))
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
