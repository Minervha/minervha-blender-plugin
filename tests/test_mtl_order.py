"""Regression tests for obj_export.reorder_mtl_blocks.

wm.obj_export writes the .obj's `usemtl` directives in Blender material-SLOT order but
the sibling .mtl's `newmtl` blocks ALPHABETICALLY. Wild Life indexes a mesh's material
sections by the .mtl order and overrides them with the prop's CustomMaterial{i} (slot
order), so an alphabetical .mtl swaps the materials on any multi-material mesh whose slot
order isn't alphabetical (the reported wood<->fabric bug). reorder_mtl_blocks realigns the
.mtl to the .obj's usemtl/slot order so the two indices match again.

Pure Python (no bpy). Run:  python tests/test_mtl_order.py  (or pytest)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import obj_export  # noqa: E402


def _obj(slot_order):
    """A minimal OBJ whose usemtl directives appear in `slot_order`."""
    lines = ["# Blender 5.1.2", "mtllib mesh.mtl", "o Mesh", "v 0 0 0"]
    for n in slot_order:
        lines += ["usemtl " + n, "f 1 1 1"]
    return "\n".join(lines) + "\n"


def _mtl(order):
    """A minimal MTL with one block per name, in `order` (mimics the alphabetical export)."""
    out = ["# Blender 5.1.2 MTL File: 'None'", "# www.blender.org", ""]
    for n in order:
        out += ["newmtl " + n, "Ns 250.0", "Kd 0.8 0.8 0.8", ""]
    return "\n".join(out) + "\n"


def _newmtl_order(mtl_text):
    return [l[len("newmtl "):].strip() for l in mtl_text.splitlines() if l.startswith("newmtl ")]


def test_alphabetical_mtl_is_realigned_to_slot_order():
    # Slot order [Wood, Fabric]; exporter wrote the .mtl alphabetically [Fabric, Wood].
    obj_text = _obj(["Wood", "Fabric"])
    mtl_text = _mtl(["Fabric", "Wood"])
    out = obj_export.reorder_mtl_blocks(obj_text, mtl_text)
    assert _newmtl_order(out) == ["Wood", "Fabric"], _newmtl_order(out)


def test_three_materials_realigned():
    obj_text = _obj(["Mzzz", "Aaa", "Kmm"])           # slot order
    mtl_text = _mtl(["Aaa", "Kmm", "Mzzz"])           # alphabetical
    out = obj_export.reorder_mtl_blocks(obj_text, mtl_text)
    assert _newmtl_order(out) == ["Mzzz", "Aaa", "Kmm"], _newmtl_order(out)


def test_already_aligned_is_returned_byte_identical():
    obj_text = _obj(["Aaa", "Bbb"])
    mtl_text = _mtl(["Aaa", "Bbb"])                   # already matches
    out = obj_export.reorder_mtl_blocks(obj_text, mtl_text)
    assert out == mtl_text, "an already-aligned .mtl must not be rewritten"


def test_header_and_block_bodies_preserved():
    obj_text = _obj(["Wood", "Fabric"])
    mtl_text = _mtl(["Fabric", "Wood"])
    out = obj_export.reorder_mtl_blocks(obj_text, mtl_text)
    # Header (the two comment lines) survives, and every block body is intact.
    assert out.startswith("# Blender 5.1.2 MTL File: 'None'\n# www.blender.org\n")
    assert out.count("Ns 250.0") == 2
    assert out.count("newmtl ") == 2


def test_unused_material_is_appended_last():
    # A .mtl material that no face references (no usemtl) keeps its order, after the used ones.
    obj_text = _obj(["Wood", "Fabric"])               # "Extra" never referenced
    mtl_text = _mtl(["Extra", "Fabric", "Wood"])
    out = obj_export.reorder_mtl_blocks(obj_text, mtl_text)
    assert _newmtl_order(out) == ["Wood", "Fabric", "Extra"], _newmtl_order(out)


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"MTL ORDER FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"MTL ORDER OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
