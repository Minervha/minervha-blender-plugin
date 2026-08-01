"""export.py — turn resolved material sources into a `_D` / `_ORM` / `_N` map set.

Policy (pure decisions at the top, orchestration below):

  `<Material>_D.png`    Base Color — the plugged texture (downscaled to the cap), a flat
                        fill for a constant colour, or a bake for a node graph.
  `<Material>_ORM.png`  R = AO, G = Roughness, B = Metallic, composed pixel-wise.
                        AO exists only when roughness/metallic come out of a **packed**
                        texture (through a Separate node); its index is then *derived*
                        from the channels those two occupy — never guessed from a file
                        name. No packed source ⇒ white (1.0).
  `<Material>_N.png`    Normal — the plugged texture, or a bake. A material with no
                        normal input gets no file (a flat 128/128/255 map is noise).

Sizes: a map that comes from a texture keeps that texture's size, capped. The ORM is
composed at its largest image contributor, capped, and its other contributors are
resampled to match. A map with no image behind it (flat or baked) uses the material's
reference size — `next_pow2` of its largest source texture, capped, 1024 if the material
has no texture at all.

The pure helpers (`sanitize_name`, `unique_name`, `reference_size`, `infer_ao`) carry the
policy and are unit-tested without Blender; the `export_*` functions do the bpy work.
"""

import contextlib
import os
import re
import unicodedata

try:
    from . import bake, images, sources          # packaged extension
except ImportError:                              # dev / sys.path import
    import bake
    import images
    import sources


SUFFIXES = {"diffuse": "_D", "orm": "_ORM", "normal": "_N"}

# Neutral values used when a channel is procedural and baking is off/unavailable: mid
# roughness and non-metal. Reported, never silent — the ORM stays complete either way.
FALLBACK_ROUGHNESS = 0.5
FALLBACK_METALLIC = 0.0

# Reference size for maps with no image behind them, when the material has no texture at all.
DEFAULT_REFERENCE_SIZE = 1024

_BAD_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


def sanitize_name(name, fallback="Material"):
    """Reduce a material name to a portable file name [A-Za-z0-9_-]: accents are
    transliterated (é→e), remaining non-ASCII dropped, every other run collapsed to "_"."""
    ascii_only = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    return _BAD_NAME_RE.sub("_", ascii_only).strip("_-") or fallback


def unique_name(base, taken):
    """`base`, or `base_2`, `base_3`... when two materials sanitize to the same name —
    otherwise the second set would overwrite the first's three files."""
    if base not in taken:
        return base
    i = 2
    while "%s_%d" % (base, i) in taken:
        i += 1
    return "%s_%d" % (base, i)


def next_pow2(n):
    p = 1
    while p < n:
        p <<= 1
    return p


def reference_size(max_source_dim, cap, default=DEFAULT_REFERENCE_SIZE):
    """Size for maps with no image source: the material's largest texture rounded up to a
    power of two, capped; `default` when the material has no texture at all."""
    base = next_pow2(int(max_source_dim)) if max_source_dim and max_source_dim > 0 else default
    return min(base, int(cap)) if cap else base


def infer_ao(roughness_src, metallic_src):
    """The AO source inside a packed ORM/MRAO texture, or None (⇒ white AO).

    Only a value arriving through a **Separate Color/RGB** node proves the texture is
    packed; a grayscale roughness plugged straight from `Color` is a lone map, not an ORM.
    The AO index is then derived from what roughness/metallic occupy on that same image:

      rough=G, metal=B  -> AO=R   (ORM)          metal=R, rough=G -> AO=B   (MRAO)
      rough=G only      -> AO=R   (ORM)          metal=B only     -> AO=R   (ORM)
      only R occupied   -> None — R is AO's own slot in both conventions, so whatever
                                  sits there is not something we can reason about.
    """
    packed = [s for s in (roughness_src, metallic_src)
              if s and s.get("kind") == "image" and s.get("packed")
              and s.get("channel") in (0, 1, 2)]
    if not packed:
        return None
    image = packed[0]["image"]
    used = {s["channel"] for s in packed if s["image"] is image}
    if used == {0}:
        return None
    free = [i for i in (0, 1, 2) if i not in used]
    return {"kind": "image", "image": image, "channel": free[0]} if free else None


def _orm_dimensions(contributors, cap, reference):
    """(w, h) the ORM is composed at: the largest image contributor (by pixel count),
    capped; `reference`×`reference` when none of the three is an image."""
    best = (0, 0)
    for src in contributors:
        if not src or src.get("kind") != "image":
            continue
        w, h = images.dimensions(src["image"])
        if w * h > best[0] * best[1]:
            best = (w, h)
    if best == (0, 0):
        return reference, reference
    return images.fit(best[0], best[1], cap)


