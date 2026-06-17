"""Filename-sanitization tests for wlsave_export.

The game only accepts ASCII [A-Za-z0-9_-] in the names it extracts from a
.wlsave (collection folder, JSON, texture files); accents (é) and other symbols
corrupt it. This checks the sanitizer reduces names to that charset and that
build_wlsave writes a ZIP + JSON whose every path component is game-safe.
Pure Python (no bpy) — the on-disk PNG copy path needs no Blender. Run:

    python tests/test_sanitize.py
"""

import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import wlsave_export  # noqa: E402

# A single safe path component: ASCII name + optional ASCII-alnum extension.
SAFE_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9]+)?$")


def test_sanitize_name():
    cases = {
        "Béton Spécial": "Beton_Special",
        "café": "cafe",
        "Wörld 99": "World_99",
        "Metal_01": "Metal_01",     # already safe — untouched
        "Wall-2": "Wall-2",
        "a/b\\c": "a_b_c",
        "../../etc": "etc",
        "!!!": "Mat",               # all-junk -> fallback
        "": "Mat",                  # empty -> fallback
    }
    for raw, exp in cases.items():
        got = wlsave_export._sanitize_name(raw, "Mat")
        assert got == exp, f"_sanitize_name({raw!r}) = {got!r}, expected {exp!r}"


def test_sanitize_basename():
    cases = {
        "béton.png": "beton.png",
        "wall normal.PNG": "wall_normal.png",
        "café.jpeg": "cafe.jpeg",
        "no_ext": "no_ext",
        "weird!@#.tga": "weird.tga",
    }
    for raw, exp in cases.items():
        got = wlsave_export._sanitize_basename(raw)
        assert got == exp, f"_sanitize_basename({raw!r}) = {got!r}, expected {exp!r}"


def test_build_wlsave_paths_are_safe():
    tmp = tempfile.mkdtemp(prefix="wlsave_test_")
    try:
        tex_path = os.path.join(tmp, "café.png")
        with open(tex_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")  # non-empty bytes; copied as-is

        norms = [{
            "name": "Bétôn Spécial",
            "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1},
            "textures": [{
                "name": "tex", "fileKind": "path",
                "path": tex_path, "basename": "café.png",
                "slots": ["Base Color"],
            }],
        }]
        dest = os.path.join(tmp, "out.wlsave")
        report = wlsave_export.build_wlsave(
            norms, "Bétôn Spécial", dest, skeleton_path=os.path.join(PKG, "skeleton.json"))

        assert report["name"] == "Beton_Special", report["name"]
        assert report["nameOriginal"] == "Bétôn Spécial"
        assert {"from": "café.png", "to": "cafe.png"} in report["texturesRenamed"]

        with zipfile.ZipFile(dest) as z:
            names = z.namelist()
            for n in names:
                for comp in n.split("/"):
                    assert SAFE_RE.match(comp), f"unsafe ZIP path component {comp!r} in {n!r}"
            assert "Beton_Special/Beton_Special.json" in names
            assert "Beton_Special/Textures/cafe.png" in names
            data = json.loads(z.read("Beton_Special/Beton_Special.json"))

        mat = data["customMaterials"][0]
        assert mat["name"] == "Beton_Special", mat["name"]
        assert mat["diffuseTexturePath"] == "Beton_Special/Textures/cafe.png", mat["diffuseTexturePath"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run():
    tests = [test_sanitize_name, test_sanitize_basename, test_build_wlsave_paths_are_safe]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"SANITIZE FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"SANITIZE OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
