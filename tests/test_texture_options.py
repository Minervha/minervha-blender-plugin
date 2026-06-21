"""Tests for the texture export options in wlsave_export.

Covers the pure decision function `_plan_texture` (JPG-vs-PNG + downscale, no bpy),
the defensive `_image_facts` fallback when bpy is absent, and an end-to-end
`build_wlsave(..., tex_opts=...)` smoke test proving the option threads through and
that on-disk files still bundle without a Blender session.

Pure Python (no bpy). Run:  python tests/test_texture_options.py  (or pytest)
"""

import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import wlsave_export  # noqa: E402


# --- _plan_texture: (target_format, needs_export) -----------------------------------------

def _plan(kind, ext, has_alpha, w=0, h=0, prefer_jpg=False, max_res=None):
    return wlsave_export._plan_texture(kind, ext, has_alpha, w, h, prefer_jpg, max_res)


def test_prefer_jpg_converts_opaque_ondisk_png():
    assert _plan("path", ".png", has_alpha=False, prefer_jpg=True) == ("JPEG", True)


def test_alpha_png_stays_png_even_with_prefer_jpg():
    # An alpha-bearing texture must never become JPG.
    assert _plan("path", ".png", has_alpha=True, prefer_jpg=True) == (None, False)


def test_opaque_ondisk_jpg_within_limits_is_copied_as_is():
    assert _plan("path", ".jpg", has_alpha=False, prefer_jpg=True) == (None, False)


def test_prefer_jpg_off_leaves_ondisk_png_as_is():
    # Legacy behavior: nothing to do for an on-disk png when no options are set.
    assert _plan("path", ".png", has_alpha=False, prefer_jpg=False) == (None, False)


def test_packed_opaque_reexports_to_png_by_default():
    assert _plan("packed", "", has_alpha=False, prefer_jpg=False) == ("PNG", True)


def test_packed_opaque_reexports_to_jpg_when_preferred():
    assert _plan("packed", "", has_alpha=False, prefer_jpg=True) == ("JPEG", True)


def test_packed_with_alpha_reexports_to_png_when_preferred():
    assert _plan("generated", "", has_alpha=True, prefer_jpg=True) == ("PNG", True)


def test_non_copyable_ext_reexports():
    assert _plan("path", ".tga", has_alpha=False, prefer_jpg=False) == ("PNG", True)
    assert _plan("path", ".exr", has_alpha=False, prefer_jpg=True) == ("JPEG", True)


def test_resize_only_keeps_jpg_format():
    # prefer_jpg off: an oversized on-disk JPG must resize but stay JPG (no PNG upconvert).
    assert _plan("path", ".jpg", has_alpha=False, w=8192, h=8192,
                 prefer_jpg=False, max_res=2048) == ("JPEG", True)


def test_resize_only_keeps_alpha_png_as_png():
    assert _plan("path", ".png", has_alpha=True, w=4096, h=4096,
                 prefer_jpg=True, max_res=2048) == ("PNG", True)


def test_under_max_res_jpg_is_not_touched():
    assert _plan("path", ".jpg", has_alpha=False, w=1024, h=1024,
                 prefer_jpg=True, max_res=2048) == (None, False)


def test_oversized_opaque_png_converts_and_resizes():
    assert _plan("path", ".png", has_alpha=False, w=4096, h=2048,
                 prefer_jpg=True, max_res=1024) == ("JPEG", True)


# --- _material_uses_alpha: the signal driving a baked diffuse's alpha channel ----------------

def test_alpha_linked_uses_alpha():
    assert wlsave_export._material_uses_alpha({"alphaLinked": True}) is True
    # even with a constant alpha of 1, a linked Alpha still counts (the mask is in the graph).
    assert wlsave_export._material_uses_alpha({"alphaLinked": True, "alpha": 1.0}) is True


def test_constant_alpha_below_one_uses_alpha():
    assert wlsave_export._material_uses_alpha({"alpha": 0.5}) is True
    assert wlsave_export._material_uses_alpha({"alpha": 0.99}) is True


def test_opaque_material_does_not_use_alpha():
    # Alpha == 1, unlinked -> opaque (pure-transmission glass also lands here: alpha 1, not linked).
    assert wlsave_export._material_uses_alpha({"alpha": 1.0, "alphaLinked": False}) is False
    assert wlsave_export._material_uses_alpha({"alpha": 0.99999}) is False     # ~1, float noise
    assert wlsave_export._material_uses_alpha({}) is False
    assert wlsave_export._material_uses_alpha({"alpha": None}) is False


