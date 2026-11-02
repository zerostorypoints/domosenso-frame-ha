"""Fictional helper boundary; removing durability/CAS/auth checks breaks these tests."""
import asyncio
import copy
import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pkg = types.ModuleType('timer_package')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg


def timer_module():
    try:
        return importlib.import_module('timer_package.timers')
    except ModuleNotFoundError:
        pytest.fail('Shared timer executor is missing')


class Rig:
    def __init__(self, saved=None):
        self.now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        self.states = {'timer.demo': self.state('idle')}
        self.endpoint = {'id': 'frame', 'timers': {'writeEnabled': True, 'items': [{'entity': 'timer.demo', 'label': 'Demo', 'alarmDeviceId': 'tablet'}]}}
        self.saved = copy.deepcopy(saved)
        self.allowed = True
        self.calls = []
        self.events = []
        self.devices = {'tablet': {'state': 'approved', 'endpoint': 'frame'}}
        self.engine = timer_module().Timers('0123456789abcdef0123456789abcdef', self.states.get, self.persist, saved, now=lambda: self.now, emit=self.events.append, endpoints={'frame':self.endpoint})
    def state(self, value, duration=300, remaining=None, deadline=None):
        return types.SimpleNamespace(entity_id='timer.demo', state=value, attributes={'duration': str(timedelta(seconds=duration)), 'remaining': str(timedelta(seconds=remaining)) if remaining is not None else None, 'finishes_at': deadline}, last_updated=self.now)
    async def persist(self, value): self.saved = copy.deepcopy(value)
    def authorize(self):
        if not self.allowed: raise ValueError('revoked')
        return self.endpoint
    def fact(self):
        return self.engine.snapshot(self.endpoint, lambda _: self.allowed, self.devices)[0]
    def request(self, action='start', **changes):
        fact = self.fact()
        return {'version': 1, 'endpoint': 'frame', 'capability': 'timers.' + action, 'revision': 1, 'subjectId': fact['subjectId'], 'requestId': 'request-1', 'expectedStateRevision': fact['stateRevision'], 'expectedRunId': fact['runId'], 'arguments': {'durationSeconds': 60} if action == 'start' else {}, **changes}
    async def service(self, name, data, context):
        assert self.saved['requests'], 'service must follow durable pending record'
        self.calls.append((name, data))
        old = self.states['timer.demo']
        if name == 'start':
            duration = data.get('duration', 30)
            new = self.state('active', duration, deadline=(self.now + timedelta(seconds=duration)).isoformat())
        elif name == 'pause': new = self.state('paused', remaining=30)
        else: new = self.state('idle')
        self.states['timer.demo'] = new
        self.engine.observe('timer.demo', new, context=context)
    async def execute(self, request, service=None):
        return await self.engine.execute(request, self.authorize, lambda _: self.allowed, self.devices, service or self.service, 'own-context')


def run(fn): return asyncio.run(fn())


def test_start_durable_retry_conflicting_payload_and_stale_revision():
    async def case():
        r = Rig(); req = r.request()
        result = await r.execute(req)
        assert result['status'] == 'applied' and result['timer']['state'] == 'active'
        assert result['timer']['runId'] and result['timer']['alarmDeviceId'] == 'tablet'
        assert await r.execute(req) == result
        assert len(r.calls) == 1
        with pytest.raises(ValueError): await r.execute(req | {'arguments': {'durationSeconds': 61}})
        assert (await r.execute(req | {'requestId': 'different'}))['status'] == 'conflict'
        restart = Rig(r.saved); restart.states = r.states
        assert await restart.execute(req) == result
        assert restart.calls == []
    run(case)


def test_active_replacement_requires_new_explicit_confirmation_and_pause_resume_keeps_run():
    async def case():
        r = Rig(); first = await r.execute(r.request()); run_id = first['timer']['runId']
        replacement = r.request(requestId='replace')
        assert (await r.execute(replacement))['status'] == 'confirmation_required'
        paused = await r.execute(r.request('pause', requestId='pause'))
        assert paused['timer']['runId'] == run_id and paused['timer']['remainingSeconds'] == 30
        resumed = await r.execute(r.request('resume', requestId='resume'))
        assert resumed['timer']['runId'] == run_id
        new = await r.execute(r.request(requestId='confirmed', arguments={'durationSeconds': 90, 'confirmReplace': True}))
        assert new['timer']['runId'] != run_id
    run(case)


@pytest.mark.parametrize('duration', [True, 0, -1, 86401, 1.5, '60'])
def test_invalid_duration_never_calls_service(duration):
    async def case():
        r = Rig()
        with pytest.raises(ValueError): await r.execute(r.request(arguments={'durationSeconds': duration}))
        assert r.calls == []
    run(case)