class _Baker:
    """Bakes a channel and hands back either a saved file or a pixel channel, keeping the
    throwaway bake image's lifetime inside the call. `scene` is None when baking is off or
    Cycles is unavailable, in which case every bake reports as unavailable."""

    def __init__(self, scene):
        self.scene = scene

    @property
    def available(self):
        return self.scene is not None

    def to_file(self, mat, channel, size, path):
        img = bake.bake_channel(mat, channel, size, size, self.scene)
        if img is None:
            return None
        try:
            return (size, size) if bake.save_baked(img, path) else None
        finally:
            images.discard(img)

    def to_channel(self, mat, channel, width, height):
        img = bake.bake_channel(mat, channel, width, height, self.scene)
        if img is None:
            return None
        try:
            return images.channel_of(img, 0)
        finally:
            images.discard(img)


def _export_diffuse(mat, src, out_dir, name, cap, reference, baker, record):
    """Write `<name>_D.png`. A base colour is a whole colour: a source reached through a
    Separate node (a single channel) is not one, so it goes down the bake path instead."""
    path = os.path.join(out_dir, name + SUFFIXES["diffuse"] + ".png")
    kind = src.get("kind")
    if kind == "image" and src.get("channel") is None:
        if images.write_direct(src["image"], path, cap):
            record["files"].append(os.path.basename(path))
            return
        record["notes"].append("diffuse: '%s' could not be read (missing file?)"
                               % src["image"].name)
        return
    if kind == "const" and src.get("value") is not None:
        if images.write_flat_color(src["value"], reference, reference, path):
            record["files"].append(os.path.basename(path))
            record["notes"].append("diffuse: constant colour — flat %dpx map" % reference)
        return
    if not baker.available:
        record["notes"].append("diffuse: procedural — enable Bake to flatten it (no _D written)")
        return
    if baker.to_file(mat, "diffuse", reference, path):
        record["files"].append(os.path.basename(path))
        record["baked"].append("diffuse")
    else:
        record["notes"].append("diffuse: bake produced nothing (no _D written)")


def _export_normal(mat, src, out_dir, name, cap, reference, baker, record):
    """Write `<name>_N.png`, or nothing when the material has no normal input at all."""
    path = os.path.join(out_dir, name + SUFFIXES["normal"] + ".png")
    kind = src.get("kind")
    if kind == "image" and src.get("channel") is None:
        if images.write_direct(src["image"], path, cap):
            record["files"].append(os.path.basename(path))
            return
        record["notes"].append("normal: '%s' could not be read (missing file?)"
                               % src["image"].name)
        return
    if kind in ("const", "none"):
        record["notes"].append("normal: no normal map on this material (no _N written)")
        return
    if not baker.available:
        record["notes"].append("normal: procedural/bump — enable Bake to flatten it (no _N written)")
        return
    if baker.to_file(mat, "normal", reference, path):
        record["files"].append(os.path.basename(path))
        record["baked"].append("normal")
    else:
        record["notes"].append("normal: bake produced nothing (no _N written)")


def _orm_channel(mat, channel, src, width, height, fallback, baker, record):
    """One ORM channel as a flat float array: read from its texture, filled from its
    constant, baked, or — when a procedural channel can't be baked — a reported neutral."""
    kind = src.get("kind") if src else "none"
    if kind == "image":
        arr = images.read_channel(src["image"], src.get("channel"), width, height)
        if arr is not None:
            return arr
        record["notes"].append("%s: '%s' could not be read — flat %.2f"
                               % (channel, src["image"].name, fallback))
        return images.flat_channel(fallback, width, height)
    if kind == "const" and src.get("value") is not None:
        value = src["value"]
        return images.flat_channel(value[0] if hasattr(value, "__len__") else value, width, height)
    if baker.available:
        arr = baker.to_channel(mat, channel, width, height)
        if arr is not None:
            record["baked"].append(channel)
            return arr
        record["notes"].append("%s: bake produced nothing — flat %.2f" % (channel, fallback))
        return images.flat_channel(fallback, width, height)
    record["notes"].append("%s: procedural — enable Bake to flatten it (flat %.2f used)"
                           % (channel, fallback))
    return images.flat_channel(fallback, width, height)


