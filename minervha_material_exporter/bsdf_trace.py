"""bsdf_trace.py — Blender node-tracing helpers.

Behavior-preserving port of the tracing logic in the original Blender material-export
script by **Nocna Klacz** (MaterialPrint_hardened.py) — used here by introspect.py to
read each material's Principled BSDF, textures, slots and mapping. Big thanks to Nocna
Klacz for the base this is built on.

Blender-only module (imports bpy for image-path resolution).
"""

import math
import bpy


def find_group_input(node_tree):
    for node in node_tree.nodes:
        if node.type == 'GROUP_INPUT':
            return node
    return None


def find_group_output(node_tree):
    for node in node_tree.nodes:
        if node.type == 'GROUP_OUTPUT':
            return node
    return None


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
                        gi = find_group_input(group_tree)
                        if gi and idx < len(gi.outputs):
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
                    go = find_group_output(source_node.node_tree)
                    if go and idx < len(go.inputs) and go.inputs[idx].is_linked:
                        stack.append((go.inputs[idx], source_node.node_tree))
                continue
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
