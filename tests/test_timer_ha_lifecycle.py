"""Focused real HA core lifecycle, no full HA bootstrap, listener or Docker.

Install Home Assistant 2026.9.2 in a separate compatible Python environment to
run this optional integration test. The ordinary suite needs no HA dependency.
"""
import copy
import importlib
import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol
from test_timers import Rig, run

pytest.importorskip('homeassistant')
from homeassistant import loader
from homeassistant.core import HomeAssistant, Context, callback
from homeassistant.components.timer import Timer
from homeassistant.helpers import device_registry, entity_registry, restore_state, config_validation as cv
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.util import dt as dt_util

api = importlib.import_module('timer_package.timer_api')


def core_lifecycle(tmp_path):
    async def case():
        hass = HomeAssistant(str(tmp_path)); loader.async_setup(hass)
        device_registry.async_setup(hass)
        await device_registry.async_load(hass)
        await entity_registry.async_load(hass)
        await restore_state.async_load(hass, load_empty=True)
        component = EntityComponent(logging.getLogger(__name__), 'timer', hass)
        component.async_register_entity_service('start', {vol.Optional('duration', default=0):cv.time_period}, 'async_start')
        config = {'id':'demo', 'name':'Demo', 'duration':'0:01:00', 'restore':True}
        timer = Timer.from_yaml(config)
        r = Rig(); r.engine.state_get = hass.states.get
        events = []
        @callback
        def changed(event):
            if event.data.get('entity_id') == 'timer.demo':
                events.append(event)
                api.observe_event(r.engine, event, hass.states.get)
        cancels = [hass.bus.async_listen(name, changed) for name in
                   ('state_changed', 'timer.started', 'timer.restarted', 'timer.paused')]
        fixed = dt_util.utcnow().replace(microsecond=0)
        try:
            with patch('homeassistant.components.timer.dt_util.utcnow', return_value=fixed):
                await component.async_add_entities([timer])
                timer.async_set_context(Context()); timer.async_start(timedelta(seconds=60))
                before = r.fact()
                await r.engine.acknowledge(r.endpoint, before['subjectId'], before['runId'], lambda _: True, r.devices)
                stored_state = hass.states.get('timer.demo'); saved = copy.deepcopy(r.saved)
                # Actual restore entity/platform lifecycle; listeners precede helper addition.
                await timer.async_remove(force_remove=True)
                restore_state.async_get(hass).last_states['timer.demo'] = restore_state.StoredState(stored_state, None, fixed)
                r.engine = type(r.engine)(r.engine.installation, hass.states.get, r.persist, saved, now=lambda: fixed, endpoints={'frame':r.endpoint})
                timer = Timer.from_yaml(config)
                await component.async_add_entities([timer])
                assert any(e.event_type == 'timer.restarted' for e in events)
                assert r.fact()['runId'] == before['runId'] and r.fact()['acknowledged']
                # Force-update reports and real config writes must keep the durable run.
                timer.async_write_ha_state()
                await timer.async_update_config(config | {'name':'Renamed'})
                assert r.fact()['runId'] == before['runId'] and r.fact()['acknowledged']
                timer.async_set_context(Context()); timer.async_pause()
                assert r.fact()['state'] == 'paused' and r.fact()['runId'] == before['runId']
                timer.async_set_context(Context()); timer.async_start()
                assert r.fact()['runId'] == before['runId'] and r.fact()['acknowledged']
                # Core rounds starts to seconds. Identical attributes still mean replacement.
                previous_state = dict(hass.states.get('timer.demo').attributes)
                await hass.services.async_call('timer','start',{'entity_id':'timer.demo','duration':60},blocking=True,context=Context())
                assert dict(hass.states.get('timer.demo').attributes) == previous_state
                replacement = r.fact()
                assert replacement['runId'] != before['runId'] and not replacement['acknowledged']
                restarted = next(e for e in reversed(events) if e.event_type == 'timer.restarted')
                api.observe_event(r.engine, restarted, hass.states.get)
                assert r.fact() == replacement
                await timer.async_update_config(config | {'name':'Renamed again'})
                renamed = r.fact()
                api.observe_event(r.engine, restarted, hass.states.get)
                assert r.fact() == renamed and renamed['runId'] == replacement['runId']
                await r.engine.flush()
                assert r.saved['observations']['timer.demo']['runId'] == replacement['runId']
        finally:
            for cancel in cancels: cancel()
            hass.services.async_remove('timer', 'start')
            await timer.async_remove(force_remove=True)
            await hass.async_stop(force=True)
    run(case)


