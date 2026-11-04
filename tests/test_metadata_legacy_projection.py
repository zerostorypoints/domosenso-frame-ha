import importlib
from datetime import datetime, timezone

import pytest

from test_assistant_api import pkg

projection = importlib.import_module(pkg.__name__ + '.assistant_metadata_legacy')
contract = importlib.import_module(pkg.__name__ + '.assistant_contract')


def capability(identifier, pair, subjects=()):
    return {'id': identifier, 'revision': 1, 'queryType': pair[0], 'answerType': pair[1],
            'availability': 'ready', 'title': {'en': 'Example', 'pl': 'Przykład'},
            'description': {'en': 'Example description'},
            'examples': {'en': ['What is the example?']}, 'subjects': list(subjects)}


def test_every_known_v1_read_has_a_v2_descriptor_and_preserves_selectors():
    routes = contract.ROUTES | {'floorplan.explain': ('component-query.v1', 'component-description.v1')}
    for identifier, pair in routes.items():
        cap = capability(identifier, pair, [{'id': 'opaque-subject', 'label': 'Fictional source',
                                             'kind': 'room', 'roomId': 'opaque-area', 'roomLabel': 'Studio'}])
        descriptor = projection.describe(cap)
        assert descriptor['answer'] == {'kind': 'records.v1'}
        assert descriptor['id'] == identifier
        assert set(descriptor['arguments']) == {row['type'] for row in descriptor['selectors']}
        assert descriptor['anchors']['en']
    subject = {'id': 'opaque-subject', 'label': 'Fern', 'kind': 'plant'}
    cap = capability('plants.last_watered', routes['plants.last_watered'], [subject])
    descriptor = projection.describe(cap)
    assert projection.make_arguments(cap, descriptor, {'subject':'opaque-subject'}) == {'plantId':'opaque-subject'}
    with pytest.raises(ValueError):
        projection.make_arguments(cap, descriptor, {'subject':'unlisted'})
    with pytest.raises(ValueError):
        projection.make_arguments(cap, descriptor, {})


def test_projection_counts_plants_and_preserves_report_evidence_and_status():
    cap = capability('plants.watering_need', contract.ROUTES['plants.watering_need'])
    result = {'availability': 'partial', 'summary': {'status': 'needs_water', 'affected': 1},
              'coverage': {'eligible': 2, 'evaluated': 1, 'missing': 1, 'unit': 'plant'},
              'limitations': ['stale'], 'evidence': [{'id': 'e1', 'sourceRef': 'opaque-src',
              'reportedAt': '2026-11-04T11:00:00+00:00', 'freshness': 'current', 'freshnessBasis': 'source_report'}],
              'facts': [{'id': 'one', 'subjectId': 's1', 'label': 'Fern', 'value': 22, 'unit': '%',
                         'status': 'usable', 'wateringAssessment':'needs_water', 'evidenceRefs': ['e1']},
                        {'id': 'two', 'subjectId': 's1', 'label': 'Fern', 'value': 19, 'unit': '%',
                         'status': 'stale', 'evidenceRefs': []}]}
    output = projection.project(result, cap)
    assert output['coverage']['unit'] == 'plant'
    assert output['assessment']['status'] == 'partial'
    assert len(output['facts']) == 2
    assert output['facts'][0]['evidence'][0]['sourceRef'] == 'opaque-src'
    assert any(cell['kind'] == 'measurement' and cell['value'] == 22 for cell in output['facts'][0]['cells'])
    assert all(cell['kind'] != 'measurement' for cell in output['facts'][1]['cells'])


def test_weather_forecast_and_presence_keep_typed_details_from_legacy_fixtures():
    import json
    from pathlib import Path
    fixtures = Path(__file__).parent / 'fixtures'
    forecast = json.loads((fixtures / 'assistant-extended.json').read_text())['weather.forecast']
    result = projection.project(forecast['answer'], forecast['capability'])
    assert any(cell['kind']=='measurement' and cell['value']==15 for fact in result['facts'] for cell in fact['cells'])
    package = json.loads((fixtures / 'assistant-package.json').read_text())
    capabilities = {entry['id']: entry for entry in package['capabilities']['capabilities']}
    presence = projection.project(package['answers']['bermuda-presence.observations'],
                                  capabilities['bermuda-presence.observations'])
    assert any(cell['kind']=='boolean' and cell['value'] is True for cell in presence['facts'][0]['cells'])
    audio = projection.project(package['answers']['audio-sensor.level'], capabilities['audio-sensor.level'])
    assert any(cell['kind']=='measurement' and cell['unit']=='dBFS' for cell in audio['facts'][0]['cells'])


@pytest.mark.parametrize(('code', 'expected'), [
    ('moisture_low', 'Watering needed'),
    ('moisture_high', 'Excess moisture'),
    ({'metric': 'co2', 'status': 'too_wet'}, 'Excess moisture'),
])
def test_legacy_problem_shapes_keep_their_assessments(code, expected):
    cells = projection._cells({'problems': [code]})
    assert cells[0]['value']['en'] == expected
