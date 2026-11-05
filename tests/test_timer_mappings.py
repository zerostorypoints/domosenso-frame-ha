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


class CallbackRig:
    """Execute the production callback body with the real Mappings transaction.

    Only HA route decorators and unrelated Studio rendering/IO are excluded.
    This keeps the same callback/closure/manager path in the standalone suite.
    """
    def __init__(self):
        import ast
        import types
        from pathlib import Path
        from test_timer_api import Connection
        self.r = Rig(); self.c = Connection(); self.c.user.is_admin = True
        self.endpoints = {'frame': {'id':'frame','display':{}}, 'other': {'id':'other','display':{}}}
        self.saved = {}; self.during_save = lambda: None
        async def persist(value):
            self.saved = copy.deepcopy(value); self.during_save()
        self.manager = mappings.Mappings(self.endpoints, {}, persist)
        self.state = {'endpoints':self.endpoints, 'grants':types.SimpleNamespace(records=self.r.devices),
                      'timer_source_verified':lambda _:True}
        tree = ast.parse((Path(__file__).parents[1] / 'custom_components/domosenso_frame/studio_api.py').read_text())
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'mapping_update')
        fn.decorator_list = []
        def admin(c):
            if not c.user.is_active or not c.user.is_admin: raise ValueError('Administrator access required')
        ns = {'state':self.state,'mappings':self.manager,'admin':admin,
              'merge_patch':mappings.merge_patch, 'validate_targets':importlib.import_module('timer_package.timers').validate_targets,
              'data':types.SimpleNamespace(cache={}), 'runtime':types.SimpleNamespace(reset=lambda _:None),
              'with_assistant_catalog':lambda result:result, 'Conflict':mappings.Conflict,
              'deny':lambda c,msg,code,message:c.send_error(msg['id'],code,message)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), 'studio_api.py', 'exec'), ns)
        self.callback = ns['mapping_update']; self.h = types.SimpleNamespace(states=self.r.states)
    async def update(self, eid, items=None, target='tablet', write=False):
        rows = copy.deepcopy(self.r.endpoint['timers']['items']) if items is None else copy.deepcopy(items)
        for row in rows: row['alarmDeviceId'] = target
        msg = {'id':1, 'endpoint':eid, 'revision':self.manager.get(eid,self.r.states)['revision'],
               'patch':{'timers':{'writeEnabled':write,'items':rows}}}
        self.c.errors.clear(); self.c.results.clear()
        await self.callback(self.h,self.c,msg)
        return self.c.results


def test_callback_rejects_approved_but_unreachable_target_without_exposing_helper():
    async def case():
        r=CallbackRig(); r.r.devices['tablet']['endpoint']='other'
        assert not await r.update('frame')
        assert r.c.errors[-1][0]=='mapping_invalid'
        assert 'other' in r.c.errors[-1][1] and 'timer.demo' in r.c.errors[-1][1]
        assert not r.saved and 'timers' not in r.endpoints['other']
    run(case)


def test_callback_projects_own_endpoint_add_and_allows_read_only_target():
    async def case():
        r=CallbackRig()
        assert await r.update('frame')
        assert await r.update('other',write=True)
        assert not r.endpoints['frame']['timers']['writeEnabled']
        restored=mappings.Mappings(copy.deepcopy(r.manager.defaults),r.saved,r.manager.persist)
        assert restored.endpoints==r.endpoints
    run(case)


def test_callback_rejects_target_removal_and_conflicting_target_change_atomically():
    async def case():
        r=CallbackRig(); assert await r.update('frame'); assert await r.update('other')
        before=copy.deepcopy(r.saved); endpoints=copy.deepcopy(r.endpoints)
        assert not await r.update('frame',items=[])
        assert 'timer.demo' in r.c.errors[-1][1] and 'frame' in r.c.errors[-1][1]
        assert r.saved==before and r.endpoints==endpoints
        assert not await r.update('other',target=None)
        assert 'same alarm device' in r.c.errors[-1][1]
        assert r.saved==before and r.endpoints==endpoints
        assert await r.update('other',items=[])
        assert await r.update('frame',items=[])
    run(case)


@pytest.mark.parametrize('change',['revoked','endpoint'])
def test_callback_rechecks_device_eligibility_after_persist_and_rolls_back(change):
    async def case():
        r=CallbackRig()
        def changed(): r.r.devices['tablet'][('state' if change=='revoked' else 'endpoint')]=('revoked' if change=='revoked' else 'other')
        r.during_save=changed
        assert not await r.update('frame')
        assert r.c.errors[-1][0]=='mapping_invalid' and r.saved=={}
        assert 'timers' not in r.endpoints['frame']
    run(case)


def test_invalid_retained_target_can_be_removed_without_implicitly_exposing_helper():
    async def case():
        r=CallbackRig();assert await r.update('frame');assert await r.update('other')
        r.r.devices['tablet']['endpoint']='new-endpoint'
        assert await r.update('other',items=[])
        assert await r.update('frame',items=[])
        assert 'new-endpoint' not in r.endpoints
    run(case)
