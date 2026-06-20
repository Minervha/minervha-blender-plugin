"""export_limits.py — pre-flight export guardrails (faces / objects / textures).

Two concerns, deliberately split so the decision logic is testable without Blender:

* **bpy counters** (`gather_scene_budget`, `gather_texture_inventory`) — cheap reads of the
  scene: face counts per unique mesh datablock, unique-geometry count, and a texture inventory
  (image name + dimensions). `len(mesh.polygons)` is O(1) per object, so the budget is cheap
  enough to recompute on every panel redraw; the texture inventory walks material node trees and
  is only meant to run on demand (a collapsed sub-panel / the export log).
* **pure decision logic** (`evaluate`, `clamp_max_res`) — no bpy. `evaluate` compares a budget to
  the configured thresholds and returns the list of breaches (soft = warn, hard = block).
  `clamp_max_res` enforces the fixed 8192 px texture ceiling. Both are imported directly by the
  unit tests (no Blender needed), mirroring scene_introspect's bpy-optional pattern.

The thresholds dict is supplied by the AddonPreferences (see ui._thresholds); DEFAULT_THRESHOLDS
mirrors those defaults for tests and as a fallback when preferences are unavailable.
"""

try:
    import bpy
except ImportError:                 # importable for tests / syntax check without Blender
    bpy = None

# Fixed hard rule: no exported texture may exceed this on its longest side. Not configurable —
# it is THE rule, enforced by clamping the texture pre-pass max_res (covers source AND baked
# textures, which both flow through wlsave_export._iter_build_material_entries).
MAX_TEXTURE_DIM = 8192

# Mirrors the AddonPreferences defaults (ui.MINERVHA_AddonPreferences). Used by tests and as the
# fallback when preferences cannot be read.
DEFAULT_THRESHOLDS = {
    "faces_scene_soft": 500_000,
    "faces_scene_hard": 2_000_000,
    "faces_mesh_soft": 100_000,
    "faces_mesh_hard": 500_000,
    "objects_soft": 3_200,
    "objects_hard": 4_000,
}


# ── bpy counters ────────────────────────────────────────────────────────────

def gather_scene_budget(objects):
    """Cheap face/geometry budget for the given bpy objects (Scene mode).

    MESH objects only, de-duplicated by mesh datablock name (`obj.data.name`, the same
    `mesh_key` build_mesh_object_map uses) so instances sharing one mesh count once — matching
    what the exporter actually writes (one OBJ per datablock). Returns:
        {"faces_total": int, "faces_by_mesh": {data_name: faces}, "geometries_unique": int}
    """
    seen = set()
    faces_by_mesh = {}
    faces_total = 0
    for o in objects or []:
        if getattr(o, "type", None) != "MESH":
            continue
        data = o.data
        if data is None or data.name in seen:
            continue
        seen.add(data.name)
        n = len(data.polygons)
        faces_by_mesh[data.name] = n
        faces_total += n
    return {
        "faces_total": faces_total,
        "faces_by_mesh": faces_by_mesh,
        "geometries_unique": len(seen),
    }


def _iter_image_nodes(node_tree, seen_trees):
    """Yield the bpy Image of every TEX_IMAGE node in `node_tree`, recursing into node groups."""
    if node_tree is None or id(node_tree) in seen_trees:
        return
    seen_trees.add(id(node_tree))
    for node in node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            yield node.image
        elif node.type == "GROUP" and getattr(node, "node_tree", None) is not None:
            yield from _iter_image_nodes(node.node_tree, seen_trees)


def _materials_from_objects(objects):
    """De-duplicated bpy materials used by `objects`; None -> all materials in the file."""
    if objects is None:
        return list(bpy.data.materials) if bpy is not None else []
    out, seen = [], set()
    for o in objects:
        for slot in getattr(o, "material_slots", []):
            m = slot.material
            if m is not None and m.name not in seen:
                seen.add(m.name)
                out.append(m)
    return out


def gather_texture_inventory(objects):
    """Texture inventory referenced by `objects`' materials (None -> whole file).

    Returns a list of (image_name, width, height) sorted by longest side, largest first.
    Approximate: it reflects TEX_IMAGE source images, not the exact set the exporter writes
    (baking/dedup can change it). Images with no resolved size are skipped.
    """
    images = {}
    for mat in _materials_from_objects(objects):
        if not getattr(mat, "use_nodes", False) or mat.node_tree is None:
            continue
        for img in _iter_image_nodes(mat.node_tree, set()):
            if img.name in images:
                continue
            size = getattr(img, "size", (0, 0))
            w = int(size[0]) if len(size) >= 2 else 0
            h = int(size[1]) if len(size) >= 2 else 0
            if w <= 0 or h <= 0:
                continue
            images[img.name] = (img.name, w, h)
    return sorted(images.values(), key=lambda t: max(t[1], t[2]), reverse=True)


def dim_histogram(inventory):
    """[(name, w, h), ...] -> [(longest_side, count), ...] sorted by dimension, largest first.

    Pure aggregation of gather_texture_inventory's output into a compact 'N× DIM' breakdown.
    """
    counts = {}
    for _name, w, h in inventory:
        d = max(w, h)
        counts[d] = counts.get(d, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[0], reverse=True)


# ── pure decision logic (no bpy) ─────────────────────────────────────────────

def clamp_max_res(user_max_res, experimental):
    """The effective texture pre-pass max_res once the 8192 px rule is applied.

    experimental ON -> the user's setting is honoured untouched (incl. None = no cap).
    Otherwise the longest side is capped at MAX_TEXTURE_DIM; None (no user cap) becomes the cap.
    """
    if experimental:
        return user_max_res
    if user_max_res is None:
        return MAX_TEXTURE_DIM
    return min(user_max_res, MAX_TEXTURE_DIM)


def _level(value, soft, hard):
    """Worst threshold `value` reaches: 'hard' >= hard, else 'soft' >= soft, else None."""
    if hard and value >= hard:
        return "hard"
    if soft and value >= soft:
        return "soft"
    return None


def evaluate(budget, thresholds, experimental):
    """Breaches of `thresholds` by `budget`. experimental ON -> [] (all guardrails disabled).

    Each breach: {"resource": "faces"|"objects", "scope": "scene"|"mesh",
                  "value": int, "limit": int, "level": "soft"|"hard", ["mesh": name]}.
    Per-mesh breaches carry the mesh datablock name; only the worst level is emitted per item.
    """
    if experimental:
        return []
    t = thresholds
    out = []

    lvl = _level(budget["faces_total"], t["faces_scene_soft"], t["faces_scene_hard"])
    if lvl:
        out.append({"resource": "faces", "scope": "scene", "value": budget["faces_total"],
                    "limit": t["faces_scene_hard"] if lvl == "hard" else t["faces_scene_soft"],
                    "level": lvl})

    geom = budget["geometries_unique"]
    lvl = _level(geom, t["objects_soft"], t["objects_hard"])
    if lvl:
        out.append({"resource": "objects", "scope": "scene", "value": geom,
                    "limit": t["objects_hard"] if lvl == "hard" else t["objects_soft"],
                    "level": lvl})

    for key, faces in budget["faces_by_mesh"].items():
        lvl = _level(faces, t["faces_mesh_soft"], t["faces_mesh_hard"])
        if lvl:
            out.append({"resource": "faces", "scope": "mesh", "mesh": key, "value": faces,
                        "limit": t["faces_mesh_hard"] if lvl == "hard" else t["faces_mesh_soft"],
                        "level": lvl})
    return out


def has_hard(violations):
    """True if any breach is a hard (blocking) one."""
    return any(v["level"] == "hard" for v in violations)
