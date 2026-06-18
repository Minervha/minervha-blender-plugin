"""ui.py — Minervha N-panel + export operator.

A "Minervha" sidebar panel in the 3D viewport with a Materials/Scene mode toggle, a
scope dropdown, and an Export .wlsave operator. Materials mode bundles just the
materials; Scene mode also exports each object's geometry (UserMesh props), transforms
and hierarchy (Group props). Opens a file-save dialog, then shows a report.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

try:
    from . import introspect, wlsave_export, scene_introspect, obj_export   # packaged extension
except ImportError:                           # dev / sys.path import (live MCP)
    import introspect, wlsave_export, scene_introspect, obj_export


# Blender works in metres; Wild Life (Unreal) world unit is the centimetre, so a Blender scene
# must be multiplied by 100 to import at the right size (calibration #2). One world scale drives
# BOTH the OBJ geometry (global_scale) and the prop positions (position_scale).
WL_UNITS_PER_METRE = 100.0


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
        name = (context.scene.minervha_wlsave_name or "").strip()
        if context.scene.minervha_export_mode == 'SCENE':
            return self._execute_scene(context, name)
        return self._execute_materials(context, name)

    def _tex_opts(self, context):
        """Texture pre-pass options from the scene props (see wlsave_export._process_textures)."""
        scene = context.scene
        mr = scene.minervha_tex_max_res
        return {
            "prefer_jpg": bool(scene.minervha_tex_prefer_jpg),
            "jpg_quality": int(scene.minervha_tex_jpg_quality),
            "max_res": None if mr == 'NONE' else int(mr),
        }

    def _execute_materials(self, context, name):
        norms = introspect.collect(context.scene.minervha_scope, _objects_for_scope(context))
        if not norms:
            self.report({'WARNING'}, "No materials in the selected scope")
            return {'CANCELLED'}
        report = wlsave_export.build_wlsave(norms, name, self.filepath, tex_opts=self._tex_opts(context))
        self.report({'INFO'}, "Built %s — %d materials, %d textures (%d missing)" % (
            self.filepath, len(report['created']),
            len(report['texturesCopied']) + len(report['texturesReExported']),
            len(report['texturesMissing'])))
        self._popup(context, report)
        return {'FINISHED'}

    def _execute_scene(self, context, name):
        objs = _scene_objects(context)
        if not objs:
            self.report({'WARNING'}, "No objects in the selected scope")
            return {'CANCELLED'}
        norms = _scene_materials(objs)
        norm_objects = scene_introspect.collect(context.scene.minervha_scope, objs)
        if not norm_objects:
            self.report({'WARNING'}, "No exportable objects (meshes/empties) in scope")
            return {'CANCELLED'}
        # 'COLLECTION' -> level "" (portable); a fixed map name -> that exact level string.
        target = context.scene.minervha_export_target
        level = "" if target == 'COLLECTION' else target
        # World scale = 100 x scene Unit Scale (Blender metres -> WL centimetres), applied UNIFORMLY
        # to geometry (obj global_scale) and prop positions — one scene-wide scale, not per-object.
        # At the default Unit Scale (1.0) this is x100; a cm-modelled scene (Unit Scale 0.01) -> x1.
        unit = context.scene.unit_settings.scale_length or 1.0
        world_scale = WL_UNITS_PER_METRE * unit
        exporter = obj_export.make_obj_exporter(scene_introspect.build_mesh_object_map(objs),
                                                global_scale=world_scale)
        report = wlsave_export.build_scene_wlsave(norms, norm_objects, name, self.filepath, exporter,
                                                  position_scale=world_scale, level=level,
                                                  tex_opts=self._tex_opts(context))
        self.report({'INFO'}, "Built %s (%s) — %d objects, %d meshes, %d materials (%d no-UV)" % (
            self.filepath, ("map '%s'" % level) if level else "collection",
            len(report['objectsExported']), len(report['meshesWritten']),
            len(report['created']), len(report['noUv'])))
        self._popup(context, report)
        return {'FINISHED'}

    def _popup(self, context, report):
        try:
            _popup_report(context, report)
        except Exception:
            pass  # popup needs UI context; the status-bar report above always fires


def _popup_report(context, report):
    def draw(self, _ctx):
        layout = self.layout
        lvl = report.get('level') or ''
        layout.label(text=("Map %s: " % lvl if lvl else "Collection: ") + report['name'])
        if report.get('nameOriginal') and report['nameOriginal'] != report['name']:
            layout.label(text="Name adjusted: %s -> %s" % (report['nameOriginal'], report['name']))
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
        # Scene-mode counters (present only for a scene export).
        if 'objectsExported' in report:
            layout.separator()
            layout.label(text="Objects exported: %d" % len(report['objectsExported']))
            layout.label(text="Meshes written: %d" % len(report['meshesWritten']))
            if report['noUv']:
                layout.label(text="Objects without UVs: %d" % len(report['noUv']), icon='ERROR')
            if report['proceduralMaterials']:
                layout.label(text="Procedural materials (bake manually): %d" % len(report['proceduralMaterials']), icon='ERROR')
            if report.get('meshExportFailed'):
                layout.label(text="Meshes failed to export: %d" % len(report['meshExportFailed']), icon='ERROR')
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
            note.label(text="• procedural textures: bake first")
            note.label(text="• verify transforms in-game")

        tex = layout.box()
        tex.label(text="Textures", icon='TEXTURE')
        tex.prop(scene, "minervha_tex_prefer_jpg")
        if scene.minervha_tex_prefer_jpg:
            tex.prop(scene, "minervha_tex_jpg_quality")
        tex.prop(scene, "minervha_tex_max_res", text="Max resolution")

        layout.separator()
        box = layout.box()
        box.label(text="Wild Life collection (.wlsave)")
        box.prop(scene, "minervha_wlsave_name", text="Name")
        box.operator("minervha.export_wlsave", icon='PACKAGE')


_classes = (MINERVHA_OT_export_wlsave, MINERVHA_PT_exporter)


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
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    for prop in ("minervha_export_mode", "minervha_export_target", "minervha_scope",
                 "minervha_collection", "minervha_wlsave_name",
                 "minervha_tex_prefer_jpg", "minervha_tex_jpg_quality", "minervha_tex_max_res"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
