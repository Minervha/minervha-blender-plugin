"""verify_tga_reexport_live.py — Blender-side regression probe for the .tga re-export fix.

A texture in a non-copyable on-disk format (.tga, .exr, ...) must be re-encoded by Blender
into a game-readable PNG/JPG. A freshly-loaded file image loads lazily (has_data False); a
past bug set file_format before that buffer materialized, so save() tried to decode the .tga
as PNG and threw — the raw .tga was then copied into the .wlsave and the game could not read
it. This probe builds a real .tga, runs the actual build_wlsave path, and asserts the bundled
file is a valid PNG/JPG (magic bytes + reloadable by Blender).

Pure-Python tests can't cover this (it needs bpy). Run headless:

    blender --background --factory-startup --python tests/verify_tga_reexport_live.py
"""
import os
import sys
import tempfile
import zipfile

import bpy

# Import the DEV exporter (relative imports fall back to absolute on sys.path).
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.join(os.path.dirname(_HERE), "minervha_material_exporter")
sys.path.insert(0, _PKG)
import wlsave_export  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPG_MAGIC = b"\xff\xd8\xff"


def _make_tga(path, w=64, h=64, alpha=False):
    img = bpy.data.images.new("probe_src", w, h, alpha=alpha)
    img.pixels = [0.5, 0.5, 1.0, 1.0] * (w * h)   # flat normal-ish
    img.update()
    img.file_format = "TARGA"
    img.filepath_raw = path
    img.save()
    bpy.data.images.remove(img)


def _fresh_tga_norm(tga_path, slot="Normal", mat="Probe"):
    """A NormalizedMaterial referencing a FRESH (never-displayed) .tga image — the bug trigger."""
    loaded = bpy.data.images.load(tga_path, check_existing=False)
    if slot == "Normal":
        loaded.colorspace_settings.name = "Non-Color"
    return {
        "name": mat, "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1}, "objects": ["Plane"],
        "textures": [{"name": loaded.name, "file": tga_path, "fileKind": "path",
                      "path": tga_path, "basename": os.path.basename(tga_path),
                      "slots": [slot], "mapping": None}],
    }


def _bundled_textures(dest):
    with zipfile.ZipFile(dest) as z:
        return {n.split("/Textures/", 1)[1]: z.read(n)
                for n in z.namelist() if "/Textures/" in n}


def _reloadable(data, ext):
    p = os.path.join(tempfile.gettempdir(), "wl_probe_check" + ext)
    with open(p, "wb") as f:
        f.write(data)
    try:
        chk = bpy.data.images.load(p, check_existing=False)
        ok = chk.size[0] > 0 and chk.size[1] > 0
        bpy.data.images.remove(chk)
        return ok
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


def main():
    work = tempfile.mkdtemp(prefix="wl_tga_probe_")
    fails = []

    def check(name, cond, detail=""):
        print(("  OK  " if cond else "  FAIL") + " " + name + ("" if cond else " :: " + detail))
        if not cond:
            fails.append(name)

    # 1) Default opts: opaque .tga normal map -> valid PNG, re-exported (not copied raw).
    tga = os.path.join(work, "rock_normal.tga")
    _make_tga(tga, alpha=False)
    dest = os.path.join(work, "default.wlsave")
    rep = wlsave_export.build_wlsave([_fresh_tga_norm(tga)], "Probe", dest)
    texs = _bundled_textures(dest)
    name = next(iter(texs), "")
    check("default: bundled as .png", name.lower().endswith(".png"), "got %r" % name)
    check("default: valid PNG magic", bool(texs) and next(iter(texs.values()))[:8] == PNG_MAGIC)
    check("default: reloadable", bool(texs) and _reloadable(next(iter(texs.values())), ".png"))
    check("default: re-exported not copied", rep["texturesReExported"] and not rep["texturesCopied"],
          "reExported=%s copied=%s" % (rep["texturesReExported"], rep["texturesCopied"]))
    check("default: no .tga left in bundle", not any(n.lower().endswith(".tga") for n in texs))

    # 2) prefer_jpg on an opaque .tga (diffuse) -> valid JPG.
    tga2 = os.path.join(work, "wood_diffuse.tga")
    _make_tga(tga2, alpha=False)
    dest2 = os.path.join(work, "jpg.wlsave")
    wlsave_export.build_wlsave([_fresh_tga_norm(tga2, slot="Base Color", mat="Wood")], "ProbeJ", dest2,
                               tex_opts={"prefer_jpg": True, "jpg_quality": 85, "max_res": None})
    texs2 = _bundled_textures(dest2)
    name2 = next(iter(texs2), "")
    check("prefer_jpg: bundled as .jpg", name2.lower().endswith((".jpg", ".jpeg")), "got %r" % name2)
    check("prefer_jpg: valid JPG magic", bool(texs2) and next(iter(texs2.values()))[:3] == JPG_MAGIC)

    # 3) max_res downscale on a big .tga -> valid PNG at the reduced size.
    big = os.path.join(work, "big_normal.tga")
    _make_tga(big, w=256, h=256, alpha=False)
    dest3 = os.path.join(work, "small.wlsave")
    wlsave_export.build_wlsave([_fresh_tga_norm(big)], "ProbeR", dest3,
                               tex_opts={"prefer_jpg": False, "jpg_quality": 90, "max_res": 64})
    texs3 = _bundled_textures(dest3)
    ok_png = bool(texs3) and next(iter(texs3.values()))[:8] == PNG_MAGIC
    check("max_res: valid PNG magic", ok_png)
    if ok_png:
        p = os.path.join(work, "downscaled.png")
        with open(p, "wb") as f:
            f.write(next(iter(texs3.values())))
        chk = bpy.data.images.load(p, check_existing=False)
        check("max_res: downscaled to <=64", max(chk.size) <= 64, "size=%s" % (tuple(chk.size),))
        bpy.data.images.remove(chk)

    print("\nTGA RE-EXPORT PROBE: " + ("OK — all checks passed" if not fails
          else "FAILED — %d: %s" % (len(fails), ", ".join(fails))))
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
