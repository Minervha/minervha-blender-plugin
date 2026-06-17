# Chunk 01 — Scaffold extension + `skeleton.json`

**Status:** `done`
**Depends on:** artifact #2 (a real collection) — **provided**
**Parent:** [`MASTER.md`](MASTER.md)

## Objective
An **installable** Blender 4.2+ extension (manifest + register) + the verified `skeleton.json`, derived from
a real game collection.

## Files touched
- `minervha_material_exporter/blender_manifest.toml` — extension manifest (id, version, min 4.2.0, `files`
  permission, GPL license).
- `minervha_material_exporter/__init__.py` — `register`/`unregister` + a **placeholder** "Minervha" N-panel
  (verifies install; replaced by `ui.py` in chunk 6).
- `minervha_material_exporter/skeleton.json` — collection skeleton (real header + emptied arrays).
- `minervha_material_exporter/summary.md` — folder doc.

## Skeleton source
Derived from `…\SandboxSaveGames\Collections\Clothes\Minervha Maya Top.json` (real save, `version:14`,
`luaVersion:11`, full `cameraOverrideSettings` block). Arrays `props/characters/sexScenes/poses/
customMaterials` **emptied**, `level:""`, `bHasDedicatedIcon:false`, `bOverrideCameraSettings:false`.

## Watch-outs
- Header values are **numeric** (`14`, not `"14"`) — like real saves.
- Manifest: `id` matches `^[a-z][a-z0-9_]*$`, `tagline` ≤ 64 with no trailing period, GPL license (bpy ⇒
  derivative work of Blender), declare `[permissions] files` (we read textures + write `.txt`/`.wlsave`
  outside the extension folder).
- `__init__.py` carries **no `bl_info`** (the manifest replaces it in a 4.2+ extension).

## Verification
- `blender --command extension validate minervha_material_exporter` (or drag-and-drop install) with no error.
- The "Minervha" tab shows up in the 3D viewport sidebar.
