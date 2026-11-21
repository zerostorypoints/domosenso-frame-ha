"""Normalize photos to screen-sized JPEG (no Home Assistant import)."""
from __future__ import annotations

import io

from PIL import Image

ORIENTATION = 0x0112
_HEIF_BRANDS = (b'heic', b'heix', b'mif1', b'msf1')
_heif_ready = False
EXIF_IFD, GPS_IFD, MAKER_NOTE = 0x8769, 0x8825, 0x927C
MAX_EXIF = 65000  # JPEG APP1 holds 64 KB
_POINTERS = {EXIF_IFD, GPS_IFD, 0xA005}
_KEEP_IFD0 = {0x010F, 0x0110, 0x0132}  # Make, Model, DateTime
# DateTimeOriginal, LensModel, FocalLength, FocalLengthIn35mmFilm, FNumber, ExposureTime, ISO
_KEEP_EXIF = {0x9003, 0xA434, 0x920A, 0xA405, 0x829D, 0x829A, 0x8827}


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
        if src.format == 'JPEG':
            src.draft('RGB', (max_edge, max_edge))  # decode at a reduced scale: tablet memory
        img = src if src.mode == 'RGB' else src.convert('RGB')
        img.thumbnail((max_edge, max_edge))  # the box is square, so before the transpose is the same
        img = _oriented(img, exif.get(ORIENTATION))
        if img is src:
            img = src.copy()  # the source is closed when the block ends
    out = io.BytesIO()
    for candidate in _exif_candidates(exif):
        out.seek(0)
        out.truncate()
        try:
            img.save(out, format='JPEG', quality=quality, **candidate)
        except ValueError:  # EXIF does not fit in one APP1 segment
            continue
        return out.getvalue()
    raise AssertionError('unreachable: the EXIF-free candidate always saves')


_TRANSPOSE = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
              4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
              6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE, 8: Image.Transpose.ROTATE_90}


def _oriented(img: Image.Image, orientation) -> Image.Image:
    """Apply the EXIF orientation to the already reduced pixels, as ImageOps.exif_transpose does."""
    method = _TRANSPOSE.get(orientation)
    return img.transpose(method) if method is not None else img


def _exif_candidates(exif: Image.Exif):
    """Yield save kwargs from fullest to emptiest EXIF."""
    if not len(exif):
        yield {}
        return
    exif[ORIENTATION] = 1
    exif.get_ifd(GPS_IFD).clear()  # never publish location: files are served unauthenticated
    exif.pop(GPS_IFD, None)
    full = exif.tobytes()
    if len(full) <= MAX_EXIF:
        yield {'exif': full}
    yield {'exif': _rebuilt(exif, drop_maker_note=True).tobytes()}
    yield {'exif': _rebuilt(exif, keep=True).tobytes()}
    yield {}


def _rebuilt(exif: Image.Exif, drop_maker_note: bool = False, keep: bool = False) -> Image.Exif:
    """Copy tags into a fresh Exif (drops the thumbnail IFD1)."""
    new = Image.Exif()
    for tag, value in exif.items():
        if tag not in _POINTERS and (not keep or tag in _KEEP_IFD0):
            new[tag] = value
    sub = exif.get_ifd(EXIF_IFD)
    for tag, value in sub.items():
        if keep and tag not in _KEEP_EXIF:
            continue
        if drop_maker_note and tag == MAKER_NOTE:
            continue
        new.get_ifd(EXIF_IFD)[tag] = value
    new[ORIENTATION] = 1
    return new
