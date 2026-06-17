"""Regenerate tests/golden/expected.json from mapper.py.

mapper.py is the single source of truth for the Blender->Wild Life mapping (the
Studio's mapMaterial.js it was once ported from has been removed). The golden is
therefore a regression snapshot of this mapper, not a cross-language parity
reference. test_mapper.py asserts the mapper still matches the committed golden,
so any accidental change to the mapping is caught.

Run from the plugin repo root:  python tests/_gen_golden.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import mapper  # noqa: E402


def main():
    fixtures = json.load(open(os.path.join(HERE, "fixtures", "normalized_materials.json"), encoding="utf-8"))
    out = []
    for norm in fixtures:
        r = mapper.map_material(norm)
        out.append({"entry": r["entry"], "textures": r["textures"], "report": r["report"]} if r else None)
    golden_path = os.path.join(HERE, "golden", "expected.json")
    with open(golden_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote golden for {len(fixtures)} fixtures -> {golden_path}")


if __name__ == "__main__":
    main()
