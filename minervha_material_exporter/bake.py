"""bake.py — flatten arbitrary node graphs to per-channel PBR textures via Cycles bake.

Net-new, bpy-side. The ONLY module that renders. It bakes the channels mapper.py flagged as
`report["bakeCandidates"]` (procedural / multi-texture / divergent-UV / packed) into flat PNGs
the WL `customMaterial` can hold — the rung **B** of the graceful-degradation ladder.

Non-destructive by contract: every bake snapshots render state, adds ONLY throwaway datablocks
(a target Image + an Image Texture node, a temporary Emission rewire), and restores everything
in a ``finally``. `mapper.py`/`introspect.py` stay pure — this module is invoked from the
bpy-side export pipeline (`wlsave_export`) and hands back on-disk PNG paths.

Validated live on Blender 5.1.2 (see docs/plans/features/shading-compatibility/chunk-07):
  - EEVEE cannot bake -> swap to CYCLES and restore the literal engine string.
  - EMIT-rewire faithfully captures a value (const 0.33 -> pixel 0.33 in a Non-Color buffer).
  - DIFFUSE/COLOR captures a procedural (Noise -> variance 0.46).
  - Colorspace is a SAVE-time encoding (the bake buffer is linear regardless); set it on the
    target image BEFORE saving: sRGB for color maps, Non-Color for data maps.
"""

import os
from contextlib import contextmanager

try:
    import bpy
except ImportError:                 # importable for syntax check without Blender
    bpy = None


# channel -> (cycles bake type, target colorspace, Principled input to EMIT-rewire | None).
# A None rewire-input means a native Cycles pass reads the whole shader (Surface left intact);
# a named input means that input's *source graph* is routed through an Emission and baked EMIT
# (the universal flattener for inputs with no dedicated pass — e.g. Metallic).
_CHANNEL_BAKE = {
    "diffuse":   ("DIFFUSE",   "sRGB",      None),
    "roughness": ("ROUGHNESS", "Non-Color", None),
    "metallic":  ("EMIT",      "Non-Color", "Metallic"),
    "normal":    ("NORMAL",    "Non-Color", None),
    "emissive":  ("EMIT",      "sRGB",      None),
}


def can_bake():
    """Cycles must be available to bake (EEVEE-Next does not bake)."""
    return bpy is not None and "cycles" in bpy.context.preferences.addons


@contextmanager
def bake_environment(samples=1):
    """Swap to CYCLES with flat-pass settings, restoring ALL touched render/scene state on exit.
    Bake passes here are unlit, so 1 sample + no denoise is enough and fast."""
    scene = bpy.context.scene
    snap = {
        "engine": scene.render.engine,
        "samples": scene.cycles.samples,
        "denoise": scene.cycles.use_denoising,
        "active": bpy.context.view_layer.objects.active,
        "selected": list(bpy.context.selected_objects),
    }
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = False
        yield
    finally:
        scene.render.engine = snap["engine"]          # restore the LITERAL engine id
        scene.cycles.samples = snap["samples"]
        scene.cycles.use_denoising = snap["denoise"]
        try:
            bpy.ops.object.select_all(action="DESELECT")
            for o in snap["selected"]:
                if o.name in bpy.data.objects:
                    o.select_set(True)
            bpy.context.view_layer.objects.active = snap["active"]
        except Exception:
            pass


def ensure_uv(obj):
    """True if the object can be baked (has a UV map). If it has none, Smart-UV-Project one.
    NEVER re-unwrap an object that already has UVs — that would destroy authored tiling."""
    if obj.type != "MESH":
        return False
    if len(obj.data.uv_layers) > 0:
        return True
    try:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        return False
    return len(obj.data.uv_layers) > 0


def _principled_of(mat):
    for n in mat.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return n
    return None


