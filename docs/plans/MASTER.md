# MASTER — Blender Plugin "Minervha Material Exporter"

**Status:** design **approved** (brainstorming done) — building chunk by chunk
**Date:** 2026-06-17
**Project location:** `F:\Minervha\Studio\Minervha Blender Plugin` (sibling of "Minervha Studio" / "Minervha Site", **own git** → GitHub public `Minervha/minervha-blender-plugin`, branch `main`)
**Target:** Blender **4.2+** extension (`blender_manifest.toml`), installable `.zip`

---

## Starting point (current state)

- A Blender script `MaterialPrint_hardened.py` exports a scene's materials (Principled BSDF + textures)
  to a `.txt` (`texture_usage.txt`). It runs **by hand** (Blender text editor), no packaging.
- **Minervha Studio** (Electron/JS) reads that `.txt` and injects the materials into a save/collection:
  - `electron-helpers/materialInjector/blenderParse.js` — `.txt` parser → `MaterialNormalized[]`
  - `electron-helpers/materialInjector/mapMaterial.js` — normalized → `customMaterials` entry + textures
  - `electron-helpers/materialInjector/injectMaterials.js` — writes into the save + copies textures
- `.wlsave` format (confirmed in `wlsaveOps.js::exportWlsave`) = a **ZIP**:
  ```
  <Name>/<Name>.json          # save JSON (collection => "level": "")
  <Name>/<Name>.png           # optional thumbnail
  <Name>/Textures/<tex>.png   # referenced textures, bundled
  ```
  and inside the JSON: `*TexturePath = "<Name>/Textures/<basename>"`.

## Final objective

A **Blender 4.2+ extension** with a panel ("Minervha" N-panel) offering **TWO exports**:
- **A. `.txt`** — current format, read by the Studio (existing path unchanged).
- **B. `.wlsave`** — a **self-contained portable bundle** (collection) containing the `.json` + textures,
  installable through the Studio's existing install flow.

## Success criteria

1. The extension installs into Blender 4.2+ (drag-and-drop) and shows the "Minervha" panel.
2. **Mode A:** the produced `.txt` is parsed by `blenderParse.js` with no regression.
3. **Mode B:** the produced `.wlsave` imports into the Studio → the collection appears with its materials
   and their textures shown in-game.
4. Mode B's `customMaterials[]` is **identical** to what `injectMaterials.js` produces for the same scene
   (guaranteed by the golden / parity test).
5. Packed/generated/`.tga` textures (which the `.txt`→Studio path cannot resolve) are **resolved** in the
   `.wlsave` via PNG re-export.

---

## Scope

**In scope:**
- Scene introspection → `NormalizedMaterial[]` (same shape as `blenderParse.js` output).
- **Per-export scope selector:** selected objects / Blender Collection / whole file.
- Serializer **A**: `.txt`, byte-identical to what the original script produces (parsed by `blenderParse.js`).
- Serializer **B**: mapper (**faithful port** of `mapMaterial.js`) → `customMaterials[]` → **bundled**
  collection skeleton → `.wlsave` ZIP.
- **Textures:** copy PNG/JPG as-is; **re-export packed/generated/`.tga` as PNG via Blender**; dedup by
  basename; ref `<Name>/Textures/<basename>`; color space preserved (Non-Color for normal/rough/metallic,
  sRGB for base/emissive).
- **Bundled** collection skeleton (`skeleton.json`) derived from a real game save.
- N-panel UI + post-run **report** (materials created, textures bundled/re-exported/skipped).
- Extension packaging 4.2+ (`blender_manifest.toml`) → installable `.zip`.
- **No-drift guard:** golden file (snapshot of the expected `customMaterials` output) + Python↔JS parity
  test on shared fixtures.

