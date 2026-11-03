"""Real resolver/executor with fictional state and durable-store boundaries."""
import asyncio
import copy
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_timers import run


def module(name):
    try: return importlib.import_module('timer_package.' + name)
    except ModuleNotFoundError: pytest.fail('Missing controls implementation: ' + name)


class Rig:
    def __init__(self, saved=None, capacity=128):
        self.now=datetime(2026,11,3,12,tzinfo=timezone.utc)
        self.states={}; self.registry={}; self.areas={'office':{'name':'Office','aliases':['Study']},'terrace':{'name':'Terrace','aliases':[]}}
        self.artifacts={}; self.allowed=True; self.denied=set(); self.calls=[]; self.saved=copy.deepcopy(saved); self.on_save=lambda:None; self.on_call=lambda:None
        self.endpoint={'id':'frame','display':{},'studio':{'lights':[{'entity':'light.desk','label':'Desk'}]},'assistant':{'subjects':[],'sensitive':False}}
        self.add('light.desk','off',area='office')
        p=module('control_profiles')
        self.resolver=p.Profiles(self.states.get,lambda:list(self.states.values()),lambda:self.artifacts,self.registry.get,lambda:self.areas)
        self.engine=module('control_actions').Controls('0123456789abcdef0123456789abcdef',self.resolver,self.persist,saved,now=lambda:self.now,capacity=capacity)
    def add(self,eid,state,area=None,**attrs):
        self.states[eid]=SimpleNamespace(entity_id=eid,state=state,attributes=attrs,last_updated=self.now,context=SimpleNamespace(id='old'))
        self.registry[eid]={'id':eid+'-registration','platform':'demo','unique_id':eid,'area_id':area,'device_area_id':None,'aliases':[]}
    def catalog(self): return self.resolver.catalog(self.endpoint)
    def enable(self):
        self.endpoint.setdefault('assistant',{})['controls']={'enabled':True,'grants':[r['grant'] for r in self.catalog()['sources'] if r.get('grant')],'mappings':[]}
    def auth(self):
        if not self.allowed: raise ValueError('revoked')
        return self.endpoint,lambda e,p:e not in self.denied
    def negotiate(self): return self.engine.targets(self.endpoint,'tablet',lambda e,p:e not in self.denied)
    def request(self,cap='lights.turn_on',scope='subject',index=0):
        n=self.negotiate(); t=[t for t in n['targets'] if t['scope']==scope and any(c['id']==cap for c in t['capabilities'])][index]
        return {'version':1,'endpoint':'frame','deviceId':'tablet','capability':cap,'revision':1,'targetId':t['id'],'targetRevision':t['revision'],'requestId':n['requestIdPrefix']+'test'}
    async def persist(self,value): self.saved=copy.deepcopy(value); self.on_save()
    async def service(self,domain,name,data,context):
        assert self.saved['requests'], 'must persist before dispatch'
        self.calls.append((domain,name,data));self.on_call()
    async def execute(self,req): return await self.engine.execute(req,self.auth,self.service,'context')
    def compiled(self):
        for name in ('lights','awnings'):
            self.artifacts[name]=json.loads((Path(__file__).parent/'fixtures/assistant-actions'/f'{name}.json').read_text())
        membership=json.loads((Path(__file__).parent/'fixtures/assistant-actions/lights-membership.json').read_text())['initial']
        for eid,s in membership.items(): self.add(eid,s['state'],area='office',**s['attributes'])
        self.add('cover.terrace','closed',area='terrace',device_class='awning',supported_features=11)
        self.add('binary_sensor.awnings_wind','off');self.add('input_datetime.wind_heard',self.now.isoformat())


def test_explicit_grants_default_disabled_read_control_and_mapping_replacement():
    r=Rig(); assert r.negotiate()['targets']==[]
    r.enable(); assert r.negotiate()['targets']
    r.denied.add('light.desk'); assert r.negotiate()['targets']==[]
    r.denied.clear();r.registry['light.desk']['id']='replacement';assert r.negotiate()['targets']==[]


