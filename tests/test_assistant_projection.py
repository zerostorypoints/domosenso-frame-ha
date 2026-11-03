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
