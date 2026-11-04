"""Authenticated assistant WebSocket API, independent of the legacy protocol."""
from datetime import datetime, timezone

from .assistant_data import calendar_window
from .assistant_extended import ExtendedRuntime
from . import timer_api
from .assistant_contract import load_directory, identity_matches

DOMAIN = 'domosenso_frame'


def state_for_assistant(state, entry):
    """Normalize only provider-declared dimensionless Ruuvi indexes, without HA writes."""
    if (state is None or entry is None or entry.platform != 'ruuvitag_ble'
            or not getattr(entry, 'entity_id', '').startswith('sensor.')
            or state.attributes.get('unit_of_measurement')):
        return state
    # HA PassiveBluetoothProcessorEntity uses address-key[-device_id] identity.
    key = getattr(entry, 'unique_id', '').split('-', 2)
    unit = {'voc_index': 'index', 'nox_index': 'index', 'iaqs': '%'}.get(key[1] if len(key) > 1 else '')
    if not unit:
        return state
    from types import SimpleNamespace
    return SimpleNamespace(state=state.state, attributes={**state.attributes, 'unit_of_measurement': unit},
                           last_reported=getattr(state, 'last_reported', None),
                           last_changed=getattr(state, 'last_changed', None),
                           last_updated=getattr(state, 'last_updated', None))


def active_artifacts(artifacts, records):
    """Match unique installed inventory entries; inventory never supplies authorization."""
    if not isinstance(records, list) or len(records) > 256:
        return {}
    result = {}
    for cid, bundle in artifacts.items():
        matches = [r for r in records if isinstance(r, dict) and r.get('id') == cid]
        if len(matches) == 1 and identity_matches(bundle, matches[0]):
            result[cid] = bundle
    return result


def inventory_diagnostics(artifacts, records):
    verified = active_artifacts(artifacts, records)
    return verified, {cid: 'contract_mismatch' for cid in artifacts if cid not in verified}


def _validate(msg, fields):
    if not isinstance(msg, dict) or set(msg) - ({'id', 'type'} | fields) or not fields <= msg.keys():
        raise ValueError('Invalid request')
    if type(msg['version']) is not int or msg['version'] != 1 or not isinstance(msg['endpoint'], str) or not 1 <= len(msg['endpoint']) <= 40:
        raise ValueError('Unsupported request')


def _engine(hass, connection, grant, msg, policy_read, now):
    _, endpoint = grant(hass, connection)
    if endpoint['id'] != msg['endpoint']:
        raise ValueError('Endpoint access denied')
    state = hass.data[DOMAIN]
    inventory = hass.states.get('sensor.domosenso_components')
    records = getattr(inventory, 'attributes', {}).get('components') if inventory else None
    artifacts = active_artifacts(state.get('assistant_artifacts', {}), records)
    return endpoint, ExtendedRuntime(state['saved']['installationId'], state.get('assistant_state_get', hass.states.get),
                             lambda eid: connection.user.permissions.check_entity(eid, policy_read), now,
                             state['settings']['stale_seconds'], state.get('assistant_source_verified'), artifacts, state.get('assistant_registry_id'))


def capabilities(hass, connection, msg, grant, policy_read, now=lambda: datetime.now(timezone.utc)):
    try:
        _validate(msg, {'endpoint', 'version', 'supported'})
        supported = msg['supported']
        if not isinstance(supported, list) or len(supported) > 64 or any(not isinstance(p, dict) or set(p) != {'queryType', 'answerType'} or any(not isinstance(v, str) or len(v) > 80 for v in p.values()) for p in supported):
            raise ValueError('Invalid supported types')
        endpoint, engine = _engine(hass, connection, grant, msg, policy_read, now)
        result = {'version': 1, 'asOf': now().isoformat(), 'capabilities': engine.capabilities(endpoint, supported) + timer_api.read_capabilities(hass, connection, msg, grant, policy_read, now)}
        current, _ = _engine(hass, connection, grant, msg, policy_read, now)
        if current is not endpoint:
            raise ValueError('Mapping changed')
        connection.send_result(msg['id'], result)
    except Exception:
        connection.send_error(msg['id'], 'frame_denied', 'Assistant unavailable or access denied')


