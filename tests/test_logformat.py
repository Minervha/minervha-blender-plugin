"""Tests for wlsave_export.format_export_log (pure text rendering of an export report)."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import wlsave_export  # noqa: E402


def _scene_report():
    return {
        "created": ["C/A", "C/B"],
        "texturesCopied": ["a.png"], "texturesReExported": ["b.jpg"], "texturesMissing": [{"basename": "x"}],
        "objectsExported": ["O1", "O2", "O3"], "noUv": ["O3"],
        "meshesWritten": ["m1.obj", "m2.obj"], "meshExportFailed": ["k3"],
        "materialsBaked": [["M", "diffuse"]], "materialsApproximated": [],
        "masterGroup": "MySave", "enableCollision": True, "thumbnail": True,
        "materialsUnused": ["U1", "U2"],
        "missingDetail": [{"texture": "x.tga", "reason": "file not found",
                           "materials": ["Bar"], "channels": ["diffuse"], "objects": ["Crate"]}],
        "needsBake": [],
    }


def test_scene_log_has_key_sections():
    txt = wlsave_export.format_export_log(
        _scene_report(), mode="scene", scope="Selected objects", level="",
        options="JPG q90, master group on", dest="C:/out/MySave.wlsave", elapsed=73.4,
        timeline=[("Meshes", 44.0), ("Textures", 18.5)])
    assert "Result: OK — collection" in txt
    assert "Mode: Scene" in txt
    assert "Scope: Selected objects" in txt
    assert "Elapsed: 73.4s" in txt
    assert "Timeline:" in txt and "Meshes" in txt and "44.0s" in txt
    assert "Materials created: 2" in txt
    assert "Meshes written: 2   (failed 1)" in txt
    assert "Master group: MySave" in txt and "Collisions: on" in txt and "Thumbnail: yes" in txt
    assert "Unused materials dropped: 2" in txt
    assert "Not transported (1):" in txt and "x.tga [file not found]" in txt
    assert txt.endswith("\n")


def test_map_to_level_label():
    txt = wlsave_export.format_export_log(_scene_report(), mode="scene", level="Showroom")
    assert "Result: OK — map 'Showroom'" in txt


def test_cancelled_log():
    txt = wlsave_export.format_export_log({}, mode="scene", cancelled=True, elapsed=12.0,
                                          timeline=[("Meshes", 12.0)])
    assert "Result: CANCELLED (no .wlsave written)" in txt
    assert "Elapsed: 12.0s" in txt
    assert "Materials created: 0" in txt


def test_materials_mode_log():
    rep = {"created": ["P/W"], "texturesCopied": [], "texturesReExported": [],
           "texturesMissing": [], "thumbnail": False, "missingDetail": []}
    txt = wlsave_export.format_export_log(rep, mode="materials", scope="Whole file")
    assert "Mode: Materials" in txt
    assert "objectsExported" not in txt           # scene-only block omitted
    assert "Thumbnail: no" in txt


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"LOGFORMAT FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"LOGFORMAT OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
