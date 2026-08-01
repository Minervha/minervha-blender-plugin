"""Tests for the texture-set exporter (minervha_texture_exporter).

Covers the pure policy layer — file naming/collisions, the resolution cap arithmetic, the
reference size for image-less maps, the derived AO channel inside a packed ORM/MRAO, and
the ORM buffer layout. No bpy: `images`/`bake` import it defensively, and every function
tested here is Blender-free.

Pure Python (numpy only). Run:  python -m pytest tests/test_texture_set.py -q
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "minervha_texture_exporter")
sys.path.insert(0, PKG)

import export  # noqa: E402
import images  # noqa: E402
import sources  # noqa: E402


class _Image:
    """Stand-in for a bpy Image — `infer_ao` only compares identity and reads `.name`."""

    def __init__(self, name):
        self.name = name


def _img_src(image, channel, packed=True):
    return {"kind": "image", "image": image, "channel": channel, "packed": packed}


# --- naming -----------------------------------------------------------------------------

def test_sanitize_name():
    assert export.sanitize_name("Béton Brut") == "Beton_Brut"
    assert export.sanitize_name("wood/oak #2") == "wood_oak_2"
    assert export.sanitize_name("") == "Material"
    assert export.sanitize_name("漢字") == "Material"


def test_unique_name_avoids_overwriting_a_clashing_set():
    taken = set()
    first = export.unique_name(export.sanitize_name("wood oak"), taken)
    taken.add(first)
    second = export.unique_name(export.sanitize_name("wood/oak"), taken)
    assert (first, second) == ("wood_oak", "wood_oak_2")


# --- resolution -------------------------------------------------------------------------

def test_fit_only_ever_shrinks():
    assert images.fit(4096, 2048, 1024) == (1024, 512)     # aspect kept
    assert images.fit(512, 512, 1024) == (512, 512)        # already under the cap
    assert images.fit(4096, 4096, None) == (4096, 4096)    # no cap
    assert images.fit(0, 0, 1024) == (0, 0)                # unreadable image


def test_reference_size():
    assert export.reference_size(1500, None) == 2048       # pow2 of the largest source
    assert export.reference_size(1500, 1024) == 1024       # capped
    assert export.reference_size(0, None) == 1024          # no texture at all
    assert export.reference_size(0, 512) == 512


# --- AO inference -----------------------------------------------------------------------

def test_infer_ao_orm_layout():
    """rough=G + metal=B on one image -> AO is the free channel, R."""
    orm = _Image("T_Crate_ORM")
    ao = export.infer_ao(_img_src(orm, 1), _img_src(orm, 2))
    assert ao == {"kind": "image", "image": orm, "channel": 0}


def test_infer_ao_mrao_layout():
    """metal=R + rough=G -> AO is B, derived rather than assumed."""
    mrao = _Image("T_Crate_MRAO")
    ao = export.infer_ao(_img_src(mrao, 1), _img_src(mrao, 0))
    assert ao["channel"] == 2


def test_infer_ao_from_roughness_alone_follows_orm_convention():
    packed = _Image("T_packed")
    ao = export.infer_ao(_img_src(packed, 1), {"kind": "const", "value": 0.0})
    assert ao["channel"] == 0


def test_infer_ao_refuses_when_only_red_is_known():
    """R is AO's own slot in both conventions — nothing can be inferred around it."""
    packed = _Image("T_packed")
    assert export.infer_ao(_img_src(packed, 0), {"kind": "const", "value": 0.0}) is None


def test_infer_ao_needs_the_packing_signal():
    """A grayscale roughness plugged straight from Color is a lone map, not an ORM."""
    grey = _Image("T_rough")
    assert export.infer_ao(_img_src(grey, None, packed=False),
                           {"kind": "const", "value": 0.0}) is None
    assert export.infer_ao({"kind": "const", "value": 0.5},
                           {"kind": "const", "value": 0.0}) is None


def test_infer_ao_ignores_a_second_unrelated_image():
    """Channels from a different image aren't evidence about the first one's layout."""
    packed, other = _Image("T_packed"), _Image("T_other")
    ao = export.infer_ao(_img_src(packed, 1), _img_src(other, 2))
    assert ao["image"] is packed and ao["channel"] == 0


# --- ORM packing ------------------------------------------------------------------------

def test_pack_orm_arrays_channel_order():
    ao = np.array([0.1, 0.2], dtype=np.float32)
    rough = np.array([0.3, 0.4], dtype=np.float32)
    metal = np.array([0.5, 0.6], dtype=np.float32)
    buf = images.pack_orm_arrays(ao, rough, metal)
    assert list(np.round(buf, 4)) == [0.1, 0.3, 0.5, 1.0, 0.2, 0.4, 0.6, 1.0]


def test_pack_orm_arrays_rejects_mismatched_lengths():
    try:
        images.pack_orm_arrays(np.zeros(4), np.zeros(4), np.zeros(3))
    except ValueError:
        return
    raise AssertionError("mismatched channel lengths must raise")


def test_flat_channel_is_a_constant_plane():
    arr = images.flat_channel(1.0, 4, 2)
    assert arr.shape == (8,) and float(arr.min()) == 1.0 and float(arr.max()) == 1.0


# --- channel mapping --------------------------------------------------------------------

def test_socket_channel_map_covers_separate_and_alpha():
    assert sources.SOCKET_CHANNEL["Red"] == 0
    assert sources.SOCKET_CHANNEL["Green"] == 1
    assert sources.SOCKET_CHANNEL["Blue"] == 2
    assert sources.SOCKET_CHANNEL["Alpha"] == 3


def test_suffixes_cover_both_orm_layouts():
    """The packed layout writes _D/_ORM/_N; the split layout swaps _ORM for _AO/_R/_M."""
    assert export.SUFFIXES == {"diffuse": "_D", "orm": "_ORM", "normal": "_N",
                               "ao": "_AO", "roughness": "_R", "metallic": "_M"}
    assert (export.ORM_PACKED, export.ORM_SPLIT) == ("packed", "split")


def test_channel_inputs_are_the_four_exported_channels():
    assert sources.CHANNEL_INPUTS == {
        "diffuse": "Base Color", "roughness": "Roughness",
        "metallic": "Metallic", "normal": "Normal"}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
