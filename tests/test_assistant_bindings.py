"""Component suggestions are not grants; chosen source identities remain explicit."""
import copy
import importlib
import sys
import types
from pathlib import Path
from datetime import datetime, timezone

import pytest

pkg = types.ModuleType('binding_test_package')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg
mapping = importlib.import_module(pkg.__name__ + '.studio_mappings')
runtime = importlib.import_module(pkg.__name__ + '.assistant_extended')
NOW = datetime(2026,11,2,tzinfo=timezone.utc)


def fixture():
    subject={'key':'plant.fern','kind':'plant','label':{'en':'Fern','pl':'Paproć'},'fields':{'plant':{'entity':'plant.fern'},'moisture':{'entity':'sensor.fern_moisture'}},'limits':{'min_moisture':30,'max_moisture':65}}
    bundle={'component':{'id':'plants','version':'0.4.0','commit':'a'*40,'dev':False},'definitions':{'capabilities':[]},'bindings':{'subjects':[subject],'policies':[]}}
    selection={'component':'plants','subject':'plant.fern','fields':copy.deepcopy(subject['fields'])}
    return bundle, selection


def test_explicit_selection_uses_live_metadata_without_persisting_policy_or_labels():
    bundle, selection=fixture()
    original={'id':'frame','display':{}}
    patch={'assistant':{'subjects':[],'sensitive':False,'component_sources':[selection]}}
    endpoint=mapping.merge_patch(original,patch)
    states={'plant.fern':types.SimpleNamespace(state='ok',attributes={},last_reported=NOW),'sensor.fern_moisture':types.SimpleNamespace(state='20',attributes={'unit_of_measurement':'%'},last_reported=NOW)}
    engine=runtime.ExtendedRuntime('0123456789abcdef0123456789abcdef',states.get,lambda _:True,lambda:NOW,verified_source=lambda _:True,artifacts={'plants':bundle})
    assert not engine.effective_endpoint(original)['assistant']['subjects']
    a=engine.query(endpoint,'plants.watering_need',1,'plant-watering.v1',{})
    assert a['summary']['status']=='needs_water'
    bundle['bindings']['subjects'][0]['limits']['min_moisture']=15
    assert engine.query(endpoint,'plants.watering_need',1,'plant-watering.v1',{})['summary']['status']=='within_band'
    assert endpoint['assistant']['component_sources']==[selection]
    assert 'limits' not in selection and original=={'id':'frame','display':{}}


def test_removed_component_or_replaced_entity_never_becomes_a_manual_adapter():
    bundle, selection=fixture()
    endpoint=mapping.merge_patch({'id':'frame'}, {'assistant':{'subjects':[],'sensitive':False,'component_sources':[selection]}})
    engine=runtime.ExtendedRuntime('0123456789abcdef0123456789abcdef',lambda _:None,lambda _:True,lambda:NOW,artifacts={'plants':bundle})
    assert len(engine.effective_endpoint(endpoint)['assistant']['subjects'])==1
    engine.artifacts={}
    assert engine.effective_endpoint(endpoint)['assistant']['subjects']==[]
    engine.artifacts={'plants':bundle}
    bundle['bindings']['subjects'][0]['fields']['moisture']['entity']='sensor.replacement'
    row=engine.effective_endpoint(endpoint)['assistant']['subjects'][0]
    assert 'moisture' not in row['fields']
    bundle['bindings']['subjects'][0]['fields']['private']={'entity':'sensor.private'}
    assert 'private' not in engine.effective_endpoint(endpoint)['assistant']['subjects'][0]['fields']


def test_component_selection_validation_and_manual_overrides():
    bundle, selection=fixture()
    manual={'key':'chosen','kind':'plant','label':'Chosen','fields':{'moisture':{'entity':'sensor.fern_moisture'}},'limits':{'min_moisture':10}}
    endpoint=mapping.merge_patch({'id':'frame'}, {'assistant':{'subjects':[manual],'sensitive':False,'component_sources':[selection]}})
    engine=runtime.ExtendedRuntime('0123456789abcdef0123456789abcdef',lambda _:None,lambda _:True,lambda:NOW,artifacts={'plants':bundle})
    rows=engine.effective_endpoint(endpoint)['assistant']['subjects']
    assert len(rows)==1 and rows[0]['label']=='Chosen'
    assert rows[0]['limits']=={'min_moisture':10,'max_moisture':65}
    for bad in [selection|{'component':'../private'},selection|{'fields':{'moisture':{'entity':'script.run'}}},selection|{'limits':{'min_moisture':1}}]:
        with pytest.raises(ValueError):mapping.merge_patch({}, {'assistant':{'subjects':[],'sensitive':False,'component_sources':[bad]}})


