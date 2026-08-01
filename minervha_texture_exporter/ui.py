"""ui.py — "Texture Set" panel + export operator.

A panel in the 3D viewport's "Minervha" sidebar tab: an output folder, a max-resolution
cap, a bake toggle, and an Export button that writes `<Material>_D/_ORM/_N.png` for every
material of the selected mesh(es).

Scene properties are prefixed `minervha_texset_` and classes `MINERVHATEX_*` so this
add-on can be installed alongside the sibling `minervha_material_exporter` without either
clobbering the other's registrations.
"""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

try:
    from . import export, sources          # packaged extension
except ImportError:                        # dev / sys.path import
    import export
    import sources


# Texture resolution cap. 'NONE' keeps originals; otherwise the identifier IS the pixel
# limit (longest side) and the operator parses int(value). Downscaling keeps aspect ratio.
MAX_RES_ITEMS = [
    ('NONE', "No limit", "Keep every map at its source resolution"),
    ('4096', "4096 px", "Downscale maps whose longest side exceeds 4096 px"),
    ('2048', "2048 px", "Downscale maps whose longest side exceeds 2048 px"),
    ('1024', "1024 px", "Downscale maps whose longest side exceeds 1024 px"),
    ('512', "512 px", "Downscale maps whose longest side exceeds 512 px"),
]

# Last run's report text, shown in the panel and echoed to the system console.
_LAST_REPORT = ""


def _selected_meshes(context):
    return [o for o in context.selected_objects if o.type == "MESH"]


def _scope_materials(context):
    return sources.materials_of(_selected_meshes(context))


def _output_dir(context):
    """The chosen output folder as an absolute path, or None when it can't be resolved
    (empty, or a `//`-relative path in a .blend that was never saved)."""
    raw = (context.scene.minervha_texset_dir or "").strip()
    if not raw:
        return None
    path = os.path.abspath(bpy.path.abspath(raw))
    if raw.startswith("//") and not bpy.data.filepath:
        return None
    return path


class MINERVHATEX_OT_export(bpy.types.Operator):
    bl_idname = "minervha.export_texture_set"
    bl_label = "Export Textures"
    bl_description = ("Write <Material>_D / _ORM / _N PNG maps for every material of the "
                      "selected mesh(es) into the output folder")
    bl_options = {'REGISTER'}

    def execute(self, context):
        global _LAST_REPORT
        scene = context.scene
        materials = _scope_materials(context)
        if not materials:
            self.report({'WARNING'}, "No material on the selected mesh(es)")
            return {'CANCELLED'}
        out_dir = _output_dir(context)
        if out_dir is None:
            self.report({'ERROR'}, "Choose an output folder (or save the .blend to use a "
                                   "relative one)")
            return {'CANCELLED'}
        cap = None if scene.minervha_texset_max_res == 'NONE' else int(scene.minervha_texset_max_res)

        wm = context.window_manager
        wm.progress_begin(0, len(materials))
        try:
            report = export.export_texture_sets(
                materials, out_dir, cap=cap, allow_bake=bool(scene.minervha_texset_bake),
                on_material=lambda i, total, name: wm.progress_update(i))
        except Exception as exc:
            self.report({'ERROR'}, "Export failed: %s" % exc)
            raise
        finally:
            wm.progress_end()

        _LAST_REPORT = export.format_report(report)
        print(_LAST_REPORT)
        self.report({'INFO'}, "%d file(s) written for %d material(s) — bake %s"
                    % (report["files"], len(materials), report["bake"]))
        # Only ever pop up with a real UI: `popup_menu` in background mode dereferences an
        # uninitialised icon system and takes Blender down with an access violation — a
        # HARD crash no try/except can catch. Headless/scripted runs get the printed report.
        if not bpy.app.background:
            try:
                _popup_report(context, report)
            except Exception:
                pass                      # needs a UI context; the console report always fires
        return {'FINISHED'}


def _popup_report(context, report):
    def draw(self, _ctx):
        layout = self.layout
        layout.label(text="Output: %s" % report["dir"])
        layout.label(text="Files written: %d   (bake: %s)" % (report["files"], report["bake"]))
        for rec in report["records"]:
            layout.separator()
            layout.label(text=rec["name"] or rec["material"], icon='MATERIAL')
            if rec["files"]:
                layout.label(text="    " + ", ".join(rec["files"]))
            if rec.get("ao"):
                layout.label(text="    AO: %s" % rec["ao"])
            for note in rec["notes"]:
                layout.label(text="    " + note, icon='INFO')
    context.window_manager.popup_menu(draw, title="Minervha — Texture set", icon='TEXTURE')


class MINERVHATEX_PT_panel(bpy.types.Panel):
    bl_label = "Texture Set"
    bl_idname = "MINERVHATEX_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Minervha"

    def draw(self, context):
        scene = context.scene
        layout = self.layout

        col = layout.column()
        col.prop(scene, "minervha_texset_dir", text="Folder")
        col.prop(scene, "minervha_texset_max_res", text="Max resolution")
        col.prop(scene, "minervha_texset_bake")

        materials = _scope_materials(context)
        box = layout.box()
        box.label(text="%d material(s) on %d selected mesh(es)"
                       % (len(materials), len(_selected_meshes(context))), icon='MATERIAL')
        for mat in materials[:8]:
            box.label(text="    %s_D / _ORM / _N" % export.sanitize_name(mat.name))
        if len(materials) > 8:
            box.label(text="    ... (+%d more)" % (len(materials) - 8))

        layout.operator("minervha.export_texture_set", icon='TEXTURE')


class MINERVHATEX_PT_report(bpy.types.Panel):
    bl_label = "Last run"
    bl_idname = "MINERVHATEX_PT_report"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Minervha"
    bl_parent_id = "MINERVHATEX_PT_panel"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        if not _LAST_REPORT:
            layout.label(text="No export yet.")
            return
        col = layout.box().column(align=True)
        for line in _LAST_REPORT.splitlines():
            col.label(text=line if line.strip() else " ")


_classes = (MINERVHATEX_OT_export, MINERVHATEX_PT_panel, MINERVHATEX_PT_report)


def register():
    bpy.types.Scene.minervha_texset_dir = StringProperty(
        name="Folder", default="//textures/", subtype='DIR_PATH',
        description="Folder the map sets are written to. A '//' path is relative to the "
                    "saved .blend")
    bpy.types.Scene.minervha_texset_max_res = EnumProperty(
        name="Max Resolution", items=MAX_RES_ITEMS, default='NONE',
        description="Downscale any map whose longest side exceeds this size (keeps aspect "
                    "ratio). Maps already smaller are left untouched")
    bpy.types.Scene.minervha_texset_bake = BoolProperty(
        name="Bake procedural channels", default=True,
        description="Flatten channels driven by a node graph (Mix / Math / Noise ...) with "
                    "a Cycles bake, instead of leaving them out. Needs Cycles enabled")
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    for prop in ("minervha_texset_dir", "minervha_texset_max_res", "minervha_texset_bake"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
