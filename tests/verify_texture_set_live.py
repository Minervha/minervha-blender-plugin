"""verify_texture_set_live.py — Blender-side probe for the texture-set exporter.

Everything pure about the exporter is unit-tested in `test_texture_set.py`; what needs a
real Blender is the pixel path — reading a packed ORM's channels, the resolution cap, and
above all the **colorspace contract**: `images.write_flat_color` writes a LINEAR socket
colour into an sRGB-tagged image and relies on Blender applying the transfer function at
save time. If that assumption is wrong, a constant Base Color exports far too dark and
nothing else would catch it.

Builds three materials (packed ORM + direct maps / all-constant / procedural roughness),
runs the real `export_texture_sets`, and re-reads the written PNGs. Run headless:

    blender --background --factory-startup --python tests/verify_texture_set_live.py
"""
import os
import sys
import tempfile

import bpy
import numpy as np

# Import the DEV package (relative imports fall back to absolute on sys.path).
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.join(os.path.dirname(_HERE), "minervha_texture_exporter")
sys.path.insert(0, _PKG)
import bake      # noqa: E402
import export    # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("  %-52s %s%s" % (label, "OK" if ok else "FAIL", "  " + detail if detail else ""))
    if not ok:
        FAILURES.append(label)


def make_image(name, size, rgb, colorspace="Non-Color"):
    """A generated flat image of one colour (per-channel constants make the assertions exact)."""
    img = bpy.data.images.new(name, size, size, alpha=False)
    img.colorspace_settings.name = colorspace
    px = list(img.pixels)
    for i in range(0, len(px), 4):
        px[i], px[i + 1], px[i + 2], px[i + 3] = rgb[0], rgb[1], rgb[2], 1.0
    img.pixels[:] = px
    return img


def read_pixel(path, index=0):
    """(r, g, b) of the first pixel of a written PNG — `pixels` is raw stored data, so this
    is literally what landed in the file."""
    img = bpy.data.images.load(path, check_existing=False)
    try:
        px = list(img.pixels[:4])
        return px[0], px[1], px[2]
    finally:
        bpy.data.images.remove(img)


def channel_spread(path, index):
    """max-min of one channel of a written PNG — proves a baked map carries detail rather
    than a flat fill."""
    img = bpy.data.images.load(path, check_existing=False)
    try:
        buf = np.empty(len(img.pixels), dtype=np.float32)
        img.pixels.foreach_get(buf)
        chan = buf[index::4]
        return float(chan.max() - chan.min())
    finally:
        bpy.data.images.remove(img)


def dims(path):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        return int(img.size[0]), int(img.size[1])
    finally:
        bpy.data.images.remove(img)


def build_packed_material():
    """Base Color + Normal from their own textures; Roughness/Metallic out of ONE packed
    image through a Separate Color node (R=AO 0.25, G=rough 0.5, B=metal 0.75)."""
    mat = bpy.data.materials.new("Packed Crate")
    mat.use_nodes = True
    tree = mat.node_tree
    principled = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")

    base = tree.nodes.new("ShaderNodeTexImage")
    base.image = make_image("T_base", 128, (0.2, 0.4, 0.6), colorspace="sRGB")
    tree.links.new(base.outputs["Color"], principled.inputs["Base Color"])

    orm = tree.nodes.new("ShaderNodeTexImage")
    orm.image = make_image("T_orm_packed", 8, (0.25, 0.5, 0.75))
    sep = tree.nodes.new("ShaderNodeSeparateColor")
    tree.links.new(orm.outputs["Color"], sep.inputs[0])
    tree.links.new(sep.outputs["Green"], principled.inputs["Roughness"])
    tree.links.new(sep.outputs["Blue"], principled.inputs["Metallic"])

    nrm = tree.nodes.new("ShaderNodeTexImage")
    nrm.image = make_image("T_normal", 32, (0.5, 0.5, 1.0))
    nmap = tree.nodes.new("ShaderNodeNormalMap")
    tree.links.new(nrm.outputs["Color"], nmap.inputs["Color"])
    tree.links.new(nmap.outputs["Normal"], principled.inputs["Normal"])
    return mat


def build_constant_material():
    """No texture at all: Base Color 0.5 linear, Roughness 0.3, Metallic 1.0, no normal."""
    mat = bpy.data.materials.new("Flat Const")
    mat.use_nodes = True
    principled = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    principled.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    principled.inputs["Roughness"].default_value = 0.3
    principled.inputs["Metallic"].default_value = 1.0
    return mat


def build_procedural_material():
    """A Noise texture drives Roughness — no image to copy, only a bake can flatten it."""
    mat = bpy.data.materials.new("Proc Rough")
    mat.use_nodes = True
    tree = mat.node_tree
    principled = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    noise = tree.nodes.new("ShaderNodeTexNoise")
    tree.links.new(noise.outputs["Fac"], principled.inputs["Roughness"])
    return mat


