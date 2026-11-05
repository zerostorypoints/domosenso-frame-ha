"""Authenticated classic HA routes: no pairing, control grants or service calls."""
import asyncio
import copy
from datetime import timedelta
import pytest
from test_timer_api import api_rig, module, Connection
from test_timers import run, timer_module


def rig():
    r, h, c, _ = api_rig()
    r.endpoint['timers']['writeEnabled'] = False
    r.endpoint['timers']['items'][0]['alarmDeviceId'] = None
    h.data['domosenso_frame'].update(endpoints={'frame': r.endpoint}, timer_source_verified=lambda _: True)
    c.user.permissions.check_entity = lambda _, policy: policy == 'read'
    return r, h, c


def complete(r):
    r.states['timer.demo'] = r.state('active', deadline=r.now.isoformat())
    r.fact()
    r.states['timer.demo'] = r.state('idle')
    r.engine.observe('timer.demo', r.states['timer.demo'], event='timer.finished')
    f = r.fact()
    return {'id': 1, 'version': 1, 'subjectId': f['subjectId'], 'runId': f['runId']}


def snapshot(h, c, entities=None, **extra):
    module().browser_snapshot(h, c, {'id': 1, 'version': 1, 'entities': entities or ['timer.demo'], **extra}, 'read')


def test_snapshot_only_requested_permitted_mapped_core_helpers_with_exact_server_anchor():
    r,h,c = rig()
    r.states['timer.demo'] = r.state('active', deadline=(r.now + timedelta(seconds=10)).isoformat())
    tick = r.now
    def now():
        nonlocal tick
        value = tick; tick += timedelta(seconds=1); return value
    r.engine.now = now
    snapshot(h,c,['timer.demo','timer.unmapped'])
    result = c.results[-1]
    assert result['asOf'] == r.now.isoformat()
    assert result['installationId'] == r.engine.installation
    assert result['timers'][0]['timer']['remainingSeconds'] == 10
    assert [(v['entity'],v['label']) for v in result['timers']] == [('timer.demo','Demo')]
    c.user.permissions.check_entity = lambda *_: False
    snapshot(h,c); assert c.results[-1]['timers'] == []
    c.user.is_active = False
    snapshot(h,c); assert c.errors
    assert not r.calls


@pytest.mark.parametrize('change', ['mapping','registry','internal','runtime','targets'])
def test_snapshot_rejects_unmapped_unverified_excluded_or_inconsistent_targets(change):
    r,h,c = rig()
    if change == 'mapping': r.endpoint['timers']['items'] = []
    if change == 'registry': h.data['domosenso_frame']['timer_source_verified'] = lambda _: False
    if change == 'internal': r.states['timer.demo'].attributes['component'] = 'ramka'
    if change == 'runtime': r.endpoint['studio'] = {'runtime': {'timer': 'timer.demo'}}
    if change == 'targets':
        other = copy.deepcopy(r.endpoint); other['timers']['items'][0]['alarmDeviceId'] = 'tablet'
        h.data['domosenso_frame']['endpoints']['other'] = other
    snapshot(h,c)
    assert c.errors or c.results[-1]['timers'] == []


@pytest.mark.parametrize('extra', [{'version':True},{'version':2},{'endpoint':'frame'},{'entities':['timer.demo']*2},{'entities':['timer.x'+str(i) for i in range(17)]},{'entities':['light.demo']},{'entities':'timer.demo'}])
def test_strict_browser_wire(extra):
    r,h,c = rig(); snapshot(h,c,**extra); assert c.errors and not c.results


def test_claim_atomic_across_tabs_users_durable_separate_from_shared_ack_and_restart():
    async def case():
        r,h,c = rig(); req = complete(r); other = Connection()
        await asyncio.gather(module().browser_claim(h,c,req,'read'), module().browser_claim(h,other,req,'read'))
        assert sorted([c.results[-1]['claimed'],other.results[-1]['claimed']]) == [False,True]
        assert not r.fact()['acknowledged'] and not r.saved['receipts']
        assert r.saved['browserDeliveries']
        r.engine = timer_module().Timers(r.engine.installation,r.states.get,r.persist,r.saved,now=lambda:r.now)
        h.data['domosenso_frame']['timers'] = r.engine
        await module().browser_claim(h,c,req,'read'); assert not c.results[-1]['claimed']
        await module().browser_acknowledge(h,c,req,'read'); assert c.results[-1]['timer']['acknowledged']
        assert r.saved['receipts'] and not r.calls
    run(case)