def test_group_grant_binds_actual_graph_and_dedupes_member_mapping():
    r=Rig();r.compiled();r.enable()
    sources=r.catalog()['sources'];proxy=next(s for s in sources if s['entity']=='light.room_office')
    assert proxy['members']==['light.desk']
    room=[t for t in r.negotiate()['targets'] if t['scope']=='room' and t['kind']=='light'][0]
    req=r.request(scope='room');result=run(lambda:r.execute(req))
    assert result['status']=='accepted' and r.calls==[('light','turn_on',{'entity_id':'light.room_office'})]
    r.add('light.ceiling','off',area='office');r.states['light.office_group'].attributes['entity_id'].append('light.ceiling')
    assert next(s for s in r.catalog()['sources'] if s['entity']=='light.room_office')['grant']!=proxy['grant']
    assert (run(lambda:r.execute(req|{'requestId':r.negotiate()['requestIdPrefix']+'changed'})))['reason']=='stale_revision'


@pytest.mark.parametrize('change',['unknown_group','missing_group','member_denied','proxy_without_binding'])
def test_group_graph_never_assumed_single(change):
    r=Rig();r.compiled();r.enable()
    if change=='unknown_group':r.states['light.office_group'].attributes['entity_id']=[]
    if change=='missing_group':del r.states['light.office_group']
    if change=='member_denied':r.denied.add('light.desk')
    if change=='proxy_without_binding':del r.artifacts['lights']['bindings']['subjects'][0]['group']
    assert not any(t['label'].get('en')=='Office' if isinstance(t['label'],dict) else False for t in r.negotiate()['targets'] if t['scope']=='subject')


def test_area_precedence_conflict_override_and_partial_room():
    r=Rig();r.registry['light.desk']['device_area_id']='terrace';r.enable()
    assert next(t for t in r.negotiate()['targets'] if t['scope']=='subject')['room']['label']=='Office'
    r.add('light.unmapped','off',area='office');r.enable()
    room=next(t for t in r.negotiate()['targets'] if t['scope']=='room');assert room['partialScope']
    assert run(lambda:r.execute(r.request(scope='room')))['reason']=='partial_scope'; assert not r.calls
    r.compiled();r.artifacts['lights']['bindings']['subjects'][1]['roomKey']='terrace'
    assert next(s for s in r.catalog()['sources'] if s['source']=='component:lights:light.desk')['reason']=='room_conflict'


@pytest.mark.parametrize('cover_class',['garage','gate','door','damper','unknown',None])
def test_unverified_cover_class_not_granted(cover_class):
    r=Rig();r.add('cover.demo','closed',area='office',device_class=cover_class,supported_features=11)
    r.endpoint['studio']['covers']=[{'entity':'cover.demo','label':'Cover'}];r.enable()
    assert not any(t['kind']!='light' for t in r.negotiate()['targets'])


@pytest.mark.parametrize('mutation',['unavailable','unsupported','revoked','grant','device','wind_alarm','wind_unknown','wind_stale','wind_future','wind_invalid','wind_permission','wind_binding'])
def test_all_member_preflight_and_recheck_after_persistence(mutation):
    r=Rig();r.compiled();r.enable();req=r.request('awnings.extend')
    def mutate():
        if mutation=='unavailable':r.states['cover.terrace'].state='unavailable'
        elif mutation=='unsupported':r.states['cover.terrace'].attributes['supported_features']=0
        elif mutation in ('revoked','device'):r.allowed=False
        elif mutation=='grant':r.endpoint['assistant']['controls']['grants']=[]
        elif mutation=='wind_alarm':r.states['binary_sensor.awnings_wind'].state='on'
        elif mutation=='wind_unknown':r.states['binary_sensor.awnings_wind'].state='unknown'
        elif mutation=='wind_stale':r.now+=timedelta(hours=5)
        elif mutation=='wind_future':r.states['input_datetime.wind_heard'].state=(r.now+timedelta(seconds=1)).isoformat()
        elif mutation=='wind_invalid':r.states['input_datetime.wind_heard'].state='yesterday'
        elif mutation=='wind_permission':r.denied.add('binary_sensor.awnings_wind')
        elif mutation=='wind_binding':r.registry['binary_sensor.awnings_wind']['id']='replaced'
    r.on_save=mutate
    assert run(lambda:r.execute(req))['status']=='failed'; assert r.calls==[]


def test_missing_wind_blocks_extend_but_allows_retract_stop():
    r=Rig();r.compiled();r.enable();del r.states['binary_sensor.awnings_wind']
    assert run(lambda:r.execute(r.request('awnings.extend')))['reason']=='wind_block'
    assert run(lambda:r.execute(r.request('awnings.retract')|{'requestId':r.negotiate()['requestIdPrefix']+'retract'}))['status']=='accepted'
    assert run(lambda:r.execute(r.request('awnings.stop')|{'requestId':r.negotiate()['requestIdPrefix']+'stop'}))['status']=='accepted'


