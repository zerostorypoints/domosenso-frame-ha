"""Negotiated shared timer actions and typed reads; no arbitrary HA services."""
import asyncio
import copy
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .timers import ACTIONS, Timers, eligible, instant, validate_targets

DOMAIN = 'domosenso_frame'
READS = {'timers.status': ('timer-query.v1', 'timer-status.v1'), 'ramka.current_time': ('time-query.v1', 'time.v1')}
TEXT = {
 'timers.status': (('Timer status', 'Timerstatus', 'Stan minutnika'), ('How much time remains?', 'Wie viel Zeit bleibt?', 'Ile zostało czasu?')),
 'ramka.current_time': (('Current time', 'Aktuelle Uhrzeit', 'Aktualny czas'), ('Which hour is it?', 'Wie spät ist es?', 'Która godzina?')),
 'timers.start': (('Start timer', 'Timer starten', 'Uruchom minutnik'), ('Set a timer for five minutes', 'Stelle einen Timer auf fünf Minuten', 'Ustaw minutnik na pięć minut')),
 'timers.pause': (('Pause timer', 'Timer pausieren', 'Wstrzymaj minutnik'), ('Pause the timer', 'Pausiere den Timer', 'Wstrzymaj minutnik')),
 'timers.resume': (('Resume timer', 'Timer fortsetzen', 'Wznów minutnik'), ('Resume the timer', 'Setze den Timer fort', 'Wznów minutnik')),
 'timers.cancel': (('Cancel timer', 'Timer abbrechen', 'Anuluj minutnik'), ('Cancel the timer', 'Brich den Timer ab', 'Anuluj minutnik')),
}


def metadata(capability):
    titles, examples = TEXT[capability]
    return {'title': dict(zip(('en','de','pl'), titles)), 'description': dict(zip(('en','de','pl'), titles)), 'examples': dict(zip(('en','de','pl'), ([e] for e in examples)))}


def localized_metadata(hass, capability, rows=None):
    """Use verified compiler text only when its bindings include the explicit selection."""
    from . import timer_contract, assistant_runtime_contract
    from .assistant_api import active_artifacts
    state = hass.data[DOMAIN]
    inventory = hass.states.get('sensor.domosenso_components')
    records = inventory.attributes.get('components') if inventory else None
    commands = capability in ACTIONS
    artifacts = (timer_contract.active(state.get('assistant_action_artifacts', {}), records) if commands
                 else assistant_runtime_contract.active(state.get('assistant_runtime_artifacts', {}), records) if capability == 'ramka.current_time'
                 else active_artifacts(state.get('assistant_artifacts', {}), records))
    bundle = artifacts.get(capability.split('.')[0])
    result = metadata(capability)
    if not bundle: return result
    if rows is not None:
        bound = {s['fields']['timer']['entity'] for s in bundle['bindings']['subjects'] if s['kind'] == 'timer' and 'timer' in s['fields']}
        if not rows or any(r['entity'] not in bound for r in rows): return result
    definition = next((d for d in bundle['definitions']['actions' if commands else 'capabilities'] if d['id'] == capability), None)
    if definition:
        for name in ('title','description','examples'):
            for lang in ('en','de','pl'):
                value = bundle['translations'][lang]
                for part in definition[name + '_key'].split('.'): value = value[part]
                result[name][lang] = value
    return result

def access(hass, connection, msg, grant, read, control=None):
    if not connection.user.is_active: raise ValueError('Inactive user')
    _, endpoint = grant(hass, connection)
    if endpoint['id'] != msg['endpoint']: raise ValueError('Endpoint mismatch')
    state = hass.data[DOMAIN]
    def permitted(entity):
        return (connection.user.is_active and connection.user.permissions.check_entity(entity, read)
                and (control is None or connection.user.permissions.check_entity(entity, control))
                and state.get('timer_source_verified', lambda _: True)(entity))
    return state, endpoint, permitted


