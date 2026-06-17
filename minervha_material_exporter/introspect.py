"""introspect.py — build NormalizedMaterial[] from Blender materials (mode B input).

Produces the SAME shape as Minervha Studio's blenderParse.js output, so mapper.py
(the port of mapMaterial.js) consumes it directly. Uses bsdf_trace.py for the node
walking.

Values are read at full precision (the .txt path rounds for display; mode B keeps
the exact float). The parser drops mapping rotation, so the normalized model carries
only location + scale, exactly what mapper.py needs.

Scope-aware: gather materials from selected objects, a Blender Collection, or the
whole file (mirrors the script's bpy.data.materials iteration).
"""

import os
import re
import bpy

try:
    from . import bsdf_trace          # packaged extension
except ImportError:                    # dev / sys.path import (tests, live MCP)
    import bsdf_trace


def _basename_of(p):
    if not p:
        return None
    parts = str(p).replace("\\", "/").split("/")
    return parts[-1] or None


def _classify_file(raw):
    """Mirror blenderParse.js classifyFile: a File value -> {kind, path, basename}."""
    v = str(raw).strip()
    if not v or v.lower() == "missing path" or v == "<missing>":
        return {"kind": "missing", "path": None, "basename": None}
    if v == "<packed>":
        return {"kind": "packed", "path": None, "basename": None}
    if v == "<generated>":
        return {"kind": "generated", "path": None, "basename": None}
    m = re.match(r"^\[UDIM\]\s*(.*)$", v, re.IGNORECASE)
    if m:
        p = m.group(1).strip()
        return {"kind": "udim", "path": p, "basename": _basename_of(p)}
    return {"kind": "path", "path": v, "basename": _basename_of(v)}


def _color(value):
    return {"r": value[0], "g": value[1], "b": value[2], "a": value[3]}


def normalize_material(mat):
    """Build one NormalizedMaterial dict (blenderParse.js shape) for a Blender material."""
    if not (mat.use_nodes and mat.node_tree):
        return {
            "name": mat.name, "skipped": True, "objects": [],
            "baseColor": None, "metallic": None, "roughness": None,
            "emissionColor": None, "emissionStrength": None, "normalStrength": None,
            "specular": None, "ior": None, "transmission": None, "alpha": None,
            "twoSided": None, "alphaCutoff": None,
            "principledNodeCount": 0, "textures": [],
        }

    parent_map = bsdf_trace.build_local_parent_map(mat.node_tree)
    textures, principled, normal_maps = [], [], []
    bsdf_trace.scan_tree(mat.node_tree, textures, principled, normal_maps)

    base_color = metallic = roughness = emission_color = emission_strength = None
    specular = ior = transmission = alpha = None
    principled_with_unlinked = 0
    for p_node, _ in principled:
        bc = p_node.inputs.get('Base Color')
        met = p_node.inputs.get('Metallic')
        rough = p_node.inputs.get('Roughness')
        em = p_node.inputs.get('Emission Color') or p_node.inputs.get('Emission')
        em_str = p_node.inputs.get('Emission Strength')
        spec = p_node.inputs.get('Specular IOR Level') or p_node.inputs.get('Specular')
        ior_in = p_node.inputs.get('IOR')
        trans = p_node.inputs.get('Transmission Weight') or p_node.inputs.get('Transmission')
        alpha_in = p_node.inputs.get('Alpha')
        unlinked = False
        if bc is not None and not bc.is_linked:
            unlinked = True
            if base_color is None:
                base_color = _color(bc.default_value)
        if met is not None and not met.is_linked:
            unlinked = True
            if metallic is None:
                metallic = float(met.default_value)
        if rough is not None and not rough.is_linked:
            unlinked = True
            if roughness is None:
                roughness = float(rough.default_value)
        if em is not None and not em.is_linked:
            unlinked = True
            if emission_color is None:
                emission_color = _color(em.default_value)
        if em_str is not None and not em_str.is_linked:
            unlinked = True
            if emission_strength is None:
                emission_strength = float(em_str.default_value)
        # Extra Principled inputs for full v18 export (first unlinked value wins).
        if spec is not None and not spec.is_linked and specular is None:
            specular = float(spec.default_value)
        if ior_in is not None and not ior_in.is_linked and ior is None:
            ior = float(ior_in.default_value)
        if trans is not None and not trans.is_linked and transmission is None:
            transmission = float(trans.default_value)
        if alpha_in is not None and not alpha_in.is_linked and alpha is None:
            alpha = float(alpha_in.default_value)
        if unlinked:
            principled_with_unlinked += 1

    normal_strength = None
    for nm_node, _ in normal_maps:
        s = nm_node.inputs.get('Strength')
        if s is not None and normal_strength is None:
            normal_strength = float(s.default_value)

    tex_list = []
    for tex_node, tree in textures:
        slots = bsdf_trace.trace_from_texture(tex_node, tree, parent_map)
        if not slots:
            slots = ["UNKNOWN (or not Principled)"]
        image = tex_node.image
        file_str = bsdf_trace.resolve_image_file(image)
        c = _classify_file(file_str)
        mnode = bsdf_trace.find_mapping_for_texture(tex_node, tree, parent_map)
        mdata = bsdf_trace.get_mapping_data(mnode) if mnode else None
        if mdata:
            loc, _rot, sca = mdata
            mapping = {
                "loc": {"x": loc[0], "y": loc[1], "z": loc[2]},
                "scale": {"x": sca[0], "y": sca[1], "z": sca[2]},
            }
        else:
            mapping = None
        tex_list.append({
            "name": image.name, "file": file_str, "fileKind": c["kind"],
            "path": c["path"], "basename": c["basename"],
            "slots": slots, "mapping": mapping,
        })

    # Material-level (not node) properties. EEVEE-Next renamed/removed some of
    # these, so read defensively and fall back to None (mapper uses defaults).
    two_sided = (not mat.use_backface_culling) if hasattr(mat, "use_backface_culling") else None
    alpha_cutoff = getattr(mat, "alpha_threshold", None)
    if alpha_cutoff is not None:
        alpha_cutoff = float(alpha_cutoff)

    return {
        "name": mat.name, "skipped": False,
        "objects": bsdf_trace.objects_using_material(mat),
        "baseColor": base_color, "metallic": metallic, "roughness": roughness,
        "emissionColor": emission_color, "emissionStrength": emission_strength,
        "normalStrength": normal_strength,
        "specular": specular, "ior": ior, "transmission": transmission, "alpha": alpha,
        "twoSided": two_sided, "alphaCutoff": alpha_cutoff,
        "principledNodeCount": principled_with_unlinked, "textures": tex_list,
    }


def _materials_for_scope(scope, objects):
    """Resolve the ordered, de-duplicated material list for a scope.

    scope='FILE'       -> every material in the file (like the original script).
    scope='SELECTED'   -> materials on the selected objects (or `objects` if given).
    scope='COLLECTION' -> materials on `objects` (the chosen collection's objects).
    """
    if scope == "FILE":
        return list(bpy.data.materials)
    if objects is None:
        objects = bpy.context.selected_objects if scope == "SELECTED" else []
    seen, out = set(), []
    for obj in objects:
        for slot in getattr(obj, "material_slots", []):
            m = slot.material
            if m is not None and m.name not in seen:
                seen.add(m.name)
                out.append(m)
    return out


def collect(scope="FILE", objects=None):
    """Return NormalizedMaterial[] for the given scope."""
    return [normalize_material(m) for m in _materials_for_scope(scope, objects)]
