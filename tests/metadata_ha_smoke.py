"""Fictional Frame v2 reads over a paired WebSocket on pinned HA 2026.9.2."""

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from aiohttp import ClientSession
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from homeassistant import bootstrap, loader
from homeassistant.const import __version__
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
import yaml


ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / 'tests/fixtures/assistant-metadata'
SUPPORTED = ['measurement.v1', 'selector.v1']


async def scenario(root):
    shutil.copytree(ROOT / 'custom_components', root / 'custom_components')
    sidecar = root / 'domosenso/assistant-metadata'
    sidecar.mkdir(parents=True)
    bundle = {
        'schema': 2, 'component': {'id': 'workbench', 'version': '1.0.0',
                                    'commit': '0123456789abcdef0123456789abcdef01234567', 'dev': False},
        'definitions': yaml.safe_load((INPUTS / 'workbench.yaml').read_text()),
        'bindings': yaml.safe_load((INPUTS / 'bindings.yaml').read_text()),
    }
    (sidecar / 'workbench.yaml').write_text(json.dumps(bundle))
    hass = HomeAssistant(str(root))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    config = {
        'homeassistant': {'name': 'Fictional metadata lab', 'time_zone': 'Europe/Zurich'},
        'http': {'server_host': '127.0.0.1', 'server_port': 18187},
        'domosenso_frame': {'endpoints': [{'id': 'frame', 'name': 'Fictional frame',
                                           'dashboard': '/dashboard-fictional/main', 'display': {}}]},
    }
    try:
        assert await bootstrap.async_from_config_dict(config, hass)
        await hass.async_start()
        await hass.async_block_till_done()
        state = hass.data['domosenso_frame']
        assert 'workbench' in state['assistant_metadata_artifacts']
        hass.states.async_set('sensor.domosenso_components', '1', {'components': [
            {**bundle['component'], 'package': '', 'installed': True}
        ]})
        registry = entity_registry.async_get(hass)
        grants = []
        for index, subject in enumerate(bundle['bindings']['subjects']):
            entity = subject['fields']['temperature']['entity']
            object_id = entity.split('.')[1]
            entry = registry.async_get_or_create('sensor', 'esphome', object_id, suggested_object_id=object_id)
            assert entry.entity_id == entity
            hass.states.async_set(entity, 20 + index, {'unit_of_measurement': '°C', 'restored': index == 1})
            for cap in bundle['definitions']:
                grants.append({'capability': cap['id'], 'revision': cap['revision'], 'subject': subject['key'],
                               'entity': entity, 'role': cap['source']['role'], 'registryId': entry.id})
        await hass.async_block_till_done()
        state['endpoints']['frame']['assistant'] = {'metadata_sources': grants}
        user = await hass.auth.async_create_user('Fictional tablet', group_ids=['system-users'])
        refresh = await hass.auth.async_create_refresh_token(user, client_id='http://localhost/')
        async with ClientSession() as client, client.ws_connect('http://127.0.0.1:18187/api/websocket') as socket:
            assert (await socket.receive_json())['type'] == 'auth_required'
            await socket.send_json({'type': 'auth', 'access_token': hass.auth.async_create_access_token(refresh)})
            assert (await socket.receive_json())['type'] == 'auth_ok'
            sequence = 0

            async def request(kind, error=None, **fields):
                nonlocal sequence
                sequence += 1
                await socket.send_json({'id': sequence, 'type': 'domosenso_frame/' + kind, **fields})
                reply = await socket.receive_json(timeout=30)
                assert reply['id'] == sequence, reply
                if error:
                    assert not reply['success'] and reply['error']['code'] == error, reply
                    return
                assert reply['success'], reply
                return reply['result']

            discover = {'endpoint': 'frame', 'version': 2, 'supported': SUPPORTED, 'locale': 'en'}
            assert 'assistantMetadataVersions' not in (await request('profile'))['endpoints'][0]
            await request('assistant_metadata', error='frame_denied', **discover)
            key = ec.generate_private_key(ec.SECP256R1())
            public = base64.b64encode(key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
            start = await request('begin', endpoint='frame', name='Fictional tablet', publicKey=public)
            signature = base64.b64encode(key.sign(start['challenge'].encode(), ec.ECDSA(hashes.SHA256()))).decode()
            pending = await request('finish', signature=signature)
            state['grants'].approve(pending['deviceId'], pending['code'])
            await request('assistant_metadata', error='frame_denied', **discover)
            state['assistant_metadata_read_enabled'] = True
            catalog = await request('assistant_metadata', **discover)
            assert len(catalog['capabilities']) == 2 and 'sensor.workbench' not in str(catalog)
            answers = {}
            for cap in catalog['capabilities']:
                target = cap['selectors'][0]
                fields = {'endpoint': 'frame', 'version': 2, 'catalogRevision': catalog['catalogRevision'],
                          'revision': cap['revision'], 'capability': cap['id']}
                answer = await request('assistant_metadata_query', arguments={target['type']: target['id']}, **fields)
                assert answer['coverage']['evaluated'] == 1, answer
                if target['type'] == 'area':
                    assert answer['assessment'] == {'status': 'partial', 'summary': None}
                answers[cap['id']] = answer
            if dest := os.environ.get('FRAME_METADATA_FIXTURE_OUT'):
                Path(dest).write_text(json.dumps({'catalog': catalog, 'answers': answers}, ensure_ascii=False, indent=2) + '\n')
            state['endpoints']['frame']['assistant']['metadata_sources'] = []
            await request('assistant_metadata_query', error='frame_metadata_stale', arguments={}, **fields)
            assert (await request('assistant_metadata', **discover))['capabilities'] == []
            state['assistant_metadata_legacy_enabled'] = True
            state['endpoints']['frame']['temperature'] = [
                {'entity': 'sensor.workbench_north', 'label': 'Studio', 'roomKey': 'studio'}
            ]
            legacy_supported = SUPPORTED + ['records.v1', 'options.v1']
            projected = await request('assistant_metadata', **(discover | {'supported': legacy_supported}))
            projected_caps = {cap['id']: cap for cap in projected['capabilities']}
            assert {'climate-air.temperature', 'ramka.current_time'} <= projected_caps.keys()
            projected_answers = {}
            for identifier in ('climate-air.temperature', 'ramka.current_time'):
                projected_answers[identifier] = await request('assistant_metadata_query', endpoint='frame', version=2,
                    catalogRevision=projected['catalogRevision'], capability=identifier, revision=1, arguments={})
                assert projected_answers[identifier]['assessment']['status'] == 'complete'
            if dest := os.environ.get('FRAME_METADATA_PROJECTED_OUT'):
                Path(dest).write_text(json.dumps({'catalog': projected, 'answers': projected_answers}, ensure_ascii=False, indent=2) + '\n')
            print('PASS HA 2026.9.2 paired WebSocket, gated v2, two metadata capabilities, partial facts and revocation')
    finally:
        await hass.async_stop()


if __name__ == '__main__':
    assert __version__ == '2026.9.2'
    with TemporaryDirectory(prefix='frame-metadata-ha-') as path:
        asyncio.run(scenario(Path(path)))
