"""Tests for the per-texture "which texture, on which mesh, wasn't transported" detail
(report["missingDetail"]) built by wlsave_export.

The exporter already counts missing textures; this pins that it ALSO says WHICH texture,
on WHICH material + meshes, and WHY (packed / generated / udim / missing path / file not
found) — so the user knows exactly what to fix by hand. Pure Python (no bpy). Run:

    python tests/test_missing_report.py   (or pytest)
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import wlsave_export  # noqa: E402


def _build(norms):
    tmp = tempfile.mkdtemp(prefix="missrep_")
    try:
        dest = os.path.join(tmp, "out.wlsave")
        return wlsave_export.build_wlsave(norms, "Coll", dest,
                                          skeleton_path=os.path.join(PKG, "skeleton.json"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _by_texture(report):
    return {d["texture"]: d for d in report.get("missingDetail", [])}


def test_packed_texture_lists_material_and_meshes():
    # A packed normal map (no file on disk) used by a material on two meshes — the user's
    # exact case: tell them which texture, on which meshes, never made it.
    norms = [{
        "name": "Rock",
        "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1},
        "objects": ["Wall_01", "Floor_03"],
        "textures": [{"name": "rock_normal", "fileKind": "packed", "path": None,
                      "basename": None, "slots": ["Normal"], "mapping": None}],
    }]
    d = _by_texture(_build(norms))
    assert "rock_normal" in d, d
    e = d["rock_normal"]
    assert e["reason"] == "packed", e
    assert e["channels"] == ["normal"], e
    assert e["materials"] == ["Rock"], e
    assert e["objects"] == ["Floor_03", "Wall_01"], e   # sorted, deduped


def test_on_disk_missing_file_is_detailed():
    # A path texture whose file does not exist -> 'file not found', tied to its mesh.
    norms = [{
        "name": "Metal",
        "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1},
        "objects": ["Pipe"],
        "textures": [{"name": "m", "fileKind": "path",
                      "path": os.path.join(tempfile.gettempdir(), "does_not_exist_diffuse.png"),
                      "basename": "diffuse.png", "slots": ["Base Color"], "mapping": None}],
    }]
    d = _by_texture(_build(norms))
    assert "diffuse.png" in d, d
    e = d["diffuse.png"]
    assert e["reason"] == "file not found", e
    assert e["materials"] == ["Metal"] and e["objects"] == ["Pipe"], e


def test_resolved_texture_produces_no_detail():
    # A texture that resolves to a real file must NOT appear in the detail.
    src = tempfile.mkdtemp(prefix="missrep_src_")
    try:
        p = os.path.join(src, "ok.png")
        with open(p, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        norms = [{
            "name": "Good", "baseColor": {"r": 1, "g": 1, "b": 1, "a": 1},
            "objects": ["Cube"],
            "textures": [{"name": "ok", "fileKind": "path", "path": p,
                          "basename": "ok.png", "slots": ["Base Color"], "mapping": None}],
        }]
        report = _build(norms)
        assert report["missingDetail"] == [], report["missingDetail"]
        assert not report["texturesMissing"]
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
        print(f"MISSING REPORT FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"MISSING REPORT OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