def query(hass, connection, msg, grant, policy_read, now=lambda: datetime.now(timezone.utc)):
    try:
        _validate(msg, {'endpoint', 'version', 'capability', 'revision', 'answerType', 'arguments'})
        endpoint, engine = _engine(hass, connection, grant, msg, policy_read, now)
        result = engine.query(endpoint, msg['capability'], msg['revision'], msg['answerType'], msg['arguments'])
        current, _ = _engine(hass, connection, grant, msg, policy_read, now)
        if current is not endpoint:
            raise ValueError('Mapping changed')
        connection.send_result(msg['id'], result)
    except Exception:
        connection.send_error(msg['id'], 'frame_denied', 'Assistant unavailable or access denied')


async def query_async(hass, connection, msg, grant, policy_read, now=lambda: datetime.now(timezone.utc)):
    if msg.get('capability') in timer_api.READS:
        try:
            _validate(msg, {'endpoint','version','capability','revision','answerType','arguments'})
            state, endpoint, _ = timer_api.access(hass, connection, msg, grant, policy_read)
            import copy
            mapping = copy.deepcopy(endpoint.get('timers'))
            result = timer_api.read_query(hass, connection, msg, grant, policy_read, now)
            if msg['capability'] == 'timers.status': await state['timers'].flush()
            _, current, _ = timer_api.access(hass, connection, msg, grant, policy_read)
            if current.get('timers') != mapping or hass.data.get(DOMAIN) is not state: raise ValueError('Mapping changed')
            # Re-evaluate permissions after persistence without releasing a formerly allowed fact.
            checked = timer_api.read_query(hass, connection, msg, grant, policy_read, now)
            if {f['subjectId'] for f in result['facts']} != {f['subjectId'] for f in checked['facts']}: raise ValueError('Authorization changed')
            connection.send_result(msg['id'], result)
        except Exception:
            connection.send_error(msg['id'], 'frame_denied', 'Timer/time unavailable or access denied')
        return
    if msg.get('capability') == 'weather.forecast':
        try:
            from zoneinfo import ZoneInfo
            _validate(msg, {'endpoint','version','capability','revision','answerType','arguments'})
            if type(msg['revision']) is not int or msg['revision'] != 1 or msg['answerType'] != 'weather.v1': raise ValueError('Unsupported forecast type')
            endpoint, engine = _engine(hass, connection, grant, msg, policy_read, now)
            snapshot = engine.effective_endpoint(endpoint,'weather.forecast')
            async def service(domain, name, data):
                kwargs = {'blocking': True, 'return_response': True}
                if hasattr(connection, 'context'): kwargs['context'] = connection.context(msg)
                return await hass.services.async_call(domain, name, data, **kwargs)
            def recheck():
                current, checked = _engine(hass, connection, grant, msg, policy_read, now)
                return current is endpoint and checked.effective_endpoint(current,'weather.forecast') == snapshot
            result = await engine.forecast(endpoint, msg['arguments'], service, ZoneInfo(hass.config.time_zone), recheck)
            connection.send_result(msg['id'], result)
        except Exception:
            connection.send_error(msg['id'], 'frame_denied', 'Assistant unavailable or access denied')
        return
    if msg.get('capability') != 'calendar.agenda':
        return query(hass, connection, msg, grant, policy_read, now)
    from datetime import date, datetime as dt, time, timedelta
    from zoneinfo import ZoneInfo
    import uuid
    try:
        _validate(msg, {'endpoint','version','capability','revision','answerType','arguments'})
        if type(msg['revision']) is not int or msg['revision'] != 1 or msg['answerType'] != 'calendar.v1':
            raise ValueError('Unsupported calendar type')
        endpoint, engine = _engine(hass, connection, grant, msg, policy_read, now)
        args = msg['arguments']; tz = ZoneInfo(hass.config.time_zone); current = now()
        start, end = calendar_window(args, current.astimezone(tz).date(), tz)
        metadata_snapshot = engine.effective_endpoint(endpoint,'calendar.agenda')
        subjects = engine._calendars(metadata_snapshot)
        if 'subjectId' in args:
            subjects = [s for s in subjects if s['id'] == args['subjectId']]
        if not subjects:
            raise ValueError('Unknown subject')
        entities = [s['fields']['state']['entity'] for s in subjects]
        kwargs = {'blocking': True, 'return_response': True}
        if hasattr(connection, 'context'):
            kwargs['context'] = connection.context(msg)
        response = await hass.services.async_call('calendar','get_events',{'entity_id':entities,'start_date_time':start.isoformat(),'end_date_time':end.isoformat()},**kwargs)
        current_endpoint, checked = _engine(hass, connection, grant, msg, policy_read, now)
        if checked.effective_endpoint(current_endpoint,'calendar.agenda') != metadata_snapshot:raise ValueError('Metadata changed')
        current_sources = {s['fields']['state']['entity'] for s in checked._calendars(checked.effective_endpoint(current_endpoint,'calendar.agenda'))}
        if current_endpoint is not endpoint or not set(entities) <= current_sources or any(not checked.permitted(eid) for eid in entities):
            raise ValueError('Authorization changed')
        facts=[]; evidence=[]; success=0; limitations={'provider_sync_unknown'}
        for subject in subjects:
            entity=subject['fields']['state']['entity']; item=response.get(entity) if isinstance(response,dict) else None
            if not isinstance(item,dict) or not isinstance(item.get('events'),list):
                limitations.add('missing_source'); continue
            source_complete=True
            evidence_id=str(uuid.uuid5(uuid.UUID(subject['id']),'calendar-evidence'))
            evidence.append({'id':evidence_id,'sourceRef':str(uuid.uuid5(uuid.UUID(subject['id']),'calendar-source')),'observedAt':None,'reportedAt':None,'freshnessBasis':'provider_response','maxAgeSeconds':None,'freshness':'current'})
            for index,event in enumerate(item['events']):
                if not isinstance(event,dict) or not isinstance(event.get('start'),str) or not isinstance(event.get('end'),str):
                    limitations.add('missing_source'); source_complete=False; continue
                try:
                    all_day = len(event['start'])==10 and len(event['end'])==10
                    if all_day:
                        event_start=dt.combine(date.fromisoformat(event['start']),time.min,tzinfo=tz); event_end=dt.combine(date.fromisoformat(event['end']),time.min,tzinfo=tz)
                    else:
                        event_start=dt.fromisoformat(event['start']); event_end=dt.fromisoformat(event['end'])
                        if event_start.tzinfo is None or event_end.tzinfo is None:raise ValueError('Unzoned event')
                    if event_start >= event_end: raise ValueError('Invalid event interval')
                    if event_start >= end or event_end <= start or (args.get('nextOnly') and event_end <= current):continue
                    title=event.get('summary') or '(untitled)'
                    if not isinstance(title,str): raise ValueError('Invalid title')
                    identity=str(uuid.uuid5(uuid.UUID(subject['id']),str(event.get('uid') or [event['start'],event['end'],title,index])))
                    facts.append({'id':identity,'subjectId':subject['id'],'calendarId':subject['id'],'eventId':identity,'title':title[:300], 'start':event['start'],'end':event['end'],'allDay':all_day,'ongoing':event_start <= current < event_end,'evidenceRefs':[evidence_id], '_sort':event_start.timestamp()})
                except (TypeError,ValueError,OverflowError):
                    limitations.add('missing_source'); source_complete=False
            success+=int(source_complete)
        facts.sort(key=lambda f:(f['_sort'],f['subjectId'],f['id']))
        truncated=len(facts)>100
        if truncated:limitations.add('truncated')
        facts=facts[:100]
        if args.get('nextOnly'):facts=facts[:1]
        for fact in facts:fact.pop('_sort')
        total=len(subjects)
        result={'version':1,'capability':'calendar.agenda','revision':1,'answerType':'calendar.v1','asOf':now().isoformat(),
                'availability':'ready' if success==total else 'partial' if success or facts else 'unavailable',
                'coverage':{'eligible':total,'evaluated':success,'missing':total-success,'unit':'event_source'},
                'summary':{'status':'events' if facts else 'none' if success==total else 'unknown','affected':len(facts)},
                'facts':facts,'evidence':evidence,'limitations':sorted(limitations),'intervalStart':start.isoformat(),'intervalEnd':end.isoformat(),
                'timezone':hass.config.time_zone,'providerSyncAt':None,'truncated':truncated}
        connection.send_result(msg['id'],result)
    except Exception:
        connection.send_error(msg['id'],'frame_denied','Assistant unavailable or access denied')


