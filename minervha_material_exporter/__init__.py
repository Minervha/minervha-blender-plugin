"""Minervha Material Exporter — Blender 4.2+ extension.

Exports a Blender scene's materials to Wild Life as a self-contained `.wlsave`
collection bundle (JSON + textures), installable via Minervha Studio.

The UI (the "Minervha" N-panel + export operator) lives in ui.py; the data layers
are introspect -> mapper -> wlsave_export over the bsdf_trace node-tracing core.

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
