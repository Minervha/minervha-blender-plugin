"""wlsave_export.py — build a portable .wlsave collection bundle (mode B).

Mirrors Minervha Studio's injectMaterials.js: map each NormalizedMaterial, dedup
material names, gather textures (deduped by basename), rewrite relative paths to
`<Name>/Textures/<basename>`, fill the bundled collection skeleton, and write a ZIP:

    <Name>/<Name>.json          # collection JSON (level "")
    <Name>/Textures/<file>      # bundled textures

Texture policy ("copy real, re-export packed"): on-disk PNG/JPG are copied as-is;
packed/generated images and other on-disk formats (.tga, .exr…) are re-exported to
PNG via Blender in a pre-pass, so the mapper then sees them as ordinary path-kind
textures. UDIM/missing stay unresolved (material created without that texture).

Runs Blender-side (needs bpy for re-export); the on-disk copy path also works without.
"""

import json
import os
import re
import shutil
import tempfile
import unicodedata
import zipfile

try:
    from . import mapper          # packaged extension
except ImportError:               # dev / sys.path import (tests, live MCP)
    import mapper

try:
    import bpy                    # only used to re-export packed/generated textures
except ImportError:
    bpy = None

_COPY_EXT = {".png", ".jpg", ".jpeg"}

# The game only accepts ASCII [A-Za-z0-9_-] in the file/folder names it extracts
# from a .wlsave; accents and other symbols corrupt it. Everything written as a
# path component (collection name, material name, texture basename) is reduced to
# that charset.
_BAD_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _sanitize_name(s, fallback):
    """Reduce `s` to a game-safe name [A-Za-z0-9_-].

    NFKD-transliterates accents (é->e, ñ->n), drops any remaining non-ASCII
    (e.g. CJK), collapses every other run of disallowed characters to a single
    "_", and trims leading/trailing separators. Returns `fallback` if nothing
    usable remains.
    """
    ascii_only = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
    return _BAD_NAME_RE.sub("_", ascii_only).strip("_-") or fallback


def _sanitize_basename(basename, fallback="texture"):
    """Sanitize a texture file name, keeping a dot + ASCII-alnum extension."""
    stem, ext = os.path.splitext(str(basename or ""))
    stem = _sanitize_name(stem, fallback)
    ascii_ext = unicodedata.normalize("NFKD", ext).encode("ascii", "ignore").decode("ascii")
    ext = re.sub(r"[^A-Za-z0-9]+", "", ascii_ext).lower()
    return f"{stem}.{ext}" if ext else stem


def _unique_name(base, taken):
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def _png_name(stem, used):
    base = re.sub(r"[\\/:\0]", "_", str(stem)).strip() or "texture"
    base = base + ".png"
    if base not in used:
        return base
    i = 2
    while f"{base[:-4]}_{i}.png" in used:
        i += 1
    return f"{base[:-4]}_{i}.png"


def _reexport_image_to_png(image_name, dest_path):
    """Re-export a bpy image (packed/generated/other format) to a PNG. True on success."""
    if bpy is None:
        return False
    img = bpy.data.images.get(image_name)
    if img is None:
        return False
    tmp = img.copy()
    try:
        tmp.file_format = "PNG"
        tmp.filepath_raw = dest_path
        tmp.save()
        return os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0
    except Exception:
        return False
    finally:
        try:
            bpy.data.images.remove(tmp)
        except Exception:
            pass


