"""sources.py — resolve each PBR channel of a material to its exportable source.

Backward-traces a Principled BSDF input (the opposite direction from the sibling
material exporter's forward trace) to one of three verdicts:

  {"kind": "image", "image": <bpy Image>, "channel": int|None, "packed": bool}
      an Image Texture reached through ONLY passthrough nodes — the texture IS this
      channel's map. `channel` is the pixel channel to read (0=R 1=G 2=B 3=A; None = the
      whole colour). `packed` is True when the value came out of a Separate node, i.e.
      the image is a packed multi-channel map — the ONLY signal we accept as proof that
      an AO channel exists next to roughness/metallic.
  {"kind": "const", "value": float | (r, g, b, a)}
      unlinked, or driven by a Value/RGB node through passthroughs — a flat value.
  {"kind": "procedural"}
      anything else (Mix / Math / ColorRamp / Noise / a texture with no image ...) — only
      a bake can flatten it.

Passthrough = a node that does not change the texture's role as this channel's data:
Reroute, Normal Map (it only re-interprets the map), Separate Color/RGB (it picks a
channel of it). Groups are structural and are walked through. Everything else transforms
the data, so the texture upstream of it is an *input* to a graph, not the channel's map.

No `bpy` import: this module only walks objects handed to it, so its helpers stay
unit-testable with plain stub nodes.
"""


# Principled BSDF input name per exported channel. "diffuse"/"normal" ship as their own
# file; "roughness"/"metallic" are packed into the ORM alongside the inferred AO.
CHANNEL_INPUTS = {
    "diffuse": "Base Color",
    "roughness": "Roughness",
    "metallic": "Metallic",
    "normal": "Normal",
}

_SEPARATE = {"SEPARATE_COLOR", "SEPARATE_RGB"}

# Output socket name -> pixel channel index. Covers both the Separate Color node
# (Red/Green/Blue) and an Image Texture's own Alpha output.
SOCKET_CHANNEL = {"Red": 0, "Green": 1, "Blue": 2, "Alpha": 3,
                  "R": 0, "G": 1, "B": 2, "X": 0, "Y": 1, "Z": 2}

# Node-graph walk guard. Blender node trees are acyclic, so this only bounds pathological
# depth (long Reroute/group chains); hitting it means "too convoluted to read" = procedural.
_MAX_DEPTH = 64


def build_parent_map(root_tree):
    """Map each nested node-group tree -> [(group_node, containing_tree)], so a
    GROUP_INPUT can be walked back up into the tree(s) that instance the group."""
    parent_map = {}
    seen = set()

    def walk(tree):
        if tree is None or tree.name in seen:
            return
        seen.add(tree.name)
        for node in tree.nodes:
            if node.type == "GROUP" and node.node_tree:
                parent_map.setdefault(node.node_tree, []).append((node, tree))
                walk(node.node_tree)

    walk(root_tree)
    return parent_map


def _socket_default(socket):
    """A socket's unlinked default as a float or an (r,g,b,a) tuple, or None."""
    try:
        v = socket.default_value
    except AttributeError:
        return None
    if hasattr(v, "__len__"):
        v = tuple(v)
        return v if len(v) >= 4 else v + (1.0,) * (4 - len(v))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _socket_index(node, socket):
    """Index of `socket` among `node.outputs`, or None."""
    return next((i for i, o in enumerate(node.outputs) if o == socket), None)


def _active_group_output(node_tree):
    """The live GROUP_OUTPUT of a node group (active flag, else the first), or None —
    reading a dead output would resolve the channel from a graph that isn't rendered."""
    outs = [n for n in node_tree.nodes if n.type == "GROUP_OUTPUT"]
    if not outs:
        return None
    return next((n for n in outs if getattr(n, "is_active_output", False)), outs[0])


