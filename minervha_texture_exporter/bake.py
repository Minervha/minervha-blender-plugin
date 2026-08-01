"""bake.py — flatten a procedural channel to pixels via a Cycles bake.

The fallback rung: when a channel is driven by a node graph (Mix / Math / ColorRamp /
Noise) there is no texture to copy, so the channel is rendered instead. Approach and its
rationale are the ones the sibling material exporter validated live on Blender 5.1.2:

  * Bake in an ISOLATED throwaway scene — `bpy.ops.object.bake()` syncs the whole active
    scene to the render device on every call, so baking inside the user's heavy scene
    re-uploads every object per bake.
  * Bake on a throwaway plane whose UVs fill the whole [0,1]² tile, NEVER a scene mesh —
    the flattened result then covers the entire UV domain and is correct for every mesh
    that shares the material (a scene mesh would only fill its own islands).
  * Inputs with no dedicated Cycles pass (Metallic) are captured by temporarily routing
    their source graph through an Emission shader and baking EMIT.
  * The target image is tagged BEFORE the bake: Cycles encodes its linear render into the
    target's colorspace as it fills the buffer (a pixel written by hand, in contrast, is
    stored verbatim — see images.py). The caller then either saves the result (diffuse,
    normal) or reads its channel straight into the ORM (roughness, metallic).

Non-destructive by contract: every bake snapshots what it touches and restores it in a
`finally` — only throwaway datablocks are added, and the material's node tree is put back.
"""

import os
from contextlib import contextmanager

try:
    import bpy
except ImportError:                 # importable for syntax check without Blender
    bpy = None


# channel -> (cycles bake type, target colorspace, Principled input to EMIT-rewire | None).
# A None rewire-input means a native Cycles pass reads the whole shader (Surface left
# intact); a named input means that input's *source graph* is routed through an Emission
# and baked EMIT (the universal flattener for inputs with no dedicated pass).
_CHANNEL_BAKE = {
    "diffuse":   ("DIFFUSE",   "sRGB",      None),
    "roughness": ("ROUGHNESS", "Non-Color", None),
    "metallic":  ("EMIT",      "Non-Color", "Metallic"),
    "normal":    ("NORMAL",    "Non-Color", None),
}


def can_bake():
    """Cycles must be available to bake (EEVEE-Next does not bake)."""
    return bpy is not None and "cycles" in bpy.context.preferences.addons


def _gpu_available():
    """True if Cycles has a configured, enabled non-CPU compute device, so the bake can
    run on the GPU instead of the CPU."""
    cprefs = bpy.context.preferences.addons.get("cycles")
    if not cprefs:
        return False
    cp = cprefs.preferences
    if getattr(cp, "compute_device_type", "NONE") in (None, "NONE"):
        return False
    try:
        cp.refresh_devices()
    except Exception:
        pass
    return any(getattr(d, "use", False) and d.type != "CPU" for d in getattr(cp, "devices", []))


@contextmanager
def bake_environment(samples=1):
    """Yield a throwaway ISOLATED scene holding ONLY the bake plane, configured for a fast
    unlit CYCLES bake on the GPU when one is available. Removed on exit; the user's scene
    and its render settings are never touched."""
    scene = bpy.data.scenes.new("_mvtex_bake_scene")
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = False
        if _gpu_available():
            scene.cycles.device = "GPU"
        yield scene
    finally:
        if scene.name in bpy.data.scenes:
            bpy.data.scenes.remove(scene)