@pytest.mark.parametrize('change', ['target','revoked_target','old','future','wrong_run','ack','active','cancelled','unknown','missing_mapping','denied','inactive','registry'])
def test_claim_requires_recent_current_unacknowledged_completion_and_current_access(change):
    async def case():
        r,h,c = rig(); req = complete(r)
        if change in ('target','revoked_target'):
            r.endpoint['timers']['items'][0]['alarmDeviceId'] = 'tablet'
            if change == 'revoked_target': r.devices['tablet']['state'] = 'revoked'
        if change == 'old': r.now += timedelta(seconds=61)
        if change == 'future': r.now -= timedelta(seconds=1)
        if change == 'wrong_run': req['runId'] = 'old-run'
        if change == 'ack': await r.engine.acknowledge(r.endpoint,req['subjectId'],req['runId'],lambda _:True,r.devices)
        if change == 'active': r.states['timer.demo'] = r.state('active',deadline=(r.now+timedelta(seconds=30)).isoformat())
        if change in ('cancelled','unknown'): r.engine.data['observations']['timer.demo']['outcome'] = change
        if change == 'missing_mapping': r.endpoint['timers']['items'] = []
        if change == 'denied': c.user.permissions.check_entity = lambda *_:False
        if change == 'inactive': c.user.is_active = False
        if change == 'registry': h.data['domosenso_frame']['timer_source_verified'] = lambda _:False
        await module().browser_claim(h,c,req,'read')
        assert c.errors or c.results[-1]['claimed'] is False
        assert not r.engine.data.get('browserDeliveries') and not r.calls
    run(case)


@pytest.mark.parametrize('change', ['mapping','target','revoke','registry','run','age','unload'])
def test_claim_rechecks_after_save_and_never_replays_consumed_delivery(change):
    async def case():
        r,h,c = rig(); req = complete(r)
        async def persist(value):
            await r.persist(value)
            if change == 'mapping': r.endpoint['timers']['items'][0]['label'] = 'Changed'
            if change == 'target': r.endpoint['timers']['items'][0]['alarmDeviceId'] = 'tablet'
            if change == 'revoke': c.user.is_active = False
            if change == 'registry': h.data['domosenso_frame']['timer_source_verified'] = lambda _:False
            if change == 'run': r.states['timer.demo'] = r.state('active',deadline=(r.now+timedelta(seconds=30)).isoformat())
            if change == 'age': r.now += timedelta(seconds=61)
            if change == 'unload': h.data['domosenso_frame'] = {}
        r.engine.persist = persist
        await module().browser_claim(h,c,req,'read')
        assert c.errors or not c.results[-1]['claimed']
        assert r.saved['browserDeliveries'] and not r.calls
    run(case)


@pytest.mark.parametrize('written', [False, True])
def test_pending_storage_failure_never_authorizes_sound_or_duplicate(written):
    async def case():
        r,h,c = rig(); req = complete(r)
        async def fail(value):
            if written: await r.persist(value)
            raise OSError('storage failure')
        r.engine.persist = fail
        await module().browser_claim(h,c,req,'read'); assert c.errors and not c.results
        r.engine.persist = r.persist
        await module().browser_claim(h,c,req,'read'); assert not c.results[-1]['claimed']
        if written:
            h.data['domosenso_frame']['timers'] = timer_module().Timers(r.engine.installation,r.states.get,r.persist,r.saved,now=lambda:r.now)
            await module().browser_claim(h,c,req,'read'); assert not c.results[-1]['claimed']
    run(case)


def test_ack_old_run_does_not_ack_replacement_and_rechecks_permissions_after_save():
    async def case():
        r,h,c = rig(); req = complete(r)
        r.states['timer.demo'] = r.state('active',deadline=(r.now+timedelta(seconds=30)).isoformat()); r.fact()
        await module().browser_acknowledge(h,c,req,'read')
        assert not c.results[-1]['timer']['acknowledged']
        req['runId'] = r.fact()['runId']; c.results.clear()
        async def revoke(value):
            await r.persist(value); c.user.is_active = False
        r.engine.persist = revoke
        await module().browser_acknowledge(h,c,req,'read'); assert c.errors and not c.results
    run(case)