def read_capabilities(hass, connection, msg, grant, policy_read, now):
    state, endpoint, permitted = access(hass, connection, msg, grant, policy_read)
    result = []
    for cap, (query_type, answer_type) in READS.items():
        if {'queryType': query_type, 'answerType': answer_type} not in msg['supported']: continue
        rows = None
        if cap == 'timers.status':
            engine = state.get('timers')
            rows = engine.rows(endpoint, permitted, state['saved'].get('devices', {})) if engine else []
            subjects = [{'id': engine.subject(r['entity']), 'kind':'timer', 'label':r['label']} for r in rows]
        else:
            subjects = [{'id': str(uuid.uuid5(uuid.UUID(state['saved']['installationId']), 'clock:' + endpoint['id'])), 'kind':'frame', 'label':endpoint.get('name', endpoint['id'])}]
        if not subjects: continue
        result.append({'id':cap,'revision':1,'queryType':query_type,'answerType':answer_type, **localized_metadata(hass, cap, rows),
                       'configuration':'configured','availability':'ready','limitations':[],
                       'availabilityBasis':'live_states','availabilityAsOf':now().isoformat(),'subjects':subjects})
    return result


def read_query(hass, connection, msg, grant, policy_read, now):
    state, endpoint, permitted = access(hass, connection, msg, grant, policy_read)
    cap = msg['capability']
    if type(msg['revision']) is not int or msg['revision'] != 1 or msg['answerType'] != READS[cap][1]: raise ValueError('Unsupported timer/time type')
    as_of = now()
    args = msg['arguments']
    if not isinstance(args, dict) or set(args) - {'subjectId'} or ('subjectId' in args and (not isinstance(args['subjectId'], str) or len(args['subjectId']) > 128)): raise ValueError('Invalid read arguments')
    if cap == 'timers.status':
        facts = state['timers'].snapshot(endpoint, permitted, state['saved'].get('devices', {}), as_of=as_of)
    else:
        subject = str(uuid.uuid5(uuid.UUID(state['saved']['installationId']), 'clock:' + endpoint['id']))
        ZoneInfo(hass.config.time_zone)  # reject invalid configuration; never invent a zone
        facts = [{'id':subject,'subjectId':subject,'instant':as_of.astimezone(timezone.utc).isoformat(),'timezone':hass.config.time_zone,'evidenceRefs':[subject]}]
    if 'subjectId' in args: facts = [f for f in facts if f['subjectId'] == args['subjectId']]
    if not facts: raise ValueError('Unknown timer/time subject')
    statuses = {f['state'] for f in facts} if cap == 'timers.status' else {'reported'}
    evaluated = sum(f.get('state') != 'unknown' for f in facts)
    return {'version':1,'capability':cap,'revision':1,'answerType':READS[cap][1],'asOf':as_of.isoformat(),
            'availability':'ready' if evaluated == len(facts) else 'partial' if evaluated else 'unavailable',
            'coverage':{'eligible':len(facts),'evaluated':evaluated,'missing':len(facts)-evaluated,'unit':'timer' if cap == 'timers.status' else 'frame'},
            'summary':{'status':next(iter(statuses)) if len(statuses) == 1 else 'mixed','affected':evaluated},
            'facts':facts,'evidence':[{'id':f['subjectId'],'sourceRef':f['subjectId'],'observedAt':None,'reportedAt':None,'freshnessBasis':'provider_response','maxAgeSeconds':None,'freshness':'current'} for f in facts],
            'limitations':[] if evaluated == len(facts) else ['missing_source']}


def actions(hass, connection, msg, grant, read, control):
    from .assistant_api import _validate
    try:
        _validate(msg, {'version','endpoint','supported'})
        supported = msg['supported']
        if not isinstance(supported, list) or len(supported) > 16 or any(not isinstance(p,dict) or set(p) != {'actionType','resultType'} or any(not isinstance(v,str) or len(v)>80 for v in p.values()) for p in supported): raise ValueError('Invalid negotiation')
        state, endpoint, permitted = access(hass, connection, msg, grant, read, control)
        engine = state['timers']; rows = engine.rows(endpoint, permitted, state['saved'].get('devices', {}), True)
        enabled = {'actionType':'timer-action.v1','resultType':'timer-result.v1'} in supported
        result = [{'id':cap,'revision':1,'actionType':'timer-action.v1','resultType':'timer-result.v1', **localized_metadata(hass, cap, rows),
                   'subjects':[{'id':engine.subject(r['entity']),'label':r['label']} for r in rows]} for cap in ACTIONS] if enabled and rows else []
        connection.send_result(msg['id'], {'version':1,'asOf':engine.now().isoformat(),'actions':result})
    except Exception:
        connection.send_error(msg['id'], 'frame_denied', 'Timer actions unavailable or access denied')


