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


_ALPHA_RE = re.compile(r"^alpha$", re.IGNORECASE)


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

    report = {"name": norm.get("name"), "ignoredSlots": [], "unresolvedTextures": [], "notes": []}
    channel_tex = {}  # channel -> chosen texture (first-wins; dict preserves insertion order)
    has_alpha = False

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
                report["unresolvedTextures"].append(
                    {"channel": ch, "texture": tex.get("name"), "fileKind": tex.get("fileKind")}
                )
                continue
            if ch not in channel_tex:
                channel_tex[ch] = tex

    # textureTiling / textureOffset come from the diffuse texture's mapping when
    # present, else from any mapped texture in the material.
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

    # type: Transparent (glass — transmission or scalar alpha < 1) wins over
    # Masked (alpha-cutout texture); else Opaque.
    transmission = norm.get("transmission") if _is_num(norm.get("transmission")) else 0
    alpha_scalar = norm.get("alpha") if _is_num(norm.get("alpha")) else 1
    is_transparent = transmission > 0 or alpha_scalar < 1
    if is_transparent:
        mat_type = "Transparent"
    elif has_alpha:
        mat_type = "Masked"
    else:
        mat_type = "Opaque"

    # refraction: only meaningful for transparent materials; reading Blender's IOR
    # (default 1.45) into an opaque material would be wrong, so keep WL's 1.
    refraction = norm.get("ior") if (is_transparent and _is_num(norm.get("ior"))) else 1
    # alpha_threshold is 0.0/vestigial under EEVEE-Next; only honor an explicit
    # (> 0) clip threshold, else keep WL's sensible default.
    ac = norm.get("alphaCutoff")
    cutoff = clamp01(ac) if (_is_num(ac) and ac > 0) else 0.5
    specular = clamp01(norm.get("specular")) if _is_num(norm.get("specular")) else 0.5
    two_sided = bool(norm.get("twoSided")) if isinstance(norm.get("twoSided"), bool) else False

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
        "textureTiling": {"x": tiling["x"], "y": tiling["y"], "z": tiling["z"]} if tiling else {"x": 1, "y": 1, "z": 1},
        "textureOffset": {"x": offset["x"], "y": offset["y"], "z": offset["z"]} if offset else {"x": 0, "y": 0, "z": 0},
        "bIsTriplanar": False,
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