def test_pending_restart_returns_unknown_without_replaying_service():
    async def case():
        r = Rig(); req = r.request()
        async def interrupted(*args): raise asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError): await r.execute(req, interrupted)
        restarted = Rig(r.saved)
        result = await restarted.execute(req)
        assert result['status'] == 'outcome_unknown' and restarted.calls == []
        assert (await restarted.execute(req))['status'] == 'outcome_unknown'
    run(case)


def test_revocation_and_remapping_while_waiting_for_lock_prevent_call():
    async def case():
        for revoke in ('grant', 'mapping', 'write'):
            r = Rig(); req = r.request()
            lock = r.engine.locks.setdefault('timer.demo', asyncio.Lock())
            await lock.acquire()
            pending = asyncio.create_task(r.execute(req)); await asyncio.sleep(0)
            if revoke == 'grant': r.allowed = False
            elif revoke == 'mapping': r.endpoint['timers']['items'].clear()
            else: r.endpoint['timers']['writeEnabled'] = False
            lock.release()
            with pytest.raises(ValueError): await pending
            assert r.calls == []
    run(case)


def test_revocation_during_persist_and_service_fails_closed():
    async def case():
        r = Rig(); req = r.request(); original = r.engine.persist
        async def persist(value): await original(value); r.allowed = False
        r.engine.persist = persist
        with pytest.raises(ValueError): await r.execute(req)
        assert r.calls == []
        r = Rig(); req = r.request()
        async def service(*args): await r.service(*args); r.allowed = False
        with pytest.raises(ValueError): await r.execute(req, service)
        assert len(r.calls) == 1
    run(case)


def test_external_transition_during_service_returns_conflict():
    async def case():
        r = Rig()
        async def service(*args):
            await r.service(*args)
            r.states['timer.demo'] = r.state('idle')
            r.engine.observe('timer.demo', r.states['timer.demo'], context='external')
        result = await r.execute(r.request(), service)
        assert result['status'] == 'conflict' and result['timer']['state'] == 'idle'
    run(case)


def test_downtime_completion_is_inferred_and_acknowledgement_cannot_touch_later_run():
    async def case():
        r = Rig(); result = await r.execute(r.request()); old_run = result['timer']['runId']
        restarted = Rig(r.saved); restarted.now += timedelta(minutes=5)
        fact = restarted.fact()
        assert fact['outcome'] == 'inferred_completed' and fact['runId'] == old_run
        await restarted.engine.acknowledge(restarted.endpoint, fact['subjectId'], old_run, lambda _: True, restarted.devices)
        assert restarted.fact()['acknowledged']
        new = await restarted.execute(restarted.request(requestId='new'))
        await restarted.engine.acknowledge(restarted.endpoint, fact['subjectId'], old_run, lambda _: True, restarted.devices)
        assert not restarted.fact()['acknowledged'] and restarted.fact()['runId'] == new['timer']['runId']
        restarted.devices['tablet']['state'] = 'revoked'
        assert restarted.fact()['alarmDeviceId'] is None
    run(case)


def test_idle_alone_is_not_confirmed_finish_and_receipts_persist():
    async def case():
        r = Rig(); assert r.fact()['outcome'] == 'none'
        result = await r.execute(r.request()); run_id = result['timer']['runId']
        r.states['timer.demo'] = r.state('idle'); r.engine.observe('timer.demo', r.states['timer.demo'])
        assert r.fact()['outcome'] == 'unknown'
        r.engine.observe('timer.demo', r.states['timer.demo'], event='timer.finished')
        assert r.fact()['outcome'] == 'completed'
        await r.engine.acknowledge(r.endpoint, r.fact()['subjectId'], run_id, lambda _: True, r.devices)
        assert Rig(r.saved).fact()['acknowledged']
    run(case)


def test_result_persistence_failure_never_claims_success_and_retry_never_repeats():
    async def case():
        r=Rig();req=r.request();calls=0
        async def persist(value):
            nonlocal calls
            calls+=1
            if calls>1: raise OSError('disk failure')
            r.saved=copy.deepcopy(value)
        r.engine.persist=persist
        with pytest.raises(OSError): await r.execute(req)
        assert len(r.calls)==1
        r.engine.persist=r.persist
        assert (await r.execute(req))['status']=='outcome_unknown'
        assert len(r.calls)==1
    run(case)


def test_external_transition_during_result_persistence_is_conflict():
    async def case():
        r=Rig();req=r.request();count=0
        async def persist(value):
            nonlocal count
            await r.persist(value);count+=1
            if count==2:
                r.states['timer.demo']=r.state('idle')
                r.engine.observe('timer.demo',r.states['timer.demo'],context='external')
        r.engine.persist=persist
        result=await r.execute(req)
        assert result['status']=='conflict' and result['timer']['state']=='idle'
        assert r.saved['requests']['frame:request-1']['result']==result
    run(case)


