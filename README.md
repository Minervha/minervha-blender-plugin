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

**Textures** (the *Textures* box): by default opaque textures are re-encoded to **JPG** at an adjustable
quality (smaller files), while textures with an alpha channel always stay **PNG**. Enable
*Keep normal maps as PNG* (off by default) to never JPG-encode normal maps — JPG's lossy compression
mangles tangent-space normals into blocky shading. An optional
**Max resolution** cap downscales anything larger (aspect ratio kept). Packed/generated images and other
formats (`.tga`, `.exr`…) are re-exported automatically. Turn *Prefer JPG over PNG* off to keep on-disk
PNG/JPG as-is and re-export the rest to PNG.

*Override specular* (off by default) forces every exported material's specular to one fixed value (0–1),
ignoring each material's own *Specular IOR Level* — handy for matching a whole kit's look in-game without
hand-editing every material's node.

**Names**: the game only accepts `A-Za-z0-9_-` in file names. The collection name, material names and
texture file names are sanitized automatically (accents transliterated — `é`→`e` — other symbols replaced
with `_`); the export report lists anything that was renamed.

## Texture Set Exporter (second, independent extension)

`minervha_texture_exporter/` is a separate add-on in this repo. It has nothing to do with Wild Life:
it writes plain, engine-agnostic PBR maps for every material of the selected mesh(es), named after
the material —

| File | Content |
|---|---|
| `<Material>_D.png` | the map feeding **Base Color** |
| `<Material>_ORM.png` | **R = AO, G = Roughness, B = Metallic** |
| `<Material>_N.png` | the map feeding **Normal** |

Install it like the other one (its own `.zip`); both can be installed at once. In the **Minervha**
tab, the **Texture Set** panel: pick an output folder, optionally a **Max resolution** cap
(downscales only what exceeds it, aspect kept), and press **Export Textures**.

Per channel: a directly plugged texture is copied (and downscaled); a constant value becomes a flat
map; a node graph is flattened with a **Cycles bake** (toggle, on by default — turn it off and a
procedural channel falls back to a reported neutral instead).

**AO** is only real when the material already uses a **packed ORM/MRAO texture** — i.e. roughness
and/or metallic come out of a *Separate Color* node. The AO slot is then *derived* from the channels
those two occupy (rough=G + metal=B → AO=R; metal=R + rough=G → AO=B), never guessed from a file
name. Without a packed source the red channel is filled white. A material with no normal input gets
no `_N` file, and the run's report says so.

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
minervha_texture_exporter/     # second extension (build root) — _D/_ORM/_N map sets
  blender_manifest.toml
  __init__.py                   # register -> ui
  ui.py                         # "Texture Set" panel + Export Textures operator
  sources.py                    # material -> per-channel source (image / constant / procedural)
  images.py                     # pixel plumbing: cap, channel read, ORM packing, PNG write
  bake.py                       # Cycles fallback for procedural channels
  export.py                     # naming, AO inference, orchestration, report
tests/                          # Python<->JS parity (mapper golden)
```

`mapper.py` is a faithful port of the Studio's `mapMaterial.js`, kept honest by a golden parity test, so the
materials written into the `.wlsave` match what the Studio would produce.

## Credits

The Principled BSDF node-tracing at the heart of this addon (`bsdf_trace.py`) is a port of an original
Blender material-export script by **Nocna Klacz** — huge thanks for the foundation this is built on.

## License

GPL-3.0-or-later (bpy add-ons are derivative works of Blender).