async def setup(hass, grant, register_command, policy_read):
    """Load metadata for diagnostics; register lifecycle-guarded commands."""
    import voluptuous as vol
    from homeassistant.components import websocket_api
    from homeassistant.core import callback

    state = hass.data[DOMAIN]
    from . import assistant_metadata, assistant_metadata_api
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)
    def source_verified(eid):
        entry = registry.async_get(eid)
        # Until an adapter establishes its report semantics, an unknown platform is not current evidence.
        return bool(entry and (entry.platform == 'esphome' or (entry.platform in ('xiaomi_ble', 'ruuvitag_ble', 'tplink') and eid.startswith('sensor.'))))
    state['assistant_source_verified'] = source_verified
    state['assistant_state_get'] = lambda eid: state_for_assistant(hass.states.get(eid), registry.async_get(eid))
    state['assistant_registry_id'] = lambda eid: getattr(registry.async_get(eid), 'id', None)
    from .assistant_refresh import MetadataRefresh, fingerprint
    def load_metadata():
        from .assistant_runtime_contract import load_directory as load_runtime
        from .timer_contract import load_directory as load_actions
        from .control_profiles import load_directory as load_controls
        runtime,runtime_errors=load_runtime(hass.config.path())
        commands,command_errors=load_actions(hass.config.path())
        controls,control_errors=load_controls(hass.config.path())
        loaded,errors=load_directory(hass.config.path())
        metadata,metadata_errors=assistant_metadata.load_metadata(hass.config.path())
        return {'control_artifacts':controls,'control_errors':control_errors,'assistant_runtime_artifacts':runtime,'assistant_runtime_errors':runtime_errors,
                'assistant_action_artifacts':commands,'assistant_action_errors':command_errors,
                'assistant_artifacts':loaded,'assistant_errors':errors,
                'assistant_metadata_artifacts':metadata,'assistant_metadata_errors':metadata_errors}
    def publish_metadata(snapshot):
        if hass.data.get(DOMAIN) is not state:return
        state.update({key:snapshot.get(key,{}) for key in ('control_artifacts','control_errors','assistant_runtime_artifacts','assistant_runtime_errors',
            'assistant_action_artifacts','assistant_action_errors','assistant_artifacts','assistant_errors',
            'assistant_metadata_artifacts','assistant_metadata_errors')})
        inventory=hass.states.get('sensor.domosenso_components') if hasattr(hass,'states') else None
        records=getattr(inventory,'attributes',{}).get('components') if inventory else None
        verified,mismatches=inventory_diagnostics(state['assistant_artifacts'],records)
        state['assistant_verified_artifacts']=verified
        state['assistant_errors']={**state['assistant_errors'],**mismatches}
        state['assistant_metadata_revision']=state.get('assistant_metadata_revision',0)+1
        if data:=state.get('studio_data'):data.cache.clear()
    refresh=MetadataRefresh(lambda:fingerprint(hass.config.path()),load_metadata,publish_metadata,hass.async_add_executor_job)
    state['assistant_refresh_close']=refresh.close
    async def reload_metadata():await refresh.check(force=True)
    await reload_metadata()
    if hasattr(hass,'bus'):
        from datetime import timedelta
        from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
        async def tick(_):await refresh.check()
        async def inventory_changed(_):await refresh.check(force=True)
        state.setdefault('subscriptions',set()).update([
            async_track_time_interval(hass,tick,timedelta(seconds=30)),
            async_track_state_change_event(hass,['sensor.domosenso_components'],inventory_changed)])
    command = websocket_api.websocket_command
    @command({vol.Required('type'): DOMAIN + '/assistant_capabilities', vol.Required('endpoint'): str, vol.Required('version'): int, vol.Required('supported'): list})
    @callback
    def cap(h, c, msg):
        capabilities(h, c, msg, grant, policy_read)
    @command({vol.Required('type'): DOMAIN + '/assistant_query', vol.Required('endpoint'): str, vol.Required('version'): int, vol.Required('capability'): str, vol.Required('revision'): int, vol.Required('answerType'): str, vol.Required('arguments'): dict})
    @websocket_api.async_response
    async def ask(h, c, msg):
        await query_async(h, c, msg, grant, policy_read)
    @command({vol.Required('type'): DOMAIN + '/assistant_reload'})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def reload(h, c, msg):
        if not c.user.is_active or not c.user.is_admin:
            c.send_error(msg['id'], 'frame_denied', 'Administrator access required')
            return
        await reload_metadata()
        if h.data.get(DOMAIN) is not state or not c.user.is_active or not c.user.is_admin:
            c.send_error(msg['id'], 'frame_denied', 'Administrator access required')
            return
        c.send_result(msg['id'], {'loaded': len(state['assistant_artifacts']), 'errors': state['assistant_errors']})
    assistant_metadata_api.setup(hass, grant, register_command, policy_read)
    for handler in (cap, ask, reload):
        register_command(hass, handler)
