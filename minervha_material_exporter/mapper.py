"""mapper.py — pure port of Minervha Studio's mapMaterial.js.

NormalizedMaterial (dict, same shape as blenderParse.js output) ->
  { "entry": <customMaterials dict>, "textures": [...], "report": {...} }
or None when the material is skipped/empty (mirrors mapMaterial returning null).

This is a FAITHFUL line-by-line port of
  ../Minervha Studio/electron-helpers/materialInjector/mapMaterial.js
— same defaults, clamps, channel mapping, emission folding, tiling/offset rules —
so the .wlsave path can never drift from the Studio's JS injector. The golden
parity test (tests/test_mapper.py) enforces this against the real mapMaterial.js.

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
    return None  # 'Specular Tint', 'UNKNOWN (or not Principled)', etc.


CHANNEL_TO_FIELD = {
    "diffuse": "diffuseTexturePath",
    "normal": "normalTexturePath",
    "roughness": "roughnessTexturePath",
    "metallic": "metallicTexturePath",
    "emissive": "emissiveTexturePath",
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
    # strength into the color (clamped). Emission Color may be absent -> white.
    es = norm.get("emissionStrength") if _is_num(norm.get("emissionStrength")) else 0
    emissive_color = {"r": 0, "g": 0, "b": 0, "a": 1}
    if es > 0:
        ec = norm.get("emissionColor") or {"r": 1, "g": 1, "b": 1, "a": 1}
        emissive_color = {
            "r": clamp01(ec["r"] * es),
            "g": clamp01(ec["g"] * es),
            "b": clamp01(ec["b"] * es),
            "a": 1,
        }
        report["notes"].append(f"emission strength {es} folded into emissiveColor (clamped)")

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

    entry = {
        "name": norm.get("name"),
        "type": "Masked" if has_alpha else "Opaque",
        "diffuseTexturePath": "",
        "normalTexturePath": "",
        "metallicTexturePath": "",
        "roughnessTexturePath": "",
        "emissiveTexturePath": "",
        "color": color,
        "metallic": clamp01(norm.get("metallic") if _is_num(norm.get("metallic")) else 0),
        "specular": 0.5,
        "roughness": clamp01(norm.get("roughness") if _is_num(norm.get("roughness")) else 0.5),
        "normalMapAmplification": norm.get("normalStrength") if _is_num(norm.get("normalStrength")) else 1,
        "emissiveColor": emissive_color,
        "maskedAlphaCutoff": 0.5,
        "textureTiling": {"x": tiling["x"], "y": tiling["y"], "z": tiling["z"]} if tiling else {"x": 1, "y": 1, "z": 1},
        "textureOffset": {"x": offset["x"], "y": offset["y"], "z": offset["z"]} if offset else {"x": 0, "y": 0, "z": 0},
        "bIsTriplanar": False,
        "bIsTwoSided": False,
        "bFlipGreenChannel": True,
        "surfaceType": "SurfaceType_Default",
        "textureMovement": {"x": 0, "y": 0, "z": 0},
        "refraction": 1,
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
