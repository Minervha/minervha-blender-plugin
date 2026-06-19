"""bsdf_trace.py — Blender node-tracing helpers.

Behavior-preserving port of the tracing logic in the original Blender material-export
script by **Nocna Klacz** (MaterialPrint_hardened.py) — used here by introspect.py to
read each material's Principled BSDF, textures, slots and mapping. Big thanks to Nocna
Klacz for the base this is built on.

Blender-only module (imports bpy for image-path resolution).
"""

import math
import bpy


def build_local_parent_map(root_tree):
    """Map each nested node-group tree -> list of (group_node, containing_tree)."""
    parent_map = {}
    visited = set()

    def traverse(tree):
        if tree in visited:
            return
        visited.add(tree)
        for node in tree.nodes:
            if node.type == 'GROUP' and node.node_tree:
                parent_map.setdefault(node.node_tree, []).append((node, tree))
                traverse(node.node_tree)

    traverse(root_tree)
    return parent_map


def trace_from_texture(texture_node, node_tree, parent_map):
    """Forward-trace a texture node's outputs to the Principled BSDF input slot names it feeds."""
    found_slots = set()
    stack = [(out, node_tree) for out in texture_node.outputs]
    visited = set()
    while stack:
        socket, tree = stack.pop()
        key = (id(tree), id(socket))
        if key in visited:
            continue
        visited.add(key)
        for link in socket.links:
            target_node = link.to_node
            target_socket = link.to_socket
            if target_node.type == 'BSDF_PRINCIPLED':
                found_slots.add(target_socket.name)
                continue
            # Height/displacement: a texture feeding a Bump/Displacement "Height"
            # input, or the Material Output "Displacement" socket, is a height map.
            # Stop here so it is NOT mis-traced through the Bump node to Principled
            # 'Normal' (a bump's output feeds the Normal slot).
            if target_node.type == 'BUMP' and target_socket.name == 'Height':
                found_slots.add('Height')
                continue
            if target_node.type == 'DISPLACEMENT' and target_socket.name == 'Height':
                found_slots.add('Height')
                continue
            if target_node.type == 'OUTPUT_MATERIAL' and target_socket.name == 'Displacement':
                found_slots.add('Height')
                continue
            if target_node.type == 'GROUP':
                group_tree = target_node.node_tree
                if group_tree:
                    idx = next((i for i, inp in enumerate(target_node.inputs) if inp == target_socket), None)
                    if idx is not None:
                        # A tree can hold SEVERAL Group Input nodes (Blender spawns a fresh one each
                        # time you drag from a group socket); the link feeding the BSDF may live on any
                        # of them. Descend through every one — picking only the first would dead-end
                        # the trace and silently lose the texture's slot. The visited-set dedups.
                        for gi in group_tree.nodes:
                            if gi.type == 'GROUP_INPUT' and idx < len(gi.outputs):
                                stack.append((gi.outputs[idx], group_tree))
                continue
            if target_node.type == 'GROUP_OUTPUT':
                idx = next((i for i, inp in enumerate(target_node.inputs) if inp == target_socket), None)
                if idx is not None:
                    for parent_node, parent_tree in parent_map.get(tree, []):
                        if idx < len(parent_node.outputs):
                            stack.append((parent_node.outputs[idx], parent_tree))
                continue
            for out in target_node.outputs:
                stack.append((out, tree))
    return sorted(found_slots)


# Intermediate node types that DON'T change a texture's role as a channel's data — the texture
# remains the channel's map "through" them. Anything else between a texture and a Principled slot
# (Mix / Math / ColorRamp / Hue-Sat / Invert / procedural ...) TRANSFORMS the texture, so the texture
# is a procedural *input*, not the final map. Groups are structural (handled explicitly below).
_TRANSPARENT_PASS = {"REROUTE", "NORMAL_MAP",
                     "SEPARATE_COLOR", "SEPARATE_RGB", "SEPARATE_XYZ"}


