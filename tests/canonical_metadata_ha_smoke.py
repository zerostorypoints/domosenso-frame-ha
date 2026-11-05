"""Canonical data-only additions over paired WebSocket on isolated pinned HA."""
import asyncio
import base64
import hashlib
import importlib
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
from homeassistant.helpers import area_registry, entity_registry

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/assistant-metadata-canonical'
SUPPORTED = ['measurement.v1', 'selector.v1']


def localized(name):
    data = json.loads((FIXTURES / name).read_text())
    for metric, label, anchors in [('temperature', 'Temperatura stacji', ['temperatura', 'temperatury', 'ciepło']),
                                    ('humidity', 'Wilgotność stacji', ['wilgotność', 'wilgotności'])]:
        if metric in data['translations']['pl']['meta']['assistant']:
            values = data['translations']['pl']['meta']['assistant'][metric]
            values.update(title=label, description='Bieżący odczyt ze stacji.', anchors=anchors,
                          examples=['Jaka jest ' + ('temperatura' if metric == 'temperature' else 'wilgotność') + ' na stacji?'])
    for subject in data['bindings']['subjects']:
        subject['label']['pl'] = 'Stacja'; subject['aliases']['pl'] = ['stacji']
        subject['roomLabel']['pl'] = 'Gabinet'; subject['roomAliases']['pl'] = ['gabinecie']
    return data


def code_digest(root):
    files = sorted(path for path in root.rglob('*') if path.suffix in ('.py', '.js', '.json'))
    return hashlib.sha256(b''.join(path.relative_to(root).as_posix().encode() + b'\0' + path.read_bytes() for path in files)).hexdigest()


