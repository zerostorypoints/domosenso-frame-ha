import importlib
import copy
from test_assistant_selection import setup
from test_assistant_bindings import pkg


def test_display_uses_resolved_plant_fields_labels_limits_and_permissions():
    module=importlib.import_module(pkg.__name__+'.assistant_projection')
    bundle,scope,endpoint=setup();before=copy.deepcopy(endpoint)
    result=module.project(endpoint,{'plants':bundle},lambda _:True,'pl')
    plant=result['studio']['plants'][0]
    assert plant['label']=='Paproć' and plant['limits']['moisture']==[30,65]
    assert plant['_fields']['moisture']['entity']=='sensor.fern_moisture'
    assert endpoint==before
    assert module.project(endpoint,{'plants':bundle},lambda _:False,'en')['studio'].get('plants',[])==[]
    scope['exclude']=['plant.fern']
    assert module.project(endpoint,{'plants':bundle},lambda _:True,'en')['studio'].get('plants',[])==[]


def test_vanilla_manual_plant_and_room_need_no_second_mapping():
    module=importlib.import_module(pkg.__name__+'.assistant_projection')
    endpoint={'id':'f','assistant':{'subjects':[
        {'key':'fern','kind':'plant','label':'Fern','fields':{'moisture':{'entity':'sensor.moisture'}},'limits':{'min_moisture':15,'max_moisture':60}},
        {'key':'study','kind':'room','label':'Study','fields':{'temperature':{'entity':'sensor.temperature'},'humidity':{'entity':'sensor.humidity'}}}]}}
    result=module.project(endpoint,{},lambda _:True,'en')
    assert result['studio']['plants'][0]['limits']['moisture']==[15,60]
    assert result['studio']['rooms'][0]['humidity']=='sensor.humidity'
    assert result['temperature'][0]['entity']=='sensor.temperature'


def test_manual_display_overrides_are_preserved_and_not_duplicated():
    module=importlib.import_module(pkg.__name__+'.assistant_projection')
    bundle,scope,endpoint=setup();endpoint['studio']={'plants':[{'entity':'plant.fern','label':'My fern','limits':{'moisture':[20,60]}}]}
    result=module.project(endpoint,{'plants':bundle},lambda _:True,'en')
    assert len(result['studio']['plants'])==1
    assert result['studio']['plants'][0]['label']=='My fern'


def test_display_air_resolves_component_and_live_helper_policy():
    import types
    module=importlib.import_module(pkg.__name__+'.assistant_projection')
    semantics=importlib.import_module(pkg.__name__+'.semantics')
    endpoint={'id':'f','assistant':{'subjects':[{'key':'room','kind':'room','label':'Study','fields':{'co2':{'entity':'sensor.co2'}}}]}}
    policy={'metric':'co2','entity':'sensor.co2','unit':'ppm','levels':[800,1200],'higher_is_better':False,'helpers':{'raised':'input_number.raised'}}
    artifacts={'climate-air':{'bindings':{'subjects':[],'policies':[policy]}}}
    get=lambda eid:types.SimpleNamespace(state='850' if eid=='input_number.raised' else '900',attributes={'unit_of_measurement':'ppm'})
    result=module.project(endpoint,artifacts,lambda _:True,'en',state_get=get)
    assert result['air'][0]['policy']['levels'][0][0]==850
    reading={**result['air'][0],'value':900,'unit':'ppm','available':True,'stale':False}
    assert semantics.assess([reading])['status']=='raised'
    denied=module.project(endpoint,artifacts,lambda eid:not eid.startswith('input_number.'),'en',state_get=get)
    assert 'policy' not in denied['air'][0]


def test_display_revalidation_detects_inventory_removal_without_waiting_for_refresh():
    module=importlib.import_module(pkg.__name__+'.assistant_projection')
    bundle,scope,endpoint=setup()
    projected=module.project(endpoint,{'plants':bundle},lambda _:True,'en')
    assert module.still_current(projected,endpoint,{'plants':bundle},lambda _:True,'en')
    assert not module.still_current(projected,endpoint,{},lambda _:True,'en')