def direct_slots_from_texture(texture_node, node_tree, parent_map):
    """Subset of `trace_from_texture` reachable through ONLY transparent intermediate nodes — i.e. the
    slots for which this texture IS the channel's data (direct map), not a procedural input feeding it.

    Same forward walk as trace_from_texture, but each stack item carries a `transformed` flag set once
    the path crosses a non-whitelisted node; a Principled slot reached while transformed is dropped.
    Used by the mapper to ship only final textures (a slot reached only through a node graph is baked
    or left empty, never shipped as a misleading raw texture)."""
    found = set()
    stack = [(out, node_tree, False) for out in texture_node.outputs]
    visited = set()
    while stack:
        socket, tree, transformed = stack.pop()
        key = (id(tree), id(socket), transformed)
        if key in visited:
            continue
        visited.add(key)
        for link in socket.links:
            tn, ts = link.to_node, link.to_socket
            if tn.type == 'BSDF_PRINCIPLED':
                if not transformed:
                    found.add(ts.name)
                continue
            if (tn.type in ('BUMP', 'DISPLACEMENT') and ts.name == 'Height') or \
               (tn.type == 'OUTPUT_MATERIAL' and ts.name == 'Displacement'):
                if not transformed:
                    found.add('Height')
                continue
            if tn.type == 'GROUP':                       # structural — preserve `transformed`
                gt = tn.node_tree
                if gt:
                    idx = next((i for i, inp in enumerate(tn.inputs) if inp == ts), None)
                    if idx is not None:
                        for gi in gt.nodes:
                            if gi.type == 'GROUP_INPUT' and idx < len(gi.outputs):
                                stack.append((gi.outputs[idx], gt, transformed))
                continue
            if tn.type == 'GROUP_OUTPUT':                # structural — preserve `transformed`
                idx = next((i for i, inp in enumerate(tn.inputs) if inp == ts), None)
                if idx is not None:
                    for pn, pt in parent_map.get(tree, []):
                        if idx < len(pn.outputs):
                            stack.append((pn.outputs[idx], pt, transformed))
                continue
            nxt = transformed or (tn.type not in _TRANSPARENT_PASS)
            for out in tn.outputs:
                stack.append((out, tree, nxt))
    return sorted(found)


def images_feeding_input(start_socket, node_tree, parent_map):
    """Backward-trace an input socket to the set of distinct Image datablock names feeding it
    (through Mix / Math / Separate / Combine / Reroute / node groups). Detects a channel built from a
    BLEND of >=2 textures — which the flat WL slot can't hold (the forward `trace_from_texture`
    attributes only one texture per slot, dropping the rest to 'UNKNOWN'). Bake to flatten.

    Visited is keyed by (tree name, node name) — stable and collision-free. Keying on `id(socket)`
    mis-dedups (bpy re-creates socket wrappers, so freed ids get reused) and dead-ends multi-Mix
    chains, which silently under-counts the textures."""
    if start_socket is None or not start_socket.is_linked:
        return set()
    images = set()
    stack = [(l.from_node, l.from_socket, node_tree) for l in start_socket.links]
    visited = set()
    while stack:
        node, from_sock, tree = stack.pop()
        key = (tree.name, node.name)
        if key in visited:
            continue
        visited.add(key)
        if node.type == 'TEX_IMAGE':
            if node.image is not None:
                images.add(node.image.name)
            continue
        if node.type == 'GROUP_INPUT':
            idx = next((i for i, out in enumerate(node.outputs) if out == from_sock), None)
            if idx is not None:
                for p_node, p_tree in parent_map.get(tree, []):
                    if idx < len(p_node.inputs) and p_node.inputs[idx].is_linked:
                        for l in p_node.inputs[idx].links:
                            stack.append((l.from_node, l.from_socket, p_tree))
            continue
        if node.type == 'GROUP' and node.node_tree:
            idx = next((i for i, out in enumerate(node.outputs) if out == from_sock), None)
            if idx is not None:
                for go in node.node_tree.nodes:
                    if go.type == 'GROUP_OUTPUT' and idx < len(go.inputs) and go.inputs[idx].is_linked:
                        for l in go.inputs[idx].links:
                            stack.append((l.from_node, l.from_socket, node.node_tree))
            continue
        # generic node (MIX / MATH / SEPARATE / COMBINE / MAPPING / ...): recurse all linked inputs
        for inp in node.inputs:
            for l in inp.links:
                stack.append((l.from_node, l.from_socket, tree))
    return images


