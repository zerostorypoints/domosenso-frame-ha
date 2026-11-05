"""Pinned canonical framework artifacts through the real scoped v2 handlers."""
import copy
import hashlib
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_assistant_api import pkg

metadata = importlib.import_module(pkg.__name__ + '.assistant_metadata')
api = importlib.import_module(pkg.__name__ + '.assistant_metadata_api')
ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/assistant-metadata-canonical'
NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def raw(both=True):
    return json.loads((FIXTURES / ('temperature-humidity.json' if both else 'temperature-only.json')).read_text())


def environment(both=True):
    bundle = metadata.validate_bundle(raw(both))
    readings = {f'sensor.workbench_{metric}': SimpleNamespace(
        state=str(value), attributes={'unit_of_measurement': unit}, last_reported=NOW-timedelta(seconds=5))
        for metric, value, unit in [('temperature', 21, '°C'), ('humidity', 48, '%')]}
    entries = {eid: 'registry-' + eid for eid in readings}
    records = [{**bundle['component'], 'package': '', 'installed': True}]
    readings['sensor.domosenso_components'] = SimpleNamespace(attributes={'components': records})
    candidates = metadata.source_candidates({'workbench': bundle}, entries.get)
    endpoint = {'id': 'frame', 'assistant': {'metadata_sources': [x['grant'] for x in candidates]}}
    permissions = set(entries)
    state = {'assistant_metadata_artifacts': {'workbench': bundle}, 'assistant_metadata_read_enabled': True,
             'assistant_metadata_key': b'fictional-canonical-test', 'assistant_state_get': readings.get,
             'assistant_registry_id': entries.get, 'assistant_area_id': lambda _: 'office',
             'assistant_source_verified': lambda eid: eid in entries}
    hass = SimpleNamespace(data={'domosenso_frame': state}, states=SimpleNamespace(get=readings.get),
                           config=SimpleNamespace(time_zone='UTC'))
    connection = SimpleNamespace(user=SimpleNamespace(id='fictional-user', permissions=SimpleNamespace(
        check_entity=lambda eid, _: eid in permissions)))
    connection.send_result = lambda _, result: setattr(connection, 'result', result)
    connection.send_error = lambda _, code, message: setattr(connection, 'error', code)
    grant = lambda *_: ({'endpoint': 'frame', 'device_id': 'fictional-device'}, endpoint)
    return hass, connection, grant, endpoint, permissions, entries, readings


def discover(env):
    hass, connection, grant, *_ = env
    connection.result = connection.error = None
    api.discover(hass, connection, {'id': 1, 'endpoint': 'frame', 'version': 2,
        'supported': ['measurement.v1', 'selector.v1'], 'locale': 'en'}, grant, 'read', lambda: NOW)
    return connection.result, connection.error


def query(env, catalog, cap, **args):
    hass, connection, grant, *_ = env
    connection.result = connection.error = None
    api.query(hass, connection, {'id': 2, 'endpoint': 'frame', 'version': 2,
        'catalogRevision': catalog['catalogRevision'], 'capability': cap['id'], 'revision': cap['revision'],
        'arguments': args}, grant, 'read', lambda: NOW)
    return connection.result, connection.error


def test_pinned_schema_and_fixture_bytes_match_framework():
    provenance = json.loads((FIXTURES / 'provenance.json').read_text())
    for relative, digest in provenance['sha256'].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest, relative


def test_two_metadata_only_capabilities_need_no_engine_registration_and_grants_stay_explicit():
    env = environment(False)
    first, error = discover(env); assert error is None
    assert [x['id'] for x in first['capabilities']] == ['workbench.station_temperature']
    cap = first['capabilities'][0]
    answer, error = query(env, first, cap)
    assert error is None and answer['facts'][0]['value'] == 21
    assert 'sensor.workbench' not in json.dumps(first)
    env[0].data['domosenso_frame']['assistant_metadata_artifacts']['workbench'] = metadata.validate_bundle(raw())
    second, error = discover(env); assert error is None
    assert [x['id'] for x in second['capabilities']] == ['workbench.station_temperature']
    bundle = env[0].data['domosenso_frame']['assistant_metadata_artifacts']['workbench']
    env[3]['assistant']['metadata_sources'] = [x['grant'] for x in metadata.source_candidates({'workbench': bundle}, env[5].get)]
    third, error = discover(env); assert error is None
    assert {x['id'] for x in third['capabilities']} == {'workbench.station_temperature', 'workbench.station_humidity'}
    for descriptor in third['capabilities']:
        result, error = query(env, third, descriptor)
        assert error is None
        assert result['facts'][0]['value'] == (48 if descriptor['answer']['unit'] == '%' else 21)
        assert {x['type'] for x in descriptor['selectors']} == {'area', 'subject'}
        assert any(x['aliases'].get('en') == ['study'] for x in descriptor['selectors'])