def _resolve_packed_textures(norms, tmpdir):
    """Pre-pass: re-export packed/generated/non-png-jpg textures to PNG; mutate the
    texture dicts in place to look like on-disk path textures. Returns the set of
    re-exported textures' sanitized basenames (matches the basenames used in the
    ZIP/report, so the copied-vs-re-exported classification holds)."""
    cache = {}      # image_name -> (png_path, basename) or None (failed)
    used = set()    # basenames already emitted into tmpdir
    reexported = set()
    for norm in norms:
        for t in norm.get("textures") or []:
            kind = t.get("fileKind")
            if kind in ("missing", "udim"):
                continue  # nothing to re-export (UDIM unsupported in v1)
            ext = os.path.splitext(t.get("path") or "")[1].lower()
            need = kind in ("packed", "generated") or (kind == "path" and ext not in _COPY_EXT)
            if not need:
                continue
            img_name = t.get("name")
            if img_name not in cache:
                stem = os.path.splitext(t.get("basename") or img_name or "texture")[0]
                base = _png_name(stem, used)
                dest = os.path.join(tmpdir, base)
                ok = _reexport_image_to_png(img_name, dest)
                if ok:
                    used.add(base)
                    cache[img_name] = (dest, base)
                else:
                    cache[img_name] = None
            res = cache.get(img_name)
            if res:
                t["fileKind"], t["path"], t["basename"] = "path", res[0], res[1]
                reexported.add(_sanitize_basename(res[1]))
            # else: leave as-is -> mapper drops it -> reported as missing
    return reexported


def _write_zip(dest_path, name, save_obj, tex_bytes):
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    tmp = dest_path + ".tmp"
    json_bytes = json.dumps(save_obj, indent=2, ensure_ascii=False).encode("utf-8")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/{name}.json", json_bytes)
        for basename, data in tex_bytes.items():
            z.writestr(f"{name}/Textures/{basename}", data)
    if os.path.exists(dest_path):
        os.remove(dest_path)
    os.replace(tmp, dest_path)


def build_wlsave(norms, name, dest_path, skeleton_path=None):
    """Build `dest_path` (.wlsave) from NormalizedMaterial[] `norms` as collection `name`.

    Collection name, material names and texture basenames are sanitized to the
    game's filename charset (see _sanitize_name). Returns a report dict: name,
    nameOriginal, created[], renamed[], skipped[], texturesCopied[],
    texturesReExported[], texturesMissing[], texturesRenamed[].
    """
    name_original = name
    name = _sanitize_name(name, "Collection")
    if skeleton_path is None:
        skeleton_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skeleton.json")
    with open(skeleton_path, encoding="utf-8") as f:
        skel = json.load(f)

    report = {
        "name": name, "nameOriginal": name_original, "dest": dest_path,
        "created": [], "renamed": [], "skipped": [],
        "texturesCopied": [], "texturesReExported": [], "texturesMissing": [],
        "texturesRenamed": [],
    }

    tmpdir = tempfile.mkdtemp(prefix="wlsave_tex_")
    try:
        reexported = _resolve_packed_textures(norms, tmpdir)

        # Pass 1: map, sanitize + dedup names, gather unique textures by basename.
        taken, mapped, unique_tex = set(), [], {}
        for norm in norms:
            m = mapper.map_material(norm)
            if not m:
                report["skipped"].append(norm.get("name"))
                continue
            orig = m["entry"]["name"]
            final = _unique_name(_sanitize_name(orig, "Material"), taken)
            if final != orig:
                report["renamed"].append({"from": orig, "to": final})
            taken.add(final)
            m["entry"]["name"] = final
            mapped.append(m)
            for t in m["textures"]:
                raw = t.get("basename")
                if not raw:
                    continue
                b = _sanitize_basename(raw)
                t["basename"] = b
                if b not in unique_tex:
                    unique_tex[b] = t.get("srcPath")
                    if b != raw:
                        report["texturesRenamed"].append({"from": raw, "to": b})

        # Read texture bytes (dedup by basename); classify copied vs re-exported.
        tex_bytes = {}
        for b, src in unique_tex.items():
            if src and os.path.isfile(src):
                with open(src, "rb") as fh:
                    tex_bytes[b] = fh.read()
                (report["texturesReExported"] if b in reexported else report["texturesCopied"]).append(b)
            else:
                report["texturesMissing"].append({"basename": b, "src": src})

        # Pass 2: finalize entries with only resolved texture paths.
        entries = []
        for m in mapped:
            ok = [t for t in m["textures"] if t["basename"] in tex_bytes]
            entry = mapper.apply_texture_paths(m["entry"], ok, name)
            entry["name"] = m["entry"]["name"]
            entries.append(entry)
            report["created"].append(entry["name"])

        skel["level"] = ""
        skel["customMaterials"] = entries
        skel["bHasDedicatedIcon"] = False

        _write_zip(dest_path, name, skel, tex_bytes)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return report