def test_component_selections_survive_restart_without_changing_other_groups():
    import asyncio
    _,selection=fixture();saved=[]
    async def persist(value):saved.append(value)
    original={'id':'frame','display':{},'temperature':[{'entity':'sensor.room','label':'Room'}]}
    store=mapping.Mappings({'frame':copy.deepcopy(original)},{},persist)
    patch={'assistant':{'subjects':[],'sensitive':False,'component_sources':[selection]}}
    asyncio.run(store.update('frame',store.get('frame',{})['revision'],patch,{},lambda:None))
    restored=mapping.Mappings({'frame':copy.deepcopy(original)},saved[-1],persist)
    assert restored.get('frame',{})['mappings']['assistant']['component_sources']==[selection]
    assert restored.endpoints['frame']['temperature']==original['temperature']


def test_admin_catalog_explains_missing_metadata_and_does_not_activate_proposals():
    from binding_test_package.assistant_bindings import catalog
    bundle,selection=fixture();states={'plant.fern':object(),'sensor.fern_moisture':object()}
    report=catalog({'plants':bundle},{},[{'id':'plants'},{'id':'weather'}],{'id':'frame'},states)
    rows={r['id']:r for r in report['components']}
    assert rows['plants']['status']=='verified'
    assert rows['plants']['subjects'][0]['selection']==selection
    assert rows['plants']['subjects'][0]['missing']==0
    assert rows['weather']['status']=='missing_metadata'
    report=catalog({}, {'plants':'contract_mismatch'}, [{'id':'plants'}], {'id':'frame'},states)
    assert report['components'][0]['subjects']==[]


def test_admin_update_rejects_outdated_or_fabricated_proposal_and_preserves_old_selections():
    from binding_test_package.assistant_bindings import validate_new_selections
    bundle,selection=fixture()
    validate_new_selections([], [selection], {'plants':bundle})
    replaced=copy.deepcopy(selection);replaced['fields']['moisture']['entity']='sensor.private'
    with pytest.raises(ValueError):validate_new_selections([], [replaced], {'plants':bundle})
    with pytest.raises(ValueError):validate_new_selections([], [selection], {})
    # Saving unrelated manual fields or removing a stale selection remains possible.
    validate_new_selections([selection], [selection], {})
    validate_new_selections([selection], [], {})


def test_selected_sources_still_enforce_ha_user_permissions_before_reading():
    bundle,selection=fixture();reads=[]
    ep=mapping.merge_patch({'id':'frame'}, {'assistant':{'subjects':[],'sensitive':False,'component_sources':[selection]}})
    def get(e):reads.append(e);raise AssertionError('Denied state must not be read')
    engine=runtime.ExtendedRuntime('0123456789abcdef0123456789abcdef',get,lambda _:False,lambda:NOW,artifacts={'plants':bundle})
    assert engine.capabilities(ep,[{'queryType':'plant-query.v1','answerType':'plant-watering.v1'}])==[]
    assert reads==[]


def test_manual_sensor_override_does_not_inherit_a_different_source_policy():
    bundle,selection=fixture()
    manual={'key':'chosen','kind':'plant','label':'Chosen','fields':{'plant':{'entity':'plant.fern'},'moisture':{'entity':'sensor.other'}}}
    ep=mapping.merge_patch({'id':'frame'},{'assistant':{'subjects':[manual],'sensitive':False,'component_sources':[selection]}})
    engine=runtime.ExtendedRuntime('0123456789abcdef0123456789abcdef',lambda _:None,lambda _:True,lambda:NOW,artifacts={'plants':bundle})
    rows=engine.effective_endpoint(ep)['assistant']['subjects']
    assert 'min_moisture' not in rows[0].get('limits',{})