def find_mapping_for_texture(tex_node, node_tree, parent_map):
    """Backward-trace a texture's Vector input to its Mapping node, if any."""
    vector_input = tex_node.inputs.get('Vector')
    if not vector_input or not vector_input.is_linked:
        return None
    stack = [(vector_input, node_tree)]
    visited = set()
    while stack:
        socket, tree = stack.pop()
        key = (id(tree), id(socket))
        if key in visited:
            continue
        visited.add(key)
        for link in socket.links:
            source_node = link.from_node
            source_socket = link.from_socket
            if source_node.type == 'MAPPING':
                return source_node
            if source_node.type == 'REROUTE':
                if source_node.inputs and source_node.inputs[0].is_linked:
                    stack.append((source_node.inputs[0], tree))
                continue
            if source_node.type == 'GROUP_INPUT':
                idx = next((i for i, out in enumerate(source_node.outputs) if out == source_socket), None)
                if idx is not None:
                    for p_node, p_tree in parent_map.get(tree, []):
                        if idx < len(p_node.inputs) and p_node.inputs[idx].is_linked:
                            stack.append((p_node.inputs[idx], p_tree))
                continue
            if source_node.type == 'GROUP' and source_node.node_tree:
                idx = next((i for i, out in enumerate(source_node.outputs) if out == source_socket), None)
                if idx is not None:
                    # A tree can hold several Group Output nodes; the matching interface socket may be
                    # linked on any of them. Follow the first linked one — assuming the first node would
                    # miss a Mapping wired through a later Group Output and silently drop its tiling/offset.
                    for go in source_node.node_tree.nodes:
                        if go.type == 'GROUP_OUTPUT' and idx < len(go.inputs) and go.inputs[idx].is_linked:
                            stack.append((go.inputs[idx], source_node.node_tree))
                            break
                continue
    return None


def texture_is_projection_mapped(tex_node, node_tree, parent_map):
    """True if the texture is driven by object/world projection rather than UVs: its
    Image node uses Box projection, or its Vector input traces back to a Texture
    Coordinate 'Object'/'Generated' output (through Reroute / Mapping / Group). Used to
    set bIsTriplanar — the closest WL equivalent to a non-UV projection."""
    if getattr(tex_node, "projection", "FLAT") == "BOX":
        return True
    vector_input = tex_node.inputs.get("Vector")
    if not vector_input or not vector_input.is_linked:
        return False
    stack = [(vector_input, node_tree)]
    visited = set()
    while stack:
        socket, tree = stack.pop()
        key = (id(tree), id(socket))
        if key in visited:
            continue
        visited.add(key)
        for link in socket.links:
            src = link.from_node
            fsock = link.from_socket
            if src.type == "TEX_COORD":
                if fsock.name in ("Object", "Generated"):
                    return True
                continue
            if src.type == "MAPPING":
                inp = src.inputs.get("Vector")
                if inp and inp.is_linked:
                    stack.append((inp, tree))
                continue
            if src.type == "REROUTE":
                if src.inputs and src.inputs[0].is_linked:
                    stack.append((src.inputs[0], tree))
                continue
            if src.type == "GROUP_INPUT":
                idx = next((i for i, out in enumerate(src.outputs) if out == fsock), None)
                if idx is not None:
                    for p_node, p_tree in parent_map.get(tree, []):
                        if idx < len(p_node.inputs) and p_node.inputs[idx].is_linked:
                            stack.append((p_node.inputs[idx], p_tree))
                continue
            if src.type == "GROUP" and src.node_tree:
                idx = next((i for i, out in enumerate(src.outputs) if out == fsock), None)
                if idx is not None:
                    for go in src.node_tree.nodes:
                        if go.type == "GROUP_OUTPUT" and idx < len(go.inputs) and go.inputs[idx].is_linked:
                            stack.append((go.inputs[idx], src.node_tree))
                            break
                continue
    return False


def first_bump_strength(node_tree, _seen=None):
    """Strength of the first Bump node reachable (recursing groups), or None — the
    fallback source for normalMapAmplification when the Normal slot is fed by a Bump
    node rather than a Normal Map node."""
    if _seen is None:
        _seen = set()
    if node_tree is None or id(node_tree) in _seen:
        return None
    _seen.add(id(node_tree))
    for n in node_tree.nodes:
        if n.type == "BUMP":
            s = n.inputs.get("Strength")
            if s is not None:
                try:
                    return float(s.default_value)
                except (AttributeError, TypeError):
                    return None
        if n.type == "GROUP" and n.node_tree:
            r = first_bump_strength(n.node_tree, _seen)
            if r is not None:
                return r
    return None


def get_mapping_data(mapping_node):
    """Return (location, rotation_degrees, scale) from a Mapping node, or None if non-standard."""
    try:
        loc = mapping_node.inputs['Location'].default_value
        rot = mapping_node.inputs['Rotation'].default_value
        sca = mapping_node.inputs['Scale'].default_value
    except (KeyError, AttributeError, IndexError):
        return None
    rot_deg = (math.degrees(rot[0]), math.degrees(rot[1]), math.degrees(rot[2]))
    return loc, rot_deg, sca


