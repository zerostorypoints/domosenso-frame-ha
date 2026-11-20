"""Normalize photos to screen-sized JPEG (no Home Assistant import)."""
from __future__ import annotations

import io

from PIL import Image, ImageOps

ORIENTATION = 0x0112
_HEIF_BRANDS = (b'heic', b'heix', b'mif1', b'msf1')
_heif_ready = False


class HeifUnavailable(Exception):
    """HEIF input without a working pillow_heif."""


def is_heif(data: bytes) -> bool:
    return data[4:8] == b'ftyp' and data[8:12] in _HEIF_BRANDS


def _register_heif() -> None:
    global _heif_ready
    if _heif_ready:
        return
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception as err:
        raise HeifUnavailable(str(err)) from err
    _heif_ready = True


def normalize(data: bytes, max_edge: int, quality: int) -> bytes:
    if is_heif(data):
        _register_heif()
    with Image.open(io.BytesIO(data)) as src:
        exif = src.getexif()
        img = ImageOps.exif_transpose(src)
        img.load()
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img.thumbnail((max_edge, max_edge))
    kwargs = {}
    if len(exif):
        exif[ORIENTATION] = 1
        kwargs['exif'] = exif.tobytes()
    out = io.BytesIO()
    img.save(out, format='JPEG', quality=quality, **kwargs)
    return out.getvalue()
