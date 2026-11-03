"""Future subjects are opt-in; future contracts are not."""
import copy
import importlib
import asyncio
import pytest
from test_assistant_bindings import fixture, mapping, runtime, NOW
bindings = importlib.import_module('binding_test_package.assistant_bindings')


def setup():
    bundle, _ = fixture()
    bundle['definitions']['capabilities'] = [dict(id='plants.watering_need',revision=1,operation='read',required_facts=[{'role':'moisture'}],optional_facts=[{'role':'plant'}])]
    scope={'component':'plants','kind':'plant','roles':['moisture','plant'],'capabilities':{'plants.watering_need':1},'exclude':[]}
    endpoint={'id':'frame','display':{},'assistant':{'subjects':[],'sensitive':False,'component_sources':[], 'source_policy':{'version':1,'scopes':[scope]}}}
    return bundle,scope,endpoint


def engine(bundle):
    return runtime.ExtendedRuntime('0123456789abcdef0123456789abcdef',lambda _:None,lambda _:True,lambda:NOW,artifacts={'plants':bundle})


def test_future_plants_follow_opt_in_and_exclusion():
    bundle,scope,endpoint=setup()
    second=copy.deepcopy(bundle['bindings']['subjects'][0]);second['key']='plant.aloe';second['fields']={'plant':{'entity':'plant.aloe'},'moisture':{'entity':'sensor.aloe'}}
    bundle['bindings']['subjects'].append(second)
    assert {s['key'] for s in bindings.selected_subjects(endpoint,{'plants':bundle})}=={'plant.fern','plant.aloe'}
    scope['exclude']=['plant.aloe']
    assert [s['key'] for s in bindings.selected_subjects(endpoint,{'plants':bundle})]==['plant.fern']
    endpoint['assistant'].pop('source_policy')
    assert bindings.selected_subjects(endpoint,{'plants':bundle})==[]


def test_dynamic_source_replacement_but_no_new_role_or_capability():
    bundle,scope,endpoint=setup();subject=bundle['bindings']['subjects'][0]
    subject['fields']['moisture']={'entity':'sensor.new'}
    subject['fields']['private']={'entity':'sensor.private'}
    rows=engine(bundle).effective_endpoint(endpoint,'plants.watering_need')['assistant']['subjects']
    assert rows[0]['fields']['moisture']['entity']=='sensor.new'
    assert 'private' not in rows[0]['fields']
    assert engine(bundle).effective_endpoint(endpoint,'plants.status')['assistant']['subjects']==[]
    bundle['definitions']['capabilities'][0]['revision']=2
    assert engine(bundle).effective_endpoint(endpoint,'plants.watering_need')['assistant']['subjects']==[]


def test_old_client_save_preserves_policy_and_restart():
    bundle,scope,ep=setup();saved=[]
    async def persist(value):saved.append(copy.deepcopy(value))
    store=mapping.Mappings({'frame':copy.deepcopy(ep)},{},persist,lambda:{'plants':bundle})
    patch={'assistant':{'subjects':[],'sensitive':False,'component_sources':[]}}
    asyncio.run(store.update('frame',store.get('frame',{})['revision'],patch,{},lambda:None))
    assert store.endpoints['frame']['assistant']['source_policy']==ep['assistant']['source_policy']
    restored=mapping.Mappings({'frame':{'id':'frame','display':{}}},saved[-1],persist)
    assert restored.endpoints['frame']['assistant']['source_policy']==ep['assistant']['source_policy']


def test_invalid_scope_cannot_be_saved():
    bundle,scope,ep=setup()
    for mutation in [{'roles':['secret']},{'capabilities':{'plants.watering_need':2}},{'component':'unknown'}]:
        bad=copy.deepcopy(ep['assistant']);bad['source_policy']['scopes'][0].update(mutation)
        async def persist(_):pass
        store=mapping.Mappings({'frame':{'id':'frame','display':{}}},{},persist,lambda:{'plants':bundle})
        with pytest.raises(ValueError):asyncio.run(store.update('frame',store.get('frame',{})['revision'],{'assistant':bad},{},lambda:None))