def build_procedural_diffuse_material():
    """Base Color driven by a Mix node — procedural (so it is baked) but with a KNOWN flat
    result of 0.5 linear, which pins down the bake's colour encoding."""
    mat = bpy.data.materials.new("Proc Diffuse")
    mat.use_nodes = True
    tree = mat.node_tree
    principled = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = 'RGBA'
    mix.inputs["Factor"].default_value = 0.5
    mix.inputs[6].default_value = (0.5, 0.5, 0.5, 1.0)      # A
    mix.inputs[7].default_value = (0.5, 0.5, 0.5, 1.0)      # B
    tree.links.new(mix.outputs[2], principled.inputs["Base Color"])
    return mat


def main():
    out_dir = tempfile.mkdtemp(prefix="mvtexset_")
    materials = [build_packed_material(), build_constant_material(),
                 build_procedural_material(), build_procedural_diffuse_material()]
    report = export.export_texture_sets(materials, out_dir, cap=64, allow_bake=True)
    print(export.format_report(report))

    print("Packed material — direct maps + packed ORM")
    d, orm, n = (os.path.join(out_dir, "Packed_Crate" + s) for s in ("_D.png", "_ORM.png", "_N.png"))
    check("_D / _ORM / _N all written", all(os.path.isfile(p) for p in (d, orm, n)))
    if os.path.isfile(d):
        check("cap 64 downscaled the 128px base colour", dims(d) == (64, 64), str(dims(d)))
    if os.path.isfile(n):
        check("cap left the 32px normal untouched", dims(n) == (32, 32), str(dims(n)))
    if os.path.isfile(orm):
        r, g, b = read_pixel(orm)
        check("ORM R = AO 0.25 (derived free channel)", abs(r - 0.25) < 0.01, "r=%.3f" % r)
        check("ORM G = roughness 0.50", abs(g - 0.50) < 0.01, "g=%.3f" % g)
        check("ORM B = metallic 0.75", abs(b - 0.75) < 0.01, "b=%.3f" % b)
        check("ORM composed at the packed source size", dims(orm) == (8, 8), str(dims(orm)))

    print("Constant material — flat maps, no normal")
    d2, orm2, n2 = (os.path.join(out_dir, "Flat_Const" + s) for s in ("_D.png", "_ORM.png", "_N.png"))
    check("_D and _ORM written, no _N", os.path.isfile(d2) and os.path.isfile(orm2)
          and not os.path.isfile(n2))
    if os.path.isfile(d2):
        r, _g, _b = read_pixel(d2)
        # THE colorspace assertion: linear 0.5 must be stored sRGB-encoded (~0.735), not raw.
        check("flat Base Color sRGB-encoded on save (0.5 lin -> ~0.735)",
              abs(r - 0.7354) < 0.02, "stored=%.3f" % r)
    if os.path.isfile(orm2):
        r, g, b = read_pixel(orm2)
        check("ORM R = white AO (no packed source)", abs(r - 1.0) < 0.01, "r=%.3f" % r)
        check("ORM G = constant roughness 0.30", abs(g - 0.30) < 0.01, "g=%.3f" % g)
        check("ORM B = constant metallic 1.00", abs(b - 1.0) < 0.01, "b=%.3f" % b)

    print("Procedural material — Cycles bake fallback")
    orm3 = os.path.join(out_dir, "Proc_Rough_ORM.png")
    d4 = os.path.join(out_dir, "Proc_Diffuse_D.png")
    if not bake.can_bake():
        print("  (Cycles unavailable — bake path skipped)")
    else:
        rec = next(r for r in report["records"] if r["material"] == "Proc Rough")
        check("roughness was baked", "roughness" in rec["baked"], str(rec["baked"]))
        check("_ORM written from the bake", os.path.isfile(orm3))
        if os.path.isfile(orm3):
            spread = channel_spread(orm3, 1)
            check("baked noise roughness carries detail (G varies)", spread > 0.05,
                  "spread=%.3f" % spread)
        if os.path.isfile(d4):
            r, _g, _b = read_pixel(d4)
            # Cycles encodes into the target's colorspace as it bakes, so a 0.5 linear
            # albedo must land sRGB-encoded exactly like the hand-written flat map does.
            check("baked Base Color sRGB-encoded (0.5 lin -> ~0.735)",
                  abs(r - 0.7354) < 0.02, "stored=%.3f" % r)

    print("\n%s — %d failure(s)%s" % ("FAILED" if FAILURES else "PASSED", len(FAILURES),
                                      (": " + ", ".join(FAILURES)) if FAILURES else ""))
    print("Output kept at: %s" % out_dir)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
