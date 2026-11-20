import os

import importlib
import types
from pathlib import Path
import sys

pkg = types.ModuleType('album_files_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg
album_files = importlib.import_module('album_files_pkg.album_files')

KEYS = {'album_id', 'remote_album_id', 'source_host', 'version', 'items', 'last_sync'}


def test_write_photo_atomic(tmp_path):
    album_files.write_photo(tmp_path, 'a_1.jpg', b'data', 1_700_000_000)
    assert [p.name for p in tmp_path.iterdir()] == ['a_1.jpg']
    assert (tmp_path / 'a_1.jpg').read_bytes() == b'data'
    assert int(os.stat(tmp_path / 'a_1.jpg').st_mtime) == 1_700_000_000


def test_remove_photo_missing_is_fine(tmp_path):
    album_files.write_photo(tmp_path, 'a.jpg', b'x', 1)
    album_files.remove_photo(tmp_path, 'a.jpg')
    album_files.remove_photo(tmp_path, 'a.jpg')
    assert list(tmp_path.iterdir()) == []


def test_marker_roundtrip_has_no_secret_keys(tmp_path):
    marker = dict.fromkeys(KEYS, 'x') | {'items': {'1': 'a_1.jpg'}, 'version': 3}
    album_files.write_marker(tmp_path, marker)
    assert album_files.read_marker(tmp_path) == marker
    assert set(album_files.read_marker(tmp_path)) == KEYS
    assert [p.name for p in tmp_path.iterdir()] == ['.sync.json']


def test_read_marker_missing_unreadable_or_non_dict(tmp_path):
    assert album_files.read_marker(tmp_path) is None
    (tmp_path / '.sync.json').write_text('{bad')
    assert album_files.read_marker(tmp_path) is None
    (tmp_path / '.sync.json').write_text('[1]')
    assert album_files.read_marker(tmp_path) is None


def test_free_bytes(tmp_path):
    assert album_files.free_bytes(tmp_path) > 0
