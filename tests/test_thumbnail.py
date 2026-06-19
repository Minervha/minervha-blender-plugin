"""Tests for the save thumbnail bundling (pure — no bpy).

Pins the certified Studio convention (electron-helpers/wlsaveOps.js):
  * the icon is bundled as `<Name>/<Name>.png`,
  * it is written BEFORE any Textures/ entry — the reader (extractFirstPngFromZip)
    takes the FIRST `.png` in archive order, so a texture PNG must never precede it,
  * `bHasDedicatedIcon` mirrors whether an icon was bundled.

Without bpy, `_prepare_icon` accepts an on-disk .png as-is and rejects other formats
(re-encoding is the Blender-side path, validated live).

Run:  python tests/test_thumbnail.py  (or pytest)
"""

import json
import os
import struct
import sys
import tempfile
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import wlsave_export  # noqa: E402


def _tiny_png(w=2, h=2):
    def chunk(typ, data):
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def test_prepare_icon_reads_png_without_bpy():
    tmp = tempfile.mkdtemp(prefix="icon_")
    try:
        png = os.path.join(tmp, "pic.png")
        with open(png, "wb") as f:
            f.write(_tiny_png())
        data = wlsave_export._prepare_icon(png)
        assert data == _tiny_png()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_prepare_icon_rejects_non_png_without_bpy():
    tmp = tempfile.mkdtemp(prefix="icon_")
    try:
        jpg = os.path.join(tmp, "pic.jpg")
        with open(jpg, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0not-really")
        # no bpy in the test runner -> only .png is accepted; .jpg falls back to None
        assert wlsave_export._prepare_icon(jpg) is None
        assert wlsave_export._prepare_icon(None) is None
        assert wlsave_export._prepare_icon(os.path.join(tmp, "missing.png")) is None
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_write_zip_icon_precedes_textures():
    tmp = tempfile.mkdtemp(prefix="zip_")
    try:
        dest = os.path.join(tmp, "out.wlsave")
        tex = {"alpha.png": b"a", "beta.png": b"b"}
        wlsave_export._write_zip(dest, "Pack", {"k": 1}, tex, icon_bytes=_tiny_png())
        with zipfile.ZipFile(dest) as z:
            names = z.namelist()
        assert "Pack/Pack.png" in names
        icon_i = names.index("Pack/Pack.png")
        tex_is = [i for i, n in enumerate(names) if n.startswith("Pack/Textures/")]
        assert tex_is and all(icon_i < i for i in tex_is), names
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_wlsave_with_thumbnail_sets_flag():
    tmp = tempfile.mkdtemp(prefix="mat_")
    try:
        png = os.path.join(tmp, "thumb.png")
        with open(png, "wb") as f:
            f.write(_tiny_png())
        dest = os.path.join(tmp, "mats.wlsave")
        norms = [{"name": "Wood", "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1}, "textures": []}]
        report = wlsave_export.build_wlsave(norms, "Pack", dest,
                                            skeleton_path=os.path.join(PKG, "skeleton.json"),
                                            thumbnail=png)
        with zipfile.ZipFile(dest) as z:
            names = z.namelist()
            data = json.loads(z.read("Pack/Pack.json"))
        assert "Pack/Pack.png" in names
        assert data["bHasDedicatedIcon"] is True
        assert report["thumbnail"] is True
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_wlsave_without_thumbnail_flag_false():
    tmp = tempfile.mkdtemp(prefix="mat_")
    try:
        dest = os.path.join(tmp, "mats.wlsave")
        norms = [{"name": "Wood", "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1}, "textures": []}]
        report = wlsave_export.build_wlsave(norms, "Pack", dest,
                                            skeleton_path=os.path.join(PKG, "skeleton.json"))
        with zipfile.ZipFile(dest) as z:
            names = z.namelist()
            data = json.loads(z.read("Pack/Pack.json"))
        assert data["bHasDedicatedIcon"] is False
        assert report["thumbnail"] is False
        assert "Pack/Pack.png" not in names
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"THUMBNAIL FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"THUMBNAIL OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
