"""mapper.py — NormalizedMaterial -> Wild Life customMaterials entry.

NormalizedMaterial (dict, blenderParse.js-style shape) ->
  { "entry": <customMaterials dict>, "textures": [...], "report": {...} }
or None when the material is skipped/empty.

Emits the complete save **v18** customMaterial struct (24 fields, in the game's
key order — see docs/wl-customMaterial-schema.md). Originally a port of the
Studio's mapMaterial.js; that file was removed from Studio (the JS injector
feature was dropped), so this module is now the SINGLE source of truth for the
Blender->Wild Life mapping. tests/test_mapper.py is a regression snapshot of this
mapper (golden regenerated via tests/_gen_golden.py).

No bpy import: this module is pure data and runs/tests outside Blender.
"""

import math
import re


def clamp01(x):
    return max(0.0, min(1.0, x))


def _is_num(x):
    # JS isNum: typeof number && Number.isFinite. Exclude bool (JSON true/false).
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _inv_scale(v):
    """Blender Mapping Scale -> Wild Life textureTiling. Blender scales the UV
    *coordinate* (a larger Scale repeats the texture more / makes it smaller); the
    game's textureTiling scales the texture *size* (a larger value zooms in), so
    the two are reciprocal. Invert per axis; 0 / non-finite -> 1 (no tiling). The
    sign is preserved (a negative Scale mirrors, which the game also reads)."""
    return 1.0 / v if (_is_num(v) and v != 0) else 1


def _vec_close(a, b, tol=1e-4):
    """True if two {x,y,z} vectors agree within tol (None-safe)."""
    if a is None or b is None:
        return a is b
    return all(abs((a.get(k) or 0) - (b.get(k) or 0)) <= tol for k in ("x", "y", "z"))


_ALPHA_RE = re.compile(r"^alpha$", re.IGNORECASE)

# Constant (unlinked) Principled Alpha at/above this stays Opaque — kills float
# noise (0.999) flipping a material to Transparent (owner decision: threshold 0.99).
_ALPHA_OPAQUE = 0.99

# Terminal shader node.types that are NOT inherently see-through (used to tell a
# lone Transparent/Translucent surface from one mixed onto an opaque branch).
_TRANSP_SHADERS = {"BSDF_TRANSPARENT", "BSDF_TRANSLUCENT", "BSDF_GLASS", "BSDF_REFRACTION"}


def channel_for_slot(slot):
    """Blender Principled input ("Slots" value) -> Wild Life texture channel."""
    s = str(slot).lower()
    if s == "base color":
        return "diffuse"
    if s == "normal":
        return "normal"
    if s == "roughness":
        return "roughness"
    if s == "metallic":
        return "metallic"
    if s in ("emission", "emission color"):
        return "emissive"
    if s == "height":
        return "height"
    return None  # 'Specular Tint', 'UNKNOWN (or not Principled)', etc.


CHANNEL_TO_FIELD = {
    "diffuse": "diffuseTexturePath",
    "normal": "normalTexturePath",
    "roughness": "roughnessTexturePath",
    "metallic": "metallicTexturePath",
    "emissive": "emissiveTexturePath",
    "height": "heightTexturePath",
}