def test_replacement_in_same_second_still_gets_new_run():
    async def case():
        r=Rig();first=await r.execute(r.request())
        second=await r.execute(r.request(requestId='second',arguments={'durationSeconds':60,'confirmReplace':True}))
        assert second['status']=='applied' and second['timer']['runId']!=first['timer']['runId']
    run(case)


def test_target_must_be_unique_across_endpoint_views():
    r=Rig()
    assert r.fact()['alarmDeviceId']=='tablet'
    r.devices['tablet']['state']='revoked'
    assert r.fact()['alarmDeviceId'] is None


def test_duplicate_completion_event_preserves_revision_and_receipt():
    r=Rig();run(lambda:r.execute(r.request()))
    r.states['timer.demo']=r.state('idle')
    r.engine.observe('timer.demo',r.states['timer.demo'],event='timer.finished')
    first=r.fact()
    r.engine.observe('timer.demo',r.states['timer.demo'],event='timer.finished')
    assert r.fact()['stateRevision']==first['stateRevision']


def test_restored_helper_temporarily_unavailable_does_not_erase_missed_deadline():
    r=Rig();run(lambda:r.execute(r.request()))
    restarted=Rig(r.saved);restarted.now+=timedelta(minutes=5)
    restarted.states['timer.demo']=restarted.state('unavailable')
    assert restarted.fact()['state']=='unknown'
    restarted.states['timer.demo']=restarted.state('idle')
    assert restarted.fact()['outcome']=='inferred_completed'


def test_old_run_receipt_is_durable_without_acknowledging_replacement():
    async def case():
        r=Rig();first=await r.execute(r.request())
        await r.execute(r.request(requestId='new',arguments={'durationSeconds':60,'confirmReplace':True}))
        current=r.fact()
        await r.engine.acknowledge(r.endpoint,first['timer']['subjectId'],first['timer']['runId'],lambda _:True,r.devices)
        assert r.saved['receipts'][first['timer']['subjectId']+':'+first['timer']['runId']]
        assert r.fact()==current
    run(case)


def test_same_second_external_restart_uses_ha_report_identity():
    r=Rig();run(lambda:r.execute(r.request()))
    first=r.fact()
    r.states['timer.demo'].last_reported=r.now+timedelta(microseconds=1)
    r.engine.observe('timer.demo',r.states['timer.demo'],context='external',event='timer.restarted')
    assert r.fact()['runId']!=first['runId']


def test_internal_control_timer_is_not_selectable_without_component_metadata():
    r=Rig();state=r.state('idle');state.entity_id='timer.demo_controls'
    assert not timer_module().eligible(state)


def test_mapping_invalidation_advances_revision_without_replacing_run():
    async def case():
        r=Rig();await r.execute(r.request());before=r.fact()
        assert hasattr(r.engine,'mapping_changed'), 'Mapping revision invalidation missing'
        await r.engine.mapping_changed()
        after=r.fact()
        assert after['runId']==before['runId'] and after['stateRevision']!=before['stateRevision']
        assert r.saved['observations']['timer.demo']['stateRevision']==after['stateRevision']
    run(case)


@pytest.mark.parametrize('pending', [False,True])
def test_permission_revoked_while_saving_early_result_never_releases_fact(pending):
    async def case():
        r=Rig();req=r.request()
        if pending:
            async def interrupted(*_): raise asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):await r.execute(req,interrupted)
        else:
            req['expectedStateRevision']='stale'
        async def persist(value):
            await r.persist(value)
            if any(record.get('result') for record in value['requests'].values()):r.allowed=False
        r.engine.persist=persist
        with pytest.raises(ValueError):await r.execute(req)
        assert r.calls==[]
    run(case)


def test_repeated_restored_reports_and_config_writes_keep_run_and_receipt():
    async def case():
        r = Rig(); before = (await r.execute(r.request()))['timer']
        await r.engine.acknowledge(r.endpoint, before['subjectId'], before['runId'], lambda _: True, r.devices)
        restored = Rig(r.saved); restored.states.update(copy.deepcopy(r.states))
        for report in range(1, 4):
            state = restored.states['timer.demo']
            state.last_reported = r.now + timedelta(microseconds=report)
            state.attributes['friendly_name'] = f'Renamed {report}'
            restored.engine.observe('timer.demo', state, event='state_changed')
            assert restored.fact()['runId'] == before['runId']
            assert restored.fact()['acknowledged']
        await restored.engine.flush()
        again = Rig(restored.saved); again.states.update(restored.states)
        assert again.fact()['runId'] == before['runId'] and again.fact()['acknowledged']
    run(case)
