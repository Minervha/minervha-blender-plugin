"""Headless Blender probe: the Cycles baker reproduces the material, not an approximation of it.

Regressions found on DAZ-converted maps (Nocna's Wellness Center / Hidden Dojo, 2026-11-21):
  1. Base Color of a METALLIC material baked black (DIFFUSE/COLOR pass = 0 on metal).
  2. Normal Map bound to a NAMED UV layer ("Default UVs") baked flat (placeholder had only "UVMap").
  3. Roughness = texture x 0.45 (Math) baked as the raw texture (ROUGHNESS pass ignored the Math).
  4. Opacity mask from a Mix Shader (Transparent vs BSDF, the DAZ "Transparent" group) not baked
     into the diffuse alpha.
Each is asserted on a synthetic material with known pixel values.

Run:  F:\\Software\\Blender\\5.1\\blender.exe --background --factory-startup --python tests/verify_bake_fidelity_live.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import bpy                 # noqa: E402
import numpy as np         # noqa: E402
import bake                # noqa: E402
import bsdf_trace          # noqa: E402

FAILS = []
TMP = tempfile.mkdtemp(prefix="bake_fid_")


def check(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)


def img(name, w, fill_fn, colorspace="Non-Color"):
    im = bpy.data.images.new(name, w, w, alpha=True)
    im.colorspace_settings.name = colorspace
    px = np.zeros((w, w, 4), dtype=np.float32)
    fill_fn(px)
    im.pixels.foreach_set(px.ravel())
    im.pack()
    return im


def load(path):
    im = bpy.data.images.load(path)
    a = np.empty(len(im.pixels), dtype=np.float32)
    im.pixels.foreach_get(a)
    w, h = im.size
    bpy.data.images.remove(im)
    return a.reshape(h, w, 4)


bpy.ops.wm.read_factory_settings(use_empty=True)
W = 64


def base_fill(px):
    px[..., 0], px[..., 1], px[..., 2], px[..., 3] = 0.8, 0.4, 0.2, 1.0


def rough_fill(px):
    px[..., :3], px[..., 3] = 0.6, 1.0


def normal_fill(px):                       # tilted normal on the right half only
    px[..., 0], px[..., 1], px[..., 2], px[..., 3] = 0.5, 0.5, 1.0, 1.0
    px[:, W // 2:, 0] = 0.85


def mask_fill(px):                         # opaque top half
    px[..., 3] = 1.0
    px[W // 2:, :, :3] = 1.0


mat = bpy.data.materials.new("Daz")
mat.use_nodes = True
nt = mat.node_tree
pr = nt.nodes["Principled BSDF"]
pr.inputs["Metallic"].default_value = 1.0
t_base = nt.nodes.new("ShaderNodeTexImage"); t_base.image = img("base", W, base_fill, "sRGB")
nt.links.new(t_base.outputs["Color"], pr.inputs["Base Color"])
t_r = nt.nodes.new("ShaderNodeTexImage"); t_r.image = img("rough", W, rough_fill)
mul = nt.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"; mul.inputs[1].default_value = 0.45
nt.links.new(t_r.outputs["Color"], mul.inputs[0]); nt.links.new(mul.outputs[0], pr.inputs["Roughness"])
t_n = nt.nodes.new("ShaderNodeTexImage"); t_n.image = img("nrm", W, normal_fill)
nm = nt.nodes.new("ShaderNodeNormalMap"); nm.uv_map = "Default UVs"
nt.links.new(t_n.outputs["Color"], nm.inputs["Color"]); nt.links.new(nm.outputs["Normal"], pr.inputs["Normal"])
# DAZ-like cutout: Mix(Fac=mask, Transparent, Principled) inside a group.
grp = bpy.data.node_groups.new("DAZ Transparent", "ShaderNodeTree")
grp.interface.new_socket("Fac", in_out="INPUT", socket_type="NodeSocketFloat")
grp.interface.new_socket("BSDF", in_out="INPUT", socket_type="NodeSocketShader")
grp.interface.new_socket("BSDF", in_out="OUTPUT", socket_type="NodeSocketShader")
gi, go = grp.nodes.new("NodeGroupInput"), grp.nodes.new("NodeGroupOutput")
tr, mix = grp.nodes.new("ShaderNodeBsdfTransparent"), grp.nodes.new("ShaderNodeMixShader")
grp.links.new(gi.outputs[0], mix.inputs[0]); grp.links.new(tr.outputs[0], mix.inputs[1])
grp.links.new(gi.outputs[1], mix.inputs[2]); grp.links.new(mix.outputs[0], go.inputs[0])
g = nt.nodes.new("ShaderNodeGroup"); g.node_tree = grp
t_m = nt.nodes.new("ShaderNodeTexImage"); t_m.image = img("mask", W, mask_fill)
nt.links.new(t_m.outputs["Color"], g.inputs[0]); nt.links.new(pr.outputs[0], g.inputs[1])
nt.links.new(g.outputs[0], nt.nodes["Material Output"].inputs["Surface"])

mask = bsdf_trace.find_alpha_mask(nt, bsdf_trace.build_local_parent_map(nt))
check(mask is not None and mask[0].node.name == t_m.name and mask[1] is False,
      "find_alpha_mask resolves the group's Fac to the top-level mask texture")

with bake.bake_environment() as sc:
    p_d = bake.bake_channel(mat, "diffuse", W, os.path.join(TMP, "d.png"), bake_alpha=True, scene=sc)
    p_r = bake.bake_channel(mat, "roughness", W, os.path.join(TMP, "r.png"), scene=sc)
    p_n = bake.bake_channel(mat, "normal", W, os.path.join(TMP, "n.png"), scene=sc)

d, r, n = load(p_d), load(p_r), load(p_n)
check(abs(d[..., 0].mean() - 0.8) < 0.03 and abs(d[..., 1].mean() - 0.4) < 0.03,
      "metallic Base Color not black (got %.3f %.3f %.3f)" % tuple(d[..., :3].reshape(-1, 3).mean(0)))
check(d[W - 4:, :, 3].mean() > 0.95 and d[:4, :, 3].mean() < 0.05,
      "Mix Shader cutout baked into diffuse alpha (top %.2f / bottom %.2f)"
      % (d[W - 4:, :, 3].mean(), d[:4, :, 3].mean()))
check(abs(r[..., 0].mean() - 0.27) < 0.02, "roughness = 0.6 x 0.45 = 0.27 (got %.3f)" % r[..., 0].mean())
# Flat (the bug) = 0 difference; the same map on the DEFAULT UV bakes right 0.650 / left 0.502.
check(n[:, W // 2 + 4:, 0].mean() - n[:, :W // 2 - 4, 0].mean() > 0.10,
      "named-UV normal map not flat (right %.3f vs left %.3f)"
      % (n[:, W // 2 + 4:, 0].mean(), n[:, :W // 2 - 4, 0].mean()))

# ── Point 1: Principled INSIDE a group (metal) + two Principled MIXED ─────────────────────────────
def solid(r, g, b):
    def f(px):
        px[..., 0], px[..., 1], px[..., 2], px[..., 3] = r, g, b, 1.0
    return f


mat2 = bpy.data.materials.new("Grouped")
mat2.use_nodes = True
nt2 = mat2.node_tree
nt2.nodes.remove(nt2.nodes["Principled BSDF"])
pg = bpy.data.node_groups.new("PBR Wrap", "ShaderNodeTree")
pg.interface.new_socket("Color", in_out="INPUT", socket_type="NodeSocketColor")
pg.interface.new_socket("BSDF", in_out="OUTPUT", socket_type="NodeSocketShader")
gi2, go2 = pg.nodes.new("NodeGroupInput"), pg.nodes.new("NodeGroupOutput")
p_in = pg.nodes.new("ShaderNodeBsdfPrincipled"); p_in.inputs["Metallic"].default_value = 1.0
pg.links.new(gi2.outputs[0], p_in.inputs["Base Color"]); pg.links.new(p_in.outputs[0], go2.inputs[0])
g2 = nt2.nodes.new("ShaderNodeGroup"); g2.node_tree = pg
t2 = nt2.nodes.new("ShaderNodeTexImage"); t2.image = img("red", W, solid(0.9, 0.1, 0.1), "sRGB")
nt2.links.new(t2.outputs["Color"], g2.inputs[0])
p_b = nt2.nodes.new("ShaderNodeBsdfPrincipled"); p_b.inputs["Base Color"].default_value = (0.1, 0.1, 0.9, 1)
mix2 = nt2.nodes.new("ShaderNodeMixShader"); mix2.inputs[0].default_value = 0.5
nt2.links.new(g2.outputs[0], mix2.inputs[1]); nt2.links.new(p_b.outputs[0], mix2.inputs[2])
nt2.links.new(mix2.outputs[0], nt2.nodes["Material Output"].inputs["Surface"])
nodes_before = (len(nt2.nodes), len(pg.nodes), len(bpy.data.materials), len(bpy.data.node_groups))
with bake.bake_environment() as sc:
    p_g = bake.bake_channel(mat2, "diffuse", W, os.path.join(TMP, "g.png"), scene=sc)
g = load(p_g)
# 50/50 mix of linear(0.9,0.1,0.1) and (0.1,0.1,0.9) -> linear (0.5, 0.1, 0.5); read back as linear.
got = tuple(g[..., :3].reshape(-1, 3).mean(0))


def s2l(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def l2s(c):
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


# texture (0.9,0.1,0.1) is sRGB-encoded, the constant (0.1,0.1,0.9) is linear; mixed 50/50 in linear,
# saved sRGB -> expected (0.698, 0.259, 0.706).
exp = tuple(l2s(0.5 * s2l(a) + 0.5 * b) for a, b in zip((0.9, 0.1, 0.1), (0.1, 0.1, 0.9)))
check(max(abs(x - y) for x, y in zip(got, exp)) < 0.02,
      "grouped METAL Principled mixed 50/50 with a second Principled = exact blend "
      "(got %.3f %.3f %.3f, expected %.3f %.3f %.3f)" % (got + exp))
check(abs(got[0] - got[2]) < 0.15, "both Principled contribute equally (R %.3f vs B %.3f)" % (got[0], got[2]))
check((len(nt2.nodes), len(pg.nodes), len(bpy.data.materials), len(bpy.data.node_groups)) == nodes_before,
      "original material + shared group untouched, no leftover copies")

# ── Point 3: Transparent BSDF reached THROUGH a group from the Mix Shader ─────────────────────────
mat3 = bpy.data.materials.new("MaskThroughGroup")
mat3.use_nodes = True
nt3 = mat3.node_tree
tg = bpy.data.node_groups.new("Clear", "ShaderNodeTree")
tg.interface.new_socket("BSDF", in_out="OUTPUT", socket_type="NodeSocketShader")
tgo, ttr = tg.nodes.new("NodeGroupOutput"), tg.nodes.new("ShaderNodeBsdfTransparent")
tg.links.new(ttr.outputs[0], tgo.inputs[0])
gclr = nt3.nodes.new("ShaderNodeGroup"); gclr.node_tree = tg
m3 = nt3.nodes.new("ShaderNodeMixShader")
t3 = nt3.nodes.new("ShaderNodeTexImage"); t3.image = img("mask3", W, mask_fill)
nt3.links.new(t3.outputs["Color"], m3.inputs[0]); nt3.links.new(gclr.outputs[0], m3.inputs[1])
nt3.links.new(nt3.nodes["Principled BSDF"].outputs[0], m3.inputs[2])
nt3.links.new(m3.outputs[0], nt3.nodes["Material Output"].inputs["Surface"])
mk = bsdf_trace.find_alpha_mask(nt3, bsdf_trace.build_local_parent_map(nt3))
check(mk is not None and mk[0].node.name == t3.name and mk[1] is False,
      "Transparent behind a group still recognised as the cutout")

if FAILS:
    print("BAKE FIDELITY: FAIL (%d)" % len(FAILS))
    sys.exit(1)
print("BAKE FIDELITY: OK")