def resolve_input(socket, tree, parent_map):
    """Backward-trace one Principled input socket to its source verdict (see module doc)."""
    channel = None          # set once a Separate node tells us which channel we're after
    cur, ctree = socket, tree
    for _ in range(_MAX_DEPTH):
        if cur is None:
            return {"kind": "procedural"}
        if not cur.is_linked:
            return {"kind": "const", "value": _socket_default(cur)}
        link = cur.links[0]
        node, from_socket = link.from_node, link.from_socket
        kind = node.type

        if kind == "TEX_IMAGE":
            if node.image is None:
                return {"kind": "procedural"}       # an empty Image node has nothing to export
            # A Separate node upstream already named the channel; otherwise the socket does
            # (an image's Alpha output), and a plain Color output means "the whole colour".
            ch = channel if channel is not None else SOCKET_CHANNEL.get(from_socket.name)
            return {"kind": "image", "image": node.image, "channel": ch,
                    "packed": channel is not None}
        if kind == "REROUTE":
            cur = node.inputs[0]
            continue
        if kind == "NORMAL_MAP":
            cur = node.inputs.get("Color")
            continue
        if kind in _SEPARATE:
            if channel is None:                     # the Separate nearest the BSDF wins
                channel = SOCKET_CHANNEL.get(from_socket.name)
            cur = node.inputs[0]
            continue
        if kind in ("VALUE", "RGB"):
            return {"kind": "const", "value": _socket_default(node.outputs[0])}
        if kind == "GROUP" and node.node_tree:
            out = _active_group_output(node.node_tree)
            idx = _socket_index(node, from_socket)
            if out is not None and idx is not None and idx < len(out.inputs):
                cur, ctree = out.inputs[idx], node.node_tree
                continue
            return {"kind": "procedural"}
        if kind == "GROUP_INPUT":
            idx = _socket_index(node, from_socket)
            parents = parent_map.get(ctree) or []
            if idx is not None and parents:
                p_node, p_tree = parents[0]         # one instance is enough to read a value
                if idx < len(p_node.inputs):
                    cur, ctree = p_node.inputs[idx], p_tree
                    continue
            return {"kind": "procedural"}
        return {"kind": "procedural"}               # transforming node — bake to flatten
    return {"kind": "procedural"}


def principled_of(mat):
    """(Principled BSDF node, the tree it lives in) driving the material's active output,
    or (None, None).

    Follows the active Material Output's Surface through Reroutes and node groups — the
    BSDF is often nested inside a group, so the tree is returned with it rather than
    guessed later. If that shader isn't a Principled (mixed / other shader trees), falls
    back to the first Principled found anywhere in the tree, so an unusual graph still
    exports its PBR inputs instead of nothing."""
    tree = getattr(mat, "node_tree", None)
    if not mat or not getattr(mat, "use_nodes", False) or tree is None:
        return None, None
    outs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"]
    pool = [n for n in outs if getattr(n, "target", "ALL") in ("EEVEE", "ALL")] or outs
    out = next((n for n in pool if getattr(n, "is_active_output", False)),
               pool[0] if pool else None)
    if out is not None:
        socket, ctree = out.inputs.get("Surface"), tree
        for _ in range(_MAX_DEPTH):
            if socket is None or not socket.is_linked:
                break
            link = socket.links[0]
            node = link.from_node
            if node.type == "BSDF_PRINCIPLED":
                return node, ctree
            if node.type == "REROUTE":
                socket = node.inputs[0]
                continue
            if node.type == "GROUP" and node.node_tree:
                go = _active_group_output(node.node_tree)
                idx = _socket_index(node, link.from_socket)
                if go is not None and idx is not None and idx < len(go.inputs):
                    socket, ctree = go.inputs[idx], node.node_tree
                    continue
            break
    for candidate_tree in _all_trees(tree):
        node = next((n for n in candidate_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if node is not None:
            return node, candidate_tree
    return None, None


def _all_trees(root_tree):
    """`root_tree` and every node-group tree nested under it (depth-first, deduped)."""
    seen, stack, out = set(), [root_tree], []
    while stack:
        tree = stack.pop()
        if tree is None or tree.name in seen:
            continue
        seen.add(tree.name)
        out.append(tree)
        for node in tree.nodes:
            if node.type == "GROUP" and getattr(node, "node_tree", None):
                stack.append(node.node_tree)
    return out


def resolve_material(mat):
    """{channel: source} for the four exported channels, or None if the material has no
    Principled BSDF (nothing to read — the caller reports it as skipped)."""
    principled, tree = principled_of(mat)
    if principled is None:
        return None
    parent_map = build_parent_map(mat.node_tree)
    out = {}
    for channel, input_name in CHANNEL_INPUTS.items():
        socket = principled.inputs.get(input_name)
        out[channel] = ({"kind": "none"} if socket is None
                        else resolve_input(socket, tree, parent_map))
    return out


def max_source_dim(mat):
    """Largest pixel dimension among the material's source images (walking node groups),
    0 if it has none. Drives the material's reference size for flat/baked maps."""
    tree = getattr(mat, "node_tree", None)
    if tree is None:
        return 0
    best = 0
    for nt in _all_trees(tree):
        for node in nt.nodes:
            if node.type == "TEX_IMAGE" and getattr(node, "image", None) is not None:
                try:
                    best = max(best, int(node.image.size[0]), int(node.image.size[1]))
                except (AttributeError, IndexError, TypeError):
                    pass
    return best


def materials_of(objects):
    """Unique materials used by `objects`, in first-seen order (a material shared by
    several selected meshes is exported once). Empty slots are skipped."""
    out, seen = [], set()
    for obj in objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is not None and mat.name not in seen:
                seen.add(mat.name)
                out.append(mat)
    return out
