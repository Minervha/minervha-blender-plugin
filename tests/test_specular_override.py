"""Tests for the global specular override in mapper.map_material.

`specular_override` (None = off) forces every exported material's `specular` field to one fixed
value, clamped to [0, 1], ignoring the per-material Principled "Specular IOR Level", and records a
report note. The default-off path leaves the per-material value untouched (golden snapshot stays
green — this only guards the new branch).

Pure Python (no bpy). Run:  python tests/test_specular_override.py  (or pytest)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_material_exporter")
sys.path.insert(0, PKG)

import mapper  # noqa: E402


def _spec(norm, override=None):
    return mapper.map_material(norm, specular_override=override)["entry"]["specular"]


def test_off_keeps_per_material_specular():
    # None = off -> the per-material value flows through unchanged.
    assert _spec({"name": "M", "specular": 0.2}) == 0.2


def test_off_defaults_to_half_when_absent():
    assert _spec({"name": "M"}) == 0.5


def test_override_forces_value_ignoring_per_material():
    assert _spec({"name": "M", "specular": 0.2}, override=0.7) == 0.7


def test_override_clamps_above_one():
    assert _spec({"name": "M", "specular": 0.2}, override=2.0) == 1.0


def test_override_clamps_below_zero():
    assert _spec({"name": "M", "specular": 0.2}, override=-5.0) == 0.0


def test_override_zero_is_honored_not_treated_as_off():
    # 0.0 is a real override value (falsy) — the code keys on `is not None`, not truthiness.
    assert _spec({"name": "M", "specular": 0.5}, override=0.0) == 0.0


def test_override_records_report_note():
    rep = mapper.map_material({"name": "M", "specular": 0.2}, specular_override=0.3)["report"]
    assert any("specular overridden globally" in n for n in rep["notes"])


def test_off_records_no_specular_note():
    rep = mapper.map_material({"name": "M", "specular": 0.2})["report"]
    assert not any("specular overridden" in n for n in rep["notes"])


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            fails.append((t.__name__, str(e)))
    if fails:
        print(f"SPECULAR OVERRIDE FAILED — {len(fails)} failure(s):")
        for name, msg in fails:
            print(f"  {name}: {msg}")
        sys.exit(1)
    print(f"SPECULAR OVERRIDE OK — {len(tests)} tests passed")


if __name__ == "__main__":
    run()