@contextmanager
def _placeholder_plane(mat, scene):
    """Yield a throwaway unit plane whose UV map fills the whole [0,1]² tile, carrying only
    `mat`, linked into the isolated bake `scene`. Fully removed on exit."""
    mesh = bpy.data.meshes.new("_mvtex_bake_plane")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
                     [], [(0, 1, 2, 3)])
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    for loop_idx, co in enumerate([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]):
        uv.data[loop_idx].uv = co
    mesh.materials.append(mat)
    obj = bpy.data.objects.new("_mvtex_bake_plane", mesh)
    obj.hide_render = False
    scene.collection.objects.link(obj)
    try:
        # Update the ISOLATED scene's view layer only — `bpy.context.view_layer` is the
        # user's (possibly huge) scene, and re-evaluating its depsgraph per bake is both
        # wasted time and the wrong scene.
        scene.view_layers[0].update()
        yield obj
    finally:
        try:
            scene.collection.objects.unlink(obj)
        except Exception:
            pass
        if obj.name in bpy.data.objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.name in bpy.data.meshes and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _bake_into(plane, mat, img, bake_type, scene, **kw):
    """Add a target Image Texture node bound to `img`, make it active, then bake the plane
    IN THE ISOLATED scene via a context override. Removes the node afterwards."""
    tree = mat.node_tree
    node = tree.nodes.new("ShaderNodeTexImage")
    node.image = img
    try:
        for n in tree.nodes:
            n.select = False
        node.select = True
        tree.nodes.active = node
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0],
                                       active_object=plane, object=plane,
                                       selected_objects=[plane],
                                       selected_editable_objects=[plane]):
            bpy.ops.object.bake(type=bake_type, **kw)
    finally:
        tree.nodes.remove(node)


def _principled_of(mat):
    return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)


def _active_output(mat):
    outs = [n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL"]
    if not outs:
        return None
    return next((n for n in outs if getattr(n, "is_active_output", False)), outs[0])


def _emit_rewire(mat, input_name):
    """Route the graph feeding a Principled input through a temporary Emission -> Surface
    so an EMIT bake captures that input. Returns a restore() callable, or None when the
    input is unlinked (a constant — the caller writes the scalar instead of baking)."""
    principled, out = _principled_of(mat), _active_output(mat)
    if principled is None or out is None:
        return None
    inp = principled.inputs.get(input_name)
    if inp is None or not inp.is_linked:
        return None
    tree = mat.node_tree
    surface = out.inputs["Surface"]
    stash = surface.links[0].from_socket if surface.is_linked else None
    emit = tree.nodes.new("ShaderNodeEmission")
    tree.links.new(inp.links[0].from_socket, emit.inputs["Color"])
    tree.links.new(emit.outputs["Emission"], surface)

    def restore():
        if emit.name in tree.nodes:
            tree.nodes.remove(emit)
        if stash is not None:
            tree.links.new(stash, surface)
    return restore


def bake_channel(mat, channel, width, height, scene):
    """Bake one channel of `mat` at `width`×`height` and return the resulting bpy Image,
    tagged with its channel's colorspace — the CALLER owns it and must remove it.

    The size is taken rather than fixed square so an ORM channel can be baked straight at
    the ORM's composition size, with no resampling step in between.

    Returns None when the channel isn't bakeable here (unknown channel, or an EMIT-rewire
    input that turned out to be constant). Must run inside `bake_environment()`."""
    spec = _CHANNEL_BAKE.get(channel)
    if spec is None:
        return None
    bake_type, colorspace, rewire_input = spec
    img = bpy.data.images.new("_mvtex_bake_%s_%s" % (mat.name, channel), width, height,
                              alpha=False)
    img.colorspace_settings.name = colorspace
    restore = None
    try:
        kw = {}
        if rewire_input is not None:
            restore = _emit_rewire(mat, rewire_input)
            if restore is None:
                return None
        elif bake_type == "DIFFUSE":
            kw["pass_filter"] = {"COLOR"}          # albedo only, no lighting
        elif bake_type == "NORMAL":
            kw["normal_space"] = "TANGENT"
        with _placeholder_plane(mat, scene) as plane:
            _bake_into(plane, mat, img, bake_type, scene, **kw)
        return img
    except Exception:
        if img.name in bpy.data.images:
            bpy.data.images.remove(img, do_unlink=True)
        raise
    finally:
        if restore is not None:
            restore()


def save_baked(img, path):
    """Write a baked image to `path` as PNG (its colorspace tag drives the encoding)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.file_format = "PNG"
    img.filepath_raw = path
    img.save()
    return os.path.isfile(path) and os.path.getsize(path) > 0