**Out of scope (explicitly):**
- Writing directly into the game's `Saved` folder (mode B = portable `.wlsave` **only**).
- Applying materials to a prop (`MaterialOverride`).
- Collection thumbnail/icon in **v1** (`bHasDedicatedIcon:false`; OpenGL render can be added later).
- Reverse round-trip WL → Blender.
- Blender < 4.2 support.

---

## Architecture — shared tracing core, two serializers

> Mirrors the Studio's proven `parse → map → inject` layering so the two outputs can never disagree about
> what they read from the scene. The node-tracing helpers from the original script are the single shared
> unit; mode A stays byte-identical, mode B builds the normalized model on top of the same tracing.

```
Scene (bpy) ──[shared node tracing]──┐
                                     ├─▶ Serializer A: texture_usage.txt   (byte-identical to the script)
                                     └─▶ introspect → NormalizedMaterial[] ─▶ map → customMaterials[] → .wlsave
```

### Python modules

| File | Role |
|---|---|
| `blender_manifest.toml` + `__init__.py` | Extension manifest (min Blender 4.2.0) + register/unregister |
| `bsdf_trace.py` | Shared node-tracing helpers ported from the script (forward/backward trace, group walk, image resolution) |
| `txt_export.py` | Writes the `.txt`, kept **byte-identical** to the original script's output |
| `introspect.py` | Builds `NormalizedMaterial` from each material (for mode B). Scope-aware |
| `mapper.py` | `NormalizedMaterial` → `customMaterials` entry — **faithful port of `mapMaterial.js`** |
| `wlsave_export.py` | Textures (copy / re-export / dedup) + fills the skeleton + writes the ZIP `<Name>/<Name>.json` + `<Name>/Textures/…` |
| `skeleton.json` | Minimal bundled collection skeleton (derived from a real save) |
| `ui.py` | "Minervha" N-panel (3D sidebar): scope, name, two operators, report |

### `.wlsave` flow (mode B)
scope + name (panel) → introspect → map each material → gather unique textures (copy PNG/JPG as-is,
re-export packed/generated/`.tga` as PNG, dedup) → load `skeleton.json`, set
`customMaterials`/`name`/`level:""` → write the ZIP → report. Result: a portable `.wlsave` installable
through the Studio.

---

## Locked decisions

1. **Two modes**: `.txt` (Studio) + `.wlsave` (self-contained bundle).
2. **Scope dropdown** per export (selected objects / Blender Collection / whole file).
3. **Bundled skeleton** (`skeleton.json`), derived from a real save.
4. **Textures**: copy real PNG/JPG + re-export packed/generated/`.tga` as PNG, dedup by basename.
5. **Blender 4.2+ Extension** (`blender_manifest.toml`).
6. **Location**: sibling project under `Studio\`, parity via a committed **golden file**.
7. **No thumbnail** in v1.
8. **Mode B = portable `.wlsave`**, no direct write into the game.
9. **`.txt` byte-identical** to the original script (the parser drops mapping rotation, so the `.txt` is
   built by reusing the script's logic, not reconstructed from the normalized model).

---

## Implementation chunks

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 1 | Scaffold extension (`blender_manifest.toml`, `__init__`, register) + `skeleton.json` | **done** | — |
| 2 | `bsdf_trace.py` + `introspect.py` — scene → `NormalizedMaterial[]` (script port, scope-aware) | pending | needs Blender (live) |
| 3 | `txt_export.py` — `.txt` byte-identical + round-trip parity vs `blenderParse.js` | pending | 2 (shared tracing) |
| 4 | `mapper.py` — faithful port of `mapMaterial.js` + golden parity | **done** | — |
| 5 | `wlsave_export.py` — textures (copy/re-export/dedup) + build JSON + ZIP `.wlsave` | pending | 1, 4 |
| 6 | `ui.py` — N-panel, scope, two operators, report | pending | 3, 5 |

> Chunk 4 is pure data (tested outside Blender). Chunks 2/6 (and the re-export part of 5) need Blender for
> live validation.
