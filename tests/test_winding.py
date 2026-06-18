"""Unit tests for obj_export.reverse_obj_winding (mirror -> CCW/outward faces).

Pure Python (no bpy). Run:  python tests/test_winding.py  (or pytest)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import obj_export  # noqa: E402  (pure helpers import fine without bpy)


def test_triangle_winding_reversed():
    src = "f 1/1/1 2/2/2 3/3/3\n"
    assert obj_export.reverse_obj_winding(src) == "f 3/3/3 2/2/2 1/1/1\n"


def test_quad_and_plain_indices():
    assert obj_export.reverse_obj_winding("f 1 2 3 4\n") == "f 4 3 2 1\n"
    assert obj_export.reverse_obj_winding("f 7//2 8//2 9//2\n") == "f 9//2 8//2 7//2\n"


def test_non_face_lines_untouched():
    src = ("v 0 0 0\n"
           "vt 0 0\n"
           "vn 0 0 1\n"
           "usemtl Wood\n"
           "f 1/1/1 2/2/2 3/3/3\n")
    out = obj_export.reverse_obj_winding(src)
    assert "v 0 0 0\n" in out and "vt 0 0\n" in out and "vn 0 0 1\n" in out and "usemtl Wood\n" in out
    assert "f 3/3/3 2/2/2 1/1/1\n" in out


def test_no_trailing_newline():
    assert obj_export.reverse_obj_winding("f 1/1/1 2/2/2 3/3/3") == "f 3/3/3 2/2/2 1/1/1"


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"WINDING FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"WINDING OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