def _export_orm(mat, srcs, out_dir, name, cap, reference, baker, record):
    """Compose and write `<name>_ORM.png` (R=AO, G=Roughness, B=Metallic)."""
    roughness_src, metallic_src = srcs.get("roughness"), srcs.get("metallic")
    ao_src = infer_ao(roughness_src, metallic_src)
    width, height = _orm_dimensions([ao_src, roughness_src, metallic_src], cap, reference)

    ao = None
    if ao_src is not None:
        ao = images.read_channel(ao_src["image"], ao_src["channel"], width, height)
        if ao is None:
            record["notes"].append("AO: '%s' could not be read — white used"
                                   % ao_src["image"].name)
    if ao is None:
        ao = images.flat_channel(1.0, width, height)
        record["ao"] = "white (no packed source)"
    else:
        record["ao"] = "packed %s (channel %s)" % (ao_src["image"].name, "RGB"[ao_src["channel"]])

    roughness = _orm_channel(mat, "roughness", roughness_src, width, height,
                             FALLBACK_ROUGHNESS, baker, record)
    metallic = _orm_channel(mat, "metallic", metallic_src, width, height,
                            FALLBACK_METALLIC, baker, record)
    path = os.path.join(out_dir, name + SUFFIXES["orm"] + ".png")
    if images.write_orm(ao, roughness, metallic, width, height, path):
        record["files"].append(os.path.basename(path))
    else:
        record["notes"].append("ORM: could not be written")


def export_material(mat, out_dir, cap, baker, taken):
    """Export one material's map set into `out_dir`. Returns its report record."""
    record = {"material": mat.name, "name": None, "files": [], "baked": [], "notes": [],
              "ao": None}
    srcs = sources.resolve_material(mat)
    if srcs is None:
        record["notes"].append("skipped: no Principled BSDF")
        return record
    name = unique_name(sanitize_name(mat.name), taken)
    taken.add(name)
    record["name"] = name
    reference = reference_size(sources.max_source_dim(mat), cap)
    _export_diffuse(mat, srcs["diffuse"], out_dir, name, cap, reference, baker, record)
    _export_orm(mat, srcs, out_dir, name, cap, reference, baker, record)
    _export_normal(mat, srcs["normal"], out_dir, name, cap, reference, baker, record)
    return record


def needs_bake(materials):
    """True if any material has a procedural channel — i.e. whether the run has to pay for
    a Cycles bake environment at all."""
    for mat in materials:
        srcs = sources.resolve_material(mat)
        if srcs and any(s.get("kind") == "procedural" for s in srcs.values()):
            return True
    return False


def export_texture_sets(materials, out_dir, cap=None, allow_bake=True, on_material=None):
    """Export every material's `_D`/`_ORM`/`_N` set into `out_dir`.

    `cap` caps the longest side (None = no limit). `allow_bake` opts into flattening
    procedural channels with Cycles. `on_material(index, total, name)` is an optional
    progress callback. Returns {"records": [...], "files": n, "bake": "..."}.

    One material's failure is isolated: it is recorded and the run continues — a single bad
    node tree must not cost the whole export."""
    os.makedirs(out_dir, exist_ok=True)
    records, taken = [], set()
    with contextlib.ExitStack() as stack:
        scene = None
        if allow_bake and bake.can_bake() and needs_bake(materials):
            scene = stack.enter_context(bake.bake_environment())
        baker = _Baker(scene)
        bake_state = ("on" if scene is not None else
                      "off (disabled)" if not allow_bake else
                      "off (Cycles unavailable)" if not bake.can_bake() else "not needed")
        for i, mat in enumerate(materials, 1):
            if on_material is not None:
                on_material(i, len(materials), mat.name)
            try:
                records.append(export_material(mat, out_dir, cap, baker, taken))
            except Exception as exc:
                records.append({"material": mat.name, "name": None, "files": [], "baked": [],
                                "ao": None,
                                "notes": ["failed: %s" % (exc or exc.__class__.__name__)]})
                print("Minervha Textures: material '%s' failed (%s) — export continues"
                      % (mat.name, exc))
    return {"records": records, "files": sum(len(r["files"]) for r in records),
            "bake": bake_state, "dir": out_dir}


def format_report(report):
    """Render a report as plain text (system console + the panel's last-run log)."""
    lines = ["Minervha Texture Exporter — last run", "=" * 36,
             "Output: %s" % report.get("dir", ""),
             "Files written: %d   Bake: %s" % (report.get("files", 0), report.get("bake", "")),
             ""]
    for rec in report.get("records", []):
        head = rec["name"] or rec["material"]
        lines.append("%s  (%s)" % (head, rec["material"]))
        if rec["files"]:
            lines.append("    wrote: %s" % ", ".join(rec["files"]))
        if rec.get("ao"):
            lines.append("    AO: %s" % rec["ao"])
        if rec["baked"]:
            lines.append("    baked: %s" % ", ".join(rec["baked"]))
        for note in rec["notes"]:
            lines.append("    - %s" % note)
    return "\n".join(lines) + "\n"
