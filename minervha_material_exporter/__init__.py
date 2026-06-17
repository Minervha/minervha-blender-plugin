"""Minervha Material Exporter — Blender 4.2+ extension.

Exports a Blender scene's materials to Wild Life, two ways:
  * a `texture_usage.txt` read by Minervha Studio's injector (mode A), or
  * a self-contained `.wlsave` collection bundle (JSON + textures) (mode B).

The UI (the "Minervha" N-panel + export operators) lives in ui.py; the data layers
are introspect/mapper/wlsave_export/txt_export over the shared bsdf_trace core.

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
