"""Tests for bounded assistant date selection without HA."""
import importlib
import sys
import types
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pkg = types.ModuleType('calendar_test_package')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg
module = importlib.import_module(pkg.__name__ + '.assistant_data')


def test_local_calendar_window_crosses_dst_with_local_midnights():
    start, end = module.calendar_window({'period': 'next_7_days'}, date(2026, 10, 24), ZoneInfo('Europe/Zurich'))
    assert start.isoformat() == '2026-10-24T00:00:00+02:00'
    assert end.isoformat() == '2026-10-31T00:00:00+01:00'


def test_dates_and_arguments_are_bounded():
    tz = ZoneInfo('UTC')
    today = date(2026, 10, 31)
    for args in ({'date': '2026-11-07'}, {'date': '2026-10-30'}, {'period': 'today', 'date': '2026-10-31'}, {'service': 'turn_on'}, {'nextOnly': 1}):
        with pytest.raises(ValueError):
            module.calendar_window(args, today, tz)
    assert module.calendar_window({'date': '2026-11-06', 'nextOnly': True}, today, tz)[0].date() == date(2026, 11, 6)
