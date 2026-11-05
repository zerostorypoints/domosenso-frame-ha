"""Metadata owns language; the native client must not acquire capability ID routes."""
import importlib
from test_assistant_api import pkg

projection = importlib.import_module(pkg.__name__ + '.assistant_metadata_legacy')


def test_status_and_temporal_phrases_distinguish_current_need_from_detected_watering():
    status = projection.ANCHORS['plants.status']
    assert 'status' in status[0].split('|') and 'status' in status[1].split('|')
    assert 'jak się teraz ma' in status[1].split('|')
    last = projection.ANCHORS['plants.last_watered']
    assert 'last receive water' in last[0].split('|')
    assert 'podlania' not in last[1].split('|')
    assert 'podlewanie' not in last[1].split('|')
    assert 'ostatnim razem' in last[1].split('|')
    estimate = projection.ANCHORS['plants.water_in']
    assert 'days until' in estimate[0].split('|')
    assert 'needs watering again' in estimate[0].split('|')
    assert 'jak' not in status[1].split('|')


def test_every_anchor_locale_stays_within_native_wire_limit():
    for identifier, languages in projection.ANCHORS.items():
        for phrases in languages:
            assert 1 <= len(phrases.split('|')) <= 16, identifier


def test_projected_help_does_not_advertise_another_capabilitys_generic_example():
    from test_metadata_legacy_projection import capability
    from frame_api_package.assistant_contract import ROUTES
    cap = capability('climate-air.air_readings', ROUTES['climate-air.air_readings'])
    cap['examples'] = {'en': ['What is the air quality?'], 'pl': ['Jaka jest jakość powietrza?']}
    value = projection.describe(cap)
    assert value['examples']['en'] == ['air readings']
    assert value['examples']['pl'] == ['pomiary powietrza']
