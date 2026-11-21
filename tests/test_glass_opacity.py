"""Glass opacity: a Transparent material see-through by TRANSMISSION gets color.a from the
`glass_opacity` setting (WL reads a Transparent's opacity from color.a — calibrated in game,
default 0.05). Pure Python."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "minervha_material_exporter"))

import mapper  # noqa: E402


def _glass(transmission=1.0, tint=(0.2, 0.8, 0.3)):
    return {"name": "G", "skipped": False, "textures": [], "alpha": 1.0, "transmission": transmission,
            "shaderTypes": ["BSDF_PRINCIPLED"], "ior": 1.45,
            "baseColor": {"r": tint[0], "g": tint[1], "b": tint[2], "a": 1.0}}


def test_default_full_transmission():
    e = mapper.map_material(_glass())["entry"]
    assert e["type"] == "Transparent"
    assert abs(e["color"]["a"] - mapper.DEFAULT_GLASS_OPACITY) < 1e-6 and mapper.DEFAULT_GLASS_OPACITY == 0.05


def test_custom_opacity_and_partial_transmission():
    assert abs(mapper.map_material(_glass(), glass_opacity=0.3)["entry"]["color"]["a"] - 0.3) < 1e-6
    # half transmission -> halfway between opaque (1) and the glass opacity
    a = mapper.map_material(_glass(0.5), glass_opacity=0.1)["entry"]["color"]["a"]
    assert abs(a - 0.55) < 1e-6, a


def test_tint_kept():
    c = mapper.map_material(_glass())["entry"]["color"]
    assert (round(c["r"], 3), round(c["g"], 3), round(c["b"], 3)) == (0.2, 0.8, 0.3)


def test_opaque_untouched():
    n = _glass(0.0)
    n["shaderTypes"] = ["BSDF_PRINCIPLED"]
    e = mapper.map_material(n)["entry"]
    assert e["type"] == "Opaque" and e["color"]["a"] == 1


if __name__ == "__main__":
    fails = []
    for k, f in sorted(globals().items()):
        if k.startswith("test_"):
            try:
                f()
            except AssertionError as e:
                fails.append("%s: %s" % (k, e))
    if fails:
        print("GLASS FAIL\n  " + "\n  ".join(fails))
        sys.exit(1)
    print("GLASS OK")