def resolve_image_file(image):
    """Parser-friendly 'File' value: an absolute path, or a <marker> for non-file images."""
    if image.source == 'GENERATED':
        return "<generated>"
    if image.packed_file is not None:
        return "<packed>"
    if not image.filepath:
        return "<missing>"
    abspath = bpy.path.abspath(image.filepath, library=image.library)
    if image.source == 'TILED':
        return "[UDIM] " + abspath
    return abspath


def scan_tree(node_tree, textures, principled_nodes, normal_maps, visited_trees=None):
    """Recursively collect (node, tree) tuples for TEX_IMAGE / BSDF_PRINCIPLED / NORMAL_MAP."""
    if visited_trees is None:
        visited_trees = set()
    if node_tree in visited_trees:
        return
    visited_trees.add(node_tree)
    for node in node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            textures.append((node, node_tree))
        elif node.type == 'BSDF_PRINCIPLED':
            principled_nodes.append((node, node_tree))
        elif node.type == 'NORMAL_MAP':
            normal_maps.append((node, node_tree))
        elif node.type == 'GROUP' and node.node_tree:
            scan_tree(node.node_tree, textures, principled_nodes, normal_maps, visited_trees)


def objects_using_material(mat):
    """Names of all objects that use `mat` in a material slot (sorted)."""
    result = []
    for obj in bpy.data.objects:
        for slot in obj.material_slots:
            if slot.material == mat:
                result.append(obj.name)
                break
    return sorted(result)


# ---------------------------------------------------------------------------
# Active-output-anchored surface-shader walk (material type / refraction).
#
# scan_tree above collects *every* Principled/texture node in *every* tree;
# that is correct for gathering textures, but the material `type`
# (Opaque/Masked/Transparent) and `refraction` must follow only the shaders
# that actually reach the *active* Material Output's Surface — through
# Reroute / Mix / Add / node groups. This block adds that walk; it returns a
# plain dict (no bpy refs) so introspect/mapper stay data-only.
# See docs/plans/features/material-type-fidelity/plan.md.
# ---------------------------------------------------------------------------

# Terminal shader node.types we record (anything not a passthrough/mix).
_PASSTHROUGH = {'REROUTE', 'GROUP', 'GROUP_INPUT', 'GROUP_OUTPUT', 'MIX_SHADER', 'ADD_SHADER'}


def _socket_scalar(socket):
    """A socket's scalar default (float), or None for color/vector/unreadable."""
    try:
        v = socket.default_value
    except AttributeError:
        return None
    if hasattr(v, '__len__'):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _socket_color(socket):
    """A socket's RGBA default as {r,g,b,a}, or None if not a color/vector socket."""
    try:
        v = socket.default_value
    except AttributeError:
        return None
    if not hasattr(v, '__len__') or len(v) < 3:
        return None
    return {"r": v[0], "g": v[1], "b": v[2], "a": v[3] if len(v) > 3 else 1}


def _resolve_input(socket, tree, parent_map):
    """Classify what drives an input socket.

    Returns ``(dynamic, value)``:
      * ``dynamic=True``  -> a spatially-varying / non-static source (texture, ramp,
        math, fresnel, ...) drives it; ``value`` is None.
      * ``dynamic=False`` -> it resolves to a constant; ``value`` is that float
        (its own default, or an upstream Value node, chased through Reroute /
        Group Input one branch at a time).
    """
    cur, ctree = socket, tree
    seen = set()
    while True:
        if not cur.is_linked:
            return (False, _socket_scalar(cur))
        key = (id(ctree), id(cur))
        if key in seen:
            return (True, None)
        seen.add(key)
        link = cur.links[0]
        src, fsock = link.from_node, link.from_socket
        if src.type == 'REROUTE':
            cur = src.inputs[0]
            continue
        if src.type == 'VALUE':
            try:
                return (False, float(src.outputs[0].default_value))
            except (AttributeError, TypeError, ValueError):
                return (True, None)
        if src.type == 'GROUP_INPUT':
            idx = next((i for i, o in enumerate(src.outputs) if o == fsock), None)
            parents = parent_map.get(ctree, [])
            if idx is not None and parents:
                p_node, p_tree = parents[0]
                if idx < len(p_node.inputs):
                    cur, ctree = p_node.inputs[idx], p_tree
                    continue
            return (True, None)
        return (True, None)


