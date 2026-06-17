# Minervha Blender Plugin — Material Exporter

Extension **Blender 4.2+** qui exporte les matériaux d'une scène Blender vers **Wild Life**, de deux façons :

- **`.txt`** — le format lu par l'injecteur de **Minervha Studio** (mappe les matières dans une save).
- **`.wlsave`** — un **bundle de collection autonome** (JSON + textures), installable directement via le Studio.

> 🚧 **WIP** — en cours de développement. Voir [`docs/plans/MASTER.md`](docs/plans/MASTER.md) pour le design et l'avancement.

## Installation (dev)

1. Récupère le `.zip` de l'extension (dossier `dist/`, ou zippe `minervha_material_exporter/`).
2. Blender → **Edit → Preferences → Add-ons →** ⌄ → **Install from Disk…** → choisis le `.zip`.
3. Dans le viewport 3D : **N** → onglet **« Minervha »**.

## Structure

```
minervha_material_exporter/   ← source de l'Extension (racine de build)
  blender_manifest.toml        manifeste Extension 4.2+
  __init__.py                  register / UI
  skeleton.json                squelette de collection pour le .wlsave
docs/plans/                    MASTER.md + chunks d'implémentation
```

## Licence

GPL-3.0-or-later (les add-ons `bpy` sont des œuvres dérivées de Blender).
