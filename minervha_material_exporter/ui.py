"""ui.py — Minervha N-panel + export operator.

A "Minervha" sidebar panel in the 3D viewport with a Materials/Scene mode toggle, a
scope dropdown, and an Export .wlsave operator. Materials mode bundles just the
materials; Scene mode also exports each object's geometry (UserMesh props), transforms
and hierarchy (Group props). Opens a file-save dialog, then shows a report.
"""

import os
import shutil
import tempfile
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

try:
    from . import introspect, wlsave_export, scene_introspect, obj_export, bake, mapper   # packaged extension
except ImportError:                           # dev / sys.path import (live MCP)
    import introspect, wlsave_export, scene_introspect, obj_export, bake, mapper


# Blender works in metres; Wild Life (Unreal) world unit is the centimetre, so a Blender scene
# must be multiplied by 100 to import at the right size (calibration #2). One world scale drives
# BOTH the OBJ geometry (global_scale) and the prop positions (position_scale).
WL_UNITS_PER_METRE = 100.0


# Human labels for the build generator's progress phases (shown in the status bar during export).
_PHASE_LABEL = {"bake": "Baking", "textures": "Textures", "map": "Mapping",
                "read": "Reading textures", "meshes": "Meshes", "props": "Placing", "zip": "Writing"}


# The single last-export log: kept in memory for the panel and mirrored to one overwritten file
# (only the latest export is retained, per request). Loaded from disk on register so it survives a
# Blender restart.
_LAST_LOG_TEXT = ""
_LAST_LOG_PATH = ""
_LOG_PANEL_MAX_LINES = 30


def _log_file_path():
    """Fixed path of the single last-export log under the extension's user dir, or None.
    `extension_path_user` only works for an installed extension (4.2+); dev imports get None
    (the in-memory log still shows in the panel)."""
    try:
        d = bpy.utils.extension_path_user(__package__, path="", create=True)
        return os.path.join(d, "last_export.log")
    except Exception:
        return None


def _write_last_log(text):
    """Store the last-export log in memory (for the panel) and overwrite the single log file."""
    global _LAST_LOG_TEXT, _LAST_LOG_PATH
    _LAST_LOG_TEXT = text
    p = _log_file_path()
    if not p:
        return
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        _LAST_LOG_PATH = p
    except Exception:
        _LAST_LOG_PATH = ""


def _load_last_log():
    """On register, load the persisted last-export log so the panel shows it after a restart."""
    global _LAST_LOG_TEXT, _LAST_LOG_PATH
    p = _log_file_path()
    if p and os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                _LAST_LOG_TEXT = f.read()
            _LAST_LOG_PATH = p
        except Exception:
            pass


def _scope_label(context):
    sc = context.scene.minervha_scope
    return {'SELECTED': "Selected objects", 'COLLECTION': "Collection", 'FILE': "Whole file"}.get(sc, sc)


def _options_label(context):
    """One-line human summary of the export options, for the log header."""
    s = context.scene
    mr = s.minervha_tex_max_res
    parts = ["JPG q%d" % s.minervha_tex_jpg_quality if s.minervha_tex_prefer_jpg else "PNG",
             "max %s" % ("none" if mr == 'NONE' else mr + "px"),
             "flipGreen " + ("on" if s.minervha_flip_green else "off")]
    if s.minervha_export_mode == 'SCENE':
        parts += ["bake " + ("on" if s.minervha_bake else "off"),
                  "master group " + ("on" if s.minervha_master_group else "off"),
                  "collisions " + ("on" if s.minervha_enable_collision else "off")]
    parts.append("thumbnail " + ("yes" if _thumbnail_path(context) else "no"))
    return ", ".join(parts)


SCOPE_ITEMS = [
    ('SELECTED', "Selected Objects", "Materials used by the selected objects"),
    ('COLLECTION', "Blender Collection", "Materials used by objects in a chosen collection"),
    ('FILE', "Whole File", "Every material in the .blend"),
]


MODE_ITEMS = [
    ('MATERIALS', "Materials", "Bundle only the materials (textures included)"),
    ('SCENE', "Scene", "Export objects' geometry + transforms + hierarchy, with their materials"),
]


# Where a Scene export lands. 'COLLECTION' -> level "" (portable, imports into any map); a fixed map
# name -> level "<map>" (a map save the Studio installs under MySaves/<map>/). The non-COLLECTION
# identifiers ARE the verbatim `level` strings, so the operator maps them straight through.
TARGET_ITEMS = [
    ('COLLECTION', "Collection", "Portable collection — imports into any map (level empty)"),
    ('Showroom', "Showroom (map)", "Full save placed on the Showroom map"),
    ('NewWildLifeMap', "New Wild Life Map", "Full save placed on the New Wild Life map"),
    ('OldWildLifeMap', "Old Wild Life Map", "Full save placed on the Old Wild Life map"),
]


