"""Minervha Texture Exporter — Blender 4.2+ extension.

Writes, for every material of the selected mesh(es), a flat folder of PBR maps named
after the material: `<Material>_D.png` (Base Color), `<Material>_ORM.png` (R=AO,
G=Roughness, B=Metallic) and `<Material>_N.png` (Normal), with a max-resolution cap.

The UI (the "Minervha" N-panel tab, "Texture Set" panel) lives in ui.py; the data layers
are sources -> export over images/bake.

Independent of the sibling `minervha_material_exporter` extension: both may be installed
at once, hence the distinct `minervha_texset_*` Scene properties and `MINERVHATEX_*`
class names.

As a Blender 4.2+ Extension this module carries NO `bl_info` — blender_manifest.toml
replaces it.
"""

try:
    from . import ui          # packaged extension
except ImportError:           # dev / sys.path import
    import ui


def register():
    ui.register()


def unregister():
    ui.unregister()
