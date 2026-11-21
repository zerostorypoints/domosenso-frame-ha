"""Album folder layout and file names (no Home Assistant import)."""
import importlib
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pkg = types.ModuleType('album_layout_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg


class _Mod:
    def __init__(self, name):
        self._name = name

    def __getattr__(self, name):
        if name.startswith('__') or name == 'pytestmark':
            raise AttributeError(name)
        try:
            return getattr(importlib.import_module(self._name), name)
        except ModuleNotFoundError:
            pytest.fail(f'{self._name} module is missing')


layout = _Mod('album_layout_pkg.album_layout')
share = _Mod('album_layout_pkg.album_share')


def test_sanitize():
    assert layout.sanitize_name('Kraków / lato 🌞') == 'Kraków _ lato 🌞'
    assert layout.sanitize_name('..ukryty') == 'ukryty'
    assert layout.sanitize_name('   ') == 'album'


def test_sanitize_backslash_control_nfc_and_length():
    assert layout.sanitize_name('a\\b\x00c') == 'a_b_c'
    assert layout.sanitize_name('é') == 'é'
    assert len(layout.sanitize_name('x' * 200)) == 80


def test_initial_path_uses_local_month():
    start = int(datetime(2026, 3, 31, 23, 30, tzinfo=timezone.utc).timestamp())
    assert layout.initial_path('trip', start, ZoneInfo('Europe/Zurich')) == '2026/04/trip'


def _folder(root, marker):
    folder = root / '2026/04/trip'
    folder.mkdir(parents=True)
    if marker is not None:
        (folder / '.sync.json').write_text(marker)


def test_resolve_path_free(tmp_path):
    assert layout.resolve_path(tmp_path, '2026/04/trip', 'a1') == '2026/04/trip'


def test_resolve_path_skips_hand_folder(tmp_path):
    _folder(tmp_path, None)
    assert layout.resolve_path(tmp_path, '2026/04/trip', 'a1') == '2026/04/trip-2'


def test_resolve_path_keeps_own_folder(tmp_path):
    _folder(tmp_path, '{"album_id": "a1"}')
    assert layout.resolve_path(tmp_path, '2026/04/trip', 'a1') == '2026/04/trip'


def test_resolve_path_skips_foreign_and_unreadable_markers(tmp_path):
    _folder(tmp_path, '{"album_id": "other"}')
    (tmp_path / '2026/04/trip-2').mkdir()
    (tmp_path / '2026/04/trip-2/.sync.json').write_text('not json')
    assert layout.resolve_path(tmp_path, '2026/04/trip', 'a1') == '2026/04/trip-3'


def test_same_filename_different_ids():
    a = share.RemoteItem(5, 'IMG_0001.JPG', 1, 0, 'photo', 'k')
    b = share.RemoteItem(6, 'IMG_0001.JPG', 1, 0, 'photo', 'k')
    assert [layout.local_filename(a), layout.local_filename(b)] == ['IMG_0001_5.jpg', 'IMG_0001_6.jpg']


@pytest.mark.parametrize('raw', ['.. ..', '. .', '. .x', '..', ' . '])
def test_sanitize_never_dot_led_or_traversal(raw):
    name = layout.sanitize_name(raw)
    assert name and not name.startswith('.') and not name.startswith(' ')
    last = layout.initial_path(raw, 0, ZoneInfo('Europe/Zurich')).split('/')[-1]
    assert last not in ('.', '..') and not last.startswith('.')


def test_sanitize_dot_space_inputs_values():
    assert layout.sanitize_name('. .x') == 'x'
    assert layout.sanitize_name('.. ..') == 'album'


def test_sanitize_caps_utf8_bytes():
    name = layout.sanitize_name('🌞' * 80)
    assert name and len(name.encode('utf-8')) <= 200