async def action(hass, connection, msg, grant, read, control):
    try:
        state, endpoint, permitted = access(hass, connection, msg, grant, read, control)
        engine = state['timers']
        def authorize():
            if hass.data.get(DOMAIN) is not state: raise ValueError('Bridge unloaded')
            return access(hass, connection, msg, grant, read, control)[1]
        context = connection.context(msg)
        async def service(name, data, _):
            await hass.services.async_call('timer', name, data, blocking=True, context=context)
        result = await engine.execute(msg, authorize, permitted, state['saved'].get('devices', {}), service, context.id)
        authorize()
        connection.send_result(msg['id'], result)
    except Exception:
        connection.send_error(msg['id'], 'frame_denied', 'Timer action unavailable or access denied')


async def acknowledge(hass, connection, msg, grant, read):
    from .assistant_api import _validate
    try:
        _validate(msg, {'version','endpoint','subjectId','runId'})
        if any(not isinstance(msg[k], str) or not 1 <= len(msg[k]) <= 128 for k in ('subjectId','runId')): raise ValueError('Invalid receipt')
        state, endpoint, permitted = access(hass, connection, msg, grant, read)
        mapping = copy.deepcopy(endpoint.get('timers'))
        result = await state['timers'].acknowledge(endpoint, msg['subjectId'], msg['runId'], permitted, state['saved'].get('devices', {}))
        _, current, checked = access(hass, connection, msg, grant, read)
        if current.get('timers') != mapping or hass.data.get(DOMAIN) is not state: raise ValueError('Mapping changed')
        state['timers'].resolve(current, msg['subjectId'], checked, state['saved'].get('devices', {}))
        connection.send_result(msg['id'], result)
    except Exception:
        connection.send_error(msg['id'], 'frame_denied', 'Timer receipt unavailable or access denied')


def browser_validate(msg, fields):
    if (not isinstance(msg, dict) or set(msg) - fields - {'id', 'type'} or not fields <= msg.keys()
            or type(msg['version']) is not int or msg['version'] != 1):
        raise ValueError('Invalid browser timer request')
    for key in ('subjectId', 'runId'):
        if key in fields and (not isinstance(msg[key], str) or not 1 <= len(msg[key]) <= 128):
            raise ValueError('Invalid timer identity')


def browser_access(hass, connection, read):
    """HA-authenticated READ access; pairing and command grants play no role here."""
    state = hass.data[DOMAIN]
    engine = state['timers']
    def current():
        if hass.data.get(DOMAIN) is not state or state.get('timers') is not engine or engine.closed or not connection.user.is_active:
            raise ValueError('Browser timer access denied')
        endpoints = state['endpoints']
        validate_targets(endpoints)
        verified = state.get('timer_source_verified')
        rows = {}
        for endpoint in endpoints.values():
            for row in engine.rows(endpoint, lambda eid: bool(verified and verified(eid)) and connection.user.permissions.check_entity(eid, read), state['saved'].get('devices', {})):
                # A helper used for Frame runtime anywhere cannot be a browser alarm.
                if all(eligible(engine.state_get(row['entity']), e) for e in endpoints.values()):
                    rows.setdefault(row['entity'], row)
        return rows
    return state, engine, current


def browser_snapshot(hass, connection, msg, read):
    try:
        browser_validate(msg, {'version', 'entities'})
        entities = msg['entities']
        if (not isinstance(entities, list) or len(entities) > 16
                or any(not isinstance(e, str) or not re.fullmatch(r'timer\.[a-z0-9_]+', e) for e in entities)
                or len(set(entities)) != len(entities)):
            raise ValueError('Invalid timer selection')
        state, engine, current = browser_access(hass, connection, read)
        rows = current()
        as_of = engine.now()
        result = {'version': 1, 'asOf': as_of.isoformat(), 'installationId': engine.installation,
                  'timers': [{'entity': eid, 'label': rows[eid]['label'],
                              'timer': engine.fact(rows[eid], state['saved'].get('devices', {}), as_of=as_of)}
                             for eid in entities if eid in rows]}
        connection.send_result(msg['id'], result)
    except Exception:
        connection.send_error(msg['id'], 'frame_denied', 'Browser timers unavailable or access denied')