@pytest.mark.parametrize('change', ['mapping','revoke','registry'])
def test_claim_rechecks_authorization_after_waiting_for_helper_lock(change):
    async def case():
        r,h,c = rig(); req = complete(r)
        lock = r.engine.locks.setdefault('timer.demo', asyncio.Lock())
        await lock.acquire()
        task = asyncio.create_task(module().browser_claim(h,c,req,'read'))
        await asyncio.sleep(0)
        if change == 'mapping': r.endpoint['timers']['items'] = []
        if change == 'revoke': c.user.permissions.check_entity = lambda *_:False
        if change == 'registry': h.data['domosenso_frame']['timer_source_verified'] = lambda _:False
        lock.release(); await task
        assert c.errors and not c.results and not r.engine.data['browserDeliveries']
    run(case)


def test_inferred_completion_at_exact_sixty_seconds_can_claim_and_ack_during_save_suppresses_sound():
    async def case():
        r,h,c = rig(); req = complete(r)
        r.engine.data['observations']['timer.demo']['outcome'] = 'inferred_completed'
        r.now += timedelta(seconds=60)
        await module().browser_claim(h,c,req,'read'); assert c.results[-1]['claimed']
        assert c.results[-1]['asOf'] == r.now.isoformat()
        r,h,c = rig(); req = complete(r)
        async def acknowledge_during_save(value):
            await r.persist(value)
            r.engine.data['receipts'][req['subjectId']+':'+req['runId']] = r.now.isoformat()
        r.engine.persist = acknowledge_during_save
        await module().browser_claim(h,c,req,'read'); assert not c.results[-1]['claimed']
    run(case)


def test_mapping_revision_change_even_if_content_restored_fails_closed():
    async def case():
        r,h,c = rig(); req = complete(r)
        async def change(value):
            await r.persist(value)
            r.engine.mapping_revision += 1
        r.engine.persist = change
        await module().browser_claim(h,c,req,'read'); assert c.errors and not c.results
    run(case)


def test_browser_ack_retry_confirms_persistence_after_failed_save():
    async def case():
        r,h,c = rig(); req = complete(r)
        async def fail(_): raise OSError('storage failure')
        r.engine.persist = fail
        await module().browser_acknowledge(h,c,req,'read'); assert c.errors and not c.results
        await module().browser_acknowledge(h,c,req,'read'); assert len(c.errors) == 2 and not c.results
        r.engine.persist = r.persist
        await module().browser_acknowledge(h,c,req,'read')
        assert c.results[-1]['timer']['acknowledged'] and r.saved['receipts']
    run(case)


@pytest.mark.parametrize('change', [{'runId':''},{'subjectId':False},{'endpoint':'frame'},{'version':True},{'arguments':{'service':'start'}}])
def test_claim_and_ack_strict_request_validation(change):
    async def case():
        r,h,c = rig(); req = complete(r)|change
        await module().browser_claim(h,c,req,'read')
        await module().browser_acknowledge(h,c,req,'read')
        assert len(c.errors) == 2 and not c.results and not r.calls
    run(case)


def test_reapproved_device_on_unmapped_endpoint_is_not_advertised_or_browser_fallback():
    async def case():
        r,h,c=rig()
        r.endpoint['timers']['items'][0]['alarmDeviceId']='tablet'
        other={'id':'other','timers':{'writeEnabled':False,'items':[]}}
        r.engine.endpoints['other']=other
        h.data['domosenso_frame']['endpoints']=r.engine.endpoints
        request=complete(r)
        assert r.fact()['alarmDeviceId']=='tablet'
        r.devices['tablet'].update(state='revoked')
        assert r.fact()['alarmDeviceId'] is None
        # A later approved binding must not make retained assignments reachable.
        r.devices['tablet'].update(state='approved',endpoint='other')
        assert r.fact()['alarmDeviceId'] is None
        await module().browser_claim(h,c,request,'read')
        assert not c.results[-1]['claimed']
        assert other['timers']['items']==[]
        other['timers']['items']=copy.deepcopy(r.endpoint['timers']['items'])
        assert r.fact()['alarmDeviceId']=='tablet'
        await module().browser_claim(h,c,request,'read')
        assert not c.results[-1]['claimed']
    run(case)