# Texture resolution cap. 'NONE' -> keep originals; otherwise the identifier IS the pixel
# limit (longest side); the operator parses int(value). Downscaling preserves aspect ratio.
MAX_RES_ITEMS = [
    ('NONE', "No limit", "Keep textures at their original resolution"),
    ('4096', "4096 px", "Downscale textures whose longest side exceeds 4096 px"),
    ('2048', "2048 px", "Downscale textures whose longest side exceeds 2048 px"),
    ('1024', "1024 px", "Downscale textures whose longest side exceeds 1024 px"),
    ('512', "512 px", "Downscale textures whose longest side exceeds 512 px"),
]


BAKE_RES_ITEMS = [
    ('512', "512 px", "Bake at most 512x512"),
    ('1024', "1024 px", "Bake at most 1024x1024"),
    ('2048', "2048 px", "Bake at most 2048x2048"),
    ('4096', "4096 px", "Bake at most 4096x4096"),
]


# WL channel -> the Blender Principled slot a baked texture should occupy.
_BAKE_CH_SLOT = {"diffuse": "Base Color", "roughness": "Roughness", "metallic": "Metallic",
                 "normal": "Normal", "emissive": "Emission Color"}


def _next_pow2(n):
    p = 1
    while p < n:
        p <<= 1
    return p


def _max_source_dim(mat):
    """Largest pixel dimension among the material's SOURCE image textures (walking node groups),
    or 0 if it has none (purely procedural). Drives the adaptive bake resolution."""
    if not mat or not mat.use_nodes or mat.node_tree is None:
        return 0
    seen, stack, best = set(), [mat.node_tree], 0
    while stack:
        nt = stack.pop()
        if nt is None or nt.name in seen:
            continue
        seen.add(nt.name)
        for n in nt.nodes:
            if n.type == "TEX_IMAGE" and getattr(n, "image", None) is not None:
                try:
                    best = max(best, int(n.image.size[0]), int(n.image.size[1]))
                except Exception:
                    pass
            elif n.type == "GROUP" and getattr(n, "node_tree", None):
                stack.append(n.node_tree)
    return best


def _adaptive_bake_res(mat, ceiling, max_res=None, floor=512, no_source_default=1024):
    """Bake a material at the resolution of its largest source texture (pow2), never above the
    user's `ceiling` (nor `max_res` if set), never below `floor`. Procedural-only (no source) ->
    `no_source_default`. So a 512² material is baked 512², not the old fixed 2048²."""
    cap = ceiling if not max_res else min(ceiling, int(max_res))
    src = _max_source_dim(mat)
    base = no_source_default if src <= 0 else _next_pow2(src)
    return min(cap, max(floor, base))


def _make_material_baker(tex_dir, ceiling, tex_opts):
    """Build the Scene-mode bake pre-pass passed to wlsave_export.build_scene_wlsave.

    Bakes the channels mapper flagged as `bakeCandidates` (procedural / multi-texture /
    divergent-UV) on a throwaway full-UV placeholder plane (NEVER a scene mesh), writing them into
    `tex_dir` and injecting them into `norms` as baked path textures (so the rest of the pipeline
    treats them as ordinary on-disk textures and the mapper resets tiling to identity).

    The baker writes each texture in its FINAL format (`tex_opts['prefer_jpg']` -> JPEG, else PNG;
    bake targets are alpha-free so JPEG is always valid) and at an ADAPTIVE resolution capped by the
    user's `ceiling` (= the Bake resolution dropdown). Writing the format here is mandatory: the
    downstream texture pre-pass can no longer re-encode a baked image (its bpy datablock is removed
    right after the bake), which is why a baked channel used to ignore 'Prefer JPG' and ship a heavy
    PNG. One bake per material -> one shared texture; one CYCLES swap for the whole batch."""
    prefer_jpg = bool((tex_opts or {}).get("prefer_jpg"))
    jpg_quality = int((tex_opts or {}).get("jpg_quality", 90))
    max_res = (tex_opts or {}).get("max_res")
    fmt, ext = ("JPEG", ".jpg") if prefer_jpg else ("PNG", ".png")

    def baker(norms):
        """Generator: yields ("bake", i, total) per material baked, returns the baked[] list.
        build_scene_wlsave's generator `yield from`s this so the (slow) Cycles bake is stepped
        and the modal export stays responsive during baking."""
        baked = []
        todo = []
        for norm in norms:
            if norm.get("skipped"):
                continue
            m = mapper.map_material(norm)
            if not m:
                continue
            chans = []
            for bc in m["report"].get("bakeCandidates", []):
                ch = bc.get("channel")
                if ch in _BAKE_CH_SLOT and ch not in chans:
                    chans.append(ch)
            mat = bpy.data.materials.get(norm.get("name"))
            if chans and mat is not None:
                todo.append((norm, mat, chans))
        if not todo or not bake.can_bake():
            return baked
        with bake.bake_environment() as bake_scene:
            for i, (norm, mat, chans) in enumerate(todo, 1):
                yield ("bake", i, len(todo))
                res = _adaptive_bake_res(mat, ceiling, max_res)
                # A material that USES transparency (Alpha linked or < 1) needs its Base Color bake
                # to carry the mask in an alpha channel -> bake alpha + force PNG for that channel.
                uses_alpha = wlsave_export._material_uses_alpha(norm)
                for ch in chans:
                    ch_alpha = uses_alpha and ch == "diffuse"
                    ch_fmt, ch_ext = ("PNG", ".png") if ch_alpha else (fmt, ext)
                    safe = wlsave_export._sanitize_name(mat.name, "mat")
                    out = os.path.join(tex_dir, "%s_%s%s" % (safe, ch, ch_ext))
                    path = bake.bake_channel(mat, ch, res, out, image_format=ch_fmt,
                                             jpg_quality=jpg_quality, bake_alpha=ch_alpha,
                                             scene=bake_scene)
                    if not path:
                        continue
                    slot = _BAKE_CH_SLOT[ch]
                    norm["textures"] = [t for t in (norm.get("textures") or [])
                                        if slot not in (t.get("slots") or [])]
                    norm["textures"].insert(0, {
                        "name": "%s_%s" % (mat.name, ch), "fileKind": "path",
                        "path": path, "basename": os.path.basename(path),
                        "slots": [slot], "mapping": None, "baked": True, "has_alpha": ch_alpha})
                    baked.append([mat.name, ch])
        return baked
    return baker


