# Minervha Blender Plugin — Material Exporter

Blender **4.2+** extension that exports a Blender scene's materials to **Wild Life**, two ways:

- **`.txt`** — the format read by **Minervha Studio**'s injector (maps the materials into a save).
- **`.wlsave`** — a **self-contained collection bundle** (JSON + textures), installable directly via the Studio.

> 🚧 **WIP** — under development.

## Installation (dev)

1. Grab the extension `.zip` (in `dist/`, or zip the `minervha_material_exporter/` folder).
2. Blender → **Edit → Preferences → Add-ons →** ⌄ → **Install from Disk…** → pick the `.zip`.
3. In the 3D viewport: **N** → **"Minervha"** tab.

## Structure

```
minervha_material_exporter/   # extension source (build root)
  blender_manifest.toml        # Blender 4.2+ extension manifest
  __init__.py                  # register / UI
  mapper.py                    # NormalizedMaterial -> customMaterials (port of mapMaterial.js)
  skeleton.json                # collection skeleton for the .wlsave
tests/                         # parity tests (Python port vs Studio JS)
```

## License

GPL-3.0-or-later (bpy add-ons are derivative works of Blender).