async def scenario(config_dir):
    integration = config_dir / 'custom_components'
    shutil.copytree(ROOT / 'custom_components', integration)
    before = code_digest(integration)
    path = config_dir / 'domosenso/assistant-metadata/workbench.yaml'; path.parent.mkdir(parents=True)
    first = localized('temperature-only.json'); both = localized('temperature-humidity.json')
    path.write_text(json.dumps(first, ensure_ascii=False))
    hass = HomeAssistant(str(config_dir)); loader.async_setup(hass); hass.config.skip_pip = True
    config = {'homeassistant': {'name': 'Fictional canonical lab', 'time_zone': 'UTC'},
              'http': {'server_host': '127.0.0.1', 'server_port': 18187},
              'domosenso_frame': {'endpoints': [{'id': 'frame', 'name': 'Fictional frame',
                                                'dashboard': '/dashboard-fictional/main', 'display': {}}]}}
    try:
        assert await bootstrap.async_from_config_dict(config, hass)
        await hass.async_start(); await hass.async_block_till_done()
        state = hass.data['domosenso_frame']
        assert not state['assistant_metadata_errors'], state['assistant_metadata_errors']
        hass.states.async_set('sensor.domosenso_components', '1', {'components': [
            {**first['component'], 'package': '', 'installed': True}]})
        registry = entity_registry.async_get(hass); areas = area_registry.async_get(hass)
        office = areas.async_create('Office'); storage = areas.async_create('Storage')
        assert office.id == 'office'
        entries = {}
        for metric, value, unit in [('temperature', 21, '°C'), ('humidity', 48, '%')]:
            object_id = 'workbench_' + metric
            entry = registry.async_get_or_create('sensor', 'esphome', object_id, suggested_object_id=object_id)
            registry.async_update_entity(entry.entity_id, area_id=office.id)
            entries[entry.entity_id] = entry.id
            hass.states.async_set(entry.entity_id, value, {'unit_of_measurement': unit})
        await hass.async_block_till_done()
        metadata = importlib.import_module('custom_components.domosenso_frame.assistant_metadata')
        endpoint = state['endpoints']['frame']
        endpoint['assistant'] = {'metadata_sources': [row['grant'] for row in metadata.source_candidates(
            state['assistant_metadata_artifacts'], entries.get)]}
        user = await hass.auth.async_create_user('Fictional tablet', group_ids=['system-users'])
        admin = await hass.auth.async_create_user('Fictional administrator', group_ids=['system-admin'])
        async with ClientSession() as client:
            async def connect(account):
                socket = await client.ws_connect('http://127.0.0.1:18187/api/websocket')
                assert (await socket.receive_json())['type'] == 'auth_required'
                refresh = await hass.auth.async_create_refresh_token(account, client_id='http://localhost/')
                await socket.send_json({'type': 'auth', 'access_token': hass.auth.async_create_access_token(refresh)})
                assert (await socket.receive_json())['type'] == 'auth_ok'
                return socket
            socket = await connect(user); admin_socket = await connect(admin)
            sequence = 0
            async def request(kind, expected_error=None, admin=False, **fields):
                nonlocal sequence
                sequence += 1; target = admin_socket if admin else socket
                await target.send_json({'id': sequence, 'type': 'domosenso_frame/' + kind, **fields})
                reply = await target.receive_json(timeout=30)
                assert reply['id'] == sequence, reply
                if expected_error:
                    assert not reply['success'] and reply['error']['code'] == expected_error, reply
                    return
                assert reply['success'], reply
                return reply['result']
            discovery = {'endpoint': 'frame', 'version': 2, 'supported': SUPPORTED, 'locale': 'en'}
            assert 'assistantMetadataVersions' not in (await request('profile'))['endpoints'][0]
            await request('assistant_metadata', expected_error='frame_denied', **discovery)
            key = ec.generate_private_key(ec.SECP256R1())
            public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
            start = await request('begin', endpoint='frame', name='Fictional tablet', publicKey=public)
            pending = await request('finish', signature=base64.b64encode(key.sign(
                start['challenge'].encode(), ec.ECDSA(hashes.SHA256()))).decode())
            state['grants'].approve(pending['deviceId'], pending['code'])
            await request('assistant_metadata', expected_error='frame_denied', **discovery)
            state['assistant_metadata_read_enabled'] = True  # Lab gate only; defaults remain disabled.
            initial = await request('assistant_metadata', **discovery)
            assert [cap['id'] for cap in initial['capabilities']] == ['workbench.station_temperature']
            first_cap = initial['capabilities'][0]
            first_subject = next(row for row in first_cap['selectors'] if row['type'] == 'subject')
            first_answer = await request('assistant_metadata_query', endpoint='frame', version=2,
                catalogRevision=initial['catalogRevision'], capability=first_cap['id'], revision=first_cap['revision'],
                arguments={'subject': first_subject['id']})
            assert first_answer['facts'][0]['value'] == 21
            initial_wire = {'catalog': initial, 'answers': {first_cap['id']: first_answer}}
            path.write_text(json.dumps(both, ensure_ascii=False))
            await request('assistant_reload', admin=True)
            assert not state['assistant_metadata_errors']
            current = await request('assistant_metadata', **discovery)
            assert [cap['id'] for cap in current['capabilities']] == ['workbench.station_temperature']
            endpoint['assistant']['metadata_sources'] = [row['grant'] for row in metadata.source_candidates(
                state['assistant_metadata_artifacts'], entries.get)]
            wire = {'before': initial_wire}
            for locale in ('en', 'pl'):
                catalog = await request('assistant_metadata', **(discovery | {'locale': locale}))
                assert {cap['id'] for cap in catalog['capabilities']} == {'workbench.station_temperature', 'workbench.station_humidity'}
                assert 'sensor.workbench' not in json.dumps(catalog)
                answers = {}
                for cap in catalog['capabilities']:
                    selector = next(row for row in cap['selectors'] if row['type'] == 'subject')
                    answer = await request('assistant_metadata_query', endpoint='frame', version=2,
                        catalogRevision=catalog['catalogRevision'], capability=cap['id'], revision=cap['revision'],
                        arguments={'subject': selector['id']})
                    assert answer['facts'][0]['value'] == (48 if cap['id'].endswith('humidity') else 21), answer
                    answers[cap['id']] = answer
                wire[locale] = {'catalog': catalog, 'answers': answers}
            cap = current['capabilities'][0]
            await request('assistant_metadata_query', expected_error='frame_metadata_stale', endpoint='frame', version=2,
                catalogRevision=initial['catalogRevision'], capability=cap['id'], revision=cap['revision'], arguments={})
            old = wire['en']['catalog']; temperature = next(cap for cap in old['capabilities'] if cap['id'].endswith('temperature'))
            registry.async_update_entity('sensor.workbench_temperature', area_id=storage.id)
            await request('assistant_metadata_query', expected_error='frame_metadata_stale', endpoint='frame', version=2,
                catalogRevision=old['catalogRevision'], capability=temperature['id'], revision=temperature['revision'], arguments={})
            state['grants'].revoke(pending['deviceId'])
            await request('assistant_metadata', expected_error='frame_denied', **discovery)
            after = code_digest(integration); assert before == after
            if output := os.environ.get('FRAME_CANONICAL_OUT'):
                out = Path(output); out.mkdir(parents=True, exist_ok=True)
                (out / 'wire.json').write_text(json.dumps(wire, indent=2, ensure_ascii=False) + '\n')
                (out / 'evidence.json').write_text(json.dumps({'haVersion': __version__, 'codeBefore': before,
                    'codeAfter': after, 'sourceVersion': first['component'], 'metadataOnlyAddition': True,
                    'explicitNewGrant': True, 'areaMoveInvalidation': True, 'pairingRevocation': True,
                    'wireSha256': hashlib.sha256((out / 'wire.json').read_bytes()).hexdigest()}, indent=2) + '\n')
            await socket.close(); await admin_socket.close()
            print('PASS canonical HA 2026.9.2: paired/authenticated data-only additions, explicit grants, PL/EN wire, reload, area move and revocation; integration code unchanged')
    finally:
        await hass.async_stop()


if __name__ == '__main__':
    assert __version__ == '2026.9.2'
    with TemporaryDirectory(prefix='frame-canonical-ha-') as directory:
        asyncio.run(scenario(Path(directory)))