def _annotate_flip(context, norms):
    """Stamp the green-flip preference onto each normalized material (mapper reads `flipGreen`,
    defaulting True = WL/DirectX). One export-wide toggle, not per-material."""
    fg = bool(context.scene.minervha_flip_green)
    for n in norms:
        n["flipGreen"] = fg


def _objects_for_scope(context):
    scope = context.scene.minervha_scope
    if scope == 'SELECTED':
        return list(context.selected_objects)
    if scope == 'COLLECTION':
        coll = context.scene.minervha_collection
        return list(coll.all_objects) if coll else []
    return None  # FILE -> all materials


def _materials_for_scope(context):
    return introspect._materials_for_scope(context.scene.minervha_scope, _objects_for_scope(context))


def _scene_objects(context):
    """Objects to export in Scene mode. FILE scope means the whole scene's objects."""
    scope = context.scene.minervha_scope
    if scope == 'SELECTED':
        return list(context.selected_objects)
    if scope == 'COLLECTION':
        coll = context.scene.minervha_collection
        return list(coll.all_objects) if coll else []
    return list(context.scene.objects)  # FILE -> whole scene


def _scene_materials(objs):
    """NormalizedMaterial[] for the materials used by `objs` (Scene mode). The
    'COLLECTION' scope just means 'gather from the given objects' (any non-FILE scope)."""
    return introspect.collect('COLLECTION', objs)


def _thumbnail_path(context):
    """The chosen thumbnail file path (absolute, expanded) or None. Blender stores a
    FILE_PATH prop with `//`-relative / `~` forms — resolve them so the exporter can open it."""
    raw = (context.scene.minervha_thumbnail_path or "").strip()
    if not raw:
        return None
    return os.path.abspath(bpy.path.abspath(raw))


