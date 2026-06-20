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


def _material_uses_alpha(norm):
    """True if the material actually USES transparency — its Principled Alpha is connected
    (`alphaLinked`) or set to a constant < 1. This is the WL-relevant signal for whether a
    baked Base Color texture must carry an alpha channel (a Masked/alpha-tested look): a flat
    RGB diffuse bake (no alpha) silently drops the mask, so foliage/glass render as solid quads.
    Pure transmission/refraction glass (Alpha = 1, unlinked) returns False — its transparency is
    refractive, not a diffuse mask."""
    if norm.get("alphaLinked"):
        return True
    a = norm.get("alpha")
    return a is not None and float(a) < 0.9999      # ≠ 1 (small margin ignores float noise)


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
        # Materialize the pixel buffer BEFORE touching file_format. A file image that was
        # never displayed (typical for a .tga normal map) loads lazily — has_data stays False
        # until something reads it. The old order set file_format='PNG' first, so save()'s lazy
        # load then tried to decode the SOURCE .tga as PNG and raised, which the broad except
        # swallowed: every .tga was silently dropped and the raw (game-unreadable) file copied
        # as-is. Reading one pixel forces the load in the source's own format; packed/generated
        # images already have data, so this is a no-op for them.
        if not tmp.has_data:
            tmp.pixels[0]
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


# ── Progress protocol ──────────────────────────────────────────────────────
# The `_iter_*` builders are generators that `yield (phase, done, total)` at every per-item
# loop (so a modal operator can step them and stay responsive) and `return` the final report.
# The plain `build_*` / `_process_textures` / `_build_material_entries` names are kept as thin
# synchronous wrappers (run the generator to completion) so existing callers + the pure tests
# are unchanged. phases: "bake" | "textures" | "map" | "read" | "meshes" | "props" | "zip".

def _drain(gen):
    """Run a progress generator to completion, ignoring its yields; return the value it
    `return`s (PEP 380 StopIteration.value). The synchronous wrappers use this."""
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


def _process_textures(norms, tmpdir, prefer_jpg=False, jpg_quality=90, max_res=None):
    """Synchronous wrapper over `_iter_process_textures` (kept for direct callers/tests)."""
    return _drain(_iter_process_textures(norms, tmpdir, prefer_jpg, jpg_quality, max_res))


def _iter_process_textures(norms, tmpdir, prefer_jpg=False, jpg_quality=90, max_res=None):
    """Pre-pass over every texture in `norms`. Re-exports packed/generated/non-copyable
    images and (per `_plan_texture`) converts opaque textures to JPG and/or downscales to
    `max_res`, writing the result into `tmpdir` and mutating the texture dict in place into a
    plain on-disk path texture. On-disk PNG/JPG that already satisfy the options are left
    alone (copied as-is downstream). Returns the set of re-exported source paths (the tmpdir
    files) — keyed on srcPath, not basename, so the copied-vs-re-exported classification
    survives a later collision rename of the bundled basename. With `tex_opts` off
    (prefer_jpg False, max_res None) this reproduces the legacy 'packed/generated → PNG'
    behavior; without bpy it re-exports nothing (pure-Python tests copy on-disk files).

    Generator: yields ("textures", i, total) after each material's textures are processed."""
    cache = {}      # image_name -> (path, basename) | None
    used = set()    # basenames already emitted into tmpdir
    reexported = set()
    total = len(norms)
    for i, norm in enumerate(norms, 1):
        for t in norm.get("textures") or []:
            if t.get("baked"):
                continue  # the baker already wrote the final format/resolution; re-encoding here
                          # would only fail (its bpy image is gone) and silently keep the raw PNG
            slots = t.get("slots") or []
            ds = t.get("directSlots")
            check = slots if ds is None else ds   # only DIRECT slots ship; absent -> all slots (compat)
            if slots and not any(mapper.channel_for_slot(s) for s in check):
                continue  # feeds no WL channel directly (pure helper / transformed) -> never bundled raw
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
        yield ("textures", i, total)
    return reexported


