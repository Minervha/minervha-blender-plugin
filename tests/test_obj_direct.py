"""Tests for obj_export.format_obj_text — the pure OBJ text builder of the direct writer.

Pure Python (no bpy/numpy): feeds synthetic mesh arrays and pins the emitted v/vt/vn/f/usemtl
lines, the per-loop vt/vn indexing, winding reversal on a mirrored basis, and material sections.

Run:  python tests/test_obj_direct.py  (or pytest)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import obj_export  # noqa: E402


def _quad(mirrored=False, uvs=True, normals=True, slots=("Wood",), mat_index=(0,)):
    return {
        "verts": [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        "loop_verts": [0, 1, 2, 3],
        "uvs": [(0, 0), (1, 0), (1, 1), (0, 1)] if uvs else None,
        "normals": [(0, 0, 1)] * 4 if normals else None,
        "loop_start": [0], "loop_total": [4],
        "mat_index": list(mat_index), "slots": list(slots), "mirrored": mirrored,
    }


def _lines(txt):
    return txt.splitlines()


def test_basic_quad_v_vt_vn_f():
    txt = obj_export.format_obj_text(_quad(), "mesh.mtl")
    L = _lines(txt)
    assert L[0] == "mtllib mesh.mtl"
    assert "v 0.000000 0.000000 0.000000" in L
    assert L.count("v 0.000000 0.000000 0.000000") == 1
    assert sum(1 for x in L if x.startswith("v ")) == 4
    assert sum(1 for x in L if x.startswith("vt ")) == 4
    assert sum(1 for x in L if x.startswith("vn ")) == 4
    assert "usemtl Wood" in L
    # per-loop vt/vn indices == global loop index + 1
    assert "f 1/1/1 2/2/2 3/3/3 4/4/4" in L


def test_winding_reversed_when_mirrored():
    txt = obj_export.format_obj_text(_quad(mirrored=True, normals=False), "m.mtl")
    # mirrored -> loop order reversed; normals absent -> v/vt only
    assert "f 4/4 3/3 2/2 1/1" in _lines(txt)


def test_no_uv_no_normal_face_is_vertex_only():
    txt = obj_export.format_obj_text(_quad(uvs=False, normals=False), None)
    L = _lines(txt)
    assert not any(x.startswith("mtllib") for x in L)
    assert "f 1 2 3 4" in L


def test_normals_only_uses_double_slash():
    txt = obj_export.format_obj_text(_quad(uvs=False, normals=True), None)
    assert "f 1//1 2//2 3//3 4//4" in _lines(txt)


def test_two_material_sections():
    a = _quad(slots=("Wood", "Metal"), mat_index=(0, 1))
    # two faces sharing the 4 verts (degenerate but fine for the text test)
    a["loop_start"] = [0, 4]; a["loop_total"] = [4, 4]
    a["loop_verts"] = [0, 1, 2, 3, 0, 1, 2, 3]
    a["uvs"] = [(0, 0)] * 8; a["normals"] = None
    L = _lines(obj_export.format_obj_text(a, None))
    i_wood, i_metal = L.index("usemtl Wood"), L.index("usemtl Metal")
    assert i_wood < i_metal                         # sections in face/slot order
    assert L.count("usemtl Wood") == 1 and L.count("usemtl Metal") == 1
    # the second face references loops 4..7
    assert "f 1/5 2/6 3/7 4/8" in L


def test_empty_slot_is_named_None():
    a = _quad(slots=("",), mat_index=(0,))
    assert "usemtl None" in _lines(obj_export.format_obj_text(a, None))


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"OBJ DIRECT FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"OBJ DIRECT OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
