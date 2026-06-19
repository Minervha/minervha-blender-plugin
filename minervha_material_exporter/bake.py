"""bake.py — flatten arbitrary node graphs to per-channel PBR textures via Cycles bake.

Net-new, bpy-side. The ONLY module that renders. It bakes the channels mapper.py flagged as
`report["bakeCandidates"]` (procedural / multi-texture / divergent-UV / packed) into flat PNGs
the WL `customMaterial` can hold — the rung **B** of the graceful-degradation ladder.

Bakes on a throwaway full-UV placeholder plane, NEVER a scene mesh: the flattened output is captured
over the whole [0,1]² UV domain, so ONE texture is correct for every mesh that shares the material
(each samples it through its own UVs — a scene mesh would only fill its own islands, blacking out the
rest of an atlas). Non-destructive by contract: every bake snapshots render state, adds ONLY throwaway
datablocks (the placeholder plane + mesh, a target Image + an Image Texture node, a temporary Emission
rewire), and restores everything in a ``finally``. `mapper.py`/`introspect.py` stay pure — this module
is invoked from the bpy-side export pipeline (`wlsave_export`) and hands back on-disk PNG paths.

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


@contextmanager
def placeholder_plane(mat):
    """Yield a throwaway unit plane whose UV map fills the WHOLE [0,1]² tile, carrying only `mat`.

    Baking the material here captures its flattened output over the ENTIRE UV domain — not one scene
    mesh's islands — so a single texture is correct for every mesh that shares the material (each
    samples it through its own UVs). The plane lives in the scene master collection (always
    render-enabled, never excluded) and is fully removed (object + mesh) on exit. Because we never
    bake a scene mesh, render-disabled objects/collections are a non-issue."""
    scene = bpy.context.scene
    mesh = bpy.data.meshes.new("_wl_bake_plane")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
                     [], [(0, 1, 2, 3)])
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    for loop_idx, co in enumerate([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]):
        uv.data[loop_idx].uv = co
    mesh.materials.append(mat)
    obj = bpy.data.objects.new("_wl_bake_plane", mesh)
    obj.hide_render = False
    scene.collection.objects.link(obj)
    try:
        bpy.context.view_layer.update()
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


def _bake_into(plane, mat, img, bake_type, **kw):
    """Add a target Image Texture node bound to `img`, make it the active/selected node and `plane`
    the active/selected object, bake, then remove the node. Caller owns image + plane teardown."""
    nt = mat.node_tree
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = img
    try:
        for n in nt.nodes:
            n.select = False
        node.select = True
        nt.nodes.active = node
        bpy.ops.object.select_all(action="DESELECT")
        plane.select_set(True)
        bpy.context.view_layer.objects.active = plane
        bpy.ops.object.bake(type=bake_type, **kw)
    finally:
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


def _bake_alpha_into(plane, mat, img):
    """Fill RGBA `img`'s ALPHA channel from the material's Principled Alpha input, so a baked
    Base Color carries its transparency mask (otherwise a Masked/alpha-tested look is lost — the
    flat RGB diffuse bake has no mask). A LINKED Alpha is EMIT-baked (captures the mask graph /
    cutoff via the node tree); a CONSTANT Alpha fills a flat value. Vectorized via numpy (a Python
    per-pixel loop over a 2K² buffer would be seconds × every material). Best-effort — on any
    failure the alpha stays 1 (opaque), never worse than the old behavior."""
    import numpy as np
    pr = _principled_of(mat)
    inp = pr.inputs.get("Alpha") if pr is not None else None
    n = len(img.pixels)
    a = np.empty(n, dtype=np.float32)
    img.pixels.foreach_get(a)
    if inp is not None and inp.is_linked:
        tmp = bpy.data.images.new("_bake_alpha_tmp", img.size[0], img.size[1], alpha=False)
        tmp.colorspace_settings.name = "Non-Color"
        restore = _emit_rewire(mat, "Alpha")
        try:
            if restore is not None:
                _bake_into(plane, mat, tmp, "EMIT")
                b = np.empty(len(tmp.pixels), dtype=np.float32)
                tmp.pixels.foreach_get(b)
                a[3::4] = b[0::4]                       # EMIT red = alpha -> target A channel
        finally:
            if restore is not None:
                restore()
            if tmp.name in bpy.data.images:
                bpy.data.images.remove(tmp, do_unlink=True)
    else:
        a[3::4] = float(inp.default_value) if inp is not None else 1.0
    img.pixels.foreach_set(a)
    img.update()


def bake_channel(mat, channel, resolution, out_path, image_format="PNG", jpg_quality=90,
                 bake_alpha=False):
    """Bake one WL channel of `mat` to `out_path` on a throwaway full-UV placeholder plane
    (NEVER a scene mesh); return the path, or None if the channel is not bakeable here (constant
    input / unknown channel). Must run inside `bake_environment()`. One bake per material -> one
    shared texture, correct for every mesh that uses it.

    `image_format` ('PNG'|'JPEG') is the on-disk encoding the BAKER picks from the user's texture
    options. Writing the final format here is mandatory: the target Image is removed in this
    function's `finally`, so the downstream texture pre-pass can no longer re-encode it (it would
    find no bpy image and silently keep PNG) — hence a baked channel never honored 'Prefer JPG'.
    `out_path`'s extension must match.

    `bake_alpha` (diffuse only): also bake the material's Alpha into the target's alpha channel and
    FORCE PNG — for a material that uses transparency, a flat RGB(JPEG) diffuse bake drops the mask.
    Ignored for non-diffuse channels (they carry no transparency)."""
    spec = _CHANNEL_BAKE.get(channel)
    if spec is None:
        return None
    bake_type, colorspace, rewire_input = spec

    want_alpha = bool(bake_alpha) and channel == "diffuse"
    if want_alpha:
        image_format = "PNG"                           # an alpha mask cannot survive JPEG
    img = bpy.data.images.new(f"_bake_{mat.name}_{channel}", resolution, resolution, alpha=want_alpha)
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

        with placeholder_plane(mat) as plane:
            _bake_into(plane, mat, img, bake_type, **kw)
            if want_alpha:
                _bake_alpha_into(plane, mat, img)      # composite the mask into the A channel

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.filepath_raw = out_path
        img.file_format = image_format
        if image_format == "JPEG":
            img.save(quality=int(jpg_quality))
        else:
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
