"""images.py — pixel plumbing for the map set (bpy + numpy).

Three jobs: re-export a source image as PNG (optionally downscaled), read one channel of
a source image as a numpy array resampled to a target size, and write the composed
`_ORM` / flat maps.

**Colorspace contract** — measured on Blender 5.1.2, not assumed (see
`tests/verify_texture_set_live.py`, which asserts it):
  * `Image.pixels` is RAW STORED DATA in both directions. Reading a map returns the values
    the file holds whatever it is tagged, and pixels written by hand are saved verbatim —
    `colorspace_settings` applies no transfer function on read or on `save()`. So data
    channels (roughness/metallic/AO) pass through the ORM byte-exact with no conversion,
    and a mistagged source cannot corrupt them.
  * The corollary bites the other way for COLOUR: a Base Color socket holds a *linear*
    value, so a flat diffuse map must be sRGB-encoded HERE (`_linear_to_srgb`) before
    being written — Blender will not do it. (Cycles *bakes* are the exception: the render
    pipeline encodes into the target image's colorspace as it fills the buffer, which is
    why `bake.py` tags its target before baking.)
  * A written map still carries the matching tag so Blender re-reads it correctly.

Every operation works on a throwaway copy — the user's images are never mutated.

Importable without bpy (numpy-only helpers are unit-tested outside Blender).
"""

import os

import numpy as np

try:
    import bpy
except ImportError:                 # importable for the pure tests / syntax check
    bpy = None


