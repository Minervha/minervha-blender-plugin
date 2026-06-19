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


def _input_active(node, names, thr=1e-4):
    """True if any of the named inputs on `node` is linked or has a default above `thr`
    (used to detect Principled features the flat WL struct cannot carry)."""
    for nm in names:
        s = node.inputs.get(nm)
        if s is None:
            continue
        if s.is_linked:
            return True
        try:
            if float(s.default_value) > thr:
                return True
        except (TypeError, ValueError):
            pass
    return False


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
    for p_node, p_tree in principled:
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
        # Linked-but-static (K rung): a Base Color / Metallic / Roughness / Emission fed by a
        # Value/RGB/Reroute/single-Group-Input hop that resolves to a constant — read it instead
        # of leaving the default. Does NOT count toward `unlinked` (that tracks genuinely
        # unlinked Principled inputs); a dynamic source falls through to a bakeCandidate downstream.
        if base_color is None and bc is not None and bc.is_linked:
            c = bsdf_trace._resolve_color(bc, p_tree, parent_map)
            if c is not None:
                base_color = c
        if metallic is None and met is not None and met.is_linked:
            dyn, val = bsdf_trace._resolve_input(met, p_tree, parent_map)
            if not dyn and val is not None:
                metallic = val
        if roughness is None and rough is not None and rough.is_linked:
            dyn, val = bsdf_trace._resolve_input(rough, p_tree, parent_map)
            if not dyn and val is not None:
                roughness = val
        if emission_color is None and em is not None and em.is_linked:
            c = bsdf_trace._resolve_color(em, p_tree, parent_map)
            if c is not None:
                emission_color = c
        if unlinked:
            principled_with_unlinked += 1

    normal_strength = None
    for nm_node, _ in normal_maps:
        s = nm_node.inputs.get('Strength')
        if s is not None and normal_strength is None:
            normal_strength = float(s.default_value)
    # Fallback: the Normal slot fed by a Bump node (not a Normal Map node) — read its Strength.
    if normal_strength is None:
        normal_strength = bsdf_trace.first_bump_strength(mat.node_tree)

    # Active-output-anchored shader walk: the source of truth for the material
    # `type` / `refraction` (Glass/Refraction/Transparent, linked Alpha/Transmission,
    # node groups). Prefer its reached-Principled scalars over the legacy loop's
    # (which reads any Principled in any tree); fall back to a Glass/Refraction node's
    # Color/Roughness when no Principled drives the surface.
    walk = bsdf_trace.trace_surface_shaders(mat.node_tree, parent_map)
    if "BSDF_PRINCIPLED" in walk["shaderTypes"]:
        alpha = walk["principledAlpha"]
        transmission = walk["principledTransmission"]
        if walk["principledIor"] is not None:
            ior = walk["principledIor"]
    if base_color is None and walk["refractiveColor"] is not None:
        base_color = walk["refractiveColor"]
    if roughness is None and walk["refractiveRoughness"] is not None:
        roughness = walk["refractiveRoughness"]

    tex_list = []
    projection_mapped = False
    for tex_node, tree in textures:
        slots = bsdf_trace.trace_from_texture(tex_node, tree, parent_map)
        if not slots:
            slots = ["UNKNOWN (or not Principled)"]
        if bsdf_trace.texture_is_projection_mapped(tex_node, tree, parent_map):
            projection_mapped = True
        image = tex_node.image
        file_str = bsdf_trace.resolve_image_file(image)
        c = _classify_file(file_str)
        mnode = bsdf_trace.find_mapping_for_texture(tex_node, tree, parent_map)
        mdata = bsdf_trace.get_mapping_data(mnode) if mnode else None
        if mdata:
            # rot is in DEGREES (get_mapping_data). The mapper has no rotation field but
            # uses it to warn when a non-zero rotation is dropped (no WL equivalent).
            loc, rot, sca = mdata
            mapping = {
                "loc": {"x": loc[0], "y": loc[1], "z": loc[2]},
                "scale": {"x": sca[0], "y": sca[1], "z": sca[2]},
                "rot": {"x": rot[0], "y": rot[1], "z": rot[2]},
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

    # EEVEE-Next blend / refraction signals (corroboration for the type decision).
    # blend_method is deliberately NOT read — deprecated, defaults HASHED on every
    # 5.x material. surface_render_method 'BLENDED' is the real alpha-blend signal.
    surface_render_method = getattr(mat, "surface_render_method", None)
    raytrace = getattr(mat, "use_raytrace_refraction", None)
    if raytrace is None:
        raytrace = getattr(mat, "use_screen_refraction", None)

    # Channels driven by a non-static node graph with NO exportable image texture (procedural
    # Noise/Voronoi, Math, Mix, ColorRamp, ...) -> flag for baking (mapper turns these into
    # bakeCandidates). A channel satisfied by a single image texture (its slot appears in
    # tex_list) or by a resolved constant is NOT flagged.
    slot_set = {s for t in tex_list for s in (t.get("slots") or [])}

    def _input_linked(input_name):
        return any((p.inputs.get(input_name) is not None and p.inputs.get(input_name).is_linked)
                   for p, _ in principled)

    dynamic_channels = []
    for input_name, ch, static_val in (("Base Color", "diffuse", base_color),
                                       ("Metallic", "metallic", metallic),
                                       ("Roughness", "roughness", roughness),
                                       ("Emission Color", "emissive", emission_color)):
        if static_val is not None or input_name in slot_set:
            continue
        if ch == "emissive" and isinstance(emission_strength, (int, float)) and emission_strength <= 0:
            continue  # no glow -> baking a black emissive is pointless
        linked = _input_linked(input_name)
        if not linked and input_name == "Emission Color":
            linked = _input_linked("Emission")
        if linked:
            dynamic_channels.append(ch)
    if "Normal" not in slot_set and _input_linked("Normal"):
        dynamic_channels.append("normal")

    # A Principled color/scalar channel fed by >=2 distinct image textures is a BLEND the flat WL
    # slot cannot hold (the common model pattern: real albedo x tiled-detail x object-color -> Mix).
    # The forward texture->slot trace attributes only ONE texture per slot; the others land "UNKNOWN"
    # and were silently dropped, so the channel shipped the WRONG single texture (often a tiled detail
    # while the real albedo was discarded). Count images backward from the input and flag the channel
    # for baking — the placeholder-plane bake flattens the blend correctly.
    multi_texture_channels = []
    for input_name, ch in (("Base Color", "diffuse"), ("Metallic", "metallic"),
                           ("Roughness", "roughness"), ("Emission Color", "emissive")):
        imgs = set()
        for p_node, p_tree in principled:
            inp = p_node.inputs.get(input_name)
            if inp is None and input_name == "Emission Color":
                inp = p_node.inputs.get("Emission")
            if inp is not None and inp.is_linked:
                imgs |= bsdf_trace.images_feeding_input(inp, p_tree, parent_map)
            if len(imgs) >= 2:
                break
        if len(imgs) >= 2:
            multi_texture_channels.append(ch)

    # Irreducible losses: Principled features with no WL slot (not even via baking).
    lossy_features = []
    for p_node, _ in principled:
        if "anisotropy" not in lossy_features and _input_active(p_node, ("Anisotropic",)):
            lossy_features.append("anisotropy")
        if "coat" not in lossy_features and _input_active(p_node, ("Coat Weight", "Coat", "Clearcoat")):
            lossy_features.append("coat")
        if "sheen" not in lossy_features and _input_active(p_node, ("Sheen Weight", "Sheen")):
            lossy_features.append("sheen")

    return {
        "name": mat.name, "skipped": False,
        "objects": bsdf_trace.objects_using_material(mat),
        "baseColor": base_color, "metallic": metallic, "roughness": roughness,
        "emissionColor": emission_color, "emissionStrength": emission_strength,
        "normalStrength": normal_strength,
        "specular": specular, "ior": ior, "transmission": transmission, "alpha": alpha,
        "twoSided": two_sided, "alphaCutoff": alpha_cutoff,
        "surfaceRenderMethod": surface_render_method,
        "useRaytraceRefraction": (bool(raytrace) if isinstance(raytrace, bool) else None),
        "shaderTypes": walk["shaderTypes"],
        "alphaLinked": walk["alphaLinked"],
        "transmissionLinked": walk["transmissionLinked"],
        "transmissionStaticValue": walk["transmissionStaticValue"],
        "refractiveIor": walk["refractiveIor"],
        "maskedFacMix": walk["maskedFacMix"],
        "projectionMapped": projection_mapped,
        "consumedByNoUvObject": False,  # set by collect() once object context is known
        "dynamicChannels": dynamic_channels,
        "multiTextureChannels": multi_texture_channels,
        "lossyFeatures": lossy_features,
        "perMeshDependency": _per_mesh_dependency(mat),
        "principledNodeCount": principled_with_unlinked, "textures": tex_list,
    }


_PERMESH_NODE_REASON = {
    "VERTEX_COLOR": "vertex-color", "ATTRIBUTE": "attribute",
    "NEW_GEOMETRY": "geometry", "BEVEL": "geometry",
    "AMBIENT_OCCLUSION": "geometry", "WIREFRAME": "geometry",
}
_NONUV_COORD_OUTPUTS = {"Generated", "Object", "Camera", "Window", "Reflection", "Normal"}
_PROCEDURAL_TEX = {"TEX_NOISE", "TEX_VORONOI", "TEX_MUSGRAVE", "TEX_WAVE", "TEX_MAGIC",
                   "TEX_GRADIENT", "TEX_CHECKER", "TEX_BRICK"}


def _per_mesh_dependency(mat):
    """Reasons a material's look depends on PER-MESH data (vertex colors / attributes / object-space
    coords / geometry) and so cannot be faithfully flattened to ONE shared baked texture — every mesh
    would need its own. Walks the node tree + nested groups; empty = UV-pure (a single placeholder-plane
    bake is exact). Threaded to the report so a baked-but-approximated material is flagged, not silent."""
    if not mat or not mat.use_nodes or mat.node_tree is None:
        return []
    seen, stack, reasons = set(), [mat.node_tree], set()
    while stack:
        nt = stack.pop()
        if nt is None or nt.name in seen:
            continue
        seen.add(nt.name)
        for n in nt.nodes:
            t = n.type
            if t == "GROUP" and getattr(n, "node_tree", None):
                stack.append(n.node_tree)
            elif t in _PERMESH_NODE_REASON:
                if any(o.is_linked for o in n.outputs):
                    reasons.add(_PERMESH_NODE_REASON[t])
            elif t == "TEX_COORD":
                if any(o.name in _NONUV_COORD_OUTPUTS and o.is_linked for o in n.outputs):
                    reasons.add("object-space")
            elif t in _PROCEDURAL_TEX:
                v = n.inputs.get("Vector")
                if v is not None and not v.is_linked:
                    reasons.add("object-space")        # unconnected Vector -> Generated coords
    return sorted(reasons)


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


def _annotate_no_uv(norms, mats, scope, objects):
    """Set consumedByNoUvObject when a mesh that uses the material has no UV layer
    (signal b for bIsTriplanar inference). Scope-aware; falls back to all objects."""
    if objects is None:
        objects = (list(bpy.context.selected_objects) if scope == "SELECTED"
                   else list(bpy.data.objects))
    no_uv_mat_names = set()
    for obj in objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        if len(obj.data.uv_layers) > 0:
            continue
        for slot in getattr(obj, "material_slots", []):
            if slot.material is not None:
                no_uv_mat_names.add(slot.material.name)
    for norm, mat in zip(norms, mats):
        if mat.name in no_uv_mat_names:
            norm["consumedByNoUvObject"] = True


def collect(scope="FILE", objects=None):
    """Return NormalizedMaterial[] for the given scope."""
    mats = _materials_for_scope(scope, objects)
    norms = [normalize_material(m) for m in mats]
    _annotate_no_uv(norms, mats, scope, objects)
    return norms
