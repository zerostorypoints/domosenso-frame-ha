"""Album sync plan (no Home Assistant import)."""
import importlib
import sys
import types
from pathlib import Path

import pytest

pkg = types.ModuleType('album_plan_pkg')
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


plan = _Mod('album_plan_pkg.album_plan')
share = _Mod('album_plan_pkg.album_share')


def item(i, kind='photo'):
    return share.RemoteItem(i, f'p{i}.jpg', 1, 0, kind, 'k')


def test_version_flag_is_bool():
    assert plan.VERSION_TRACKS_CONTENT is True


def test_plan_new_and_removed():
    p = plan.plan_sync({1: 'a', 2: 'b'}, [item(1), item(3)], 2, False)
    assert [i.id for i in p.download] == [3] and p.delete == ['b'] and p.hold is False


def test_plan_skips_videos():
    p = plan.plan_sync({}, [item(1), item(2, 'video')], 2, False)
    assert [i.id for i in p.download] == [1]


def test_plan_counts_live_photos_as_photos():
    p = plan.plan_sync({}, [item(1, 'live'), item(2, 'video'), item(3)], 3, False)
    assert [i.id for i in p.download] == [1, 3]
    p = plan.plan_sync({1: 'a'}, [item(1, 'live')], 1, False)
    assert p.hold is False and p.delete == [] and p.download == []


def test_plan_holds_when_more_than_half_go():
    p = plan.plan_sync({1: 'a', 2: 'b', 3: 'c', 4: 'd'}, [item(1), item(9)], 2, False)
    assert p.hold is True and p.download == [] and p.delete == []


def test_plan_half_exactly_is_not_held():
    p = plan.plan_sync({1: 'a', 2: 'b', 3: 'c', 4: 'd'}, [item(1), item(2)], 2, False)
    assert p.hold is False and p.delete == ['c', 'd']


def test_plan_holds_when_remote_empties():
    p = plan.plan_sync({1: 'a', 2: 'b', 3: 'c'}, [], 0, False)
    assert p.hold is True and p.delete == []


def test_plan_confirm_releases_hold():
    p = plan.plan_sync({1: 'a', 2: 'b', 3: 'c'}, [], 0, True)
    assert p.hold is False and p.delete == ['a', 'b', 'c']


def test_plan_holds_when_only_videos_remain():
    p = plan.plan_sync({1: 'a'}, [item(2, 'video')], 1, False)
    assert p.hold is True


def test_plan_empty_local_never_holds():
    p = plan.plan_sync({}, [], 0, False)
    assert p.hold is False and p.download == [] and p.delete == []


def test_plan_incomplete_listing_raises():
    with pytest.raises(plan.IncompleteListing):
        plan.plan_sync({}, [item(1)], 2, False)