def _resolve_color(socket, tree, parent_map):
    """RGBA dict a color socket resolves to as a constant (its own default, or upstream
    through Reroute / RGB node / single Group-Input hop), or None if dynamic/unreadable.
    The color sibling of ``_resolve_input`` — used to read a linked-but-static Base Color
    or Emission into the flat WL color instead of leaving the default."""
    cur, ctree = socket, tree
    seen = set()
    while True:
        if not cur.is_linked:
            return _socket_color(cur)
        key = (id(ctree), id(cur))
        if key in seen:
            return None
        seen.add(key)
        link = cur.links[0]
        src, fsock = link.from_node, link.from_socket
        if src.type == 'REROUTE':
            cur = src.inputs[0]
            continue
        if src.type == 'RGB':
            try:
                v = src.outputs[0].default_value
                return {"r": v[0], "g": v[1], "b": v[2], "a": v[3] if len(v) > 3 else 1}
            except (AttributeError, TypeError, IndexError):
                return None
        if src.type == 'GROUP_INPUT':
            idx = next((i for i, o in enumerate(src.outputs) if o == fsock), None)
            parents = parent_map.get(ctree, [])
            if idx is not None and parents:
                p_node, p_tree = parents[0]
                if idx < len(p_node.inputs):
                    cur, ctree = p_node.inputs[idx], p_tree
                    continue
        return None


