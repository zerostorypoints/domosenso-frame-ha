import copy
import importlib
import pytest
from test_timers import Rig, run

mappings = importlib.import_module('timer_package.studio_mappings')


def test_vanilla_mapping_is_explicit_and_survives_reload_without_yaml():
    async def case():
        r = Rig(); endpoint = {'id':'frame','display':{}}; saved = {}
        async def persist(value): saved.update(value)
        manager = mappings.Mappings({'frame':endpoint}, {}, persist)
        before = manager.get('frame',r.states)
        assert before['mappings']['timers'] == {'writeEnabled':False,'items':[]}
        assert before['candidates']['timers'][0]['entity'] == 'timer.demo'
        updated = await manager.update('frame',before['revision'],{'timers':r.endpoint['timers']},r.states,lambda:None)
        restored = mappings.Mappings({'frame':endpoint},saved,persist)
        assert restored.get('frame',r.states)['mappings']['timers'] == updated['mappings']['timers']
        assert 'timers' not in endpoint
    run(case)


@pytest.mark.parametrize('mutation', ['internal','duplicate','grant','fields','domain'])
def test_mapping_rejects_internal_helpers_and_implicit_grants(mutation):
    r = Rig(); value = copy.deepcopy(r.endpoint['timers'])
    if mutation == 'internal': r.states['timer.demo'].attributes['component'] = 'ramka'
    if mutation == 'duplicate': value['items'] *= 2
    if mutation == 'grant': value.pop('writeEnabled')
    if mutation == 'fields': value['items'][0]['service'] = 'start'
    if mutation == 'domain': value['items'][0]['entity'] = 'script.demo'
    with pytest.raises(ValueError): mappings.merge_patch({'id':'frame'}, {'timers':value}, r.states)


def test_conflicting_alarm_targets_across_endpoints_are_rejected():
    from test_timers import timer_module
    timers=timer_module();r=Rig();other=copy.deepcopy(r.endpoint);other['id']='other'
    other['timers']['items'][0]['alarmDeviceId']='second-tablet'
    assert hasattr(timers,'validate_targets'), 'Cross-endpoint target validation missing'
    with pytest.raises(ValueError): timers.validate_targets({'frame':r.endpoint,'other':other})
    other['timers']['items'][0]['alarmDeviceId']='tablet'
    timers.validate_targets({'frame':r.endpoint,'other':other})
