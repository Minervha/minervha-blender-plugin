# Minervha Exporter (Blender to Wild Life)

Blender extension that exports a Blender scene's materials to **Wild Life** as a self-contained
**collection bundle** (`.wlsave`) — the collection's JSON plus all its textures, ready to install via
**Minervha Studio**.

> Early release: functional end-to-end; in-game verification on large scenes still in progress.

## Compatibility

| | |
|---|---|
| **Blender** | **4.2 or newer** (the Extensions platform). Developed & validated on **5.1**. Uses the Blender 4.0+ Principled BSDF socket names; the shader-node API it relies on is stable across 4.2–5.x. No upper bound. |
| **OS** | Windows, macOS, Linux — pure Python / `bpy`, no platform-specific code. |
| **Wild Life / Studio** | The `.wlsave` installs through **Minervha Studio** (collection save format, `version 14`+; older versions auto-upgrade in-game). |

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
2. **Name** — the Wild Life collection name.
3. **Export .wlsave** — builds a portable collection bundle (textures included). A report shows what was
   created / copied / re-exported / missing. Install the `.wlsave` in Minervha Studio.

**Scene mode** (the *Mode* toggle) also exports each object's geometry, transforms and hierarchy, not just
its materials. A **Target** dropdown then chooses what the `.wlsave` becomes: a portable **Collection**
(imports into any map) or a **map save** placed on a fixed Wild Life map — *Showroom*, *New Wild Life Map*
or *Old Wild Life Map*. The Studio installs a collection under `Collections/` and a map save under
`MySaves/<map>/`.

**Textures**: on-disk PNG/JPG are copied as-is; packed/generated images and other formats (`.tga`, `.exr`…)
are re-exported to PNG automatically.

**Names**: the game only accepts `A-Za-z0-9_-` in file names. The collection name, material names and
texture file names are sanitized automatically (accents transliterated — `é`→`e` — other symbols replaced
with `_`); the export report lists anything that was renamed.

## Structure

```
minervha_material_exporter/    # extension source (build root)
  blender_manifest.toml         # Blender 4.2+ extension manifest
  __init__.py                   # register -> ui
  ui.py                         # "Minervha" N-panel + Export .wlsave operator
  bsdf_trace.py                 # node-tracing helpers
  introspect.py                 # scene -> NormalizedMaterial[]
  mapper.py                     # NormalizedMaterial -> customMaterials  (port of mapMaterial.js)
  wlsave_export.py              # NormalizedMaterial[] -> .wlsave bundle
  skeleton.json                 # collection skeleton for the .wlsave
tests/                          # Python<->JS parity (mapper golden)
```

`mapper.py` is a faithful port of the Studio's `mapMaterial.js`, kept honest by a golden parity test, so the
materials written into the `.wlsave` match what the Studio would produce.

## Credits

The Principled BSDF node-tracing at the heart of this addon (`bsdf_trace.py`) is a port of an original
Blender material-export script by **Nocna Klacz** — huge thanks for the foundation this is built on.

## License

GPL-3.0-or-later (bpy add-ons are derivative works of Blender).
