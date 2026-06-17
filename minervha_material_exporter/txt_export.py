"""txt_export.py — write the texture_usage.txt (mode A), byte-identical to the script.

This serializer reproduces MaterialPrint_hardened.py's output exactly, so the file
stays a drop-in input for Minervha Studio's blenderParse.js (no regression). It reuses
the shared bsdf_trace helpers — the same tracing core introspect.py uses — so mode A
and mode B always read the node graph the same way.

The parser drops mapping rotation; we still emit it (Rot(...)) to match the script
byte-for-byte. Floats use the script's display precision (%.2f colors/mapping, %.3f
scalars). Output is UTF-8, LF, no BOM.
"""

import os

try:
    from . import bsdf_trace          # packaged extension
except ImportError:                    # dev / sys.path import (tests, live MCP)
    import bsdf_trace


def build_text(materials):
    """Return the full texture_usage.txt content for an iterable of Blender materials."""
    out = []
    for mat in materials:
        if not (mat.use_nodes and mat.node_tree):
            out.append("=" * 80 + "\n")
            out.append("Material: " + mat.name + "\n")
            out.append("No node tree (skipped)\n\n")
            continue

        out.append("=" * 80 + "\n")
        out.append("Material: " + mat.name + "\n")

        users = bsdf_trace.objects_using_material(mat)
        if users:
            out.append("Objects:\n")
            for name in users:
                out.append("  - " + name + "\n")
        out.append("\n")

        parent_map = bsdf_trace.build_local_parent_map(mat.node_tree)
        textures, principled, normal_maps = [], [], []
        bsdf_trace.scan_tree(mat.node_tree, textures, principled, normal_maps)

        # 1. Principled BSDF base properties (unlinked only).
        if principled:
            has_unlinked = False
            lines = []
            for p_node, p_tree in principled:
                bc = p_node.inputs.get('Base Color')
                met = p_node.inputs.get('Metallic')
                rough = p_node.inputs.get('Roughness')
                em = p_node.inputs.get('Emission Color') or p_node.inputs.get('Emission')
                em_str = p_node.inputs.get('Emission Strength')
                bc_un = bc and not bc.is_linked
                met_un = met and not met.is_linked
                rough_un = rough and not rough.is_linked
                em_un = em and not em.is_linked
                em_str_un = em_str and not em_str.is_linked
                if any([bc_un, met_un, rough_un, em_un, em_str_un]):
                    has_unlinked = True
                    tree_name = "Root" if p_tree == mat.node_tree else "Group: " + p_tree.name
                    lines.append("  Node: " + p_node.name + " (" + tree_name + ")\n")
                    if bc_un:
                        c = bc.default_value
                        lines.append("    Base Color       : (R: %.2f, G: %.2f, B: %.2f, A: %.2f)\n"
                                     % (c[0], c[1], c[2], c[3]))
                    if met_un:
                        lines.append("    Metallic         : %.3f\n" % met.default_value)
                    if rough_un:
                        lines.append("    Roughness        : %.3f\n" % rough.default_value)
                    if em_un:
                        c = em.default_value
                        lines.append("    Emission Color   : (R: %.2f, G: %.2f, B: %.2f, A: %.2f)\n"
                                     % (c[0], c[1], c[2], c[3]))
                    if em_str_un:
                        lines.append("    Emission Strength: %.3f\n" % em_str.default_value)
            if has_unlinked:
                out.append("--- Base Properties (Unlinked) ---\n")
                out.extend(lines)
                out.append("\n")

        # 2. Normal Map nodes.
        if normal_maps:
            out.append("--- Normal Map Nodes ---\n")
            for nm_node, nm_tree in normal_maps:
                strength = nm_node.inputs.get('Strength')
                if strength:
                    linked = " (Driven by link)" if strength.is_linked else ""
                    tree_name = "Root" if nm_tree == mat.node_tree else "Group: " + nm_tree.name
                    out.append("  Node: " + nm_node.name + " (" + tree_name + ")\n")
                    out.append("    Strength: %.3f%s\n" % (strength.default_value, linked))
            out.append("\n")

        # 3. Textures, their usage and mapping.
        if not textures:
            out.append("No textures found\n\n")
            continue

        out.append("--- Textures ---\n")
        for tex_node, tree in textures:
            slots = bsdf_trace.trace_from_texture(tex_node, tree, parent_map)
            image = tex_node.image
            out.append("Texture : " + image.name + "\n")
            out.append("File    : " + bsdf_trace.resolve_image_file(image) + "\n")
            if slots:
                out.append("Slots   : " + ", ".join(slots) + "\n")
            else:
                out.append("Slots   : UNKNOWN (or not Principled)\n")
            mnode = bsdf_trace.find_mapping_for_texture(tex_node, tree, parent_map)
            mdata = bsdf_trace.get_mapping_data(mnode) if mnode else None
            if mdata:
                loc, rot, sca = mdata
                out.append("Mapping : Loc(%.2f, %.2f, %.2f) | "
                           "Rot(%.1f°, %.1f°, %.1f°) | "
                           "Scale(%.2f, %.2f, %.2f)\n"
                           % (loc[0], loc[1], loc[2], rot[0], rot[1], rot[2], sca[0], sca[1], sca[2]))
            else:
                out.append("Mapping : None / Default\n")
            out.append("\n")

    return "".join(out)


def export_txt(materials, filepath):
    """Write the .txt to `filepath` (UTF-8, LF, atomic tmp->replace). Returns count of blocks."""
    text = build_text(materials)
    tmp = filepath + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        if os.path.exists(filepath):
            os.remove(filepath)
        os.replace(tmp, filepath)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    return text.count("Material: ")