def test_concurrent_duplicate_payload_conflict_and_restart_pending_never_replay():
    async def case():
        r=Rig();r.enable();req=r.request()
        a,b=await asyncio.gather(r.execute(req),r.execute(req));assert a==b and len(r.calls)==1
        assert (await r.execute(req|{'capability':'lights.turn_off'}))['reason']=='request_conflict'
        restart=Rig(r.saved);restart.enable(); assert await restart.execute(req)==a;assert not restart.calls
        pending=copy.deepcopy(r.saved);pending['requests'][req['requestId']]['result']=None
        restart=Rig(pending);restart.enable();assert (await restart.execute(req))['status']=='unknown';assert not restart.calls
    run(case)


def test_ledger_capacity_and_expired_epoch_cleanup_never_replay():
    async def case():
        r=Rig(capacity=1);r.enable();req=r.request();await r.execute(req)
        assert (await r.execute(req|{'requestId':r.negotiate()['requestIdPrefix']+'second'}))['reason']=='ledger_full'
        r.now+=timedelta(minutes=11);new=r.request();assert new['requestId']!=req['requestId']
        assert (await r.execute(new))['status']=='accepted';assert len(r.saved['requests'])==1
        assert (await r.execute(req))['reason']=='stale_revision';assert len(r.calls)==2
        restart=Rig(r.saved);restart.now=r.now;restart.enable();assert (await restart.execute(req))['reason']=='stale_revision'
    run(case)


def test_partial_failure_rechecks_next_member_and_retains_outcomes():
    r=Rig();r.add('light.second','off',area='office');r.endpoint['studio']['lights'].append({'entity':'light.second','label':'Second'});r.enable()
    req=r.request(scope='room');r.on_call=lambda:r.denied.add('light.second')
    result=run(lambda:r.execute(req));assert result['reason']=='partial_failure' and result['status']=='failed'
    assert [o['status'] for o in result['outcomes']]==['accepted','failed'];assert len(r.calls)==1


def test_storage_failure_before_dispatch_and_uncertainty_after_dispatch():
    async def case():
        r=Rig();r.enable();req=r.request()
        async def fail(value):raise OSError('disk')
        r.engine.persist=fail;assert (await r.execute(req))['reason']=='storage_failed';assert not r.calls
        r=Rig();r.enable();req=r.request();n=0
        async def second_fail(value):
            nonlocal n
            n+=1
            if n==2:raise OSError('disk')
            await r.persist(value)
        r.engine.persist=second_fail;assert (await r.execute(req))['status']=='unknown';assert len(r.calls)==1
        restart=Rig(r.saved);restart.enable();assert (await restart.execute(req))['status']=='unknown';assert not restart.calls
    run(case)


def test_assumed_or_unchanged_state_never_physical_confirmation():
    r=Rig();r.enable();r.states['light.desk'].attributes['assumed_state']=True
    result=run(lambda:r.execute(r.request()));assert result['status']=='accepted';assert result['outcomes'][0]['assumedState']


def test_partial_scope_requires_explicit_current_choice_and_never_expands():
    r=Rig();r.add('light.hidden','off',area='office');r.enable();req=r.request(scope='room')
    assert run(lambda:r.execute(req))['reason']=='partial_scope'
    result=run(lambda:r.execute(req|{'scopeChoice':'configured'}));assert result['status']=='accepted'
    assert r.calls==[('light','turn_on',{'entity_id':'light.desk'})]
    r.add('light.later','off',area='office')
    assert run(lambda:r.execute(req|{'scopeChoice':'configured','requestId':r.negotiate()['requestIdPrefix']+'late'}))['reason']=='stale_revision'


def test_controls_only_and_legacy_read_saves_preserve_each_other():
    mappings=module('studio_mappings');r=Rig();r.enable();before=copy.deepcopy(r.endpoint)
    before['assistant']['source_policy']={'version':1,'scopes':[]}
    new=mappings.merge_patch(before,{'assistant':{'controls':{**before['assistant']['controls'],'enabled':False}}},r.states)
    assert new['assistant']['subjects']==before['assistant']['subjects']
    assert new['assistant']['source_policy']==before['assistant']['source_policy']
    legacy=mappings.merge_patch(before,{'assistant':{'subjects':[],'sensitive':True}},r.states)
    assert legacy['assistant']['controls']==before['assistant']['controls']