def find_active_output(tree):
    """The Material Output that drives the render: prefer an EEVEE/ALL-targeted one
    (the plugin targets EEVEE-Next — a Cycles-only output must not win), then the
    active flag, then the first. None if the tree has no Material Output."""
    outs = [n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL']
    if not outs:
        return None
    pool = [n for n in outs if getattr(n, 'target', 'ALL') in ('EEVEE', 'ALL')] or outs
    for n in pool:
        if getattr(n, 'is_active_output', False):
            return n
    return pool[0]


def _active_group_output(node_tree):
    """The live GROUP_OUTPUT inside a node group (active flag, else first). None if
    the group exposes no output — iterating *every* GROUP_OUTPUT would pull shaders
    from a dead/inactive output and mis-classify the material."""
    gos = [n for n in node_tree.nodes if n.type == 'GROUP_OUTPUT']
    if not gos:
        return None
    for n in gos:
        if getattr(n, 'is_active_output', False):
            return n
    return gos[0]


def _read_principled(node, tree, parent_map, out):
    """Record the first reached Principled's Alpha / Transmission / IOR (resolved)."""
    if out["_principledReached"]:
        return  # first reached wins (mirrors introspect's first-unlinked-wins)
    out["_principledReached"] = True
    alpha_in = node.inputs.get('Alpha')
    if alpha_in is not None:
        dyn, val = _resolve_input(alpha_in, tree, parent_map)
        if dyn:
            out["alphaLinked"] = True
        else:
            out["principledAlpha"] = val
    trans_in = node.inputs.get('Transmission Weight') or node.inputs.get('Transmission')
    if trans_in is not None:
        dyn, val = _resolve_input(trans_in, tree, parent_map)
        if dyn:
            out["transmissionLinked"] = True
        else:
            out["principledTransmission"] = val
            out["transmissionStaticValue"] = val
    ior_in = node.inputs.get('IOR')
    if ior_in is not None:
        dyn, val = _resolve_input(ior_in, tree, parent_map)
        if not dyn:
            out["principledIor"] = val


def _read_refractive(node, tree, parent_map, out):
    """Record a Glass/Refraction node's IOR / Color / Roughness (group-resolved IOR)."""
    if out["refractiveIor"] is None:
        ior_in = node.inputs.get('IOR')
        if ior_in is not None:
            _dyn, val = _resolve_input(ior_in, tree, parent_map)
            if val is not None:
                out["refractiveIor"] = val
    if out["refractiveColor"] is None:
        c = node.inputs.get('Color')
        if c is not None:
            out["refractiveColor"] = _socket_color(c)
    if out["refractiveRoughness"] is None:
        r = node.inputs.get('Roughness')
        if r is not None:
            _dyn, val = _resolve_input(r, tree, parent_map)
            if val is not None:
                out["refractiveRoughness"] = val


def trace_surface_shaders(mat_tree, parent_map):
    """Walk backward from the active Material Output's Surface, through Reroute /
    Mix / Add / GROUP / GROUP_INPUT / GROUP_OUTPUT, collecting the effective shader
    set and the transparency-relevant signals. Returns a plain (bpy-free) dict."""
    out = {
        "shaderTypes": [],
        "alphaLinked": False,
        "transmissionLinked": False,
        "transmissionStaticValue": None,
        "refractiveIor": None,
        "refractiveColor": None,
        "refractiveRoughness": None,
        "maskedFacMix": False,
        # internal scalars introspect prefers over the legacy (unanchored) reads:
        "_principledReached": False,
        "principledAlpha": None,
        "principledTransmission": None,
        "principledIor": None,
    }
    output = find_active_output(mat_tree)
    surf = output.inputs.get('Surface') if output else None
    if not surf or not surf.is_linked:
        out.pop("_principledReached", None)
        return out

    shader_types = set()
    has_linked_fac_mix = False
    stack = [(surf, mat_tree)]
    visited = set()
    while stack:
        socket, tree = stack.pop()
        key = (id(tree), id(socket))
        if key in visited:
            continue
        visited.add(key)
        for link in socket.links:
            node = link.from_node
            t = node.type
            if t == 'REROUTE':
                if node.inputs and node.inputs[0].is_linked:
                    stack.append((node.inputs[0], tree))
                continue
            if t == 'GROUP' and node.node_tree:
                go = _active_group_output(node.node_tree)
                if go:
                    idx = next((i for i, o in enumerate(node.outputs) if o == link.from_socket), None)
                    if idx is not None and idx < len(go.inputs) and go.inputs[idx].is_linked:
                        stack.append((go.inputs[idx], node.node_tree))
                continue
            if t == 'GROUP_INPUT':
                idx = next((i for i, o in enumerate(node.outputs) if o == link.from_socket), None)
                if idx is not None:
                    for p_node, p_tree in parent_map.get(tree, []):
                        if idx < len(p_node.inputs) and p_node.inputs[idx].is_linked:
                            stack.append((p_node.inputs[idx], p_tree))
                continue
            if t == 'GROUP_OUTPUT':
                continue
            if t == 'MIX_SHADER':
                fac = node.inputs[0] if len(node.inputs) > 0 else None
                in1 = node.inputs[1] if len(node.inputs) > 1 else None
                in2 = node.inputs[2] if len(node.inputs) > 2 else None
                fdyn, fval = _resolve_input(fac, tree, parent_map) if fac is not None else (False, 0.5)
                if fdyn:
                    has_linked_fac_mix = True
                    if in1 and in1.is_linked:
                        stack.append((in1, tree))
                    if in2 and in2.is_linked:
                        stack.append((in2, tree))
                else:
                    fv = fval if fval is not None else 0.5
                    if fv < 1.0 and in1 and in1.is_linked:
                        stack.append((in1, tree))
                    if fv > 0.0 and in2 and in2.is_linked:
                        stack.append((in2, tree))
                continue
            if t == 'ADD_SHADER':
                for ai in (node.inputs[0] if len(node.inputs) > 0 else None,
                           node.inputs[1] if len(node.inputs) > 1 else None):
                    if ai and ai.is_linked:
                        stack.append((ai, tree))
                continue
            # terminal shader node
            shader_types.add(t)
            if t == 'BSDF_PRINCIPLED':
                _read_principled(node, tree, parent_map, out)
            elif t in ('BSDF_GLASS', 'BSDF_REFRACTION'):
                _read_refractive(node, tree, parent_map, out)
            elif t in ('BSDF_TRANSPARENT', 'BSDF_TRANSLUCENT'):
                if out["refractiveColor"] is None:
                    c = node.inputs.get('Color')
                    if c is not None:
                        out["refractiveColor"] = _socket_color(c)

    # A linked-Fac Mix that gates a transparent-ish branch against an opaque one is a
    # per-pixel cutout -> Masked (rule 3). A Glass branch still wins Transparent in the
    # mapper (rule 1 is tested first), per the documented owner decision.
    transp_ish = bool(shader_types & {'BSDF_TRANSPARENT', 'BSDF_TRANSLUCENT', 'BSDF_GLASS', 'BSDF_REFRACTION'})
    has_opaque = any(s not in {'BSDF_TRANSPARENT', 'BSDF_TRANSLUCENT', 'BSDF_GLASS', 'BSDF_REFRACTION'}
                     for s in shader_types)
    out["maskedFacMix"] = has_linked_fac_mix and transp_ish and has_opaque
    out["shaderTypes"] = sorted(shader_types)
    out.pop("_principledReached", None)
    return out