class MINERVHA_OT_export_wlsave(bpy.types.Operator, ExportHelper):
    bl_idname = "minervha.export_wlsave"
    bl_label = "Export .wlsave"
    bl_description = "Build a portable Wild Life collection bundle (.wlsave)"
    filename_ext = ".wlsave"
    filter_glob: StringProperty(default="*.wlsave", options={'HIDDEN'})

    def invoke(self, context, event):
        safe = wlsave_export._sanitize_name(context.scene.minervha_wlsave_name or "", "MyMaterials")
        self.filepath = safe + ".wlsave"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        """Set up the export (brief, synchronous) then run the heavy build MODALLY: a timer steps
        the build generator a chunk at a time so Blender redraws between chunks (no "Not Responding")
        and a progress bar advances; Esc cancels cleanly. Returns RUNNING_MODAL on success."""
        name = (context.scene.minervha_wlsave_name or "").strip()
        self._reset_state()
        prep = (self._prepare_scene if context.scene.minervha_export_mode == 'SCENE'
                else self._prepare_materials)(context, name)
        if prep is not None:
            return prep                       # {'CANCELLED'} — a warning was already reported
        return self._start_modal(context)

    def _reset_state(self):
        self._t_start = time.monotonic()
        self._gen = None
        self._mode = None
        self._level = ""
        self._bake_tmp = None
        self._timer = None
        self._report = None
        self._unused = []
        self._phase, self._done, self._total = "", 0, 1
        self._phase_order = []        # phases in first-seen order
        self._phase_start = {}        # phase -> elapsed seconds when first seen (for the log timeline)

    def _tex_opts(self, context):
        """Texture pre-pass options from the scene props (see wlsave_export._process_textures)."""
        scene = context.scene
        mr = scene.minervha_tex_max_res
        return {
            "prefer_jpg": bool(scene.minervha_tex_prefer_jpg),
            "jpg_quality": int(scene.minervha_tex_jpg_quality),
            "max_res": None if mr == 'NONE' else int(mr),
        }

    def _prepare_materials(self, context, name):
        """Synchronous setup for a materials-only export; builds the step generator. Returns
        {'CANCELLED'} (with a warning) or None when prepared."""
        norms = introspect.collect(context.scene.minervha_scope, _objects_for_scope(context))
        if not norms:
            self.report({'WARNING'}, "No materials in the selected scope")
            return {'CANCELLED'}
        _annotate_flip(context, norms)
        self._mode = 'materials'
        self._gen = wlsave_export._iter_build_wlsave(
            norms, name, self.filepath, tex_opts=self._tex_opts(context),
            thumbnail=_thumbnail_path(context))
        self._unused = introspect.unused_materials(
            context.scene.minervha_scope, _objects_for_scope(context))
        return None

    def _prepare_scene(self, context, name):
        """Synchronous setup for a scene export; builds the step generator. Returns
        {'CANCELLED'} (with a warning) or None when prepared."""
        objs = _scene_objects(context)
        if not objs:
            self.report({'WARNING'}, "No objects in the selected scope")
            return {'CANCELLED'}
        norms = _scene_materials(objs)
        _annotate_flip(context, norms)
        norm_objects = scene_introspect.collect(context.scene.minervha_scope, objs)
        if not norm_objects:
            self.report({'WARNING'}, "No exportable objects (meshes/empties) in scope")
            return {'CANCELLED'}
        # 'COLLECTION' -> level "" (portable); a fixed map name -> that exact level string.
        target = context.scene.minervha_export_target
        level = "" if target == 'COLLECTION' else target
        # World scale = 100 x scene Unit Scale (Blender metres -> WL centimetres), applied UNIFORMLY
        # to geometry (obj global_scale) and prop positions — one scene-wide scale, not per-object.
        unit = context.scene.unit_settings.scale_length or 1.0
        world_scale = WL_UNITS_PER_METRE * unit
        exporter = obj_export.make_obj_exporter(scene_introspect.build_mesh_object_map(objs),
                                                global_scale=world_scale)
        # Scene-mode bake pre-pass (opt-in): the baker is a generator factory, stepped by the build.
        baker = None
        if context.scene.minervha_bake:
            self._bake_tmp = tempfile.mkdtemp(prefix="wlsave_bake_")
            baker = _make_material_baker(self._bake_tmp, int(context.scene.minervha_bake_res),
                                         self._tex_opts(context))
        self._mode = 'scene'
        self._level = level
        self._gen = wlsave_export._iter_build_scene_wlsave(
            norms, norm_objects, name, self.filepath, exporter,
            position_scale=world_scale, level=level, tex_opts=self._tex_opts(context),
            material_baker=baker, thumbnail=_thumbnail_path(context),
            master_group=bool(context.scene.minervha_master_group),
            enable_collision=bool(context.scene.minervha_enable_collision))
        self._unused = introspect.unused_materials('COLLECTION', objs)
        return None

    # ── Modal stepping ─────────────────────────────────────────────────────
    def _start_modal(self, context):
        wm = context.window_manager
        wm.progress_begin(0.0, 1.0)
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        try:
            context.window.cursor_modal_set('WAIT')
        except Exception:
            pass
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            return self._cancel(context)
        if event.type == 'TIMER':
            try:
                finished = self._pump(context)
            except Exception as exc:
                return self._abort(context, exc)
            if finished:
                return self._finish(context)
        return {'RUNNING_MODAL'}

    # Work budget per timer tick. Bigger = fewer viewport redraws between chunks (the redraw of a
    # heavy scene is pure overhead during export) → faster, more useful CPU; smaller = smoother UI.
    # 50 ms keeps ~20 progress updates/sec (Esc still responsive) while cutting redraw waste.
    _PUMP_BUDGET = 0.05

    def _pump(self, context):
        """Advance the build generator within the per-tick budget; True when it's done."""
        t0 = time.monotonic()
        while True:
            try:
                self._phase, self._done, self._total = next(self._gen)
            except StopIteration as e:
                self._report = e.value
                return True
            if self._phase not in self._phase_start:
                self._phase_start[self._phase] = time.monotonic() - self._t_start
                self._phase_order.append(self._phase)
            if time.monotonic() - t0 > self._PUMP_BUDGET:
                break
        self._update_progress(context)
        return False

    def _build_timeline(self, total_elapsed):
        """Per-phase durations (label, seconds) from the recorded phase-start stamps."""
        tl, order = [], self._phase_order
        for i, ph in enumerate(order):
            start = self._phase_start[ph]
            end = self._phase_start[order[i + 1]] if i + 1 < len(order) else total_elapsed
            tl.append((_PHASE_LABEL.get(ph, ph), max(0.0, end - start)))
        return tl

    def _write_log(self, context, report, elapsed, cancelled):
        try:
            text = wlsave_export.format_export_log(
                report or {}, mode=self._mode or "scene", scope=_scope_label(context),
                level=self._level, options=_options_label(context), dest=self.filepath,
                elapsed=elapsed, cancelled=cancelled, timeline=self._build_timeline(elapsed))
            _write_last_log(text)
        except Exception:
            pass   # logging must never break the export

    def _update_progress(self, context):
        total = self._total or 1
        frac = max(0.0, min(1.0, self._done / total))
        try:
            context.window_manager.progress_update(frac)
        except Exception:
            pass
        try:
            context.workspace.status_text_set(
                "Minervha export — %s %d/%d (%d%%)  ·  Esc to cancel" % (
                    _PHASE_LABEL.get(self._phase, self._phase), self._done, self._total, int(frac * 100)))
        except Exception:
            pass

    def _finish(self, context):
        report = self._report or {}
        report["materialsUnused"] = self._unused or []
        elapsed = time.monotonic() - self._t_start
        self._write_log(context, report, elapsed, cancelled=False)
        self._cleanup(context)
        if self._mode == 'scene':
            baked_n = len(report.get('materialsBaked') or [])
            approx_n = len(report.get('materialsApproximated') or [])
            self.report({'INFO'}, "Built %s (%s) — %d objects, %d meshes, %d materials (%d no-UV, %d baked, %d approx) in %.1fs" % (
                self.filepath, ("map '%s'" % self._level) if self._level else "collection",
                len(report.get('objectsExported') or []), len(report.get('meshesWritten') or []),
                len(report.get('created') or []), len(report.get('noUv') or []), baked_n, approx_n, elapsed))
        else:
            self.report({'INFO'}, "Built %s — %d materials, %d textures (%d missing) in %.1fs" % (
                self.filepath, len(report.get('created') or []),
                len(report.get('texturesCopied') or []) + len(report.get('texturesReExported') or []),
                len(report.get('texturesMissing') or []), elapsed))
        self._popup(context, report)
        return {'FINISHED'}

    def _cancel(self, context):
        try:
            if self._gen:
                self._gen.close()       # GeneratorExit -> the build's finally cleans its tmpdir
        except Exception:
            pass
        elapsed = time.monotonic() - self._t_start
        self._write_log(context, self._report, elapsed, cancelled=True)
        self._cleanup(context)
        self.report({'WARNING'}, "Export cancelled — no .wlsave written")
        return {'CANCELLED'}

    def _abort(self, context, exc):
        try:
            if self._gen:
                self._gen.close()
        except Exception:
            pass
        self._cleanup(context)
        self.report({'ERROR'}, "Export failed: %s" % exc)
        return {'CANCELLED'}

    def _cleanup(self, context):
        wm = context.window_manager
        if self._timer is not None:
            try:
                wm.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None
        try:
            wm.progress_end()
        except Exception:
            pass
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        if self._bake_tmp:
            shutil.rmtree(self._bake_tmp, ignore_errors=True)
            self._bake_tmp = None

    def _popup(self, context, report):
        _print_missing_detail(report)   # full list to the console (the popup truncates)
        try:
            _popup_report(context, report)
        except Exception:
            pass  # popup needs UI context; the status-bar report above always fires


