import io
import sys

import pytest
from PIL import Image

import importlib
import types
from pathlib import Path
import sys

pkg = types.ModuleType('album_image_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg
album_image = importlib.import_module('album_image_pkg.album_image')

DATE, MODEL = 36867, 0x0110


def _jpeg(size, orientation=None, exif=True):
    img = Image.new('RGB', size, (200, 30, 30))
    ex = Image.Exif()
    if exif:
        ex[MODEL] = 'Cam One'
        ex.get_ifd(0x8769)[DATE] = '2026:05:01 10:00:00'
        if orientation:
            ex[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format='JPEG', **({'exif': ex.tobytes()} if exif else {}))
    return buf.getvalue()


def _open(data):
    img = Image.open(io.BytesIO(data))
    assert img.format == 'JPEG'
    return img


def test_downscales_and_keeps_exif():
    img = _open(album_image.normalize(_jpeg((4000, 3000)), 2560, 85))
    assert img.size == (2560, 1920)
    exif = img.getexif()
    assert exif[MODEL] == 'Cam One'
    assert exif.get_ifd(0x8769)[DATE] == '2026:05:01 10:00:00'


def test_never_upscales():
    assert _open(album_image.normalize(_jpeg((800, 600)), 2560, 85)).size == (800, 600)


def test_orientation_applied():
    img = _open(album_image.normalize(_jpeg((300, 200), orientation=6), 2560, 85))
    assert img.size == (200, 300)
    assert img.getexif()[0x0112] == 1


def test_no_exif_stays_without_exif():
    assert len(_open(album_image.normalize(_jpeg((100, 80), exif=False), 2560, 85)).getexif()) == 0


def test_converts_non_rgb_modes():
    buf = io.BytesIO()
    Image.new('RGBA', (50, 40), (1, 2, 3, 128)).save(buf, format='PNG')
    assert _open(album_image.normalize(buf.getvalue(), 2560, 85)).mode == 'RGB'


def _heic():
    import pillow_heif
    pillow_heif.register_heif_opener()
    ex = Image.Exif()
    ex[MODEL] = 'Phone X'
    buf = io.BytesIO()
    Image.new('RGB', (64, 48), (10, 120, 10)).save(buf, format='HEIF', exif=ex.tobytes())
    return buf.getvalue()


def test_is_heif():
    assert album_image.is_heif(_heic())
    assert not album_image.is_heif(_jpeg((10, 10)))
    assert not album_image.is_heif(b'')


def test_heic_converted_with_exif():
    img = _open(album_image.normalize(_heic(), 2560, 85))
    assert img.size == (64, 48)
    assert img.getexif()[MODEL] == 'Phone X'


def test_heic_without_plugin(monkeypatch):
    data = _heic()
    monkeypatch.setitem(sys.modules, 'pillow_heif', None)
    monkeypatch.setattr(album_image, '_heif_ready', False)
    with pytest.raises(album_image.HeifUnavailable):
        album_image.normalize(data, 2560, 85)


def _big_exif(size_kb=70):
    ex = Image.Exif()
    ex[MODEL] = 'Cam One'
    ex[0x0112] = 6
    sub = ex.get_ifd(0x8769)
    sub[DATE] = '2026:05:01 10:00:00'
    sub[0xA434] = 'Lens 50'
    sub[0x927C] = b'\x00' * (size_kb * 1024)
    return ex.tobytes()


def test_oversized_maker_note_is_dropped_not_fatal():
    exif_bytes = _big_exif()
    assert len(exif_bytes) > 65535
    src = io.BytesIO()
    Image.new('RGB', (300, 200)).save(src, format='PNG', exif=exif_bytes)
    exif = _open(album_image.normalize(src.getvalue(), 2560, 85)).getexif()
    sub = exif.get_ifd(0x8769)
    assert exif[MODEL] == 'Cam One'
    assert exif[0x0112] == 1
    assert sub[DATE] == '2026:05:01 10:00:00'
    assert sub[0xA434] == 'Lens 50'
    assert 0x927C not in sub


def test_oversized_exif_without_maker_note_falls_back_to_key_tags():
    ex = Image.Exif()
    ex[MODEL] = 'Cam One'
    ex[0x010E] = 'x' * 70000  # ImageDescription
    ex.get_ifd(0x8769)[DATE] = '2026:05:01 10:00:00'
    src = io.BytesIO()
    Image.new('RGB', (300, 200)).save(src, format='PNG', exif=ex.tobytes())
    exif = _open(album_image.normalize(src.getvalue(), 2560, 85)).getexif()
    assert exif[MODEL] == 'Cam One'
    assert exif.get_ifd(0x8769)[DATE] == '2026:05:01 10:00:00'
    assert 0x010E not in exif


def _gps_source(extra=None):
    ex = Image.Exif()
    ex[MODEL] = 'Cam One'
    ex.get_ifd(0x8769)[DATE] = '2026:05:01 10:00:00'
    ex.get_ifd(0x8825)[1] = 'N'
    ex.get_ifd(0x8825)[2] = (47.0, 22.0, 0.0)
    for tag, value in (extra or {}).items():
        ex[tag] = value
    src = io.BytesIO()
    Image.new('RGB', (300, 200)).save(src, format='PNG', exif=ex.tobytes())
    return src.getvalue()


@pytest.mark.parametrize('extra', [None, {0x010E: 'x' * 70000}])
def test_gps_is_never_written(extra):
    src = _gps_source(extra)
    assert 0x8825 in Image.open(io.BytesIO(src)).getexif()
    exif = _open(album_image.normalize(src, 2560, 85)).getexif()
    assert 0x8825 not in exif
    assert not exif.get_ifd(0x8825)
    assert exif.get_ifd(0x8769)[DATE] == '2026:05:01 10:00:00'


def test_large_jpeg_keeps_size_and_orientation():
    img = Image.new('RGB', (6000, 4000), (220, 20, 20))
    img.paste((20, 20, 220), (3000, 0, 6000, 4000))  # left half red, right half blue
    ex = Image.Exif()
    ex[0x0112] = 6  # display rotated 90 degrees clockwise: the left half ends on top
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90, exif=ex.tobytes())
    out = _open(album_image.normalize(buf.getvalue(), 2560, 85))
    assert out.size == (1707, 2560)
    assert out.getexif()[0x0112] == 1
    top, bottom = out.getpixel((853, 100)), out.getpixel((853, 2460))
    assert top[0] > 150 and top[2] < 80 and bottom[2] > 150 and bottom[0] < 80