def browser_subject(hass, connection, msg, read):
    browser_validate(msg, {'version', 'subjectId', 'runId'})
    state, engine, current = browser_access(hass, connection, read)
    initial = copy.deepcopy(state['endpoints'])
    mapping_revision = engine.mapping_revision
    def recheck():
        rows = current()
        if state['endpoints'] != initial or engine.mapping_revision != mapping_revision: raise ValueError('Timer mapping changed')
        row = next((row for row in rows.values() if engine.subject(row['entity']) == msg['subjectId']), None)
        if row is None: raise ValueError('Timer not authorized')
        return row
    return state, engine, recheck


async def browser_claim(hass, connection, msg, read):
    try:
        state, engine, recheck = browser_subject(hass, connection, msg, read)
        row = recheck()
        async with engine.locks.setdefault(row['entity'], asyncio.Lock()):
            row = recheck()
            def sample():
                as_of = engine.now()
                fact = engine.fact(row, state['saved'].get('devices', {}), as_of=as_of)
                deadline = instant(fact['deadline'])
                allowed = (row['alarmDeviceId'] is None and fact['state'] == 'idle'
                           and fact['runId'] == msg['runId'] and not fact['acknowledged']
                           and fact['outcome'] in ('completed', 'inferred_completed')
                           and deadline is not None and 0 <= (as_of - deadline).total_seconds() <= 60)
                return as_of, fact, allowed
            as_of, fact, allowed = sample()
            key = msg['subjectId'] + ':' + msg['runId']
            claimed = False
            if allowed and key not in engine.data['browserDeliveries']:
                # Tombstone before persistence. Failed/uncertain saves never permit sound
                # or retry in this process; committed tombstones also survive restart.
                engine.data['browserDeliveries'][key] = as_of.isoformat()
                await engine.flush()
                row = recheck()
                as_of, fact, claimed = sample()
            connection.send_result(msg['id'], {'version': 1, 'asOf': as_of.isoformat(), 'claimed': claimed, 'timer': fact})
    except Exception:
        connection.send_error(msg['id'], 'frame_denied', 'Browser timer claim unavailable or access denied')


async def browser_acknowledge(hass, connection, msg, read):
    try:
        state, engine, recheck = browser_subject(hass, connection, msg, read)
        row = recheck()
        async with engine.locks.setdefault(row['entity'], asyncio.Lock()):
            row = recheck()
            endpoint = {'timers': {'writeEnabled': False, 'items': [row]}}
            await engine.acknowledge(endpoint, msg['subjectId'], msg['runId'], lambda _: True, state['saved'].get('devices', {}))
            row = recheck()
            as_of = engine.now()
            connection.send_result(msg['id'], {'version': 1, 'asOf': as_of.isoformat(),
                                   'timer': engine.fact(row, state['saved'].get('devices', {}), as_of=as_of)})
    except Exception:
        connection.send_error(msg['id'], 'frame_denied', 'Browser timer receipt unavailable or access denied')


def observe_event(engine, event, state_get):
    """Correlate lifecycle events with core state; late events cannot finish a new run."""
    eid = event.data['entity_id']
    sample = event.data.get('new_state') if event.event_type == 'state_changed' else state_get(eid)
    if event.event_type in ('timer.finished','timer.cancelled'):
        if not sample or sample.state != 'idle' or sample.attributes.get('last_transition') != event.event_type.split('.')[1]: return
        previous = engine.data['observations'].get(eid, {})
        if event.event_type == 'timer.finished' and previous.get('deadline'):
            from .timers import instant
            if instant(event.data.get('finished_at')) != instant(previous['deadline']): return
    # Core timer lifecycle events do not carry the service context; its state does.
    context = event.context.id if event.event_type == 'state_changed' else getattr(getattr(sample, 'context', None), 'id', None)
    engine.observe(eid, sample, context=context, event=event.event_type)
    if event.event_type == 'timer.finished' and not engine.data['observations'][eid].get('deadline'):
        from .timers import instant
        finished = instant(event.data.get('finished_at'))
        if finished: engine.data['observations'][eid]['deadline'] = finished.isoformat()