class MINERVHA_OT_capture_thumbnail(bpy.types.Operator):
    bl_idname = "minervha.capture_thumbnail"
    bl_label = "Capture 3D View"
    bl_description = ("Render the current 3D viewport to an image and use it as the save thumbnail "
                     "(longest side 512 px, aspect of the viewport)")
    bl_options = {'REGISTER'}

    def execute(self, context):
        area = next((a for a in context.screen.areas if a.type == 'VIEW_3D'), None)
        if area is None:
            self.report({'WARNING'}, "No 3D viewport found to capture")
            return {'CANCELLED'}
        region = next((rg for rg in area.regions if rg.type == 'WINDOW'), None)
        rw, rh = (region.width, region.height) if region else (512, 512)
        scale = 512.0 / max(rw, rh, 1)
        res_x, res_y = max(1, int(round(rw * scale))), max(1, int(round(rh * scale)))
        # An explicit filepath ending in `.png` (PNG format) is written verbatim by render.opengl —
        # no frame digits appended (frame_path() would wrongly suffix `0001`, so don't use it).
        out = os.path.join(tempfile.gettempdir(), "minervha_thumbnail_capture.png")
        r = context.scene.render
        saved = (r.filepath, r.resolution_x, r.resolution_y, r.resolution_percentage,
                 r.image_settings.file_format)
        try:
            r.resolution_x, r.resolution_y = res_x, res_y
            r.resolution_percentage = 100
            r.image_settings.file_format = 'PNG'
            r.filepath = out
            with context.temp_override(window=context.window, area=area, region=region):
                bpy.ops.render.opengl(write_still=True, view_context=True)
        except Exception as e:
            self.report({'WARNING'}, "Viewport capture failed: %s" % e)
            return {'CANCELLED'}
        finally:
            (r.filepath, r.resolution_x, r.resolution_y, r.resolution_percentage,
             r.image_settings.file_format) = saved
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            context.scene.minervha_thumbnail_path = out
            self.report({'INFO'}, "Captured viewport thumbnail (%dx%d)" % (res_x, res_y))
            return {'FINISHED'}
        self.report({'WARNING'}, "Viewport capture produced no image")
        return {'CANCELLED'}


