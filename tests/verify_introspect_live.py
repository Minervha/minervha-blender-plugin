"""Live Blender probe for the full-material-export feature (chunks 03/08).

Run INSIDE Blender (it needs bpy). From Blender's Python Console:

    exec(open(r"F:\\Minervha\\Studio\\Minervha Blender Plugin\\tests\\verify_introspect_live.py").read())

It (1) confirms the Principled BSDF input names this Blender build exposes,
(2) builds a synthetic material exercising every new path (height via Bump and via
Displacement, transmission, specular, IOR, backface culling, Mapping-node tiling),
(3) runs introspect + mapper on it, and (4) also dumps the whole current file
(scope FILE). Results are written to tests/_live_probe_output.json so they can be
read back without scraping the console.
"""

import os
import sys
import json
import bpy

# Robust when run via exec(open(...).read()) in Blender's console, where __file__
# is undefined: fall back to the known repo path on this machine.
_REPO = r"F:\Minervha\Studio\Minervha Blender Plugin"
try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    HERE = os.path.join(_REPO, "tests")
PKG = os.path.join(_REPO, "minervha_material_exporter")
for p in (PKG, _REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

import bsdf_trace        # noqa: E402
import introspect        # noqa: E402
import mapper            # noqa: E402


def _principled_input_names():
    mat = bpy.data.materials.new("ProbeNames")
    mat.use_nodes = True
    p = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    names = [s.name for s in p.inputs]
    bpy.data.materials.remove(mat)
    return names


def _build_synthetic():
    mat = bpy.data.materials.new("ProbeFull")
    mat.use_nodes = True
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = True  # -> bIsTwoSided should become False
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    out = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")

    def set_in(name, val):
        s = bsdf.inputs.get(name)
        if s is not None:
            try:
                s.default_value = val
            except Exception:
                pass

    set_in("Metallic", 0.8)
    set_in("Roughness", 0.25)
    set_in("Specular IOR Level", 0.2)
    set_in("IOR", 1.45)
    set_in("Transmission Weight", 0.7)   # -> type Transparent, refraction 1.45

    def img(name):
        i = bpy.data.images.get(name) or bpy.data.images.new(name, 8, 8)
        i.filepath = "C:/tex/%s.png" % name
        return i

    # diffuse via a Mapping node (tiling 3,3 / offset 0.5,0.25)
    tex_d = nodes.new("ShaderNodeTexImage"); tex_d.image = img("ProbeColor")
    mapn = nodes.new("ShaderNodeMapping")
    mapn.inputs["Scale"].default_value = (3.0, 3.0, 1.0)
    mapn.inputs["Location"].default_value = (0.5, 0.25, 0.0)
    uv = nodes.new("ShaderNodeTexCoord")
    links.new(uv.outputs["UV"], mapn.inputs["Vector"])
    links.new(mapn.outputs["Vector"], tex_d.inputs["Vector"])
    links.new(tex_d.outputs["Color"], bsdf.inputs["Base Color"])

    # height via Bump
    tex_hb = nodes.new("ShaderNodeTexImage"); tex_hb.image = img("ProbeHeightBump")
    bump = nodes.new("ShaderNodeBump")
    links.new(tex_hb.outputs["Color"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    # height via Displacement -> Material Output
    tex_hd = nodes.new("ShaderNodeTexImage"); tex_hd.image = img("ProbeHeightDisp")
    disp = nodes.new("ShaderNodeDisplacement")
    links.new(tex_hd.outputs["Color"], disp.inputs["Height"])
    links.new(disp.outputs["Displacement"], out.inputs["Displacement"])
    return mat


def main():
    result = {"blenderVersion": bpy.app.version_string, "principledInputs": _principled_input_names()}

    mat = _build_synthetic()
    try:
        norm = introspect.normalize_material(mat)
        mapped = mapper.map_material(norm)
        result["synthetic"] = {
            "normalized": norm,
            "entry": mapped["entry"] if mapped else None,
            "entryKeyOrder": list(mapped["entry"].keys()) if mapped else None,
            "textures": mapped["textures"] if mapped else None,
        }
    finally:
        bpy.data.materials.remove(mat)

    # whole current file (the user's open scene) — normalized only
    result["currentFile"] = introspect.collect(scope="FILE")

    dest = os.path.join(HERE, "_live_probe_output.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("[probe] wrote", dest)
    print("[probe] Blender", result["blenderVersion"])
    if result.get("synthetic", {}).get("entry"):
        e = result["synthetic"]["entry"]
        print("[probe] synthetic type=%s refraction=%s specular=%s twoSided=%s height=%r keys=%d"
              % (e["type"], e["refraction"], e["specular"], e["bIsTwoSided"],
                 e["heightTexturePath"], len(e)))
    return result


main()
