"""Regression tests for multi-material texture handling in wlsave_export.

Two distinct texture files that share a basename (same file name in different
folders, or names that sanitise to the same ASCII string) must NOT collapse into
one bundled file — each material has to keep its own texture. A previous version
deduped textures by basename alone, so the second file silently overwrote the
first in the ZIP and both materials pointed at one image. These tests pin the
collision-safe behaviour (dedup by source path, suffix colliding basenames) AND
the legitimate case (the same file reused across materials -> one bundled copy).

Pure Python (no bpy). Run:  python tests/test_texture_collision.py  (or pytest)
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


def _tex_norm(mat_name, tex_path, img_name=None):
    """A NormalizedMaterial with one path-kind diffuse texture at `tex_path`."""
    return {
        "name": mat_name,
        "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1},
        "textures": [{
            "name": img_name or (mat_name + "_img"), "fileKind": "path",
            "path": tex_path, "basename": os.path.basename(tex_path),
            "slots": ["Base Color"], "mapping": None,
        }],
    }


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _build(norms):
    tmp = tempfile.mkdtemp(prefix="texcollide_")
    dest = os.path.join(tmp, "out.wlsave")
    report = wlsave_export.build_wlsave(norms, "Coll", dest,
                                        skeleton_path=os.path.join(PKG, "skeleton.json"))
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        data = json.loads(z.read("Coll/Coll.json"))
        tex_bytes = {n.split("/Textures/", 1)[1]: z.read(n)
                     for n in names if "/Textures/" in n}
    return report, data, tex_bytes, tmp


def test_same_basename_distinct_files_are_kept_separate():
    src = tempfile.mkdtemp(prefix="texsrc_")
    try:
        a = os.path.join(src, "oak", "wood.png"); _write(a, b"OAK_BYTES")
        b = os.path.join(src, "pine", "wood.png"); _write(b, b"PINE_BYTES")
        norms = [_tex_norm("OakMat", a), _tex_norm("PineMat", b)]
        report, data, tex_bytes, tmp = _build(norms)
        try:
            # Two distinct bundled files (one renamed to avoid the clash).
            assert len(tex_bytes) == 2, tex_bytes.keys()
            assert set(tex_bytes.values()) == {b"OAK_BYTES", b"PINE_BYTES"}, "both files must survive"
            # Each material points at a DIFFERENT bundled texture.
            mats = {m["name"]: m for m in data["customMaterials"]}
            oak_path = mats["Coll/OakMat"]["diffuseTexturePath"]
            pine_path = mats["Coll/PineMat"]["diffuseTexturePath"]
            assert oak_path and pine_path and oak_path != pine_path, (oak_path, pine_path)
            # The collision was surfaced, not silent.
            assert report["texturesRenamed"], "collision rename must be reported"
            assert not report["texturesMissing"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        shutil.rmtree(src, ignore_errors=True)


def test_accent_collapse_distinct_files_are_kept_separate():
    # "café.png" sanitises to "cafe.png" -> collides with a real "cafe.png".
    src = tempfile.mkdtemp(prefix="texsrc_")
    try:
        a = os.path.join(src, "a", "café.png"); _write(a, b"ACCENT_BYTES")
        b = os.path.join(src, "b", "cafe.png"); _write(b, b"PLAIN_BYTES")
        norms = [_tex_norm("MatA", a), _tex_norm("MatB", b)]
        report, data, tex_bytes, tmp = _build(norms)
        try:
            assert len(tex_bytes) == 2, tex_bytes.keys()
            assert set(tex_bytes.values()) == {b"ACCENT_BYTES", b"PLAIN_BYTES"}
            mats = {m["name"]: m for m in data["customMaterials"]}
            assert mats["Coll/MatA"]["diffuseTexturePath"] != mats["Coll/MatB"]["diffuseTexturePath"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        shutil.rmtree(src, ignore_errors=True)


def test_same_file_reused_dedups_to_one_copy():
    # The SAME file referenced by two materials must bundle ONCE (legit dedup).
    src = tempfile.mkdtemp(prefix="texsrc_")
    try:
        shared = os.path.join(src, "shared.png"); _write(shared, b"SHARED")
        norms = [_tex_norm("MatA", shared), _tex_norm("MatB", shared)]
        report, data, tex_bytes, tmp = _build(norms)
        try:
            assert len(tex_bytes) == 1, tex_bytes.keys()
            mats = {m["name"]: m for m in data["customMaterials"]}
            # Both point at the one shared bundled copy.
            assert mats["Coll/MatA"]["diffuseTexturePath"] == mats["Coll/MatB"]["diffuseTexturePath"]
            assert not report["texturesRenamed"], "no rename for a genuinely shared file"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        shutil.rmtree(src, ignore_errors=True)


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"TEXTURE COLLISION FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"TEXTURE COLLISION OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