async def setup(hass, grant, register_command):
    import voluptuous as vol
    from homeassistant.auth.permissions.const import POLICY_READ, POLICY_CONTROL
    from homeassistant.components import websocket_api
    from homeassistant.core import callback
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.storage import Store
    from pathlib import Path
    from .layouts import save_verified
    state = hass.data[DOMAIN]
    registry = er.async_get(hass)
    state['timer_source_verified'] = lambda eid: bool((entry := registry.async_get(eid)) and entry.platform == 'timer')
    validate_targets(state['endpoints'])
    store = Store(hass, 1, DOMAIN + '_timers')
    async def persist(value):
        await save_verified(store, value, Path(hass.config.path('.storage', DOMAIN + '_timers')), DOMAIN + '_timers', hass.async_add_executor_job)
    def emit(entity=None):
        if hass.data.get(DOMAIN) is not state: return
        for eid, endpoint in state['endpoints'].items():
            if entity is None or any(r['entity'] == entity for r in endpoint.get('timers', {}).get('items', [])):
                hass.bus.async_fire(DOMAIN + '_event', {'kind':'timers','endpoint':eid})
    engine = state['timers'] = Timers(state['saved']['installationId'], hass.states.get, persist, await store.async_load(), emit=emit)
    state['timers_changed'] = emit
    tasks = set()
    def schedule_save():
        task = hass.async_create_task(engine.flush())
        tasks.add(task); task.add_done_callback(tasks.discard)
    @callback
    def changed(event):
        eid = event.data.get('entity_id')
        if not isinstance(eid, str) or not any(eid == r['entity'] for e in state['endpoints'].values() for r in e.get('timers', {}).get('items', [])): return
        # Observe synchronously, before an awaiting service caller resumes.
        observe_event(engine, event, hass.states.get)
        schedule_save()
    cancels = [hass.bus.async_listen(name, changed) for name in ('state_changed','timer.started','timer.restarted','timer.paused','timer.changed','timer.cancelled','timer.finished')]
    @callback
    def config_changed(event): emit()
    cancels.append(hass.bus.async_listen('core_config_updated', config_changed))
    # Restore reconciliation runs after HA helper restoration, not while setup is still starting.
    async def reconcile(event=None):
        for endpoint in state['endpoints'].values():
            engine.snapshot(endpoint, state['timer_source_verified'], state['saved']['devices'])
        await engine.flush(); emit()
    from homeassistant.core import CoreState
    if hass.state == CoreState.running: await reconcile()
    else: cancels.append(hass.bus.async_listen('homeassistant_started', reconcile))
    async def close():
        for cancel in cancels: cancel()
        engine.close()
        if tasks:
            import asyncio
            await asyncio.gather(*tasks, return_exceptions=True)
        await engine.flush()
    state['timers_close'] = close
    def command(name, fields):
        return websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/' + name, vol.Required('version'): int, vol.Required('endpoint'): str, **{vol.Required(k):v for k,v in fields.items()}})
    @command('assistant_actions', {'supported':list})
    @callback
    def negotiate(h,c,msg): actions(h,c,msg,grant,POLICY_READ,POLICY_CONTROL)
    @command('assistant_action', {'capability':str,'revision':int,'subjectId':str,'requestId':str,'expectedStateRevision':str,'expectedRunId':vol.Any(str,None),'arguments':dict})
    @websocket_api.async_response
    async def execute(h,c,msg): await action(h,c,msg,grant,POLICY_READ,POLICY_CONTROL)
    @command('timer_acknowledge', {'subjectId':str,'runId':str})
    @websocket_api.async_response
    async def receipt(h,c,msg): await acknowledge(h,c,msg,grant,POLICY_READ)
    def browser_command(name, fields):
        return websocket_api.websocket_command({vol.Required('type'): DOMAIN + '/' + name, vol.Required('version'): int,
                                                **{vol.Required(k): v for k, v in fields.items()}})
    @browser_command('timer_browser_snapshot', {'entities': list})
    @callback
    def browser_read(h, c, msg): browser_snapshot(h, c, msg, POLICY_READ)
    @browser_command('timer_browser_claim', {'subjectId': str, 'runId': str})
    @websocket_api.async_response
    async def browser_delivery(h, c, msg): await browser_claim(h, c, msg, POLICY_READ)
    @browser_command('timer_browser_acknowledge', {'subjectId': str, 'runId': str})
    @websocket_api.async_response
    async def browser_receipt(h, c, msg): await browser_acknowledge(h, c, msg, POLICY_READ)
    for handler in (negotiate, execute, receipt, browser_read, browser_delivery, browser_receipt): register_command(hass, handler)
