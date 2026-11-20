"""Share link parsing and host map for album sync (no Home Assistant import)."""
import importlib
import sys
import types
from pathlib import Path

import pytest

pkg = types.ModuleType('album_share_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg


class _Share:
    def __getattr__(self, name):
        if name.startswith('__') or name == 'pytestmark':
            raise AttributeError(name)
        try:
            return getattr(importlib.import_module('album_share_pkg.album_share'), name)
        except ModuleNotFoundError:
            pytest.fail('album_share module is missing')


share = _Share()


def test_parse_photo_prefix():
    link = share.parse_share_url('https://photos.example.org/photo/mo/sharing/blAbC123')
    assert link == share.ShareLink('https://photos.example.org', 'https://photos.example.org/photo', 'blAbC123')


def test_parse_ignores_trailing_slash_query_fragment():
    assert share.parse_share_url('http://nas.example:5000/photo/mo/sharing/Xy9/?x=1#/').passphrase == 'Xy9'


@pytest.mark.parametrize('bad', ['https://example.org/photo/', 'ftp://h/photo/mo/sharing/a', 'https://h/mo/sharing/a-b', ''])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        share.parse_share_url(bad)


def test_host_map_rewrites_matching_origin_only():
    link = share.parse_share_url('https://photos.example.org/photo/mo/sharing/a1')
    m = [{'from': 'https://other.example', 'to': 'http://10.0.0.2:5000'}, {'from': 'https://photos.example.org', 'to': 'http://10.0.0.9:5000'}]
    assert share.api_base(link, m) == 'http://10.0.0.9:5000/photo'
    assert share.api_base(link, []) == 'https://photos.example.org/photo'