def map_material(norm):
    if not norm or norm.get("skipped"):
        return None

    report = {"name": norm.get("name"), "ignoredSlots": [], "unresolvedTextures": [],
              "notes": [], "bakeCandidates": []}
    channel_tex = {}  # channel -> chosen texture (first-wins; dict preserves insertion order)
    has_alpha = False

    def _bake_candidate(channel, reason):
        # Structured, de-duplicated trigger the Phase-2 bake pipeline consumes. Every
        # silent loss below also records one so nothing is dropped without a signal.
        rec = {"channel": channel, "reason": reason}
        if rec not in report["bakeCandidates"]:
            report["bakeCandidates"].append(rec)

    for tex in (norm.get("textures") or []):
        for slot in (tex.get("slots") or []):
            if _ALPHA_RE.match(str(slot)):
                has_alpha = True
                continue
            ch = channel_for_slot(slot)
            if not ch:
                report["ignoredSlots"].append({"slot": slot, "texture": tex.get("name")})
                continue
            if tex.get("fileKind") != "path":
                # packed / generated / missing / udim — no single file to copy
                kind = tex.get("fileKind")
                report["unresolvedTextures"].append(
                    {"channel": ch, "texture": tex.get("name"), "fileKind": kind}
                )
                hint = "unpack or bake" if kind == "packed" else "bake to recover"
                report["notes"].append(f"{ch}: '{tex.get('name')}' is {kind} (no file) — {hint}")
                _bake_candidate(ch, kind)
                continue
            if ch not in channel_tex:
                channel_tex[ch] = tex
            elif channel_tex[ch].get("path") != tex.get("path"):
                # A second, different image targets an already-filled channel (MixRGB /
                # decal / detail / AO layering). First-wins keeps the first — record the
                # dropped one so the loss is never silent (bake to merge faithfully).
                report["notes"].append(
                    f"{ch}: kept '{channel_tex[ch].get('name')}', dropped '{tex.get('name')}' — bake to merge")
                _bake_candidate(ch, "multi-texture")

    # ORM/MRAO: the same source image landing in two channels (e.g. a Separate Color
    # feeding metallic + roughness from one packed map) is not independent. First-wins
    # kept it in each channel as-is; flag it so the user can bake to split the channels.
    _seen_path = {}
    for ch, tex in channel_tex.items():
        p = tex.get("path")
        if not p:
            continue
        if p in _seen_path:
            report["notes"].append(
                f"{ch}: shares image '{tex.get('basename')}' with {_seen_path[p]} "
                f"(packed ORM/MRAO?) — bake to split channels")
            _bake_candidate(ch, "orm-packed")
        else:
            _seen_path[p] = ch

    # textureTiling / textureOffset come from the diffuse texture's mapping when
    # present, else from any mapped texture in the material. textureTiling is the
    # reciprocal of Blender's Mapping Scale (see _inv_scale).
    diffuse = channel_tex.get("diffuse")
    if diffuse and diffuse.get("mapping"):
        mapping = diffuse.get("mapping")
    else:
        mapping = None
        for t in (norm.get("textures") or []):
            if t.get("mapping"):
                mapping = t.get("mapping")
                break
    tiling = mapping.get("scale") if (mapping and mapping.get("scale")) else None
    offset = mapping.get("loc") if (mapping and mapping.get("loc")) else None

    # Divergence: WL stores ONE tiling/offset per material. A mapped channel whose
    # Mapping disagrees with the exported one is silently flattened — warn (bake to
    # reconcile). A non-zero rotation on the chosen mapping has no WL field at all.
    if mapping is not None:
        chosen_scale, chosen_loc = mapping.get("scale"), mapping.get("loc")
        for ch, tex in channel_tex.items():
            m = tex.get("mapping")
            if not m or m is mapping:
                continue
            if not _vec_close(m.get("scale"), chosen_scale) or not _vec_close(m.get("loc"), chosen_loc):
                report["notes"].append(f"{ch}: Mapping differs from the exported tiling/offset — bake to reconcile")
                _bake_candidate(ch, "divergent-uv")
        rot = mapping.get("rot")
        if rot and any(abs(rot.get(k) or 0) > 1e-4 for k in ("x", "y", "z")):
            report["notes"].append("texture Mapping rotation dropped (no WL field) — bake to fold it in")
            _bake_candidate("diffuse", "rotation")

    # Emission: only glow when strength > 0. WL has no strength field, so fold
    # strength into the color. NOT clamped — the game stores HDR emissive (>1
    # observed in real saves), so clamping would lose intensity. Color may be
    # absent -> white.
    es = norm.get("emissionStrength") if _is_num(norm.get("emissionStrength")) else 0
    emissive_color = {"r": 0, "g": 0, "b": 0, "a": 1}
    if es > 0:
        ec = norm.get("emissionColor") or {"r": 1, "g": 1, "b": 1, "a": 1}
        emissive_color = {"r": ec["r"] * es, "g": ec["g"] * es, "b": ec["b"] * es, "a": 1}
        if any(emissive_color[k] > 1 for k in ("r", "g", "b")):
            report["notes"].append(f"emission strength {es} folded into emissiveColor (HDR, unclamped)")
        else:
            report["notes"].append(f"emission strength {es} folded into emissiveColor")

    bc = norm.get("baseColor")
    if bc:
        color = {
            "r": clamp01(bc["r"]),
            "g": clamp01(bc["g"]),
            "b": clamp01(bc["b"]),
            "a": bc["a"] if _is_num(bc.get("a")) else 1,
        }
    else:
        color = {"r": 1, "g": 1, "b": 1, "a": 1}

    # type: classified from the shaders that actually reach the active Material
    # Output (introspect's trace_surface_shaders), with the legacy Principled
    # scalars as fallback. First-match-wins; Masked (linked-alpha cutout)
    # deliberately beats BLENDED/raytrace corroboration, which is never a sole
    # trigger. See docs/plans/features/material-type-fidelity/plan.md.
    shaders = set(norm.get("shaderTypes") or [])
    has_glass = bool(shaders & {"BSDF_GLASS", "BSDF_REFRACTION"})
    has_transp = bool(shaders & {"BSDF_TRANSPARENT", "BSDF_TRANSLUCENT"})
    has_opaque_shader = any(s not in _TRANSP_SHADERS for s in shaders)
    corroboration = (norm.get("surfaceRenderMethod") == "BLENDED") or bool(norm.get("useRaytraceRefraction"))

    transmission = norm.get("transmission") if _is_num(norm.get("transmission")) else 0
    trans_static = norm.get("transmissionStaticValue")
    trans_linked = bool(norm.get("transmissionLinked"))
    alpha_scalar = norm.get("alpha") if _is_num(norm.get("alpha")) else 1
    alpha_linked = bool(norm.get("alphaLinked"))
    masked_fac = bool(norm.get("maskedFacMix"))

    trans_active = (
        transmission > 0
        or (_is_num(trans_static) and trans_static > 0)
        or (trans_linked and corroboration)
    )

    # `refractive` marks transparency that physically bends light (glass /
    # transmission / alpha-blend) — those get a 1.45 IOR fallback. A pure
    # Transparent/Translucent BSDF (rule 5) has no IOR socket, so it keeps 1.
    if has_glass:                                                      # rule 1
        mat_type, refractive = "Transparent", True
    elif trans_active:                                                 # rule 2
        mat_type, refractive = "Transparent", True
    elif alpha_linked or masked_fac or has_alpha:                      # rule 3
        mat_type, refractive = "Masked", False
    elif alpha_scalar < _ALPHA_OPAQUE:                                 # rule 4
        mat_type, refractive = "Transparent", True
    elif has_transp and (not has_opaque_shader or corroboration):      # rule 5
        mat_type, refractive = "Transparent", False
    else:                                                              # rule 7
        mat_type, refractive = "Opaque", False

    is_transparent = mat_type == "Transparent"

    # A constant (unlinked) sub-1 Alpha sets the whole-surface opacity.
    if is_transparent and _is_num(norm.get("alpha")) and alpha_scalar < 1:
        color["a"] = clamp01(alpha_scalar)

    # refraction: real IOR only when Transparent. Prefer the (group-resolved)
    # refractive-node IOR, else the Principled IOR; clamp [1,3]. Fallback is 1.45
    # for refractive transparency, 1 for a non-refractive Transparent BSDF.
    if is_transparent:
        ior_src = norm.get("refractiveIor")
        if not _is_num(ior_src):
            ior_src = norm.get("ior")
        if _is_num(ior_src):
            refraction = min(3.0, max(1.0, ior_src))
        else:
            refraction = 1.45 if refractive else 1
    else:
        refraction = 1

    if is_transparent and (alpha_linked or masked_fac or has_alpha):
        report["notes"].append("alpha cutout dropped — refraction/transparency won over Masked")
    if is_transparent and corroboration and not (has_glass or transmission > 0):
        report["notes"].append("surface_render_method=BLENDED / raytrace_refraction drove Transparent")
    if is_transparent and refractive and not _is_num(norm.get("refractiveIor")) and not _is_num(norm.get("ior")):
        report["notes"].append("refraction defaulted to 1.45 (no static IOR source)")
    # alpha_threshold is 0.0/vestigial under EEVEE-Next; only honor an explicit
    # (> 0) clip threshold, else keep WL's sensible default.
    ac = norm.get("alphaCutoff")
    cutoff = clamp01(ac) if (_is_num(ac) and ac > 0) else 0.5
    specular = clamp01(norm.get("specular")) if _is_num(norm.get("specular")) else 0.5
    two_sided = bool(norm.get("twoSided")) if isinstance(norm.get("twoSided"), bool) else False

    # Triplanar: a real projection-mapped texture (Object/Generated coords or Box
    # projection), or — when the material is consumed by a mesh with no UVs — inferred
    # so a no-UV object renders as a plausible projection instead of a UV smear.
    projection_mapped = bool(norm.get("projectionMapped", False))
    no_uv = bool(norm.get("consumedByNoUvObject", False))
    triplanar = projection_mapped or no_uv
    if triplanar and no_uv and not projection_mapped:
        report["notes"].append("bIsTriplanar inferred from a consumer mesh without UVs (no projection node)")

    entry = {
        "name": norm.get("name"),
        "type": mat_type,
        "diffuseTexturePath": "",
        "normalTexturePath": "",
        "metallicTexturePath": "",
        "roughnessTexturePath": "",
        "emissiveTexturePath": "",
        "heightTexturePath": "",
        "color": color,
        "metallic": clamp01(norm.get("metallic") if _is_num(norm.get("metallic")) else 0),
        "specular": specular,
        "roughness": clamp01(norm.get("roughness") if _is_num(norm.get("roughness")) else 0.5),
        "normalMapAmplification": norm.get("normalStrength") if _is_num(norm.get("normalStrength")) else 1,
        "emissiveColor": emissive_color,
        "maskedAlphaCutoff": cutoff,
        "textureTiling": {"x": _inv_scale(tiling["x"]), "y": _inv_scale(tiling["y"]), "z": _inv_scale(tiling["z"])} if tiling else {"x": 1, "y": 1, "z": 1},
        "textureOffset": {"x": offset["x"], "y": offset["y"], "z": offset["z"]} if offset else {"x": 0, "y": 0, "z": 0},
        "bIsTriplanar": triplanar,
        "bIsTwoSided": two_sided,
        "bFlipGreenChannel": True,
        "textureRandomness": 0,
        "surfaceType": "SurfaceType_Default",
        "textureMovement": {"x": 0, "y": 0, "z": 0},
        "refraction": refraction,
    }

    textures = [
        {
            "channel": ch,
            "field": CHANNEL_TO_FIELD[ch],
            "srcPath": channel_tex[ch].get("path"),
            "basename": channel_tex[ch].get("basename"),
        }
        for ch in channel_tex
    ]

    return {"entry": entry, "textures": textures, "report": report}


def apply_texture_paths(entry, textures, save_folder):
    """Fill relative '<SaveFolder>/Textures/<basename>' paths. Mirrors applyTexturePaths."""
    out = dict(entry)
    for t in (textures or []):
        out[t["field"]] = f"{save_folder}/Textures/{t['basename']}"
    return out