def _active_output(mat):
    outs = [n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL"]
    if not outs:
        return None
    for n in outs:
        if getattr(n, "is_active_output", False):
            return n
    return outs[0]


def _force_render_enabled(obj):
    """Temporarily clear render-disable on `obj` and every ancestor collection so it can be baked.

    `bpy.ops.object.bake()` refuses an object that is not enabled for rendering, and a collection's
    `hide_render` (the camera icon) propagates to ALL its descendants — so an object whose own
    `hide_render` is False can still be render-disabled purely because an ancestor collection is.
    Snapshots only the flags it actually clears and returns an idempotent restore() (the module's
    non-destructive contract); a view-layer update recomputes the base render-enable flags so the
    bake op sees the change."""
    snap = []
    if obj.hide_render:
        snap.append((obj, True))
        obj.hide_render = False
    # Collections have no `.parent`; build a child -> parent index to walk ancestors.
    parent = {}
    def _index(coll):
        for ch in coll.children:
            parent[ch.name] = coll
            _index(ch)
    _index(bpy.context.scene.collection)
    seen = set()
    for c in obj.users_collection:
        cur = c
        while cur is not None and cur.name not in seen:
            seen.add(cur.name)
            if getattr(cur, "hide_render", False):
                snap.append((cur, True))
                cur.hide_render = False
            cur = parent.get(cur.name)
    if snap:
        bpy.context.view_layer.update()

    def restore():
        for datablock, val in snap:
            try:
                datablock.hide_render = val
            except Exception:
                pass
        if snap:
            bpy.context.view_layer.update()
    return restore


def _bake_into(obj, mat, img, bake_type, **kw):
    """Add a target Image Texture node bound to `img`, make it the active/selected node and the
    object the active/selected object, bake, then remove the node. Caller owns image teardown."""
    nt = mat.node_tree
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = img
    render_restore = _force_render_enabled(obj)        # bake refuses render-disabled objects/collections
    try:
        for n in nt.nodes:
            n.select = False
        node.select = True
        nt.nodes.active = node
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.bake(type=bake_type, **kw)
    finally:
        render_restore()
        nt.nodes.remove(node)


def _emit_rewire(mat, input_name):
    """Route the source feeding a Principled input through a temporary Emission -> Surface so an
    EMIT bake captures that input's graph. Returns a restore() callable (idempotent). If the input
    is unlinked (a constant) returns None — the caller writes the scalar instead of baking."""
    pr = _principled_of(mat)
    out = _active_output(mat)
    if pr is None or out is None:
        return None
    inp = pr.inputs.get(input_name)
    if inp is None or not inp.is_linked:
        return None
    nt = mat.node_tree
    surf = out.inputs["Surface"]
    stash = surf.links[0].from_socket if surf.is_linked else None
    emit = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(inp.links[0].from_socket, emit.inputs["Color"])
    nt.links.new(emit.outputs["Emission"], surf)

    def restore():
        if emit.name in nt.nodes:
            nt.nodes.remove(emit)
        if stash is not None:
            nt.links.new(stash, surf)
    return restore


def bake_channel(obj, mat, channel, resolution, out_path):
    """Bake one WL channel of `mat` on `obj` to a PNG at `out_path`; return the path, or None if
    the channel is not bakeable here (constant input / unknown channel / no UV). Must run inside
    `bake_environment()`. `obj` must have a UV map (call `ensure_uv` first)."""
    spec = _CHANNEL_BAKE.get(channel)
    if spec is None:
        return None
    bake_type, colorspace, rewire_input = spec

    img = bpy.data.images.new(f"_bake_{mat.name}_{channel}", resolution, resolution, alpha=False)
    img.colorspace_settings.name = colorspace          # encodes the PNG on save (buffer is linear)
    restore = None
    try:
        kw = {}
        if rewire_input is not None:
            restore = _emit_rewire(mat, rewire_input)
            if restore is None:
                return None                            # constant input -> mapper writes the scalar
        elif bake_type == "DIFFUSE":
            kw["pass_filter"] = {"COLOR"}              # albedo only, no lighting
        elif bake_type == "NORMAL":
            kw["normal_space"] = "TANGENT"

        _bake_into(obj, mat, img, bake_type, **kw)

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.filepath_raw = out_path
        img.file_format = "PNG"
        img.save()
        return out_path
    finally:
        if restore is not None:
            restore()
        if img.name in bpy.data.images:
            bpy.data.images.remove(img, do_unlink=True)


def extract_orm_channel(image, index, out_path):
    """Split one channel (0=R,1=G,2=B) of a packed ORM/MRAO image into a grayscale PNG by copying
    pixels — NO render. Cheap and exact; the near-pure-data 'bake' for packed maps."""
    w, h = image.size
    src = list(image.pixels)
    out = bpy.data.images.new(f"_orm_{os.path.basename(out_path)}", w, h, alpha=False)
    out.colorspace_settings.name = "Non-Color"
    dst = [0.0] * (w * h * 4)
    for p in range(w * h):
        v = src[p * 4 + index]
        dst[p * 4 + 0] = v
        dst[p * 4 + 1] = v
        dst[p * 4 + 2] = v
        dst[p * 4 + 3] = 1.0
    out.pixels[:] = dst
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        out.filepath_raw = out_path
        out.file_format = "PNG"
        out.save()
        return out_path
    finally:
        if out.name in bpy.data.images:
            bpy.data.images.remove(out, do_unlink=True)
