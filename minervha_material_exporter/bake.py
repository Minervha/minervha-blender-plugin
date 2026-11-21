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

if bpy is not None:
    try:
        from . import bsdf_trace        # packaged extension
    except ImportError:
        import bsdf_trace               # flat import (tests / headless probes)
else:
    bsdf_trace = None


# channel -> (cycles bake type, target colorspace, Principled input to EMIT-rewire | None).
# A None rewire-input means a native Cycles pass reads the whole shader (Surface left intact);
# a named input means that input's *source graph* is routed through an Emission and baked EMIT
# (the universal flattener for inputs with no dedicated pass — e.g. Metallic).
# Base Color and Roughness are EMIT-rewired too: the DIFFUSE/COLOR pass is the *diffuse* albedo, which
# Cycles zeroes where Metallic = 1 (a metal baked BLACK), and the ROUGHNESS pass ignored a Math node
# scaling the roughness texture (DAZ's `tex x 0.45` came out as the raw texture). EMIT of the input's
# own graph is exact for both.
_CHANNEL_BAKE = {
    "diffuse":   ("EMIT",      "sRGB",      "Base Color"),
    "roughness": ("EMIT",      "Non-Color", "Roughness"),
    "metallic":  ("EMIT",      "Non-Color", "Metallic"),
    "normal":    ("NORMAL",    "Non-Color", None),
    "emissive":  ("EMIT",      "sRGB",      None),
}
# Native pass used when the material holds no Principled at all (Diffuse / Glossy / custom shader) —
# the EMIT substitute (_EmitSubstitute, any group depth / mixed Principled) has nothing to read.
_NATIVE_FALLBACK = {"diffuse": "DIFFUSE", "roughness": "ROUGHNESS"}


def can_bake():
    """Cycles must be available to bake (EEVEE-Next does not bake)."""
    return bpy is not None and "cycles" in bpy.context.preferences.addons


def _gpu_available():
    """True if Cycles has a configured, enabled non-CPU compute device (CUDA/OPTIX/HIP/...).
    Lets the bake run on the GPU instead of the CPU when the user has one set up in Preferences."""
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
    """Yield a throwaway ISOLATED scene that will hold ONLY the bake placeholder plane, configured
    for a fast unlit CYCLES bake on the GPU when available.

    Why a separate scene: `bpy.ops.object.bake()` syncs the *whole* active scene to the render
    device every call. Baking the placeholder plane in the user's 10k-object scene therefore
    re-uploads every object + texture per bake — measured at ~2.5 s/bake (GPU starved at <20%),
    so a bake-heavy export crawls. Baking in a scene that contains only the plane (the bake is
    unlit COLOR/EMIT, so it needs nothing else) syncs one object → milliseconds per bake. The
    user's scene and its render settings are never touched; the temp scene is removed on exit."""
    bake_scene = bpy.data.scenes.new("_wl_bake_scene")
    try:
        bake_scene.render.engine = "CYCLES"
        bake_scene.cycles.samples = samples
        bake_scene.cycles.use_denoising = False
        if _gpu_available():
            bake_scene.cycles.device = "GPU"          # offload the bake to the GPU when available
        yield bake_scene
    finally:
        if bake_scene.name in bpy.data.scenes:
            bpy.data.scenes.remove(bake_scene)


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


def _uv_names_used(node_tree, seen=None):
    """Sorted UV layer names referenced by name in `node_tree` (and its groups): Normal Map
    (tangent space), UV Map and Tangent (UV map mode) nodes. Empty names = the default layer."""
    seen = seen if seen is not None else set()
    names = set()
    if node_tree is None or node_tree.as_pointer() in seen:
        return []
    seen.add(node_tree.as_pointer())
    for n in node_tree.nodes:
        name = None
        if n.type in ("NORMAL_MAP", "UVMAP", "TANGENT"):
            name = getattr(n, "uv_map", None)
        if name:
            names.add(name)
        if n.type == "GROUP" and getattr(n, "node_tree", None) is not None:
            names.update(_uv_names_used(n.node_tree, seen))
    return sorted(names)