def test_missing_and_udim_are_skipped():
    assert _plan("missing", "", has_alpha=False, prefer_jpg=True) == (None, False)
    assert _plan("udim", ".png", has_alpha=False, prefer_jpg=True, max_res=512) == (None, False)


# --- keep_normal_png: normal maps stay lossless even with prefer_jpg --------------------------

def _capture_targets(norms, **opts):
    """Run the texture pre-pass with bpy faked: every image reported opaque (so the JPG path is
    reachable without Blender) and `_export_image` stubbed to record the chosen target format
    per image name. Returns {img_name: target_format} for the images that needed re-encoding
    (an on-disk file copied as-is never reaches `_export_image`, so it won't appear)."""
    captured = {}
    orig_facts, orig_export = wlsave_export._image_facts, wlsave_export._export_image
    wlsave_export._image_facts = lambda name: (False, 0, 0)        # opaque, no size cap
    def _fake_export(image_name, dest_dir, used, target_format, jpg_quality, max_res):
        captured[image_name] = target_format
        return ("/tmp/%s" % image_name, image_name)
    wlsave_export._export_image = _fake_export
    try:
        wlsave_export._process_textures(norms, "/tmp", **opts)
    finally:
        wlsave_export._image_facts, wlsave_export._export_image = orig_facts, orig_export
    return captured


def _one_tex_norm(name, path, slot):
    return [{"name": "M", "textures": [
        {"name": name, "fileKind": "path", "path": path, "basename": path,
         "slots": [slot], "directSlots": [slot]}]}]


def test_keep_normal_png_keeps_tga_normal_as_png_with_prefer_jpg():
    norms = _one_tex_norm("nrm", "n.tga", "Normal")
    assert _capture_targets(norms, prefer_jpg=True, keep_normal_png=True) == {"nrm": "PNG"}


def test_normal_becomes_jpg_without_keep_normal_png():
    # Legacy behavior (option off): a normal map is converted like any opaque texture.
    norms = _one_tex_norm("nrm", "n.tga", "Normal")
    assert _capture_targets(norms, prefer_jpg=True, keep_normal_png=False) == {"nrm": "JPEG"}


def test_keep_normal_png_does_not_affect_diffuse():
    norms = _one_tex_norm("col", "c.tga", "Base Color")
    assert _capture_targets(norms, prefer_jpg=True, keep_normal_png=True) == {"col": "JPEG"}


def test_ondisk_png_normal_is_copied_not_converted_with_keep_normal_png():
    # An on-disk PNG normal already satisfies prefer_jpg-off, so it's copied as-is (never
    # re-encoded) — it must not appear among the re-exported images.
    norms = _one_tex_norm("nrm", "n.png", "Normal")
    assert _capture_targets(norms, prefer_jpg=True, keep_normal_png=True) == {}


# --- _image_facts fallback (no bpy) -------------------------------------------------------

def test_image_facts_without_bpy_assumes_alpha_and_no_size():
    # wlsave_export.bpy is None outside Blender, so facts are conservative:
    assert wlsave_export.bpy is None
    assert wlsave_export._image_facts("anything") == (True, 0, 0)


# --- end-to-end: tex_opts threads through, on-disk file still bundles (bpy absent) --------

def test_build_wlsave_with_tex_opts_bundles_ondisk_png():
    src = tempfile.mkdtemp(prefix="texopt_src_")
    out = tempfile.mkdtemp(prefix="texopt_out_")
    try:
        png = os.path.join(src, "wood.png")
        with open(png, "wb") as f:
            f.write(b"PNG_BYTES")
        norms = [{
            "name": "Mat",
            "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1},
            "textures": [{
                "name": "wood_img", "fileKind": "path", "path": png,
                "basename": "wood.png", "slots": ["Base Color"], "mapping": None,
            }],
        }]
        dest = os.path.join(out, "out.wlsave")
        report = wlsave_export.build_wlsave(
            norms, "Coll", dest, skeleton_path=os.path.join(PKG, "skeleton.json"),
            tex_opts={"prefer_jpg": True, "jpg_quality": 85, "max_res": 2048})
        # Without bpy the image is treated as alpha-bearing -> stays PNG -> copied as-is.
        with zipfile.ZipFile(dest) as z:
            tex = {n.split("/Textures/", 1)[1]: z.read(n)
                   for n in z.namelist() if "/Textures/" in n}
        assert tex == {"wood.png": b"PNG_BYTES"}, tex
        assert report["texturesCopied"] == ["wood.png"], report["texturesCopied"]
        assert not report["texturesReExported"]
    finally:
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"TEXTURE OPTIONS FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"TEXTURE OPTIONS OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