def test_compiled_contract_checks_provenance_and_mixed_timer_directory(tmp_path):
    import yaml
    p=module('control_profiles');r=Rig();r.compiled()
    inventory=[{**b['component'],'package':''} for b in r.artifacts.values()]
    assert set(p.active(r.artifacts,inventory))=={'lights','awnings'}
    inventory[0]['commit']='c'*40;assert set(p.active(r.artifacts,inventory))=={'awnings'}
    folder=tmp_path/'domosenso/assistant-actions';folder.mkdir(parents=True)
    for path in (Path(__file__).parent/'fixtures/assistant-actions').glob('*.json'):
        if path.stem in ('lights','awnings','timers'):(folder/(path.stem+'.yaml')).write_text(yaml.safe_dump(json.loads(path.read_text())))
    assert set(p.load_directory(tmp_path)[0])=={'lights','awnings'}
    assert set(module('timer_contract').load_directory(tmp_path)[0])=={'timers'}
    bad=copy.deepcopy(r.artifacts['lights']);bad['definitions']['actions'][0]['parameters']={'service':'toggle'}
    with pytest.raises(ValueError):p.validate_bundle(bad)


def test_grant_review_rejects_new_binding_but_allows_removing_stale():
    r=Rig();r.enable();old=copy.deepcopy(r.endpoint['assistant']['controls'])
    r.registry['light.desk']['id']='new'
    proposed=copy.deepcopy(old);proposed['grants'][0]['bindingRevision']='f'*64
    with pytest.raises(ValueError):r.resolver.validate_change(r.endpoint,proposed)
    r.resolver.validate_change(r.endpoint,{**old,'grants':[]})


@pytest.mark.parametrize('seconds,expected',[(0,'accepted'),(1,'failed'),(-15000,'failed')])
def test_local_input_datetime_timestamp_is_epoch_not_naive_utc(seconds,expected):
    r=Rig();r.compiled();r.enable()
    # Europe/Warsaw is UTC+1 here; the HA helper supplies an epoch timestamp.
    instant=r.now+timedelta(seconds=seconds)
    r.states['input_datetime.wind_heard'].state=(instant+timedelta(hours=1)).replace(tzinfo=None).isoformat(' ')
    r.states['input_datetime.wind_heard'].attributes['timestamp']=instant.timestamp()
    assert run(lambda:r.execute(r.request('awnings.extend')))['status']==expected


def test_dispatch_timeout_returns_unknown_and_is_not_replayed():
    async def case():
        r=Rig();r.enable();r.engine.SERVICE_TIMEOUT=.001
        async def stuck(*args):
            r.calls.append('attempt');await asyncio.Event().wait()
        r.service=stuck;req=r.request()
        result=await r.execute(req);assert result['status']=='unknown'
        assert await r.execute(req)==result and r.calls==['attempt']
    # The implementation must provide a finite await boundary before this test runs.
    assert hasattr(module('control_actions').Controls,'SERVICE_TIMEOUT'), 'Service timeout missing'
    run(case)


def test_room_choice_identifies_exact_canonical_subjects():
    r=Rig();r.compiled();r.enable();n=r.negotiate()
    room=next(t for t in n['targets'] if t['scope']=='room' and t['kind']=='light')
    assert 'memberIds' in room, 'Native must show the exact configured set before choice'
    subjects={t['id']:t for t in n['targets'] if t['scope']=='subject'}
    assert len(room['memberIds'])==1
    assert subjects[room['memberIds'][0]]['label']['en']=='Office'


def test_proxy_member_room_conflict_requires_explicit_review():
    r=Rig();r.compiled();r.registry['light.desk']['area_id']='terrace'
    proxy=next(s for s in r.catalog()['sources'] if s['entity']=='light.room_office')
    assert proxy.get('reason')=='room_conflict'


def test_grant_is_invalidated_by_member_area_change():
    r=Rig();r.compiled();r.enable();req=r.request(scope='room')
    r.registry['light.desk']['area_id']='terrace'
    assert run(lambda:r.execute(req))['reason']=='stale_revision';assert not r.calls