def test_dynamic_metadata_removal_never_falls_back_to_old_mapping():
    bundle,scope,ep=setup()
    assert engine(bundle).effective_endpoint(ep)['assistant']['subjects']
    assert engine({}).effective_endpoint(ep)['assistant']['subjects']==[]


def test_manual_override_cannot_gain_new_capabilities_from_dynamic_fields():
    bundle,scope,ep=setup()
    ep['assistant']['subjects']=[{'key':'manual','kind':'plant','label':'Fern','fields':{'plant':{'entity':'plant.fern'}}}]
    rows=engine(bundle).effective_endpoint(ep,'plants.status')['assistant']['subjects']
    assert len(rows)==1 and set(rows[0]['fields'])=={'plant'}


def test_first_dynamic_save_preserves_pre_policy_backup(tmp_path,monkeypatch):
    path=tmp_path/'domosenso_frame_mappings';old=b'{"version":1,"data":{}}';path.write_bytes(old)
    async def executor(fn,*args):return fn(*args)
    async def save(*args):path.write_text('new')
    monkeypatch.setattr(mapping,'save_verified',save)
    _,_,ep=setup();value={'frame':{'patch':{'assistant':ep['assistant']},'sequence':1}}
    asyncio.run(mapping.save_mappings_verified(None,value,path,executor))
    backup=path.with_name(path.name+'.pre-source-policy-v1')
    assert backup.read_bytes()==old
    asyncio.run(mapping.save_mappings_verified(None,value,path,executor))
    assert backup.read_bytes()==old


def test_exclusion_also_blocks_an_old_editor_explicit_component_grant():
    bundle,scope,ep=setup();scope['exclude']=['plant.fern']
    ep['assistant']['component_sources']=[{'component':'plants','subject':'plant.fern','fields':copy.deepcopy(bundle['bindings']['subjects'][0]['fields'])}]
    assert bindings.selected_subjects(ep,{'plants':bundle})==[]


def test_catalog_reports_unsupported_scope_and_selected_counts():
    bundle,scope,ep=setup()
    report=bindings.catalog({'plants':bundle},{},[{'id':'plants'}],ep,{})['components'][0]
    assert report['coverage']=={'discovered':1,'selected':1,'excluded':0,'incomplete':1,'scopeChanged':False}
    bundle['definitions']['capabilities'][0]['revision']=2
    report=bindings.catalog({'plants':bundle},{},[{'id':'plants'}],ep,{})['components'][0]
    assert report['coverage']['selected']==0 and report['coverage']['scopeChanged']


def test_new_room_temperature_and_humidity_follow_same_scope():
    import types
    definitions=[{'id':cap,'revision':1,'operation':'read','required_facts':[{'role':role}],'optional_facts':[]} for cap,role in [('climate-air.temperature','temperature'),('climate-air.humidity','humidity')]]
    room=lambda name:{'key':name,'kind':'room','label':name,'fields':{role:{'entity':f'sensor.{name}_{role}'} for role in ('temperature','humidity')}}
    bundle={'definitions':{'capabilities':definitions},'bindings':{'subjects':[room('study')],'policies':[]}}
    scope={'component':'climate-air','kind':'room','roles':['temperature','humidity'],'capabilities':{c['id']:1 for c in definitions},'exclude':[]}
    endpoint={'id':'frame','assistant':{'subjects':[],'component_sources':[],'source_policy':{'version':1,'scopes':[scope]}}}
    def get(eid):return types.SimpleNamespace(state='21' if eid.endswith('temperature') else '45',attributes={'unit_of_measurement':'°C' if eid.endswith('temperature') else '%'},last_reported=NOW)
    r=runtime.ExtendedRuntime('0123456789abcdef0123456789abcdef',get,lambda _:True,lambda:NOW,verified_source=lambda _:True,artifacts={'climate-air':bundle})
    bundle['bindings']['subjects'].append(room('bedroom'))
    temp=r.query(endpoint,'climate-air.temperature',1,'temperature.v1',{})
    humidity=r.query(endpoint,'climate-air.humidity',1,'environment.v1',{})
    assert len(temp['facts'])==len(humidity['facts'])==2
    scope['exclude']=['bedroom']
    assert len(r.query(endpoint,'climate-air.temperature',1,'temperature.v1',{})['facts'])==1
