"""Exercise real Home Assistant websocket schemas and the Studio route stack.

Run with Home Assistant 2026.9.2 installed, or from its Docker image.
This test uses a fictional in-memory provider and no external network.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_hifisync_bridge import sample

pytest.importorskip('homeassistant')
from homeassistant import loader
from homeassistant.components import websocket_api
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import device_registry, entity_registry

from custom_components import domosenso_frame as frame
from custom_components.domosenso_frame import studio_api, studio_runtime
from custom_components.domosenso_frame.grants import Grants
from custom_components.domosenso_frame.layouts import Layouts


class Connection:
    def __init__(self, name, *, admin=False, blocked=None):
        self.user = SimpleNamespace(
            id=name, is_admin=admin, is_active=True,
            permissions=SimpleNamespace(check_entity=lambda entity, _: entity != blocked),
        )
        self.subscriptions = {}

    def send_result(self, _, result):
        self.result.set_result(result)

    def send_error(self, _, code, message):
        self.result.set_result({'error': code, 'message': message})

    def context(self, _):
        return Context(user_id=self.user.id)

    async def call(self, hass, command, **fields):
        self.result = asyncio.get_running_loop().create_future()
        route = f'domosenso_frame/{command}'
        handler, schema = hass.data[websocket_api.DOMAIN][route]
        msg = {'type': route, 'id': 1, **fields}
        handler(hass, self, schema(msg) if callable(schema) else msg)
        return await asyncio.wait_for(self.result, 5)


def test_real_ha_negotiation_mapping_and_room_departure(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        loader.async_setup(hass)
        device_registry.async_setup(hass)
        await device_registry.async_load(hass)
        await entity_registry.async_load(hass)

        async def unused(*_):
            pass

        endpoints = {
            eid: {
                'id': eid, 'display': {'locale': 'en', 'status': []},
                'studio': {'media': {'entity': 'media_player.demo'}, 'runtime': {'scripts': {'media': 'script.demo_music'}}},
                'calendars': [], 'temperature': [], 'air': [],
            }
            for eid in ('frame', 'other')
        }
        admin = Connection('admin', admin=True, blocked='media_player.forbidden')
        panel = Connection('panel')
        legacy = Connection('legacy')
        other = Connection('other')
        grants = Grants('fictional', {
            client.user.id: {'state': 'approved', 'user': client.user.id, 'endpoint': eid, 'name': client.user.id}
            for client, eid in ((panel,'frame'), (legacy,'frame'), (other,'other'))
        })
        for client, eid in ((panel,'frame'), (legacy,'frame'), (other,'other')):
            grants.sessions[client] = (client.user.id, client.user.id, eid)
        hass.data[frame.DOMAIN] = {
            'endpoints': endpoints,
            'grants': grants,
            'layouts': Layouts(endpoints, {}, unused),
            'settings': {'stale_seconds': 900},
        }
        hass.states.async_set('media_player.demo', 'idle')
        hass.states.async_set('media_player.forbidden', 'idle')
        read = []
        clock = SimpleNamespace(monotonic=lambda: tick[0])
        tick = [1000]

        def summary(entity):
            read.append(entity)
            return {'status': 'ready', 'systemName': 'Fictional system', 'roomName': 'Fictional room'}
        provider_snapshot = sample()
        original_room_id = '00000000-0000-4000-8000-000000000001'
        other_room_id = '00000000-0000-4000-8000-000000000002'
        provider_snapshot['snapshot']['room']['id'] = original_room_id
        flags = {'capabilitiesVersion': 1, 'hifisyncStudioVersion': 1}
        try:
            await studio_api.setup(hass, frame.grant, frame.reading, frame.display_snapshot,
                                   websocket_api.async_register_command, unused)
            async def mapping(client):
                return await client.call(hass, 'studio_mappings', version=1, endpoint='frame', hifisyncStudioVersion=1)

            absent = await mapping(admin)
            assert absent['hifisyncInstalled'] is False
            with pytest.raises(Unauthorized):
                await mapping(panel)
            hass.data['hifisync'] = {
                'studio_link_v1': summary,
                'studio_snapshot_v1': lambda eid: provider_snapshot if eid == 'media_player.demo' else None,
            }
            before = await admin.call(hass, 'studio_library')
            ready = await mapping(admin)
            assert ready['hifisyncInstalled'] is True
            by_entity = {row['entity']: row for row in ready['candidates']['media']}
            assert by_entity['media_player.demo']['hifisyncLink']['roomName'] == 'Fictional room'
            assert 'hifisyncLink' not in by_entity['media_player.forbidden']
            assert read == ['media_player.demo']
            mapped = await admin.call(hass, 'studio_mappings_update', version=1, hifisyncStudioVersion=1,
                                      endpoint='frame', revision=ready['revision'], patch={'hifisync': True})
            assert mapped['mappings']['hifisync'] is True
            assert await admin.call(hass, 'studio_library') == before

            with patch.object(studio_api, 'time', clock), patch.object(studio_runtime, 'time', clock):
                hass.states.async_set('media_player.demo', 'playing')
                display = await panel.call(hass, 'studio_display', **flags)
                assert display['mode'] == 'media' and display['hifisyncStudioVersion'] == 1
                screens = next(mode['screens'] for mode in display['config']['modes'] if mode['id'] == 'media')
                assert [page['id'] for page in screens] == ['home', 'hifisync-system', 'hifisync-room']
                assert 'roomVisitId' not in display
                separate = await other.call(hass, 'studio_display', **flags)
                assert separate['data']['hifisync'] is None
                older = await legacy.call(hass, 'studio_display', hifisyncVersion=1)
                assert 'hifisyncStudioVersion' not in older
                assert 'id' not in older['data']['hifisync']['snapshot']['room']
                assert not any(page['requires'] for mode in older['config']['modes'] for page in mode['screens'])

                async def action(client, value):
                    return await client.call(hass, 'studio_action', action=value)

                step = {'kind': 'navigation', 'action': 'next'}
                assert await action(panel, step) == {'ok': True}
                assert (await panel.call(hass, 'studio_display', **flags))['screen'] == 'hifisync-system'
                assert await action(panel, step) == {'ok': True}
                room = await panel.call(hass, 'studio_display', **flags)
                visit = room['roomVisitId']
                assert room['data']['hifisync']['snapshot']['room']['id'] == original_room_id
                touch = {'kind': 'room_interaction', 'action': 'touch', 'visitId': visit}
                assert await action(panel, touch) == {'ok': True}
                assert (await action(other, touch))['error'] == 'studio_room_inactive'
                assert (await action(legacy, touch))['error'] == 'studio_room_inactive'
                preview = await admin.call(hass, 'studio_dashboard', endpoint='frame', **flags)
                assert preview['readOnly'] and 'roomVisitId' not in preview
                tick[0] += 20
                provider_snapshot['snapshot']['room']['widthM'] = 4.5
                provider_snapshot['snapshot']['room']['name'] = 'Room renamed'
                provider_snapshot['snapshot']['revision'] = 'room edited'
                assert (await panel.call(hass, 'studio_display', **flags))['roomVisitId'] == visit
                provider_snapshot['snapshot']['room']['id'] = other_room_id
                assert (await action(panel, touch))['error'] == 'studio_room_inactive'
                assert (await panel.call(hass, 'studio_display', **flags))['screen'] == 'home'
                assert await action(panel, {'kind': 'navigation', 'action': 'previous'}) == {'ok': True}
                replacement = await panel.call(hass, 'studio_display', **flags)
                assert replacement['roomVisitId'] != visit
                assert replacement['data']['hifisync']['snapshot']['room']['id'] == other_room_id
                touch['visitId'] = replacement['roomVisitId']
                assert await action(panel, touch) == {'ok': True}
                assert await action(panel, step) == {'ok': True}
                assert (await action(panel, touch))['error'] == 'studio_room_inactive'
                assert (await panel.call(hass, 'studio_display', **flags))['screen'] == 'home'
                assert await action(panel, {'kind': 'navigation', 'action': 'previous'}) == {'ok': True}
                touch['visitId'] = (await panel.call(hass, 'studio_display', **flags))['roomVisitId']
                panel.user.permissions.check_entity = lambda entity, _: entity != 'media_player.demo'
                assert (await action(panel, touch))['error'] == 'studio_failed'
                panel.user.permissions.check_entity = lambda *_: True
                hass.data['hifisync'].pop('studio_snapshot_v1')
                assert (await action(panel, touch))['error'] == 'studio_room_inactive'
                fallback = await panel.call(hass, 'studio_display', **flags)
                assert fallback['screen'] == 'home' and fallback['data']['hifisync'] is None
                assert 'roomVisitId' not in fallback
        finally:
            for client in (panel, other, legacy):
                for unsubscribe in client.subscriptions.values():
                    unsubscribe()
            await hass.async_stop(force=True)

    asyncio.run(run())
