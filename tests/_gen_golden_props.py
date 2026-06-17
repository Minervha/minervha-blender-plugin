"""Regenerate tests/golden/expected_props.json from prop_mapper.py.

prop_mapper.py is the single source of truth for the Blender->Wild Life prop
mapping. The golden is a regression snapshot of it (test_prop_mapper.py asserts the
mapper still matches the committed golden), so any accidental drift — or a
deliberate calibration change (OFFSET / transform convention, chunk-06) — is caught
and reviewed as a diff.

`build()` is reused by test_prop_mapper.test_golden_matches. Run from the repo root:

    python tests/_gen_golden_props.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "minervha_material_exporter"))

import prop_mapper  # noqa: E402

COLLECTION = "Scene"  # the export name used to namespace materials / Models paths in the fixtures


def _material_names(fixtures):
    """{blender_mat_name -> "<Collection>/<name>"} for every slot used (mirrors wlsave_export)."""
    names = {}
    for o in fixtures:
        for slot in (o.get("material_slots") or []):
            if slot and slot not in names:
                names[slot] = f"{COLLECTION}/{slot}"
    return names


def _mesh_path(o):
    key = o.get("mesh_key")
    return f"{COLLECTION}/Models/{key}.obj" if key else None


def build():
    fixtures = json.load(open(os.path.join(HERE, "fixtures", "normalized_objects.json"), encoding="utf-8"))
    names = _material_names(fixtures)
    return [prop_mapper.map_object(o, mesh_path=_mesh_path(o), material_names=names) for o in fixtures]


def main():
    out = build()
    golden_path = os.path.join(HERE, "golden", "expected_props.json")
    with open(golden_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote golden for {len(out)} props -> {golden_path}")


if __name__ == "__main__":
    main()