class MINERVHA_OT_open_log(bpy.types.Operator):
    bl_idname = "minervha.open_log"
    bl_label = "Open log in Text Editor"
    bl_description = "Load the last export log into a Text datablock (shown if a Text Editor is open)"
    bl_options = {'REGISTER'}

    def execute(self, context):
        if not _LAST_LOG_TEXT:
            self.report({'WARNING'}, "No export log yet")
            return {'CANCELLED'}
        name = "Minervha Export Log"
        txt = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        txt.clear()
        txt.write(_LAST_LOG_TEXT)
        shown = False
        for area in context.screen.areas:
            if area.type == 'TEXT_EDITOR':
                for space in area.spaces:
                    if space.type == 'TEXT_EDITOR':
                        space.text = txt
                        shown = True
                area.tag_redraw()
        self.report({'INFO'}, "Loaded '%s'%s" % (name, "" if shown else " — open a Text Editor to view"))
        return {'FINISHED'}


def _print_missing_detail(report):
    """Echo the full 'which texture, on which mesh, wasn't transported' list to the system
    console (the popup truncates to the first 10). Best-effort — never raises."""
    try:
        detail = report.get('missingDetail') or []
        if not detail:
            return
        print("Minervha — %d texture(s) not transported:" % len(detail))
        for d in detail:
            print("  - %s [%s] (%s)  mat: %s  meshes: %s" % (
                d.get('texture'), d.get('reason'), "/".join(d.get('channels') or []) or "-",
                ", ".join(d.get('materials') or []) or "-",
                ", ".join(d.get('objects') or []) or "-"))
    except Exception:
        pass