def test_mapping_store_rolls_back_if_metadata_changes_while_saving():
    import asyncio
    bundle,selection=fixture();artifacts={'plants':bundle};saved=[]
    async def persist(value):saved.append(copy.deepcopy(value));artifacts.clear()
    store=mapping.Mappings({'frame':{'id':'frame','display':{}}},{},persist,lambda:artifacts)
    before=copy.deepcopy(store.endpoints)
    with pytest.raises(ValueError):asyncio.run(store.update('frame',store.get('frame',{})['revision'],{'assistant':{'subjects':[],'sensitive':False,'component_sources':[selection]}},{},lambda:None))
    assert store.saved=={} and store.endpoints==before and saved[-1]=={}


def test_distinct_selected_plants_sharing_ambient_sensor_remain_distinct():
    bundle, selection=fixture()
    fern=bundle['bindings']['subjects'][0]
    fern['fields']['temperature']={'entity':'sensor.ambient'}
    selection['fields']=copy.deepcopy(fern['fields'])
    aloe=copy.deepcopy(fern)
    aloe.update(key='plant.aloe',label='Aloe')
    aloe['fields']['plant']={'entity':'plant.aloe'}
    aloe['fields']['moisture']={'entity':'sensor.aloe_moisture'}
    bundle['bindings']['subjects'].append(aloe)
    selections=[selection,{'component':'plants','subject':aloe['key'],'fields':copy.deepcopy(aloe['fields'])}]
    ep={'id':'frame','assistant':{'subjects':[],'component_sources':selections}}
    from binding_test_package.assistant_data import merge_verified_bindings
    rows=merge_verified_bindings(ep,{'plants':bundle})
    assert {r['key'] for r in rows}=={'plant.fern','plant.aloe'}
    assert {r['fields']['moisture']['entity'] for r in rows}=={'sensor.fern_moisture','sensor.aloe_moisture'}


def test_stale_selection_cannot_apply_component_policy_to_manual_measurement():
    bundle,selection=fixture()
    manual={'key':'manual','kind':'plant','label':'Fern','fields':{'plant':{'entity':'plant.fern'},'moisture':{'entity':'sensor.manual'}}}
    ep={'id':'frame','assistant':{'subjects':[manual],'component_sources':[selection]}}
    bundle['bindings']['subjects'][0]['fields']['moisture']={'entity':'sensor.replacement'}
    from binding_test_package.assistant_data import merge_verified_bindings
    rows=merge_verified_bindings(ep,{'plants':bundle})
    assert rows[0]['fields']['moisture']=={'entity':'sensor.manual'}
    assert 'min_moisture' not in rows[0].get('limits',{})


def test_legacy_exposure_cannot_reintroduce_a_reassigned_selected_role():
    bundle,selection=fixture()
    ep={'id':'frame','assistant':{'subjects':[],'component_sources':[selection]},'studio':{'status':[{'entity':'sensor.replacement','label':'Other source'}]}}
    bundle['bindings']['subjects'][0]['fields']['moisture']={'entity':'sensor.replacement'}
    from binding_test_package.assistant_data import merge_verified_bindings
    rows=merge_verified_bindings(ep,{'plants':bundle})
    assert all('moisture' not in r['fields'] for r in rows)


def test_shared_ambient_sensor_cannot_merge_a_component_into_another_manual_plant():
    bundle,selection=fixture()
    bundle['bindings']['subjects'][0]['fields']['temperature']={'entity':'sensor.ambient'}
    selection['fields']['temperature']={'entity':'sensor.ambient'}
    manual={'key':'plant.aloe','kind':'plant','label':'Aloe','fields':{'plant':{'entity':'plant.aloe'},'temperature':{'entity':'sensor.ambient'}}}
    ep={'id':'frame','assistant':{'subjects':[manual],'component_sources':[selection]}}
    from binding_test_package.assistant_data import merge_verified_bindings
    assert len(merge_verified_bindings(ep,{'plants':bundle}))==2


def test_removing_all_component_selections_cannot_reactivate_legacy_bindings():
    bundle,_=fixture()
    ep={'id':'frame','assistant':{'subjects':[],'component_sources':[]},'studio':{'plants':[{'entity':'plant.fern'}]}}
    from binding_test_package.assistant_data import merge_verified_bindings
    assert merge_verified_bindings(ep,{'plants':bundle})==[]