def test_missing_service_is_preflighted_before_any_dispatch():
    # A service disappearing is a prerequisite failure, not a provider timeout.
    api=module('control_api')
    from test_control_api import api_rig
    r,h,c,grant=api_rig();r.enable();h.services.has_service=lambda d,n:False
    run(lambda:api.action(h,c,r.request()|{'id':1},grant,'read','control'))
    assert not r.calls and c.results[-1]['reason']=='unsupported'


def test_permission_change_during_final_save_persists_unknown_result():
    async def case():
        r=Rig();r.enable();req=r.request();count=0
        def during_save():
            nonlocal count
            count+=1
            if count==2:r.allowed=False
        r.on_save=during_save
        assert (await r.execute(req))['status']=='unknown'
        restart=Rig(r.saved);restart.enable()
        assert (await restart.execute(req))['status']=='unknown';assert not restart.calls
    run(case)


def test_mixed_directory_does_not_report_other_supported_family_as_invalid(tmp_path):
    import yaml
    folder=tmp_path/'domosenso/assistant-actions';folder.mkdir(parents=True)
    for name in ('lights','awnings','timers'):
        (folder/(name+'.yaml')).write_text(yaml.safe_dump(json.loads((Path(__file__).parent/'fixtures/assistant-actions'/f'{name}.json').read_text())))
    assert module('timer_contract').load_directory(tmp_path)[1]=={}
    assert module('control_profiles').load_directory(tmp_path)[1]=={}


@pytest.mark.parametrize('change',[{'capability':'light.toggle'},{'revision':True},{'version':True},{'entity_id':'light.other'},{'service':'turn_off'},{'scopeChoice':'all'},{'targetRevision':'bad'}])
def test_closed_request_cannot_name_services_or_entities(change):
    r=Rig();r.enable()
    with pytest.raises(ValueError):run(lambda:r.execute(r.request()|change))
    assert not r.calls


def test_vanilla_class_mapping_and_missing_guard_allow_only_safe_directions():
    r=Rig();r.add('cover.demo','closed',area='office',supported_features=3)
    r.endpoint['studio']['covers']=[{'entity':'cover.demo','label':'Awning'}]
    r.endpoint['assistant']['controls']={'enabled':False,'grants':[],'mappings':[{'source':'studio:cover.demo','entity':'cover.demo','coverClass':'awning'}]}
    grants=[s['grant'] for s in r.catalog()['sources'] if s['grant']]
    r.endpoint['assistant']['controls'].update(enabled=True,grants=grants)
    assert not any(c['id']=='awnings.stop' for t in r.negotiate()['targets'] for c in t['capabilities'])
    assert run(lambda:r.execute(r.request('awnings.extend')))['reason']=='wind_block'
    assert run(lambda:r.execute(r.request('awnings.retract')))['status']=='accepted'


def test_future_read_policy_never_enables_controls():
    r=Rig();r.compiled();r.endpoint['assistant']['source_policy']={'version':1,'scopes':[{'component':'lights','kind':'light','roles':['light'],'capabilities':{'lights.state':1},'exclude':[]}]}
    assert r.negotiate()['targets']==[]


def test_service_error_after_first_dispatch_keeps_each_outcome():
    async def case():
        r=Rig();r.add('light.second','off',area='office');r.endpoint['studio']['lights'].append({'entity':'light.second','label':'Second'});r.enable()
        async def fail(domain,name,data,context):
            r.calls.append(data)
            if data['entity_id']=='light.second':raise TimeoutError()
        r.service=fail;result=await r.execute(r.request(scope='room'))
        assert result['status']=='unknown' and result['reason']=='partial_failure'
        assert [o['status'] for o in result['outcomes']]==['accepted','unknown']
    run(case)


def test_cancelled_room_dispatch_restarts_with_per_target_unknown_without_replay():
    async def case():
        r=Rig();r.add('light.second','off',area='office');r.endpoint['studio']['lights'].append({'entity':'light.second','label':'Second'});r.enable();req=r.request(scope='room')
        async def interrupted(*args):raise asyncio.CancelledError()
        r.service=interrupted
        with pytest.raises(asyncio.CancelledError):await r.execute(req)
        r.engine=module('control_actions').Controls('0123456789abcdef0123456789abcdef',r.resolver,r.persist,r.saved,now=lambda:r.now)
        result=await r.execute(req)
        assert result['status']=='unknown' and len(result['outcomes'])==2
        assert all(o['status']=='unknown' for o in result['outcomes']);assert not r.calls
    run(case)
