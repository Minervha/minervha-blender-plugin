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
    from . import mapper, prop_mapper   # packaged extension
except ImportError:                      # dev / sys.path import (tests, live MCP)
    import mapper
    import prop_mapper

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


def _unique_basename(base, taken):
    """A bundle-unique texture basename: suffix `_2`, `_3`... before the extension."""
    if base not in taken:
        return base
    stem, ext = os.path.splitext(base)
    i = 2
    while f"{stem}_{i}{ext}" in taken:
        i += 1
    return f"{stem}_{i}{ext}"


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
    re-exported textures' source paths (the tmpdir PNGs) — keyed on srcPath, not
    basename, so the copied-vs-re-exported classification survives a later collision
    rename of the bundled basename."""
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
                reexported.add(res[0])      # srcPath of the re-exported PNG
            # else: leave as-is -> mapper drops it -> reported as missing
    return reexported


def _write_zip(dest_path, name, save_obj, tex_bytes, model_bytes=None):
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    tmp = dest_path + ".tmp"
    json_bytes = json.dumps(save_obj, indent=2, ensure_ascii=False).encode("utf-8")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/{name}.json", json_bytes)
        for basename, data in tex_bytes.items():
            z.writestr(f"{name}/Textures/{basename}", data)
        for basename, data in (model_bytes or {}).items():
            z.writestr(f"{name}/Models/{basename}", data)
    if os.path.exists(dest_path):
        os.remove(dest_path)
    os.replace(tmp, dest_path)


def _load_skeleton(skeleton_path):
    if skeleton_path is None:
        skeleton_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skeleton.json")
    with open(skeleton_path, encoding="utf-8") as f:
        return json.load(f)


def _new_report(name, name_original, dest_path):
    return {
        "name": name, "nameOriginal": name_original, "dest": dest_path,
        "created": [], "renamed": [], "skipped": [],
        "texturesCopied": [], "texturesReExported": [], "texturesMissing": [],
        "texturesRenamed": [],
    }


def _build_material_entries(norms, name, report, tmpdir):
    """Map -> sanitize+dedup+namespace material names -> gather textures. Mutates `report`.

    Material names are namespaced "<name>/<final>" (both export modes); a prop's
    CustomMaterial{i} references these exact strings. The "/" is legal in the `name`
    field (an internal reference, not a filename). Returns (entries, tex_bytes,
    material_names) where material_names maps the original Blender material name ->
    its final namespaced customMaterials name.
    """
    reexported = _resolve_packed_textures(norms, tmpdir)

    # Pass 1: map, sanitize + dedup + namespace names, gather unique textures by SOURCE path.
    # Textures are deduped by srcPath, NOT by basename: two distinct files that share a basename
    # (same name in different folders, or names that sanitise/NFKD-collapse to the same ASCII string)
    # must each get a distinct bundled basename — keying on basename alone made the second file
    # silently overwrite the first in the ZIP, so both materials pointed at one texture. The same file
    # reused across channels/materials still dedups to a single bundled copy.
    taken, mapped, unique_tex, material_names = set(), [], {}, {}
    by_src, taken_bases = {}, set()
    for norm in norms:
        m = mapper.map_material(norm)
        if not m:
            report["skipped"].append(norm.get("name"))
            continue
        orig = m["entry"]["name"]                       # = Blender material name
        final = _unique_name(_sanitize_name(orig, "Material"), taken)
        if final != orig:
            report["renamed"].append({"from": orig, "to": final})
        taken.add(final)
        namespaced = f"{name}/{final}"
        m["entry"]["name"] = namespaced
        material_names[orig] = namespaced
        mapped.append(m)
        for t in m["textures"]:
            raw = t.get("basename")
            if not raw:
                continue
            src = t.get("srcPath")
            if src is not None and src in by_src:
                t["basename"] = by_src[src]             # same file reused -> one bundled copy
                continue
            b = _sanitize_basename(raw)
            if b in taken_bases:                        # basename clash with a DIFFERENT source
                b = _unique_basename(b, taken_bases)
                report["texturesRenamed"].append({"from": raw, "to": b})
            elif b != raw:
                report["texturesRenamed"].append({"from": raw, "to": b})
            taken_bases.add(b)
            t["basename"] = b
            unique_tex[b] = src
            if src is not None:
                by_src[src] = b

    # Read texture bytes (one per bundled basename); classify copied vs re-exported by srcPath.
    tex_bytes = {}
    for b, src in unique_tex.items():
        if src and os.path.isfile(src):
            with open(src, "rb") as fh:
                tex_bytes[b] = fh.read()
            (report["texturesReExported"] if src in reexported else report["texturesCopied"]).append(b)
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

    return entries, tex_bytes, material_names


def build_wlsave(norms, name, dest_path, skeleton_path=None):
    """Build `dest_path` (.wlsave) from NormalizedMaterial[] `norms` as collection `name`.

    Materials-only bundle. Collection name, material names and texture basenames are
    sanitized to the game's filename charset (see _sanitize_name); material names are
    additionally namespaced "<Collection>/<Mat>". Returns a report dict: name,
    nameOriginal, created[], renamed[], skipped[], texturesCopied[],
    texturesReExported[], texturesMissing[], texturesRenamed[].
    """
    name_original = name
    name = _sanitize_name(name, "Collection")
    skel = _load_skeleton(skeleton_path)
    report = _new_report(name, name_original, dest_path)

    tmpdir = tempfile.mkdtemp(prefix="wlsave_tex_")
    try:
        entries, tex_bytes, _ = _build_material_entries(norms, name, report, tmpdir)
        skel["level"] = ""
        skel["customMaterials"] = entries
        skel["bHasDedicatedIcon"] = False
        _write_zip(dest_path, name, skel, tex_bytes)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return report


def build_scene_wlsave(norms, norm_objects, name, dest_path, obj_exporter, skeleton_path=None,
                       position_scale=1.0, level=""):
    """Build a full-scene `.wlsave`: customMaterials + props (UserMesh/Group) + Models/ OBJs.

    `norms`        — NormalizedMaterial[] for the materials used by the in-scope objects.
    `norm_objects` — NormalizedObject[] (scene_introspect output) in export order.
    `obj_exporter` — callable (mesh_key, dest_dir, used_basenames) -> basename | None. It
                     writes one OBJ (+ optional sibling .mtl) into dest_dir and returns its
                     basename. Production wires it to obj_export over the live bpy objects;
                     tests inject a fake, so this assembly stays Blender-free and unit-testable.
    `position_scale` — world scale factor (1 / scene Unit Scale) applied to prop positions; the
                     same factor must be passed to obj_exporter as its geometry global_scale.
    `level`        — the save's `level` field. `""` (default) = a portable collection; a fixed map
                     name ("Showroom"/"NewWildLifeMap"/"OldWildLifeMap") = a map save the Studio
                     installs under MySaves/<level>/. Only the JSON field changes — the ZIP layout
                     is identical either way.

    One OBJ per unique mesh datablock (instances reuse the same MeshPath). Material names are
    namespaced; each prop's CustomMaterial{i} references them by that exact name. Returns the
    materials report extended with: objectsExported[], objectsSkipped[], noUv[],
    proceduralMaterials[], meshesWritten[], materialNamespaced, level.
    """
    name_original = name
    name = _sanitize_name(name, "Collection")
    skel = _load_skeleton(skeleton_path)
    report = _new_report(name, name_original, dest_path)
    report.update({"objectsExported": [], "objectsSkipped": [], "noUv": [],
                   "proceduralMaterials": [], "meshesWritten": [], "meshExportFailed": [],
                   "materialNamespaced": True, "level": level})

    tmpdir = tempfile.mkdtemp(prefix="wlsave_scene_")
    try:
        entries, tex_bytes, material_names = _build_material_entries(norms, name, report, tmpdir)

        # One OBJ per unique mesh datablock (instances reuse it).
        models_dir = os.path.join(tmpdir, "Models")
        os.makedirs(models_dir, exist_ok=True)
        mesh_path_by_key, model_bytes, used_basenames, failed_keys = {}, {}, set(), set()
        for o in norm_objects:
            key = o.get("mesh_key")
            if not key or key in mesh_path_by_key or key in failed_keys:
                continue
            basename = obj_exporter(key, models_dir, used_basenames)
            if not basename:
                # Export failed: record it (so it isn't silent), don't retry the same datablock.
                # Objects of this key keep MeshPath "" — still placed (hierarchy intact), no geometry.
                failed_keys.add(key)
                report["meshExportFailed"].append(key)
                continue
            used_basenames.add(basename)
            obj_path = os.path.join(models_dir, basename)
            if not os.path.isfile(obj_path):
                continue
            with open(obj_path, "rb") as fh:
                model_bytes[basename] = fh.read()
            mesh_path_by_key[key] = f"{name}/Models/{basename}"
            report["meshesWritten"].append(basename)
            mtl = os.path.splitext(basename)[0] + ".mtl"     # sibling .mtl, if the exporter wrote one
            mtl_path = os.path.join(models_dir, mtl)
            if os.path.isfile(mtl_path):
                with open(mtl_path, "rb") as fh:
                    model_bytes[mtl] = fh.read()

        # Build props; tally the report.
        props = []
        for o in norm_objects:
            mp = mesh_path_by_key.get(o.get("mesh_key"))
            props.append(prop_mapper.map_object(o, mesh_path=mp, material_names=material_names,
                                                position_scale=position_scale))
            if o.get("kind") == "mesh":
                report["objectsExported"].append(o.get("name"))
                v = o.get("validation") or {}
                if not v.get("has_uv"):
                    report["noUv"].append(o.get("name"))
                for pm in (v.get("procedural_materials") or []):
                    if pm not in report["proceduralMaterials"]:
                        report["proceduralMaterials"].append(pm)

        skel["level"] = level
        skel["customMaterials"] = entries
        skel["props"] = props
        skel["bHasDedicatedIcon"] = False
        _write_zip(dest_path, name, skel, tex_bytes, model_bytes)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return report
