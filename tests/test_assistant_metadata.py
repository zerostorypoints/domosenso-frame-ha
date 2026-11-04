from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import importlib

import pytest
import yaml

from test_assistant_api import pkg

metadata = importlib.import_module(pkg.__name__ + '.assistant_metadata')
api = importlib.import_module(pkg.__name__ + '.assistant_metadata_api')


FIXTURES = Path(__file__).parent / 'fixtures/assistant-metadata'
NOW = datetime(2026, 11, 4, 9, tzinfo=timezone.utc)
ENDPOINT = 'demo-frame'
SUPPORTED = ['measurement.v1', 'selector.v1']
REVISION = '0123456789abcdef0123456789abcdef01234567'


@pytest.fixture
def env():
    bundle = metadata.validate_bundle({
        'schema': 2,
        'component': {'id': 'workbench', 'version': '1.0.0', 'commit': REVISION, 'dev': False},
        'definitions': yaml.safe_load((FIXTURES / 'workbench.yaml').read_text()),
        'bindings': yaml.safe_load((FIXTURES / 'bindings.yaml').read_text()),
    })
    readings = {}
    grants = []
    records = [{**bundle['component'], 'package': '', 'installed': True}]
    for index, subject in enumerate(bundle['bindings']['subjects']):
        eid = subject['fields']['temperature']['entity']
        readings[eid] = SimpleNamespace(state=str(20 + index), attributes={'unit_of_measurement': '°C'},
                                        last_reported=NOW - timedelta(seconds=index * 301))
        for definition in bundle['definitions']:
            grants.append({'capability': definition['id'], 'revision': 1, 'subject': subject['key'],
                           'entity': eid, 'registryId': f'fixture-{eid}'})
    endpoint = {'id': ENDPOINT, 'assistant': {'metadata_sources': grants}}
    permissions = set(readings)
    entries = {eid: f'fixture-{eid}' for eid in readings}
    readings['sensor.domosenso_components'] = SimpleNamespace(attributes={'components': records})
    state = {'assistant_metadata_artifacts': {'workbench': bundle}, 'assistant_metadata_read_enabled': True,
             'assistant_metadata_key': b'demo-only-fixture', 'assistant_state_get': readings.get,
             'assistant_registry_id': entries.get, 'assistant_source_verified': lambda eid: eid in entries}
    hass = SimpleNamespace(data={'domosenso_frame': state}, states=SimpleNamespace(get=readings.get),
                           config=SimpleNamespace(time_zone='UTC'))
    connection = SimpleNamespace(user=SimpleNamespace(id='demo-user', permissions=SimpleNamespace(
        check_entity=lambda eid, policy: eid in permissions)))
    connection.send_result = lambda message, value: setattr(connection, 'result', value)
    connection.send_error = lambda message, code, description: setattr(connection, 'error', code)
    grant = lambda h, c: ({'endpoint': ENDPOINT, 'device_id': 'demo-device'}, endpoint)
    return hass, connection, grant, endpoint, permissions, entries, bundle


def discover(env, **changes):
    hass, connection, grant, *_ = env
    connection.result = connection.error = None
    api.discover(hass, connection, {'id': 1, 'type': 'domosenso_frame/assistant_metadata', 'endpoint': ENDPOINT,
                                 'version': 2, 'locale': 'en', 'supported': SUPPORTED, **changes},
                 grant, 'read', now=lambda: NOW)
    return connection.result, connection.error


def query(env, catalog, definition, arguments):
    hass, connection, grant, *_ = env
    connection.result = connection.error = None
    api.query(hass, connection, {'id': 2, 'type': 'domosenso_frame/assistant_metadata_query', 'endpoint': ENDPOINT,
                                'version': 2, 'catalogRevision': catalog['catalogRevision'], 'revision': 1,
                                'capability': definition['id'], 'arguments': arguments},
              grant, 'read', now=lambda: NOW)
    return connection.result, connection.error


def test_discovery_and_query_use_metadata_for_two_unregistered_capabilities(env):
    catalog, error = discover(env)
    assert error is None
    by_id = {cap['id']: cap for cap in catalog['capabilities']}
    assert set(by_id) == {'workbench.station_temperature', 'workbench.probe_temperature'}
    area = by_id['workbench.station_temperature']
    assert 'sensor.workbench' not in str(catalog)
    answer, error = query(env, catalog, area, {'area': area['selectors'][0]['id']})
    assert error is None
    assert answer['coverage'] == {'eligible': 2, 'evaluated': 1, 'omitted': 0}
    assert answer['assessment'] == {'status': 'partial', 'summary': None}
    assert [fact['status'] for fact in answer['facts']] == ['current', 'stale']
    probe = by_id['workbench.probe_temperature']
    result, error = query(env, catalog, probe, {'subject': probe['selectors'][0]['id']})
    assert error is None and result['facts'][0]['value'] == 20


@pytest.mark.parametrize('change', ['permission', 'registry', 'capability', 'inventory', 'policy', 'label'])
def test_changed_scope_or_policy_invalidates_snapshot(env, change):
    hass, _, _, endpoint, permissions, entries, bundle = env
    catalog, _ = discover(env)
    cap = catalog['capabilities'][0]
    if change == 'permission':
        permissions.remove('sensor.workbench_north')
    elif change == 'registry':
        entries['sensor.workbench_north'] = 'replacement'
    elif change == 'capability':
        endpoint['assistant']['metadata_sources'].clear()
    elif change == 'inventory':
        hass.states.get('sensor.domosenso_components').attributes['components'].clear()
    elif change == 'policy':
        bundle['definitions'][0]['evaluator']['maxAgeSeconds'] = 60
    else:
        bundle['bindings']['subjects'][0]['label']['en'] = 'Renamed probe'
    assert query(env, catalog, cap, {}) == (None, 'frame_metadata_stale')


def test_invalid_selectors_or_unpaired_session_do_not_fall_back(env):
    catalog, _ = discover(env)
    cap = catalog['capabilities'][0]
    assert query(env, catalog, cap, {'area': 'not-a-current-id'}) == (None, 'frame_denied')
    assert query(env, catalog, cap, {'service': 'light.turn_on'}) == (None, 'frame_denied')
    hass, connection, _, *_ = env
    assert discover((hass, connection, lambda *_: (None, None))) == (None, 'frame_denied')


def test_refresh_detects_the_private_directory(tmp_path):
    fingerprint = importlib.import_module(pkg.__name__ + '.assistant_refresh').fingerprint

    before = fingerprint(tmp_path)
    directory = tmp_path / 'domosenso/assistant-metadata'
    directory.mkdir(parents=True)
    (directory / 'workbench.yaml').write_text('invalid: fixture\n')
    assert fingerprint(tmp_path) != before
