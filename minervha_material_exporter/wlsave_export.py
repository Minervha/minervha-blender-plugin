"""wlsave_export.py — build a portable .wlsave collection bundle (mode B).

Mirrors Minervha Studio's injectMaterials.js: map each NormalizedMaterial, dedup
material names, gather textures (deduped by basename), rewrite relative paths to
`<Name>/Textures/<basename>`, fill the bundled collection skeleton, and write a ZIP:

    <Name>/<Name>.json          # collection JSON (level "")
    <Name>/Textures/<file>      # bundled textures

Texture policy ("copy real, re-export the rest"): on-disk PNG/JPG that already satisfy
the export options are copied as-is; packed/generated images, other on-disk formats
(.tga, .exr…), and anything needing a format/resolution change are re-exported via
Blender in a pre-pass, so the mapper then sees them as ordinary path-kind textures.
Optional `tex_opts` drive that pre-pass: prefer JPG for opaque textures (alpha-bearing
images always stay PNG), a JPG quality, and a max-resolution downscale cap. UDIM/missing
stay unresolved (material created without that texture).

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


def _export_name(stem, ext, used):
    """A tmpdir-unique re-export file name `<sanitized stem><ext>` (ext includes the dot)."""
    base = re.sub(r"[\\/:\0]", "_", str(stem)).strip() or "texture"
    name = base + ext
    if name not in used:
        return name
    i = 2
    while f"{base}_{i}{ext}" in used:
        i += 1
    return f"{base}_{i}{ext}"


def _unique_basename(base, taken):
    """A bundle-unique texture basename: suffix `_2`, `_3`... before the extension."""
    if base not in taken:
        return base
    stem, ext = os.path.splitext(base)
    i = 2
    while f"{stem}_{i}{ext}" in taken:
        i += 1
    return f"{stem}_{i}{ext}"


# Image.depth is total bits across all channels. Blender always loads a 4-channel RGBA
# buffer (Image.channels is always 4), so depth — not channels — is what tells RGB from
# RGBA: grayscale=8, RGB(8)=24, RGB half=48, RGB float=96 carry NO alpha; 16/32/64/128 do.
# Any other / unknown / 0 (buffer not loaded) depth is treated as alpha-bearing, so we keep
# it PNG and never silently drop a real alpha channel.
_NO_ALPHA_DEPTHS = {8, 24, 48, 96}


def _image_facts(image_name):
    """(has_alpha, width, height) for a bpy image, queried defensively. Without bpy (or a
    missing image) returns (True, 0, 0): 'assume alpha' keeps the texture PNG (never drop an
    alpha channel) and a 0 size disables the resolution cap for it."""
    if bpy is None:
        return True, 0, 0
    img = bpy.data.images.get(image_name)
    if img is None:
        return True, 0, 0
    try:
        has_alpha = int(img.depth or 0) not in _NO_ALPHA_DEPTHS
    except Exception:
        has_alpha = True
    try:
        w, h = int(img.size[0]), int(img.size[1])
    except Exception:
        w, h = 0, 0
    return has_alpha, w, h


def _plan_texture(file_kind, ext, has_alpha, width, height, prefer_jpg, max_res):
    """Pure decision for one texture. Returns (target_format, needs_export):
      target_format — 'JPEG' or 'PNG' to write (None when nothing is needed).
      needs_export  — True to re-encode via Blender; False to copy the on-disk file as-is.

    A texture with an alpha channel always stays PNG (JPEG can't carry alpha). Otherwise
    `prefer_jpg` picks JPEG; with it off, an existing JPG stays JPG and everything else maps
    to PNG (mirrors the legacy 'packed/generated → PNG' policy). On-disk PNG/JPG already in
    the right format and within `max_res` are left to be copied untouched."""
    if file_kind in ("missing", "udim"):
        return None, False
    on_disk = file_kind == "path" and ext in _COPY_EXT
    if has_alpha:
        target = "PNG"
    elif prefer_jpg:
        target = "JPEG"
    elif ext in (".jpg", ".jpeg"):
        target = "JPEG"
    else:
        target = "PNG"
    too_big = bool(max_res) and (width > max_res or height > max_res)
    if on_disk:
        needs_convert = ext == ".png" and target == "JPEG"
        if not needs_convert and not too_big:
            return None, False          # already fine — copy as-is
    return target, True


def _export_image(image_name, dest_dir, used, target_format, jpg_quality, max_res):
    """Re-export a bpy image into `dest_dir` as `target_format` ('JPEG'|'PNG'), downscaled to
    fit `max_res` (longest side) when set. Mutates only a throwaway copy, so the user's image
    is never touched. Returns (dest_path, basename) on success, else None."""
    if bpy is None:
        return None
    img = bpy.data.images.get(image_name)
    if img is None:
        return None
    ext = ".jpg" if target_format == "JPEG" else ".png"
    stem = os.path.splitext(image_name or "texture")[0]
    base = _export_name(stem, ext, used)
    dest = os.path.join(dest_dir, base)
    tmp = img.copy()
    try:
        if max_res:
            w, h = int(tmp.size[0]), int(tmp.size[1])
            if w and h and (w > max_res or h > max_res):
                scale = max_res / float(max(w, h))
                tmp.scale(max(1, round(w * scale)), max(1, round(h * scale)))
        tmp.file_format = target_format
        tmp.filepath_raw = dest
        if target_format == "JPEG":
            tmp.save(quality=int(jpg_quality))
        else:
            tmp.save()
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            used.add(base)
            return dest, base
        return None
    except Exception:
        return None
    finally:
        try:
            bpy.data.images.remove(tmp)
        except Exception:
            pass


def _process_textures(norms, tmpdir, prefer_jpg=False, jpg_quality=90, max_res=None):
    """Pre-pass over every texture in `norms`. Re-exports packed/generated/non-copyable
    images and (per `_plan_texture`) converts opaque textures to JPG and/or downscales to
    `max_res`, writing the result into `tmpdir` and mutating the texture dict in place into a
    plain on-disk path texture. On-disk PNG/JPG that already satisfy the options are left
    alone (copied as-is downstream). Returns the set of re-exported source paths (the tmpdir
    files) — keyed on srcPath, not basename, so the copied-vs-re-exported classification
    survives a later collision rename of the bundled basename. With `tex_opts` off
    (prefer_jpg False, max_res None) this reproduces the legacy 'packed/generated → PNG'
    behavior; without bpy it re-exports nothing (pure-Python tests copy on-disk files)."""
    cache = {}      # image_name -> (path, basename) | None
    used = set()    # basenames already emitted into tmpdir
    reexported = set()
    for norm in norms:
        for t in norm.get("textures") or []:
            kind = t.get("fileKind")
            if kind in ("missing", "udim"):
                continue  # nothing to re-export (UDIM unsupported in v1)
            img_name = t.get("name")
            if img_name not in cache:
                ext = os.path.splitext(t.get("path") or t.get("basename") or "")[1].lower()
                has_alpha, w, h = _image_facts(img_name)
                target, needs = _plan_texture(kind, ext, has_alpha, w, h, prefer_jpg, max_res)
                cache[img_name] = (_export_image(img_name, tmpdir, used, target, jpg_quality, max_res)
                                   if needs else None)
            res = cache.get(img_name)
            if res:
                t["fileKind"], t["path"], t["basename"] = "path", res[0], res[1]
                reexported.add(res[0])      # srcPath of the re-exported file
            # else: on-disk path -> copy as-is; packed/generated that failed -> mapper drops it
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
        "texturesRenamed": [], "missingDetail": [],
    }


def _build_material_entries(norms, name, report, tmpdir, tex_opts=None):
    """Map -> sanitize+dedup+namespace material names -> gather textures. Mutates `report`.

    Material names are namespaced "<name>/<final>" (both export modes); a prop's
    CustomMaterial{i} references these exact strings. The "/" is legal in the `name`
    field (an internal reference, not a filename). `tex_opts` (or None) drives the texture
    pre-pass: {prefer_jpg, jpg_quality, max_res}. Returns (entries, tex_bytes,
    material_names) where material_names maps the original Blender material name ->
    its final namespaced customMaterials name.
    """
    opts = tex_opts or {}
    reexported = _process_textures(norms, tmpdir,
                                   prefer_jpg=bool(opts.get("prefer_jpg")),
                                   jpg_quality=int(opts.get("jpg_quality", 90)),
                                   max_res=opts.get("max_res"))

    # Per-texture "what didn't make it, on which mesh, and why" detail (grouped by texture).
    #   missing:       (texture, reason) -> {texture, reason, materials/channels/objects: set}
    #   usage_by_base: final bundled basename -> the same usage sets, so an on-disk 'file not
    #                  found' (detected when reading bytes below) can be tied back to its
    #                  material(s)/mesh(es)/channel(s).
    missing, usage_by_base = {}, {}

    def _flag_missing(texture, reason, channel, material, objs):
        rec = missing.get((texture or "?", reason))
        if rec is None:
            rec = missing[(texture or "?", reason)] = {
                "texture": texture or "?", "reason": reason,
                "materials": set(), "channels": set(), "objects": set()}
        if material:
            rec["materials"].add(material)
        if channel:
            rec["channels"].add(channel)
        rec["objects"].update(objs or ())

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
        objs = norm.get("objects") or []                # meshes/objects using this material
        final = _unique_name(_sanitize_name(orig, "Material"), taken)
        if final != orig:
            report["renamed"].append({"from": orig, "to": final})
        taken.add(final)
        namespaced = f"{name}/{final}"
        m["entry"]["name"] = namespaced
        material_names[orig] = namespaced
        mapped.append(m)
        # packed / generated / udim / missing-path textures: mapper found no file to copy.
        for u in m["report"].get("unresolvedTextures", []):
            _flag_missing(u.get("texture"), u.get("fileKind") or "unresolved",
                          u.get("channel"), orig, objs)
        for t in m["textures"]:
            raw = t.get("basename")
            if not raw:
                continue
            src = t.get("srcPath")
            if src is not None and src in by_src:
                t["basename"] = by_src[src]             # same file reused -> one bundled copy
            else:
                b = _sanitize_basename(raw)
                if b in taken_bases:                    # basename clash with a DIFFERENT source
                    b = _unique_basename(b, taken_bases)
                    report["texturesRenamed"].append({"from": raw, "to": b})
                elif b != raw:
                    report["texturesRenamed"].append({"from": raw, "to": b})
                taken_bases.add(b)
                t["basename"] = b
                unique_tex[b] = src
                if src is not None:
                    by_src[src] = b
            # remember which material/mesh/channel uses this bundled file (for the on-disk
            # 'file not found' tie-back when reading bytes below).
            usg = usage_by_base.setdefault(
                t["basename"], {"materials": set(), "channels": set(), "objects": set()})
            usg["materials"].add(orig)
            if t.get("channel"):
                usg["channels"].add(t["channel"])
            usg["objects"].update(objs)

    # Read texture bytes (one per bundled basename); classify copied vs re-exported by srcPath.
    tex_bytes = {}
    for b, src in unique_tex.items():
        if src and os.path.isfile(src):
            with open(src, "rb") as fh:
                tex_bytes[b] = fh.read()
            (report["texturesReExported"] if src in reexported else report["texturesCopied"]).append(b)
        else:
            report["texturesMissing"].append({"basename": b, "src": src})
            usg = usage_by_base.get(b) or {}
            rec = missing.get((b, "file not found"))
            if rec is None:
                rec = missing[(b, "file not found")] = {
                    "texture": b, "reason": "file not found",
                    "materials": set(), "channels": set(), "objects": set()}
            rec["materials"].update(usg.get("materials") or ())
            rec["channels"].update(usg.get("channels") or ())
            rec["objects"].update(usg.get("objects") or ())

    # Pass 2: finalize entries with only resolved texture paths.
    entries = []
    for m in mapped:
        ok = [t for t in m["textures"] if t["basename"] in tex_bytes]
        entry = mapper.apply_texture_paths(m["entry"], ok, name)
        entry["name"] = m["entry"]["name"]
        entries.append(entry)
        report["created"].append(entry["name"])

    # Flatten the grouped detail into the report (deterministic order: by texture then reason).
    report["missingDetail"] = [
        {"texture": r["texture"], "reason": r["reason"],
         "materials": sorted(r["materials"]), "channels": sorted(r["channels"]),
         "objects": sorted(r["objects"])}
        for _, r in sorted(missing.items(), key=lambda kv: kv[0])
    ]

    return entries, tex_bytes, material_names


def build_wlsave(norms, name, dest_path, skeleton_path=None, tex_opts=None):
    """Build `dest_path` (.wlsave) from NormalizedMaterial[] `norms` as collection `name`.

    Materials-only bundle. Collection name, material names and texture basenames are
    sanitized to the game's filename charset (see _sanitize_name); material names are
    additionally namespaced "<Collection>/<Mat>". `tex_opts` (or None) drives the texture
    pre-pass: {prefer_jpg, jpg_quality, max_res}. Returns a report dict: name,
    nameOriginal, created[], renamed[], skipped[], texturesCopied[],
    texturesReExported[], texturesMissing[], texturesRenamed[].
    """
    name_original = name
    name = _sanitize_name(name, "Collection")
    skel = _load_skeleton(skeleton_path)
    report = _new_report(name, name_original, dest_path)

    tmpdir = tempfile.mkdtemp(prefix="wlsave_tex_")
    try:
        entries, tex_bytes, _ = _build_material_entries(norms, name, report, tmpdir, tex_opts)
        skel["level"] = ""
        skel["customMaterials"] = entries
        skel["bHasDedicatedIcon"] = False
        _write_zip(dest_path, name, skel, tex_bytes)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return report


def build_scene_wlsave(norms, norm_objects, name, dest_path, obj_exporter, skeleton_path=None,
                       position_scale=1.0, level="", tex_opts=None, material_baker=None):
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
    `tex_opts`     — texture pre-pass options (or None): {prefer_jpg, jpg_quality, max_res}.
    `material_baker` — optional callable `(norms) -> baked[]`, run BEFORE mapping. Bakes the
                     channels mapper flagged as `bakeCandidates` (procedural / multi-texture /
                     divergent-UV) into PNGs and injects them into `norms` as baked path textures,
                     so the rest of the pipeline treats them as ordinary on-disk textures. bpy-side
                     and Scene-mode only (baking needs an object+UV); the UI builds it, tests pass
                     None. Returns the list recorded as report["materialsBaked"].

    One OBJ per unique mesh datablock (instances reuse the same MeshPath). Material names are
    namespaced; each prop's CustomMaterial{i} references them by that exact name. Returns the
    materials report extended with: objectsExported[], objectsSkipped[], noUv[],
    proceduralMaterials[], meshesWritten[], materialsBaked[], materialNamespaced, level.
    """
    name_original = name
    name = _sanitize_name(name, "Collection")
    skel = _load_skeleton(skeleton_path)
    report = _new_report(name, name_original, dest_path)
    report.update({"objectsExported": [], "objectsSkipped": [], "noUv": [],
                   "proceduralMaterials": [], "meshesWritten": [], "meshExportFailed": [],
                   "materialsBaked": [], "materialNamespaced": True, "level": level})

    tmpdir = tempfile.mkdtemp(prefix="wlsave_scene_")
    try:
        # Bake pre-pass (Scene mode only): flatten flagged channels into PNGs injected into `norms`
        # BEFORE mapping, so _process_textures/mapper see them as ordinary path textures.
        if material_baker is not None:
            report["materialsBaked"] = material_baker(norms) or []
        entries, tex_bytes, material_names = _build_material_entries(norms, name, report, tmpdir, tex_opts)

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