def _popup_report(context, report):
    def draw(self, _ctx):
        layout = self.layout
        lvl = report.get('level') or ''
        layout.label(text=("Map %s: " % lvl if lvl else "Collection: ") + report['name'])
        if report.get('nameOriginal') and report['nameOriginal'] != report['name']:
            layout.label(text="Name adjusted: %s -> %s" % (report['nameOriginal'], report['name']))
        if report.get('thumbnail'):
            layout.label(text="Thumbnail: included", icon='IMAGE_DATA')
        layout.label(text="Materials created: %d" % len(report['created']))
        layout.label(text="Textures copied: %d" % len(report['texturesCopied']))
        if report['texturesReExported']:
            layout.label(text="Textures re-encoded (JPG/PNG/resized): %d" % len(report['texturesReExported']))
        if report.get('texturesRenamed'):
            layout.label(text="Textures renamed (safe): %d" % len(report['texturesRenamed']))
        if report['texturesMissing']:
            layout.label(text="Textures missing: %d" % len(report['texturesMissing']), icon='ERROR')
        if report['renamed']:
            layout.label(text="Renamed (sanitized/clash): %d" % len(report['renamed']))
        if report['skipped']:
            layout.label(text="Skipped (no node tree): %d" % len(report['skipped']))
        if report.get('materialsUnused'):
            layout.label(text="Ignored (unused — no face): %d" % len(report['materialsUnused']))
        if report.get('needsBake'):
            n_ch = sum(len(x.get('channels') or []) for x in report['needsBake'])
            layout.label(text="Channels left empty (enable Bake): %d in %d material(s)" % (
                n_ch, len(report['needsBake'])), icon='INFO')
        # Scene-mode counters (present only for a scene export).
        if 'objectsExported' in report:
            layout.separator()
            layout.label(text="Objects exported: %d" % len(report['objectsExported']))
            layout.label(text="Meshes written: %d" % len(report['meshesWritten']))
            if report.get('masterGroup'):
                layout.label(text="Master group: %s" % report['masterGroup'], icon='GROUP')
            if report.get('enableCollision'):
                layout.label(text="Collisions: enabled", icon='PHYSICS')
            if report['noUv']:
                layout.label(text="Objects without UVs: %d" % len(report['noUv']), icon='ERROR')
            if report.get('materialsBaked'):
                layout.label(text="Channels baked: %d" % len(report['materialsBaked']), icon='RENDER_STILL')
            if report.get('materialsApproximated'):
                layout.label(text="Approximated (per-mesh data): %d" % len(report['materialsApproximated']), icon='INFO')
            if report['proceduralMaterials']:
                icon = 'INFO' if report.get('materialsBaked') else 'ERROR'
                layout.label(text="Procedural materials: %d (enable Bake)" % len(report['proceduralMaterials']), icon=icon)
            if report.get('meshExportFailed'):
                layout.label(text="Meshes failed to export: %d" % len(report['meshExportFailed']), icon='ERROR')
        # Per-texture detail: which texture, on which material + meshes, wasn't transported.
        detail = report.get('missingDetail') or []
        if detail:
            layout.separator()
            layout.label(text="Not transported — fix manually:", icon='ERROR')
            for d in detail[:10]:
                chans = "/".join(d.get('channels') or [])
                layout.label(text="• %s  [%s]%s" % (
                    d['texture'], d['reason'], ("  " + chans) if chans else ""))
                layout.label(text="     %s  ->  %s" % (
                    ", ".join(d.get('materials') or []) or "-",
                    ", ".join(d.get('objects') or []) or "-"))
            if len(detail) > 10:
                layout.label(text="  ...and %d more (full list in the system console)" % (len(detail) - 10))
    context.window_manager.popup_menu(draw, title="Minervha — Export report", icon='CHECKMARK')


class MINERVHA_PT_exporter(bpy.types.Panel):
    bl_label = "Exporter"
    bl_idname = "MINERVHA_PT_exporter"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Minervha"

    def draw(self, context):
        scene = context.scene
        layout = self.layout

        col = layout.column()
        col.prop(scene, "minervha_export_mode", text="Mode", expand=True)
        col.prop(scene, "minervha_scope", text="Scope")
        if scene.minervha_scope == 'COLLECTION':
            col.prop(scene, "minervha_collection", text="")

        scene_mode = scene.minervha_export_mode == 'SCENE'
        if scene_mode:
            col.prop(scene, "minervha_export_target", text="Target")
        try:
            if scene_mode:
                col.label(text="%d object(s) in scope" % len(_scene_objects(context)))
            else:
                col.label(text="%d material(s) in scope" % len(_materials_for_scope(context)))
        except Exception:
            pass

        if scene_mode:
            note = layout.box()
            note.label(text="Scene export limits:", icon='INFO')
            note.label(text="• meshes need UVs")
            note.label(text="• procedural textures: enable Bake below")
            note.label(text="• verify transforms in-game")

        tex = layout.box()
        tex.label(text="Textures", icon='TEXTURE')
        tex.prop(scene, "minervha_tex_prefer_jpg")
        if scene.minervha_tex_prefer_jpg:
            tex.prop(scene, "minervha_tex_jpg_quality")
        tex.prop(scene, "minervha_tex_max_res", text="Max resolution")
        tex.prop(scene, "minervha_flip_green")
        if scene_mode:
            tex.separator()
            tex.prop(scene, "minervha_bake")
            if scene.minervha_bake:
                tex.prop(scene, "minervha_bake_res", text="Bake resolution")

        if scene_mode:
            scn = layout.box()
            scn.label(text="Scene options", icon='OBJECT_DATA')
            scn.prop(scene, "minervha_master_group")
            scn.prop(scene, "minervha_enable_collision")

        layout.separator()
        box = layout.box()
        box.label(text="Wild Life collection (.wlsave)")
        box.prop(scene, "minervha_wlsave_name", text="Name")
        row = box.row(align=True)
        row.prop(scene, "minervha_thumbnail_path", text="Thumbnail")
        row.operator("minervha.capture_thumbnail", text="", icon='RENDER_STILL')
        box.operator("minervha.export_wlsave", icon='PACKAGE')