@pytest.mark.parametrize('change', ['permission', 'registry', 'area', 'grant'])
def test_canonical_read_is_invalidated_when_authorization_or_identity_changes(change):
    env = environment(); before, error = discover(env); assert not error
    cap = next(x for x in before['capabilities'] if x['id'].endswith('temperature'))
    if change == 'permission': env[4].remove('sensor.workbench_temperature')
    elif change == 'registry': env[5]['sensor.workbench_temperature'] = 'replaced'
    elif change == 'area': env[0].data['domosenso_frame']['assistant_area_id'] = lambda _: 'other'
    else: env[3]['assistant']['metadata_sources'].clear()
    assert query(env, before, cap) == (None, 'frame_metadata_stale')


@pytest.mark.parametrize('mutate', [
    lambda b: b['definitions']['capabilities'][0].update(id='other.temperature'),
    lambda b: b['definitions']['capabilities'][0]['client'].update(script='execute'),
    lambda b: b['definitions']['capabilities'][0]['policy'].update(evaluator='execute.v1'),
    lambda b: b['bindings']['subjects'][0].update(aliases={'en': ['bad\u202etext']}),
    lambda b: b['bindings']['subjects'][0]['fields'].update(hidden={'entity': 'sensor.hidden'}),
])
def test_canonical_artifact_cannot_supply_unknown_code_roles_or_owners(mutate):
    bundle = raw(); mutate(bundle)
    with pytest.raises(ValueError): metadata.validate_bundle(bundle)


def test_canonical_wrong_unit_or_restored_value_never_becomes_current():
    env = environment(); catalog, error = discover(env); assert not error
    cap = next(x for x in catalog['capabilities'] if x['id'].endswith('humidity'))
    env[6]['sensor.workbench_humidity'].attributes['unit_of_measurement'] = '°C'
    answer, error = query(env, catalog, cap)
    assert not error and answer['facts'][0]['value'] is None
    assert answer['facts'][0]['status'] == 'invalid_unit'
    env[6]['sensor.workbench_humidity'].attributes.update(unit_of_measurement='%', restored=True)
    answer, error = query(env, catalog, cap)
    assert not error and answer['facts'][0]['value'] is None


def test_specialized_projection_uses_canonical_words_without_expanding_allowed_scope():
    projection = importlib.import_module(pkg.__name__ + '.assistant_metadata_legacy')
    data = raw(False)
    cap = data['definitions']['capabilities'][0]
    cap.update(id='workbench.explain', query_type='component-query.v1', answer_type='component-description.v1',
               required_facts=[], policy={'evaluator': 'workbench.explain'},
               freshness={'basis': 'installed_metadata', 'max_age_seconds': None})
    cap['client']['presentation'] = {'summary': [], 'details': []}
    cap['client']['selectors'] = []
    data['bindings']['subjects'] = []
    bundle = metadata.validate_bundle(data)
    legacy_cap = {'id': 'workbench.explain', 'revision': 1, 'queryType': 'component-query.v1',
                  'answerType': 'component-description.v1', 'availability': 'ready',
                  'title': {'en': 'Legacy title'}, 'description': {'en': 'Legacy description'},
                  'examples': {'en': ['Explain the component']}, 'subjects': []}
    descriptor = projection.describe(legacy_cap)
    decorated = metadata.decorate_projection(legacy_cap, descriptor, {'workbench': bundle})
    assert decorated['anchors']['en'] == ['temperature', 'warm']
    assert decorated['title']['en'] == 'Station temperature'
    assert decorated['selectors'] == [] and decorated['arguments'] == []


def test_per_source_freshness_can_tighten_but_never_extend_capability_limit():
    env = environment(False)
    for override, age, status in [(60, 61, 'stale'), (1800, 901, 'stale'), (60, 5, 'current')]:
        bundle = raw(False)
        bundle['bindings']['subjects'][0]['fields']['temperature']['maxAgeSeconds'] = override
        env[0].data['domosenso_frame']['assistant_metadata_artifacts']['workbench'] = metadata.validate_bundle(bundle)
        env[6]['sensor.workbench_temperature'].last_reported = NOW-timedelta(seconds=age)
        catalog, error = discover(env); assert not error
        assert len(catalog['capabilities']) == 1
        answer, error = query(env, catalog, catalog['capabilities'][0])
        assert not error and answer['facts'][0]['status'] == status