def _prepare_icon(path, max_side=512):
    """A user-chosen thumbnail image -> PNG bytes (longest side <= `max_side`), or None.

    With bpy: load any Blender-readable format, downscale to fit `max_side` (aspect kept),
    re-encode PNG — so the bundle's icon is always a PNG (the Studio reader matches the first
    `.png` entry) regardless of the source format. Without bpy (pure-Python tests): accept an
    on-disk `.png` as-is, reject anything else. Best-effort — never raises."""
    if not path or not os.path.isfile(path):
        return None
    if bpy is None:
        if os.path.splitext(path)[1].lower() == ".png":
            with open(path, "rb") as f:
                return f.read()
        return None
    img = None
    icon_dir = None
    try:
        img = bpy.data.images.load(path, check_existing=False)
        if not img.has_data:
            img.pixels[0]                       # force the lazy load in the source format
        w, h = int(img.size[0]), int(img.size[1])
        if w and h and max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            img.scale(max(1, round(w * scale)), max(1, round(h * scale)))
        icon_dir = tempfile.mkdtemp(prefix="wlsave_icon_")
        out = os.path.join(icon_dir, "icon.png")
        img.file_format = "PNG"
        img.filepath_raw = out
        img.save()
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            with open(out, "rb") as f:
                return f.read()
        return None
    except Exception:
        return None
    finally:
        if img is not None:
            try:
                bpy.data.images.remove(img)
            except Exception:
                pass
        if icon_dir:
            shutil.rmtree(icon_dir, ignore_errors=True)


def _write_zip(dest_path, name, save_obj, tex_bytes, model_bytes=None, icon_bytes=None):
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    tmp = dest_path + ".tmp"
    json_bytes = json.dumps(save_obj, indent=2, ensure_ascii=False).encode("utf-8")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/{name}.json", json_bytes)
        # The thumbnail MUST precede every Textures/ PNG: the Studio reader takes the FIRST
        # `.png` entry in archive order as the save's icon (wlsaveOps.extractFirstPngFromZip).
        if icon_bytes:
            z.writestr(f"{name}/{name}.png", icon_bytes)
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
        "texturesRenamed": [], "missingDetail": [], "needsBake": [],
    }


def _build_material_entries(norms, name, report, tmpdir, tex_opts=None, surface_type="SurfaceType_Default"):
    """Synchronous wrapper over `_iter_build_material_entries` (kept for direct callers/tests)."""
    return _drain(_iter_build_material_entries(norms, name, report, tmpdir, tex_opts, surface_type))