class MINERVHA_PT_log(bpy.types.Panel):
    bl_label = "Last export log"
    bl_idname = "MINERVHA_PT_log"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Minervha"
    bl_parent_id = "MINERVHA_PT_exporter"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        if not _LAST_LOG_TEXT:
            layout.label(text="No export yet — run an export.")
            return
        row = layout.row(align=True)
        row.operator("minervha.open_log", icon='TEXT')
        if _LAST_LOG_PATH:
            row.operator("wm.path_open", text="Open file", icon='FILEBROWSER').filepath = _LAST_LOG_PATH
        box = layout.box()
        col = box.column(align=True)
        lines = _LAST_LOG_TEXT.splitlines()
        for ln in lines[:_LOG_PANEL_MAX_LINES]:
            col.label(text=ln if ln.strip() else " ")
        if len(lines) > _LOG_PANEL_MAX_LINES:
            col.label(text="... (%d more lines — open the full log)" % (len(lines) - _LOG_PANEL_MAX_LINES))


_classes = (MINERVHA_OT_export_wlsave, MINERVHA_OT_capture_thumbnail, MINERVHA_OT_open_log,
            MINERVHA_PT_exporter, MINERVHA_PT_log)


def register():
    bpy.types.Scene.minervha_export_mode = EnumProperty(name="Mode", items=MODE_ITEMS, default='SCENE')
    bpy.types.Scene.minervha_export_target = EnumProperty(name="Target", items=TARGET_ITEMS, default='Showroom')
    bpy.types.Scene.minervha_scope = EnumProperty(name="Scope", items=SCOPE_ITEMS, default='SELECTED')
    bpy.types.Scene.minervha_collection = PointerProperty(name="Collection", type=bpy.types.Collection)
    bpy.types.Scene.minervha_wlsave_name = StringProperty(name="Name", default="MyMaterials")
    bpy.types.Scene.minervha_tex_prefer_jpg = BoolProperty(
        name="Prefer JPG over PNG", default=True,
        description="Re-encode opaque textures as JPG (smaller files). Textures with an "
                    "alpha channel always stay PNG")
    bpy.types.Scene.minervha_tex_jpg_quality = IntProperty(
        name="JPG Quality", default=90, min=1, max=100, subtype='PERCENTAGE',
        description="JPEG compression quality — higher is better quality but larger files")
    bpy.types.Scene.minervha_tex_max_res = EnumProperty(
        name="Max Resolution", items=MAX_RES_ITEMS, default='NONE',
        description="Downscale textures whose longest side exceeds this size (keeps aspect ratio)")
    bpy.types.Scene.minervha_bake = BoolProperty(
        name="Bake procedural / complex shading", default=False,
        description="Scene mode: bake the channels flagged as bakeCandidates (procedural, "
                    "multi-texture, divergent-UV) onto a UV-bearing mesh, into flat PBR textures. "
                    "Uses Cycles; only objects that already have UVs are baked")
    bpy.types.Scene.minervha_bake_res = EnumProperty(
        name="Bake Resolution (max)", items=BAKE_RES_ITEMS, default='2048',
        description="Maximum bake resolution. Each material is baked at the resolution of its "
                    "largest source texture (rounded up to a power of two), never exceeding this "
                    "cap — so a 512px material is not baked at 4K. Procedural-only materials bake "
                    "at 1024 (capped here)")
    bpy.types.Scene.minervha_flip_green = BoolProperty(
        name="Flip normal green (DirectX)", default=True,
        description="Wild Life reads DirectX-convention normal maps (green channel flipped). "
                    "Leave on for OpenGL-authored normals (the Blender default); turn off if your "
                    "normal maps are already DirectX")
    bpy.types.Scene.minervha_master_group = BoolProperty(
        name="Wrap in master group", default=False,
        description="Scene mode: parent every top-level object under one Wild Life group named after "
                    "the save, so the imported scene hangs off a single node")
    bpy.types.Scene.minervha_enable_collision = BoolProperty(
        name="Enable collisions", default=False,
        description="Scene mode: export every mesh with collision enabled (EnableCollision). Off by "
                    "default, matching the majority of Wild Life saves")
    bpy.types.Scene.minervha_thumbnail_path = StringProperty(
        name="Thumbnail", default="", subtype='FILE_PATH',
        description="Image bundled as the save's thumbnail (<Name>/<Name>.png). Any Blender-readable "
                    "image; re-encoded to PNG at 512 px. Use the camera button to capture the 3D viewport")
    for cls in _classes:
        bpy.utils.register_class(cls)
    _load_last_log()        # show the previous run's log in the panel after a restart


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    for prop in ("minervha_export_mode", "minervha_export_target", "minervha_scope",
                 "minervha_collection", "minervha_wlsave_name",
                 "minervha_tex_prefer_jpg", "minervha_tex_jpg_quality", "minervha_tex_max_res",
                 "minervha_bake", "minervha_bake_res", "minervha_flip_green",
                 "minervha_master_group", "minervha_enable_collision", "minervha_thumbnail_path"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
