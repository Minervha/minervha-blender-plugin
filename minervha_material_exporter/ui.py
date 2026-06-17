"""ui.py — Minervha N-panel + export operators.

A "Minervha" sidebar panel in the 3D viewport with a scope dropdown and two export
operators: Export .txt (mode A, for the Studio injector) and Export .wlsave (mode B,
a portable collection bundle). Both open a file-save dialog; .wlsave shows a report.
"""

import bpy
from bpy.props import EnumProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

try:
    from . import introspect, txt_export, wlsave_export   # packaged extension
except ImportError:                                        # dev / sys.path import (live MCP)
    import introspect, txt_export, wlsave_export


SCOPE_ITEMS = [
    ('SELECTED', "Selected Objects", "Materials used by the selected objects"),
    ('COLLECTION', "Blender Collection", "Materials used by objects in a chosen collection"),
    ('FILE', "Whole File", "Every material in the .blend"),
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


def _safe_name(name):
    return bool(name) and name not in ('.', '..') and not any(c in name for c in ('/', '\\', '\0', ':'))


class MINERVHA_OT_export_txt(bpy.types.Operator, ExportHelper):
    bl_idname = "minervha.export_txt"
    bl_label = "Export .txt"
    bl_description = "Write a texture_usage.txt for Minervha Studio's material injector"
    filename_ext = ".txt"
    filter_glob: StringProperty(default="*.txt", options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = "texture_usage.txt"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        mats = _materials_for_scope(context)
        if not mats:
            self.report({'WARNING'}, "No materials in the selected scope")
            return {'CANCELLED'}
        count = txt_export.export_txt(mats, self.filepath)
        self.report({'INFO'}, "Exported %d materials to %s" % (count, self.filepath))
        return {'FINISHED'}


class MINERVHA_OT_export_wlsave(bpy.types.Operator, ExportHelper):
    bl_idname = "minervha.export_wlsave"
    bl_label = "Export .wlsave"
    bl_description = "Build a portable Wild Life collection bundle (.wlsave) of the materials"
    filename_ext = ".wlsave"
    filter_glob: StringProperty(default="*.wlsave", options={'HIDDEN'})

    def invoke(self, context, event):
        self.filepath = (context.scene.minervha_wlsave_name or "MyMaterials") + ".wlsave"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        name = (context.scene.minervha_wlsave_name or "").strip()
        if not _safe_name(name):
            self.report({'ERROR'}, "Invalid collection name (no / \\ : characters)")
            return {'CANCELLED'}
        norms = introspect.collect(context.scene.minervha_scope, _objects_for_scope(context))
        if not norms:
            self.report({'WARNING'}, "No materials in the selected scope")
            return {'CANCELLED'}
        report = wlsave_export.build_wlsave(norms, name, self.filepath)
        self.report({'INFO'}, "Built %s — %d materials, %d textures (%d missing)" % (
            self.filepath, len(report['created']),
            len(report['texturesCopied']) + len(report['texturesReExported']),
            len(report['texturesMissing'])))
        try:
            _popup_report(context, report)
        except Exception:
            pass  # popup needs UI context; the status-bar report above always fires
        return {'FINISHED'}


def _popup_report(context, report):
    def draw(self, _ctx):
        layout = self.layout
        layout.label(text="Collection: " + report['name'])
        layout.label(text="Materials created: %d" % len(report['created']))
        layout.label(text="Textures copied: %d" % len(report['texturesCopied']))
        if report['texturesReExported']:
            layout.label(text="Textures re-exported (PNG): %d" % len(report['texturesReExported']))
        if report['texturesMissing']:
            layout.label(text="Textures missing: %d" % len(report['texturesMissing']), icon='ERROR')
        if report['renamed']:
            layout.label(text="Renamed (name clash): %d" % len(report['renamed']))
        if report['skipped']:
            layout.label(text="Skipped (no node tree): %d" % len(report['skipped']))
    context.window_manager.popup_menu(draw, title="Minervha — Export report", icon='CHECKMARK')


class MINERVHA_PT_exporter(bpy.types.Panel):
    bl_label = "Material Exporter"
    bl_idname = "MINERVHA_PT_exporter"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Minervha"

    def draw(self, context):
        scene = context.scene
        layout = self.layout

        col = layout.column()
        col.prop(scene, "minervha_scope", text="Scope")
        if scene.minervha_scope == 'COLLECTION':
            col.prop(scene, "minervha_collection", text="")
        try:
            count = len(_materials_for_scope(context))
            col.label(text="%d material(s) in scope" % count)
        except Exception:
            pass

        layout.separator()
        layout.operator("minervha.export_txt", icon='TEXT')

        layout.separator()
        box = layout.box()
        box.label(text="Wild Life collection (.wlsave)")
        box.prop(scene, "minervha_wlsave_name", text="Name")
        box.operator("minervha.export_wlsave", icon='PACKAGE')


_classes = (MINERVHA_OT_export_txt, MINERVHA_OT_export_wlsave, MINERVHA_PT_exporter)


def register():
    bpy.types.Scene.minervha_scope = EnumProperty(name="Scope", items=SCOPE_ITEMS, default='SELECTED')
    bpy.types.Scene.minervha_collection = PointerProperty(name="Collection", type=bpy.types.Collection)
    bpy.types.Scene.minervha_wlsave_name = StringProperty(name="Name", default="MyMaterials")
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    for prop in ("minervha_scope", "minervha_collection", "minervha_wlsave_name"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
