# Minervha Blender Plugin — Material Exporter

Blender extension that exports a Blender scene's materials to **Wild Life**, two ways:

- **`.txt`** — `texture_usage.txt`, read by **Minervha Studio**'s material injector (maps the materials into a save).
- **`.wlsave`** — a self-contained Wild Life **collection bundle** (JSON + textures), installable directly via the Studio.

> 🚧 Early release — functional end-to-end; in-game verification on large scenes still in progress.

## Compatibility

| | |
|---|---|
| **Blender** | **4.2 or newer** (the Extensions platform). Developed & validated on **5.1**. Uses the Blender 4.0+ Principled BSDF socket names; the shader-node API it relies on is stable across 4.2–5.x. No upper bound. |
| **OS** | Windows, macOS, Linux — pure Python / `bpy`, no platform-specific code. |
| **Wild Life / Studio** | `.wlsave` installs through **Minervha Studio** (collection save format, `version 14`+; older versions auto-upgrade in-game). `.txt` is consumed by the Studio's Blender→WL material injector. |

## Installation

1. Get the extension `.zip` — from **Releases** (when published), or build it by compressing the **contents**
   of `minervha_material_exporter/` so that `blender_manifest.toml` sits at the zip root.
2. Blender → **Edit → Preferences → Add-ons →** ⌄ → **Install from Disk…** → pick the `.zip`
   (or drag the zip into the Blender window).
3. In the 3D viewport press **N** → the **"Minervha"** tab.

## Usage

In the **Minervha** panel:

1. **Scope** — what to export: *Selected Objects*, a *Blender Collection*, or the *Whole File*.
   The panel shows how many materials are in scope.
2. **Export .txt** — writes `texture_usage.txt` for the Studio's injector.
3. **Wild Life collection (.wlsave)** — set a **Name**, then **Export .wlsave** to build a portable
   collection bundle (textures included). A report shows what was created / copied / re-exported / missing.
   Install the `.wlsave` in Minervha Studio.

**Textures**: on-disk PNG/JPG are copied as-is; packed/generated images and other formats (`.tga`, `.exr`…)
are re-exported to PNG automatically.

## Structure

```
minervha_material_exporter/    # extension source (build root)
  blender_manifest.toml         # Blender 4.2+ extension manifest
  __init__.py                   # register -> ui
  ui.py                         # "Minervha" N-panel + export operators
  bsdf_trace.py                 # shared node-tracing helpers
  introspect.py                 # scene -> NormalizedMaterial[]            (mode B)
  txt_export.py                 # materials -> texture_usage.txt           (mode A)
  mapper.py                     # NormalizedMaterial -> customMaterials    (port of mapMaterial.js)
  wlsave_export.py              # NormalizedMaterial[] -> .wlsave bundle
  skeleton.json                 # collection skeleton for the .wlsave
tests/                          # Python<->JS parity (mapper golden)
```

The two exports share one node-tracing core (`bsdf_trace`), so mode A (the byte-identical `.txt`) and mode B
(the `.wlsave`) always read the material graph the same way. `mapper.py` is a faithful port of the Studio's
`mapMaterial.js`, kept honest by a golden parity test.

## License

GPL-3.0-or-later (bpy add-ons are derivative works of Blender).
