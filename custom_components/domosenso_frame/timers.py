"""Durable, fixed-purpose bridge over HA core timers (not a second scheduler).

The per-helper lock covers bridge calls only. State event contexts detect observed
external changes; ordinary HA actions retain HA ordering. Pending requests are
never replayed, including when an interrupted call might have reached HA.
"""
import asyncio
import copy
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone

ACTIONS = ('timers.start', 'timers.pause', 'timers.resume', 'timers.cancel')


def instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def seconds(value):
    try:
        if not isinstance(value, str): return None
        parts = value.split(':')
        if len(parts) != 3: return None
        result = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        return max(0, math.ceil(result)) if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def eligible(state, endpoint=None):
    if not state or not re.fullmatch(r'timer\.[a-z0-9_]+', state.entity_id): return False
    # Component-owned navigation/rotation helpers are never user countdowns.
    if state.attributes.get('component') == 'ramka': return False
    if state.entity_id.endswith(('_controls', '_rotation')): return False
    runtime = (endpoint or {}).get('studio', {}).get('runtime', {})
    return state.entity_id not in json.dumps(runtime)


def validate_mapping(value, states=None, endpoint=None):
    if not isinstance(value, dict) or set(value) != {'writeEnabled', 'items'} or type(value['writeEnabled']) is not bool:
        raise ValueError('Invalid timer mapping')
    if not isinstance(value['items'], list) or len(value['items']) > 16: raise ValueError('Too many timers')
    seen = set()
    for row in value['items']:
        if not isinstance(row, dict) or set(row) != {'entity', 'label', 'alarmDeviceId'}: raise ValueError('Invalid timer row')
        eid, label, device = row['entity'], row['label'], row['alarmDeviceId']
        if not isinstance(eid, str) or not re.fullmatch(r'timer\.[a-z0-9_]+', eid) or eid in seen: raise ValueError('Invalid timer entity')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or any(ord(c) < 32 for c in label): raise ValueError('Invalid timer label')
        if device is not None and (not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', device)): raise ValueError('Invalid alarm target')
        if states is not None and not eligible(states.get(eid), endpoint): raise ValueError('Select an existing user timer helper')
        seen.add(eid)
    return copy.deepcopy(value)


def validate_targets(endpoints):
    targets = {}
    for endpoint in endpoints.values():
        mapping = validate_mapping(endpoint.get('timers', {'writeEnabled':False,'items':[]}))
        for row in mapping['items']:
            if row['entity'] in targets and targets[row['entity']] != row['alarmDeviceId']:
                raise ValueError('Shared timer must use the same alarm device on every frame')
            targets[row['entity']] = row['alarmDeviceId']


def validate_request(req):
    required = {'version', 'endpoint', 'capability', 'revision', 'subjectId', 'requestId', 'expectedStateRevision', 'expectedRunId', 'arguments'}
    if not isinstance(req, dict) or set(req) - required - {'id', 'type'} or not required <= req.keys(): raise ValueError('Invalid timer request')
    if type(req['version']) is not int or req['version'] != 1 or type(req['revision']) is not int or req['revision'] != 1 or req['capability'] not in ACTIONS: raise ValueError('Unsupported timer action')
    for field in ('endpoint', 'subjectId', 'requestId', 'expectedStateRevision'):
        if not isinstance(req[field], str) or not 1 <= len(req[field]) <= (40 if field == 'endpoint' else 128): raise ValueError('Invalid request identity')
    if req['expectedRunId'] is not None and (not isinstance(req['expectedRunId'], str) or not 1 <= len(req['expectedRunId']) <= 128): raise ValueError('Invalid run identity')
    args = req['arguments']
    if not isinstance(args, dict): raise ValueError('Invalid arguments')
    if req['capability'] == 'timers.start':
        if set(args) - {'durationSeconds', 'confirmReplace'} or type(args.get('durationSeconds')) is not int or not 1 <= args['durationSeconds'] <= 86400: raise ValueError('Invalid duration')
        if 'confirmReplace' in args and type(args['confirmReplace']) is not bool: raise ValueError('Invalid confirmation')
    elif args: raise ValueError('Unexpected arguments')


class Timers:
    def __init__(self, installation, state_get, persist, saved=None, *, now=lambda: datetime.now(timezone.utc), emit=lambda _: None):
        self.installation, self.state_get, self.persist = installation, state_get, persist
        self.now, self.emit = now, emit
        self.data = copy.deepcopy(saved) if saved is not None else {'version': 1, 'observations': {}, 'requests': {}, 'receipts': {}}
        if self.data.get('version') != 1 or any(not isinstance(self.data.get(k), dict) for k in ('observations', 'requests', 'receipts')): raise ValueError('Invalid timer ledger')
        self.data.setdefault('browserDeliveries', {})
        if not isinstance(self.data['browserDeliveries'], dict): raise ValueError('Invalid browser delivery ledger')
        self.data.setdefault('runs', {})
        for entity, record in self.data['observations'].items():
            if record.get('runId'): self.data['runs'][self.subject(entity) + ':' + record['runId']] = True
        self.locks, self.inflight = {}, {}
        self.save_lock = asyncio.Lock()
        self.restoring = set(self.data['observations'])
        self.closed = False
        self.mapping_revision = 0

    def subject(self, entity):
        return str(uuid.uuid5(uuid.UUID(self.installation), 'shared-timer:' + entity))

    async def flush(self):
        async with self.save_lock:
            await self.persist(copy.deepcopy(self.data))

    def observe(self, entity, state, *, context=None, event=None):
        if self.closed: return
        previous = self.data['observations'].get(entity)
        attrs = state.attributes if state else {}
        status = state.state if state and state.state in ('idle', 'active', 'paused') else 'unknown'
        deadline = instant(attrs.get('finishes_at')) if status == 'active' else None
        signature = [status, attrs.get('duration'), attrs.get('remaining'), attrs.get('finishes_at'), str(getattr(state, 'last_reported', ''))]
        restoring = entity in self.restoring
        if restoring and status == 'unknown': return
        self.restoring.discard(entity)
        event_outcome = {'timer.finished':'completed','timer.cancelled':'cancelled'}.get(event)
        if previous and event_outcome == previous['outcome'] and previous['signature'] == signature: return
        changed = previous is None or previous['signature'] != signature
        operation = self.inflight.get(entity)
        force_start = bool(operation and context == operation['context'] and operation['action'] == 'start' and not operation.get('observed') and status == 'active')
        if not changed and event not in ('timer.finished', 'timer.cancelled') and not force_start: return
        if operation and context != operation['context']: operation['external'] = True
        record = copy.deepcopy(previous) if previous else {'runId': None, 'outcome': 'none', 'deadline': None}
        prior_state = record.get('state')
        new_run = status == 'active' and (prior_state not in ('active', 'paused') or (prior_state == 'active' and changed and not restoring))
        if force_start:
            new_run = True
            operation['observed'] = True
        if new_run:
            record.update(runId=str(uuid.uuid4()), outcome='none')
        elif status == 'paused' and not record['runId']:
            record.update(runId=str(uuid.uuid4()), outcome='none')
        if status == 'idle' and prior_state in ('active', 'paused'):
            old_deadline = instant(record.get('deadline'))
            record['outcome'] = 'inferred_completed' if restoring and prior_state == 'active' and old_deadline and old_deadline <= self.now() else 'unknown'
        if event in ('timer.finished', 'timer.cancelled') and status == 'idle' and record['runId']:
            record['outcome'] = 'completed' if event == 'timer.finished' else 'cancelled'
        if status == 'active': record['deadline'] = deadline.isoformat() if deadline else None
        elif status == 'paused': record['deadline'] = None
        record.update(state=status, durationSeconds=seconds(attrs.get('duration')), remainingSeconds=seconds(attrs.get('remaining')) if status == 'paused' else 0,
                      signature=signature, stateRevision=str(uuid.uuid4()))
        if record['runId']: self.data['runs'][self.subject(entity) + ':' + record['runId']] = True
        self.data['observations'][entity] = record
        self.emit(entity)

    def rows(self, endpoint, permitted, devices, write=False):
        mapping = validate_mapping(endpoint.get('timers', {'writeEnabled': False, 'items': []}))
        if self.closed or (write and not mapping['writeEnabled']): return []
        result = []
        for row in mapping['items']:
            eid = row['entity']
            if not permitted(eid) or not eligible(self.state_get(eid), endpoint): continue
            result.append(row)
        return result

    def fact(self, row, devices, *, as_of=None):
        as_of = self.now() if as_of is None else as_of
        eid = row['entity']; self.observe(eid, self.state_get(eid))
        record = self.data['observations'][eid]
        subject = self.subject(eid)
        remaining = record['remainingSeconds']
        if record['state'] == 'active':
            deadline = instant(record['deadline'])
            remaining = max(0, math.ceil((deadline - as_of).total_seconds())) if deadline else None
        target = row.get('alarmDeviceId')
        if devices.get(target, {}).get('state') != 'approved': target = None
        visible_state = record['state']
        live = self.state_get(eid)
        if not live or live.state not in ('idle','active','paused'):
            visible_state = 'unknown'
            remaining = None
        return {'id': subject, 'subjectId': subject, 'evidenceRefs': [subject], 'state': visible_state,
                'durationSeconds': record['durationSeconds'], 'remainingSeconds': remaining, 'deadline': record['deadline'],
                'runId': record['runId'], 'stateRevision': record['stateRevision'], 'outcome': record['outcome'],
                'alarmDeviceId': target, 'acknowledged': bool(record['runId'] and self.data['receipts'].get(subject + ':' + record['runId']))}

    def snapshot(self, endpoint, permitted, devices, *, as_of=None):
        as_of = self.now() if as_of is None else as_of
        return [self.fact(row, devices, as_of=as_of) for row in self.rows(endpoint, permitted, devices)]

    def resolve(self, endpoint, subject, permitted, devices, write=False):
        row = next((r for r in self.rows(endpoint, permitted, devices, write) if self.subject(r['entity']) == subject), None)
        if row is None: raise ValueError('Timer not authorized')
        return row

    async def execute(self, req, authorize, permitted, devices, service, context):
        validate_request(req)
        endpoint = authorize()
        if endpoint['id'] != req['endpoint']: raise ValueError('Endpoint mismatch')
        initial = copy.deepcopy(endpoint.get('timers'))
        row = self.resolve(endpoint, req['subjectId'], permitted, devices, True)
        entity = row['entity']
        def recheck():
            current = authorize()
            if current['id'] != req['endpoint'] or current.get('timers') != initial: raise ValueError('Timer mapping changed')
            return self.resolve(current, req['subjectId'], permitted, devices, True)
        async with self.locks.setdefault(entity, asyncio.Lock()):
            row = recheck()
            key = req['endpoint'] + ':' + req['requestId']
            fingerprint = hashlib.sha256(json.dumps({k:v for k,v in req.items() if k not in ('id','type')}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            async def finish(status):
                result = await self.result(req, row, devices, key, status)
                recheck()
                return result
            prior = self.data['requests'].get(key)
            if prior:
                if prior['fingerprint'] != fingerprint: raise ValueError('Request ID reused with different content')
                if prior.get('result'): return copy.deepcopy(prior['result'])
                return await finish('outcome_unknown')
            # Never evict deduplication tombstones: exhausting the bound disables new writes
            # until an administrator archives the ledger, rather than replaying old starts.
            if len(self.data['requests']) >= 100000: raise ValueError('Timer request ledger full')
            fact = self.fact(row, devices)
            action = req['capability'].split('.')[1]
            status = None
            if fact['stateRevision'] != req['expectedStateRevision'] or fact['runId'] != req['expectedRunId']: status = 'conflict'
            elif action == 'start' and fact['state'] in ('active', 'paused') and not req['arguments'].get('confirmReplace'): status = 'confirmation_required'
            elif fact['state'] == 'unknown' or (action == 'pause' and fact['state'] != 'active') or (action == 'resume' and fact['state'] != 'paused'): status = 'conflict'
            self.data['requests'][key] = {'fingerprint': fingerprint, 'result': None}
            await self.flush()  # durable pending BEFORE any service await
            recheck()
            if status: return await finish(status)
            # A direct HA change while the pending record was being persisted also invalidates CAS.
            current = self.fact(row, devices)
            if current['stateRevision'] != fact['stateRevision']: return await finish('conflict')
            operation = {'context': context, 'action': action, 'external': False}
            self.inflight[entity] = operation
            try:
                data = {'entity_id': entity}
                if action == 'start': data['duration'] = req['arguments']['durationSeconds']
                await service('start' if action == 'resume' else action, data, context)
                recheck()
                self.observe(entity, self.state_get(entity), context=context)
                current = self.fact(row, devices)
                expected = {'start':'active', 'resume':'active', 'pause':'paused', 'cancel':'idle'}[action]
                status = 'conflict' if operation['external'] or current['state'] != expected else 'applied'
                if action == 'cancel' and status == 'applied':
                    self.observe(entity, self.state_get(entity), context=context, event='timer.cancelled')
                result = await self.result(req, row, devices, key, status)
                recheck()
                return result
            except asyncio.CancelledError:
                raise  # pending survives unload/restart; no retry ever invokes the service
            except Exception:
                recheck()  # suppress facts if permission was revoked
                return await finish('outcome_unknown')
            finally:
                self.inflight.pop(entity, None)

    async def result(self, req, row, devices, key, status):
        result = {'version': 1, 'capability': req['capability'], 'revision': 1, 'resultType': 'timer-result.v1',
                  'requestId': req['requestId'], 'status': status, 'asOf': self.now().isoformat(), 'timer': self.fact(row, devices)}
        self.data['requests'][key]['result'] = copy.deepcopy(result)
        try:
            await self.flush()
            current = self.fact(row, devices)
            if current['stateRevision'] != result['timer']['stateRevision']:
                return await self.result(req, row, devices, key, 'conflict')
        except BaseException:
            self.data['requests'][key]['result'] = None
            raise
        return result

    async def acknowledge(self, endpoint, subject, run, permitted, devices):
        row = self.resolve(endpoint, subject, permitted, devices)
        fact = self.fact(row, devices)
        # An old acknowledgement cannot affect a replacement run.
        key = subject + ':' + run
        if self.data['runs'].get(key):
            if key not in self.data['receipts']:
                self.data['receipts'][key] = self.now().isoformat()
                if fact['runId'] == run: self.data['observations'][row['entity']]['stateRevision'] = str(uuid.uuid4())
            # Confirm even a retry: an earlier write may have failed after the
            # in-memory receipt was recorded. Never report an unpersisted ack.
            await self.flush()
            self.emit(row['entity'])
        return {'version': 1, 'asOf': self.now().isoformat(), 'timer': self.fact(row, devices)}

    async def mapping_changed(self):
        self.mapping_revision += 1
        for record in self.data['observations'].values():
            record['stateRevision'] = str(uuid.uuid4())
        await self.flush()
        self.emit(None)

    def close(self):
        self.closed = True