@contextmanager
def placeholder_plane(mat, scene):
    """Yield a throwaway unit plane whose UV map fills the WHOLE [0,1]² tile, carrying only `mat`,
    linked into the ISOLATED bake `scene` (from `bake_environment`).

    Baking the material here captures its flattened output over the ENTIRE UV domain — not one scene
    mesh's islands — so a single texture is correct for every mesh that shares the material (each
    samples it through its own UVs). The plane is the ONLY object in the bake scene, so a bake syncs
    just it. Fully removed (object + mesh) on exit."""
    mesh = bpy.data.meshes.new("_wl_bake_plane")
    mesh.from_pydata([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
                     [], [(0, 1, 2, 3)])
    mesh.update()
    # One identical full-tile UV layer per UV name the material asks for: a Normal Map / UV Map /
    # Tangent node bound to a NAMED layer ("Default UVs" from the DAZ importer, "UVMap.001"...) finds
    # no such layer on a plane that only has "UVMap" -> no tangents -> the normal bakes FLAT.
    for uv_name in ["UVMap"] + [n for n in _uv_names_used(mat.node_tree) if n != "UVMap"]:
        uv = mesh.uv_layers.new(name=uv_name)
        for loop_idx, co in enumerate([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]):
            uv.data[loop_idx].uv = co
    mesh.materials.append(mat)
    obj = bpy.data.objects.new("_wl_bake_plane", mesh)
    obj.hide_render = False
    scene.collection.objects.link(obj)
    try:
        # NB: do NOT call bpy.context.view_layer.update() here — context.view_layer is the USER's
        # heavy scene, so that re-evaluates its whole (10k-object) depsgraph per bake (~0.3 s wasted,
        # and the wrong scene). The bake operator runs under temp_override(scene=bake_scene) and
        # evaluates the isolated scene's depsgraph itself, which is all that's needed.
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
    """Add a target Image Texture node bound to `img`, make it the active/selected node, then bake
    the `plane` IN THE ISOLATED `scene` via a context override (so Cycles syncs only the plane, not
    the user's scene). Removes the node after. Caller owns image + plane teardown."""
    nt = mat.node_tree
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = img
    try:
        for n in nt.nodes:
            n.select = False
        node.select = True
        nt.nodes.active = node
        vl = scene.view_layers[0]
        with bpy.context.temp_override(scene=scene, view_layer=vl,
                                       active_object=plane, object=plane,
                                       selected_objects=[plane], selected_editable_objects=[plane]):
            bpy.ops.object.bake(type=bake_type, **kw)
    finally:
        nt.nodes.remove(node)


def _emit_rewire_socket(mat, src_socket):
    """Route an arbitrary top-level output socket through a temporary Emission -> Surface (EMIT
    bake of that socket's graph). Returns an idempotent restore() callable, or None."""
    out = _active_output(mat)
    if out is None or src_socket is None:
        return None
    nt = mat.node_tree
    surf = out.inputs["Surface"]
    stash = surf.links[0].from_socket if surf.is_linked else None
    emit = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(src_socket, emit.inputs["Color"])
    nt.links.new(emit.outputs["Emission"], surf)

    def restore():
        if emit.name in nt.nodes:
            nt.nodes.remove(emit)
        if stash is not None:
            nt.links.new(stash, surf)
    return restore


def _bake_alpha_into(plane, mat, img, scene):
    """Fill RGBA `img`'s ALPHA channel from the material's opacity mask (Principled Alpha, or a Mix
    Shader Transparent-vs-BSDF cutout such as the DAZ "Transparent" group), so a baked
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
    # The mask source: Principled Alpha OR a Mix Shader cutout (Transparent vs BSDF, e.g. the DAZ
    # "Transparent" group) — see bsdf_trace.find_alpha_mask.
    mask = bsdf_trace.find_alpha_mask(mat.node_tree, bsdf_trace.build_local_parent_map(mat.node_tree))
    if mask is not None:
        src, invert = mask
        tmp = bpy.data.images.new("_bake_alpha_tmp", img.size[0], img.size[1], alpha=False)
        tmp.colorspace_settings.name = "Non-Color"
        restore = _emit_rewire_socket(mat, src)
        try:
            if restore is not None:
                _bake_into(plane, mat, tmp, "EMIT", scene)
                b = np.empty(len(tmp.pixels), dtype=np.float32)
                tmp.pixels.foreach_get(b)
                m = np.clip(b[0::4], 0.0, 1.0)
                a[3::4] = (1.0 - m) if invert else m    # EMIT red = mask -> target A channel
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
                 bake_alpha=False, scene=None):
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
    if scene is None:                                  # no isolated scene given -> bake in the active
        scene = bpy.context.scene                      # one (un-isolated, legacy path)

    want_alpha = bool(bake_alpha) and channel == "diffuse"
    if want_alpha:
        image_format = "PNG"                           # an alpha mask cannot survive JPEG
    img = bpy.data.images.new(f"_bake_{mat.name}_{channel}", resolution, resolution, alpha=want_alpha)
    img.colorspace_settings.name = colorspace          # encodes the PNG on save (buffer is linear)
    sub = None
    try:
        kw = {}
        bake_mat = mat
        if rewire_input is not None:
            # Bake a throwaway COPY where every Principled (groups included) emits this input: exact
            # for nested / mixed Principled, and the user's material and shared groups are untouched.
            sub = _emit_substitute(mat, rewire_input)
            if sub.material is not None:
                bake_mat = sub.material
            elif sub.constant:
                return None                            # constant input -> mapper writes the scalar
            else:
                # No Principled at all (Diffuse / Glossy / custom shader): the native pass reads it.
                bake_type = _NATIVE_FALLBACK.get(channel)
                if bake_type is None:
                    return None
        if bake_type == "DIFFUSE":
            kw["pass_filter"] = {"COLOR"}              # albedo only, no lighting
        elif bake_type == "NORMAL":
            kw["normal_space"] = "TANGENT"

        with placeholder_plane(bake_mat, scene) as plane:
            _bake_into(plane, bake_mat, img, bake_type, scene, **kw)
            if want_alpha:
                _bake_alpha_into(plane, bake_mat, img, scene)   # composite the mask into the A channel

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.filepath_raw = out_path
        img.file_format = image_format
        if image_format == "JPEG":
            img.save(quality=int(jpg_quality))
        else:
            img.save()
        return out_path
    finally:
        if sub is not None:
            sub.cleanup()
        if img.name in bpy.data.images:
            bpy.data.images.remove(img, do_unlink=True)


class _EmitSubstitute:
    """A throwaway copy of a material in which every Principled BSDF (at any group depth) is replaced
    by an Emission carrying one of its inputs (its link, or its constant). Mix / Add shaders are kept,
    so the EMIT bake returns the channel blended exactly like the shaders were; a Transparent cutout
    branch is bypassed (its mask is baked separately into alpha — leaving it would darken the colour
    at the cut edges). Node groups are copied too (single-user), so shared groups never change.

    `.material` = the copy (None if the material holds no Principled); `.constant` = True when there
    is a single Principled with that input unlinked (nothing to bake). Call `.cleanup()`."""

    def __init__(self, mat, input_name):
        self.material, self.constant, self._groups = None, False, []
        self._copy = mat.copy()
        try:
            self._build(mat, input_name)
        except Exception:
            self.cleanup()                     # never leak the copy / copied groups on a failure
            raise

    def _build(self, mat, input_name):
        self._copy.name = "_wl_bake_" + mat.name
        self._single_user(self._copy.node_tree)
        trees = [self._copy.node_tree] + self._groups
        principled, linked = 0, 0
        for nt in trees:
            for n in list(nt.nodes):
                if n.type != "BSDF_PRINCIPLED":
                    continue
                principled += 1
                inp = n.inputs.get(input_name)
                em = nt.nodes.new("ShaderNodeEmission")
                if inp is not None and inp.is_linked:
                    linked += 1
                    nt.links.new(inp.links[0].from_socket, em.inputs["Color"])
                elif inp is not None:
                    v = inp.default_value
                    em.inputs["Color"].default_value = (
                        (float(v), float(v), float(v), 1.0) if isinstance(v, float) else tuple(v))
                for out in n.outputs:
                    for l in list(out.links):
                        nt.links.new(em.outputs["Emission"], l.to_socket)
        if principled == 0:
            return
        if linked == 0 and principled == 1:
            self.constant = True
            return
        pm = bsdf_trace.build_local_parent_map(self._copy.node_tree)
        for nt in trees:
            for n in list(nt.nodes):
                if n.type != "MIX_SHADER" or len(n.inputs) < 3:
                    continue
                kinds = [bsdf_trace._shader_source_type(n.inputs[i], nt, pm) for i in (1, 2)]
                if "BSDF_TRANSPARENT" in kinds and kinds[0] != kinds[1]:
                    keep = n.inputs[2 if kinds[0] == "BSDF_TRANSPARENT" else 1]
                    if keep.is_linked:
                        src = keep.links[0].from_socket
                        for l in list(n.outputs[0].links):
                            nt.links.new(src, l.to_socket)
        self.material = self._copy

    def _single_user(self, nt):
        for n in nt.nodes:
            if n.type == "GROUP" and n.node_tree is not None:
                g = n.node_tree.copy()
                self._groups.append(g)
                n.node_tree = g
                self._single_user(g)

    def cleanup(self):
        if self._copy is not None and self._copy.name in bpy.data.materials:
            bpy.data.materials.remove(self._copy, do_unlink=True)
        for g in reversed(self._groups):
            if g.name in bpy.data.node_groups:
                bpy.data.node_groups.remove(g, do_unlink=True)
        self._copy, self._groups = None, []


def _emit_substitute(mat, input_name):
    return _EmitSubstitute(mat, input_name)


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