def _iter_build_material_entries(norms, name, report, tmpdir, tex_opts=None, surface_type="SurfaceType_Default"):
    """Map -> sanitize+dedup+namespace material names -> gather textures. Mutates `report`.

    Material names are namespaced "<name>/<final>" (both export modes); a prop's
    CustomMaterial{i} references these exact strings. The "/" is legal in the `name`
    field (an internal reference, not a filename). `tex_opts` (or None) drives the texture
    pre-pass: {prefer_jpg, jpg_quality, max_res}. `surface_type` is the WL EPhysicalSurface
    name written to every material's `surfaceType` (one scene-wide choice). Returns (entries, tex_bytes,
    material_names) where material_names maps the original Blender material name ->
    its final namespaced customMaterials name.

    Generator: yields ("textures"/"map"/"read", done, total) through its loops."""
    opts = tex_opts or {}
    reexported = yield from _iter_process_textures(norms, tmpdir,
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
    n_norms = len(norms)
    for mi, norm in enumerate(norms, 1):
        if mi % 128 == 0:
            yield ("map", mi, n_norms)
        m = mapper.map_material(norm, surface_type)
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
        # channels left empty because the texture was procedural/blended and not baked (Bake off).
        nb = m["report"].get("needsBake") or []
        if nb:
            report["needsBake"].append({"material": orig, "channels": nb})
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
    n_tex = len(unique_tex)
    for ri, (b, src) in enumerate(unique_tex.items(), 1):
        if ri % 32 == 0:
            yield ("read", ri, n_tex)
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


def build_wlsave(norms, name, dest_path, skeleton_path=None, tex_opts=None, thumbnail=None,
                 surface_type="SurfaceType_Default"):
    """Build `dest_path` (.wlsave) from NormalizedMaterial[] `norms` as collection `name`.

    Materials-only bundle. Collection name, material names and texture basenames are
    sanitized to the game's filename charset (see _sanitize_name); material names are
    additionally namespaced "<Collection>/<Mat>". `tex_opts` (or None) drives the texture
    pre-pass: {prefer_jpg, jpg_quality, max_res}. `surface_type` is the WL EPhysicalSurface
    name written to every material's `surfaceType`. `thumbnail` (or None) is a path to an image
    file bundled as the save's icon `<Name>/<Name>.png` (set `bHasDedicatedIcon`). Returns a
    report dict: name, nameOriginal, created[], renamed[], skipped[], texturesCopied[],
    texturesReExported[], texturesMissing[], texturesRenamed[], thumbnail.
    """
    return _drain(_iter_build_wlsave(norms, name, dest_path, skeleton_path, tex_opts, thumbnail, surface_type))


def _iter_build_wlsave(norms, name, dest_path, skeleton_path=None, tex_opts=None, thumbnail=None,
                       surface_type="SurfaceType_Default"):
    """Generator form of `build_wlsave` — yields progress, returns the report. Owns its tmpdir
    in a `try/finally`, so `gen.close()` on a cancelled modal run still cleans it up."""
    name_original = name
    name = _sanitize_name(name, "Collection")
    skel = _load_skeleton(skeleton_path)
    report = _new_report(name, name_original, dest_path)

    tmpdir = tempfile.mkdtemp(prefix="wlsave_tex_")
    try:
        entries, tex_bytes, _ = yield from _iter_build_material_entries(
            norms, name, report, tmpdir, tex_opts, surface_type)
        icon_bytes = _prepare_icon(thumbnail)
        skel["level"] = ""
        skel["customMaterials"] = entries
        skel["bHasDedicatedIcon"] = bool(icon_bytes)
        report["thumbnail"] = bool(icon_bytes)
        yield ("zip", 0, 1)
        _write_zip(dest_path, name, skel, tex_bytes, icon_bytes=icon_bytes)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return report


def build_scene_wlsave(norms, norm_objects, name, dest_path, obj_exporter, skeleton_path=None,
                       position_scale=1.0, level="", tex_opts=None, material_baker=None,
                       thumbnail=None, master_group=False, enable_collision=False,
                       surface_type="SurfaceType_Default"):
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
    `thumbnail`    — optional path to an image bundled as the save icon `<Name>/<Name>.png`
                     (sets `bHasDedicatedIcon`); see `_prepare_icon`.
    `master_group` — when True, wrap every otherwise-root prop under one synthetic root `Group`
                     named after the save (`prop_mapper.master_group`), so the in-game scene has a
                     single top node. Identity transform keeps every child's placement.
    `enable_collision` — drive every UserMesh's `boolSettings.EnableCollision` (one scene-wide toggle).
    `surface_type` — the WL EPhysicalSurface name written to every material's `surfaceType` (one
                     scene-wide choice; e.g. "SurfaceType3"=Stone, "SurfaceType2"=Sand).

    One OBJ per unique mesh datablock (instances reuse the same MeshPath). Material names are
    namespaced; each prop's CustomMaterial{i} references them by that exact name. Returns the
    materials report extended with: objectsExported[], objectsSkipped[], noUv[],
    proceduralMaterials[], meshesWritten[], materialsBaked[], materialNamespaced, level,
    thumbnail, masterGroup.
    """
    return _drain(_iter_build_scene_wlsave(
        norms, norm_objects, name, dest_path, obj_exporter, skeleton_path, position_scale, level,
        tex_opts, material_baker, thumbnail, master_group, enable_collision, surface_type))


def _iter_build_scene_wlsave(norms, norm_objects, name, dest_path, obj_exporter, skeleton_path=None,
                             position_scale=1.0, level="", tex_opts=None, material_baker=None,
                             thumbnail=None, master_group=False, enable_collision=False,
                             surface_type="SurfaceType_Default"):
    """Generator form of `build_scene_wlsave` — yields ("bake"/"textures"/"map"/"read"/"meshes"/
    "props"/"zip", done, total) and returns the report. Owns its tmpdir in a try/finally so a
    cancelled modal run (`gen.close()`) still cleans up. `material_baker` may be a plain callable
    `(norms)->baked[]` OR a generator factory (yields bake progress, returns baked[])."""
    name_original = name
    name = _sanitize_name(name, "Collection")
    skel = _load_skeleton(skeleton_path)
    report = _new_report(name, name_original, dest_path)
    report.update({"objectsExported": [], "objectsSkipped": [], "noUv": [],
                   "proceduralMaterials": [], "meshesWritten": [], "meshExportFailed": [],
                   "materialsBaked": [], "materialsApproximated": [], "bakeFailed": [],
                   "materialNamespaced": True, "level": level,
                   "thumbnail": False, "masterGroup": None, "enableCollision": bool(enable_collision)})

    tmpdir = tempfile.mkdtemp(prefix="wlsave_scene_")
    try:
        # Bake pre-pass (Scene mode only): flatten flagged channels into PNGs injected into `norms`
        # BEFORE mapping, so _process_textures/mapper see them as ordinary path textures.
        if material_baker is not None:
            res = material_baker(norms)
            # A generator factory yields bake progress and returns baked[]; a plain callable just
            # returns baked[]. Support both so the UI can step the (slow) bake while tests pass None.
            report["materialsBaked"] = ((yield from res) if hasattr(res, "__next__") else res) or []
            # A baked material whose look depends on per-mesh data (vertex colors / object-space)
            # can't be faithful as ONE shared texture — flag it (baked to a representative state).
            _baked_names = {b[0] for b in report["materialsBaked"]}
            report["materialsApproximated"] = [
                {"material": n.get("name"), "reasons": n.get("perMeshDependency")}
                for n in norms
                if n.get("name") in _baked_names and n.get("perMeshDependency")]
            # Materials the baker tagged as failed (a bake raised, isolated per-material so the run
            # survives): the channel stays un-baked and degrades like Bake-off. Surface them, never
            # lose the whole export over one bad material.
            report["bakeFailed"] = [{"material": n.get("name"), "error": n.get("bakeFailed")}
                                    for n in norms if n.get("bakeFailed")]
        entries, tex_bytes, material_names = yield from _iter_build_material_entries(
            norms, name, report, tmpdir, tex_opts, surface_type)

        # One OBJ per unique mesh datablock (instances reuse it). This loop is the export's long
        # pole (one wm.obj_export per datablock) — yield per datablock so a modal driver stays
        # responsive and shows mesh progress.
        models_dir = os.path.join(tmpdir, "Models")
        os.makedirs(models_dir, exist_ok=True)
        mesh_path_by_key, model_bytes, used_basenames, failed_keys = {}, {}, set(), set()
        total_meshes = len({o.get("mesh_key") for o in norm_objects if o.get("mesh_key")})
        done_meshes = 0
        for o in norm_objects:
            key = o.get("mesh_key")
            if not key or key in mesh_path_by_key or key in failed_keys:
                continue
            done_meshes += 1
            yield ("meshes", done_meshes, total_meshes)
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
        n_objs = len(norm_objects)
        for pi, o in enumerate(norm_objects, 1):
            mp = mesh_path_by_key.get(o.get("mesh_key"))
            props.append(prop_mapper.map_object(o, mesh_path=mp, material_names=material_names,
                                                position_scale=position_scale,
                                                enable_collision=enable_collision))
            if o.get("kind") == "mesh":
                report["objectsExported"].append(o.get("name"))
                v = o.get("validation") or {}
                if not v.get("has_uv"):
                    report["noUv"].append(o.get("name"))
                for pm in (v.get("procedural_materials") or []):
                    if pm not in report["proceduralMaterials"]:
                        report["proceduralMaterials"].append(pm)
            if pi % 256 == 0:
                yield ("props", pi, n_objs)

        # Optional master group: a single synthetic root Group parents every otherwise-root prop,
        # so the imported scene hangs off one node. Its identity transform keeps children placed.
        if master_group and props:
            mg = prop_mapper.master_group(name)
            mg_guid, root = mg["guid"], prop_mapper.root_guid()
            for p in props:
                if p.get("parent") == root:
                    p["parent"] = mg_guid
            props.insert(0, mg)
            report["masterGroup"] = mg["label"]

        icon_bytes = _prepare_icon(thumbnail)
        skel["level"] = level
        skel["customMaterials"] = entries
        skel["props"] = props
        skel["bHasDedicatedIcon"] = bool(icon_bytes)
        report["thumbnail"] = bool(icon_bytes)
        yield ("zip", 0, 1)
        _write_zip(dest_path, name, skel, tex_bytes, model_bytes, icon_bytes=icon_bytes)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return report


def format_export_log(report, *, mode="scene", scope="", level="", options="", dest="",
                      elapsed=0.0, cancelled=False, timeline=None, error=None):
    """Render an export `report` (+ run context) as a human-readable text log. Pure — the UI
    writes the result to the single overwritten last-export log and shows it in the panel.
    `timeline` is a list of (phase_label, seconds). `error` (text, with traceback) marks the run
    FAILED and is appended verbatim so a crashed export leaves a real diagnostic, not a stale OK."""
    lines = []
    add = lines.append
    add("Minervha export log — last run")
    add("=" * 32)
    if error:
        add("Result: FAILED — %s" % (error.strip().splitlines() or ["error"])[0])
    elif cancelled:
        add("Result: CANCELLED (no .wlsave written)")
    else:
        add("Result: OK — %s" % ("map '%s'" % level if level else "collection"))
    add("Mode: %s" % ("Scene" if mode == "scene" else "Materials"))
    if scope:
        add("Scope: %s" % scope)
    if options:
        add("Options: %s" % options)
    if dest:
        add("Output: %s" % dest)
    add("Elapsed: %.1fs" % elapsed)

    if timeline:
        add("")
        add("Timeline:")
        for label, secs in timeline:
            add("  %-18s %6.1fs" % (label, secs))

    add("")
    add("Results:")
    add("  Materials created: %d" % len(report.get("created") or []))
    add("  Textures: copied %d, re-encoded %d, missing %d" % (
        len(report.get("texturesCopied") or []), len(report.get("texturesReExported") or []),
        len(report.get("texturesMissing") or [])))
    if "objectsExported" in report:                       # scene mode
        add("  Objects exported: %d   (no-UV %d)" % (
            len(report.get("objectsExported") or []), len(report.get("noUv") or [])))
        add("  Meshes written: %d   (failed %d)" % (
            len(report.get("meshesWritten") or []), len(report.get("meshExportFailed") or [])))
        if report.get("materialsBaked"):
            add("  Channels baked: %d   (approximated %d)" % (
                len(report["materialsBaked"]), len(report.get("materialsApproximated") or [])))
        if report.get("bakeFailed"):
            add("  Bakes failed: %d (channels left un-baked — see detail below)" % len(report["bakeFailed"]))
        add("  Master group: %s   Collisions: %s   Thumbnail: %s" % (
            report.get("masterGroup") or "no",
            "on" if report.get("enableCollision") else "off",
            "yes" if report.get("thumbnail") else "no"))
    else:
        add("  Thumbnail: %s" % ("yes" if report.get("thumbnail") else "no"))
    if report.get("materialsUnused"):
        add("  Unused materials dropped: %d" % len(report["materialsUnused"]))
    if report.get("needsBake"):
        nch = sum(len(x.get("channels") or []) for x in report["needsBake"])
        add("  Channels left empty (enable Bake): %d in %d material(s)" % (nch, len(report["needsBake"])))

    detail = report.get("missingDetail") or []
    if detail:
        add("")
        add("Not transported (%d):" % len(detail))
        for d in detail:
            chans = "/".join(d.get("channels") or [])
            add("  - %s [%s]%s" % (d.get("texture"), d.get("reason"), ("  " + chans) if chans else ""))
            add("      materials: %s" % (", ".join(d.get("materials") or []) or "-"))
            add("      meshes:    %s" % (", ".join(d.get("objects") or []) or "-"))

    bake_failed = report.get("bakeFailed") or []
    if bake_failed:
        add("")
        add("Bakes failed (%d) — material exported, channel left un-baked:" % len(bake_failed))
        for b in bake_failed:
            add("  - %s: %s" % (b.get("material") or "?", b.get("error") or "?"))

    if error:
        add("")
        add("Error detail:")
        for ln in error.strip().splitlines():
            add("  " + ln)
    return "\n".join(lines) + "\n"