def registered_mapping(tmp_path):
    """Real Studio setup, HA route decorators, Mappings and verified Store writes."""
    import json
    import asyncio
    from test_timer_api import Connection
    from homeassistant.components import websocket_api
    studio_api = importlib.import_module('timer_package.studio_api')
    layouts = importlib.import_module('timer_package.layouts')
    grants = importlib.import_module('timer_package.grants')
    async def case():
        hass = HomeAssistant(str(tmp_path)); loader.async_setup(hass)
        device_registry.async_setup(hass)
        await device_registry.async_load(hass); await entity_registry.async_load(hass)
        await restore_state.async_load(hass, load_empty=True)
        component = EntityComponent(logging.getLogger(__name__), 'timer', hass)
        timer = Timer.from_yaml({'id':'demo','name':'Demo','duration':'0:01:00','restore':True})
        await component.async_add_entities([timer])
        r = Rig(); r.devices['tablet'].update(endpoint='other', name='Tablet')
        endpoints = {eid:{'id':eid,'display':{'locale':'en'}} for eid in ('frame','other')}
        async def unused(_): pass
        state = hass.data['domosenso_frame'] = {'endpoints':endpoints,
                 'grants':grants.Grants(r.engine.installation,r.devices),
                 'layouts':layouts.Layouts(endpoints,{},unused),
                 'timer_source_verified':lambda eid: entity_registry.async_get(hass).async_get(eid).platform == 'timer'}
        async def no_static(*_): pass
        c = Connection(); c.user.is_admin = True
        received = asyncio.Event()
        result, error = c.send_result, c.send_error
        def receive_result(*args): result(*args); received.set()
        def receive_error(*args): error(*args); received.set()
        c.send_result, c.send_error = receive_result, receive_error
        try:
            await studio_api.setup(hass, None, None, None, websocket_api.async_register_command, no_static)
            handler,schema=hass.data[websocket_api.DOMAIN]['domosenso_frame/studio_mappings_update']
            manager=state['studio_mappings']
            async def update(eid, rows):
                c.results.clear();c.errors.clear();received.clear()
                msg={'id':1,'type':'domosenso_frame/studio_mappings_update','version':1,'endpoint':eid,
                     'revision':manager.get(eid,hass.states)['revision'],
                     'patch':{'timers':{'writeEnabled':False,'items':rows}}}
                handler(hass,c,schema(msg)); await asyncio.wait_for(received.wait(), 5)
            rows=r.endpoint['timers']['items']
            await update('frame',rows)
            assert c.errors[-1][0]=='mapping_invalid' and not manager.saved
            await update('other',rows)
            assert not c.errors and c.results[-1]['mappings']['timers']['items']==rows
            await update('frame',rows)
            assert not c.errors and c.results
            saved=copy.deepcopy(manager.saved)
            await update('other',[])
            assert c.errors[-1][0]=='mapping_invalid' and manager.saved==saved
            assert json.loads((tmp_path/'.storage/domosenso_frame_mappings').read_text())['data']==saved
        finally:
            await timer.async_remove(force_remove=True)
            await hass.async_stop(force=True)
    run(case)


@pytest.mark.parametrize('scenario', ['core_lifecycle', 'registered_mapping'])
def test_real_ha_boundary(scenario, tmp_path):
    # HA/schema registries are process-global. Give each fictional installation
    # an independent process as well as a fresh directory and event loop.
    import subprocess
    import sys
    result = subprocess.run([sys.executable, __file__, scenario, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    import sys
    from pathlib import Path
    {'core_lifecycle':core_lifecycle, 'registered_mapping':registered_mapping}[sys.argv[1]](Path(sys.argv[2]))