def fit(width, height, cap):
    """(w, h) downscaled to fit `cap` on the longest side, aspect kept. `cap` falsy, or an
    image already within it, returns the size unchanged — the cap only ever shrinks."""
    width, height = int(width), int(height)
    if not cap or width <= 0 or height <= 0 or max(width, height) <= cap:
        return width, height
    scale = cap / float(max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def pack_orm_arrays(ao, roughness, metallic):
    """Flat RGBA float buffer (len = 4 × pixels) with R=AO, G=Roughness, B=Metallic, A=1.

    Pure numpy — the ORM layout itself, unit-tested without Blender. The three inputs must
    already be the same length (the caller resamples them to one size first)."""
    n = len(ao)
    if len(roughness) != n or len(metallic) != n:
        raise ValueError("ORM channels must have the same length (%d/%d/%d)"
                         % (n, len(roughness), len(metallic)))
    buf = np.empty(n * 4, dtype=np.float32)
    buf[0::4] = ao
    buf[1::4] = roughness
    buf[2::4] = metallic
    buf[3::4] = 1.0
    return buf


def _ensure_loaded(img):
    """Force a lazily-loaded file image's pixel buffer to materialize IN ITS OWN FORMAT.

    A file image that was never displayed (typical for a `.tga`/`.exr` map) has no buffer
    until something reads it; touching `file_format` first would make `save()` decode the
    source with the wrong reader. Reading one pixel loads it correctly. Packed/generated
    images already have data, so this is a no-op for them."""
    if not img.has_data:
        img.pixels[0]


def dimensions(image):
    """(w, h) of a source image, or (0, 0) if it has no resolvable buffer (missing file)."""
    try:
        return int(image.size[0]), int(image.size[1])
    except (AttributeError, IndexError, TypeError):
        return 0, 0


def _working_copy(image):
    """A throwaway copy that actually carries the source pixels.

    `Image.copy()` is the cheap, faithful path for FILE and packed images — but a
    GENERATED image (Blender's "New Image": a painted or in-memory map that was never
    saved to disk) re-generates from its generated settings when copied, so the copy comes
    back flat black and every channel read off it would be zero. Those are rebuilt by hand
    from the source buffer instead. Measured on 5.1.2; `scale()` then preserves the buffer
    in both cases."""
    if getattr(image, "source", "") != "GENERATED":
        return image.copy()
    width, height = dimensions(image)
    tmp = bpy.data.images.new("_mvtex_work", width, height, alpha=True)
    tmp.colorspace_settings.name = image.colorspace_settings.name
    buf = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(buf)
    tmp.pixels.foreach_set(buf)
    return tmp


def _linear_to_srgb(value):
    """sRGB transfer function — Blender stores socket colours linear but writes pixels
    verbatim, so a flat colour map has to be encoded here or it exports far too dark."""
    value = min(max(float(value), 0.0), 1.0)
    return 12.92 * value if value <= 0.0031308 else 1.055 * (value ** (1 / 2.4)) - 0.055


def _save(img, path):
    """Write `img` as PNG to `path` (creating the folder); True if a non-empty file landed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.file_format = "PNG"
    img.filepath_raw = path
    img.save()
    return os.path.isfile(path) and os.path.getsize(path) > 0


def write_direct(image, path, cap):
    """Re-export a source image to `path` as PNG, downscaled to `cap` if it exceeds it.

    The copy keeps the source's colorspace tag, so the re-encoded file is read back the
    same way the original was. Returns (w, h) written, or None on failure (missing file /
    unreadable buffer)."""
    tmp = _working_copy(image)
    try:
        _ensure_loaded(tmp)
        width, height = dimensions(tmp)
        if width <= 0 or height <= 0:
            return None
        target = fit(width, height, cap)
        if target != (width, height):
            tmp.scale(*target)
        return target if _save(tmp, path) else None
    except Exception:
        return None
    finally:
        try:
            bpy.data.images.remove(tmp)
        except Exception:
            pass


def read_channel(image, channel, width, height):
    """One channel of `image` as a flat float array of `width`×`height` values.

    `channel` is 0=R 1=G 2=B 3=A; None means "the whole colour", of which a scalar map
    takes red. The copy is resampled to the requested size so mismatched ORM contributors
    compose. No colorspace juggling is needed — `pixels` is raw stored data (module doc).
    Returns None if the image has no readable buffer."""
    tmp = _working_copy(image)
    try:
        _ensure_loaded(tmp)
        if dimensions(tmp) == (0, 0):
            return None
        if dimensions(tmp) != (width, height):
            tmp.scale(width, height)
        buf = np.empty(width * height * 4, dtype=np.float32)
        tmp.pixels.foreach_get(buf)
        return buf[(0 if channel is None else int(channel))::4]
    except Exception:
        return None
    finally:
        try:
            bpy.data.images.remove(tmp)
        except Exception:
            pass


def _new_image(name, width, height, colorspace):
    """A throwaway RGB image tagged `colorspace` (the save-time encoding)."""
    img = bpy.data.images.new(name, width, height, alpha=False)
    img.colorspace_settings.name = colorspace
    return img


def write_buffer(buf, width, height, path, colorspace):
    """Write a flat RGBA float buffer as a PNG tagged `colorspace`. True on success.

    The tag is metadata for whoever reads the file back; the values themselves are stored
    verbatim, so the caller hands over pixels already in their final encoding."""
    img = _new_image("_mvtex_" + os.path.basename(path), width, height, colorspace)
    try:
        img.pixels.foreach_set(np.asarray(buf, dtype=np.float32))
        return _save(img, path)
    except Exception:
        return False
    finally:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass


def write_orm(ao, roughness, metallic, width, height, path):
    """Compose and write `<Material>_ORM.png` — a Non-Color (raw) map, R=AO G=Rough B=Metal."""
    return write_buffer(pack_orm_arrays(ao, roughness, metallic), width, height,
                        path, "Non-Color")


def write_flat_color(rgba, width, height, path):
    """Write a solid Base Color map from a LINEAR socket colour.

    The value is sRGB-encoded here (Blender saves hand-written pixels verbatim), so a
    linear 0.5 lands at ~0.735 — byte 188, the colour the user sees in the viewport — and
    the file is tagged sRGB to be read back the same way."""
    buf = np.empty(width * height * 4, dtype=np.float32)
    for i in range(3):
        buf[i::4] = _linear_to_srgb(rgba[i])
    buf[3::4] = 1.0
    return write_buffer(buf, width, height, path, "sRGB")


def flat_channel(value, width, height):
    """A constant channel as a flat float array — a scalar Roughness/Metallic in the ORM."""
    return np.full(width * height, float(value), dtype=np.float32)


def channel_of(img, index):
    """One channel of an image WE own (a bake target): read as-is — no copy, no resample
    and no colorspace change, since it is already ours, already the right size, and
    already tagged for its channel."""
    buf = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf[int(index)::4]


def discard(img):
    """Remove a throwaway image datablock. Best-effort — cleanup must never raise."""
    try:
        bpy.data.images.remove(img, do_unlink=True)
    except Exception:
        pass
