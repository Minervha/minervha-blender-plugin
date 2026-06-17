"""Minervha Material Exporter — Blender 4.2+ extension.

Exports a Blender scene's materials to Wild Life, two ways:
  * a `texture_usage.txt` read by Minervha Studio's injector, or
  * a self-contained `.wlsave` collection bundle (JSON + textures).

Scaffold (chunk 1): registers a placeholder N-panel so the extension is
installable and verifiable. The real UI (scope dropdown, export operators)
lands in chunk 6 (ui.py); introspection / mapping / serializers in chunks 2-5.

Note: as a Blender 4.2+ Extension this module carries NO `bl_info` — the
`blender_manifest.toml` replaces it.
"""

import bpy


class MINERVHA_PT_exporter(bpy.types.Panel):
    bl_label = "Minervha Material Exporter"
    bl_idname = "MINERVHA_PT_exporter"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Minervha"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Installed", icon="CHECKMARK")
        col = layout.column(align=True)
        col.enabled = False
        col.label(text="Export .txt (coming soon)")
        col.label(text="Export .wlsave (coming soon)")


_classes = (MINERVHA_PT_exporter,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
