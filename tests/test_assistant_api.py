"""API seam tests with a fictional endpoint and paired-session grant."""
import asyncio
import importlib
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

pkg = types.ModuleType('frame_api_package')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[pkg.__name__] = pkg
api = importlib.import_module('frame_api_package.assistant_api')


class Connection:
    def __init__(self):
        self.user = types.SimpleNamespace(is_active=True, permissions=types.SimpleNamespace(check_entity=lambda eid, policy: eid == 'sensor.one'))
        self.results = []
        self.errors = []
    def send_result(self, id, value): self.results.append(value)
    def send_error(self, id, code, message): self.errors.append((code, message))


def test_session_endpoint_and_read_permission_checked_on_each_request():
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    endpoint = {'id': 'frame', 'temperature': [{'label': 'One', 'entity': 'sensor.one'}, {'label': 'Secret', 'entity': 'sensor.secret'}], 'air': [], 'calendars': []}
    state = types.SimpleNamespace(state='20', attributes={'unit_of_measurement': '°C'}, last_reported=now)
    hass = types.SimpleNamespace(data={'domosenso_frame': {'saved': {'installationId': '0123456789abcdef0123456789abcdef'}, 'settings': {'stale_seconds': 900}, 'assistant_source_verified':lambda _:True}}, states=types.SimpleNamespace(get=lambda eid: state))
    conn = Connection()
    granted = True
    def grant(h, c):
        if not granted: raise ValueError('revoked')
        return {}, endpoint
    req = {'id': 1, 'endpoint': 'frame', 'version': 1, 'supported': [{'queryType': 'temperature-query.v1', 'answerType': 'temperature.v1'}]}
    api.capabilities(hass, conn, req, grant, 'read', lambda: now)
    assert len(conn.results[0]['capabilities'][0]['subjects']) == 1
    assert 'secret' not in str(conn.results)
    granted = False
    api.capabilities(hass, conn, req, grant, 'read', lambda: now)
    assert conn.errors[-1][0] == 'frame_denied'
    granted = True
    api.capabilities(hass, conn, req | {'endpoint': 'other'}, grant, 'read', lambda: now)
    assert conn.errors[-1][0] == 'frame_denied'


def test_api_rejects_unknown_request_fields_before_processing():
    conn = Connection()
    api.capabilities(None, conn, {'id': 1, 'endpoint': 'frame', 'version': 1, 'supported': [], 'action': 'turn_on'}, lambda *_: pytest.fail('must not grant'), 'read')
    assert conn.errors[-1][0] == 'frame_denied'


def test_setup_registers_routes_loads_metadata_and_admin_reload(tmp_path, monkeypatch):
    ws = types.ModuleType('homeassistant.components.websocket_api')
    ws.websocket_command = lambda schema: lambda fn: fn
    ws.require_admin = lambda fn: fn
    ws.async_response = lambda fn: fn
    core = types.ModuleType('homeassistant.core'); core.callback = lambda fn: fn
    homeassistant = types.ModuleType('homeassistant'); homeassistant.__path__ = []
    components = types.ModuleType('homeassistant.components'); components.websocket_api = ws
    helpers = types.ModuleType('homeassistant.helpers'); helpers.entity_registry = types.SimpleNamespace(async_get=lambda h: types.SimpleNamespace(async_get=lambda eid: types.SimpleNamespace(platform=eid.split('.',1)[1]) if '.' in eid else None))
    monkeypatch.setitem(sys.modules, 'homeassistant.helpers', helpers)
    monkeypatch.setitem(sys.modules, 'homeassistant', homeassistant)
    monkeypatch.setitem(sys.modules, 'homeassistant.components', components)
    monkeypatch.setitem(sys.modules, 'homeassistant.core', core)
    state = {}
    async def executor(fn, *args): return fn(*args)
    hass = types.SimpleNamespace(data={'domosenso_frame': state}, config=types.SimpleNamespace(path=lambda: str(tmp_path)), async_add_executor_job=executor)
    registered = []
    asyncio.run(api.setup(hass, lambda *_: None, lambda h, f: registered.append(f), 'read'))
    assert {f.__name__ for f in registered} == {'cap', 'ask', 'reload'}
    assert state['assistant_artifacts'] == {}
    assert state['assistant_source_verified']('sensor.xiaomi_ble')
    assert state['assistant_source_verified']('sensor.esphome')
    assert not any(state['assistant_source_verified'](p) for p in ('sensor.template','sensor.statistics','unknown','binary_sensor.xiaomi_ble'))
    now=datetime(2026,10,31,12,tzinfo=timezone.utc)
    from frame_api_package.assistant_extended import ExtendedRuntime
    sample=types.SimpleNamespace(state='10',attributes={'unit_of_measurement':'%'},last_reported=now)
    endpoint={'id':'f','assistant':{'subjects':[{'key':'p','kind':'plant','label':'Fern','fields':{'moisture':{'entity':'sensor.xiaomi_ble'}},'limits':{'min_moisture':15,'max_moisture':60}}]}}
    engine=ExtendedRuntime('0123456789abcdef0123456789abcdef',lambda _:sample,lambda _:True,lambda:now,verified_source=state['assistant_source_verified'])
    def answer(): return engine.query(endpoint,'plants.watering_need',1,'plant-watering.v1',{})
    assert answer()['facts'][0]['value']==10
    assert answer()['evidence'][0]['freshnessBasis']=='source_report'
    sample.attributes['restored']=True
    assert answer()['availability']=='unavailable'
    sample.attributes.pop('restored'); sample.last_reported=now.replace(day=30)
    assert answer()['availability']=='unavailable'
    sample.last_reported=None
    assert answer()['availability']=='unavailable'
    sample.last_reported=now
    for eid in ('sensor.template','sensor.statistics','unknown'):
        endpoint['assistant']['subjects'][0]['fields']['moisture']['entity']=eid
        assert answer()['availability']=='unavailable'
    connection = Connection(); connection.user.is_admin = True
    asyncio.run(next(f for f in registered if f.__name__ == 'reload')(hass, connection, {'id': 3}))
    assert connection.results[-1] == {'loaded': 0, 'errors': {}}


def test_only_matching_installed_inventory_can_activate_metadata_bindings():
    b = {'schema': 1, 'component': {'id': 'climate-air', 'version':'1.0.0', 'commit':'123456789abc1111111111111111111111111111', 'dev':False},
         'definitions': {'capabilities': []}, 'translations': {}, 'bindings': {'subjects': [], 'policies': []}}
    records = [{'id':'climate-air','version':'1.0.0','commit':'123456789abc','dev':False,'package':''}]
    # Matching must fail closed for malformed metadata even if inventory matches.
    assert api.active_artifacts({'climate-air': b}, records) == {}
    assert api.active_artifacts({}, records) == {}
    from test_assistant_contract import bundle
    lights = bundle()
    lights['component']['commit'] = '123456789abc1111111111111111111111111111'
    item = {'id':'lights','version':'1.0.0','commit':'123456789abc','dev':False,'package':''}
    assert 'lights' in api.active_artifacts({'lights':lights}, [item])
    assert api.active_artifacts({'lights':lights}, [item | {'package':'device.yaml'}]) == {}
    assert api.active_artifacts({'lights':lights}, [item,item]) == {}


def test_calendar_uses_only_authorized_server_selected_entities_and_rechecks_grant():
    from datetime import date
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    endpoint = {'id':'frame','temperature':[],'calendars':['calendar.shared','calendar.secret']}
    calls=[]
    async def call(domain, service, data, **kwargs):
        calls.append((domain,service,data))
        return {'calendar.shared':{'events':[{'summary':'Visit', 'start':'2026-10-31T13:00:00+00:00','end':'2026-10-31T14:00:00+00:00', 'location':'private'}]}}
    conn=Connection()
    conn.user.permissions.check_entity=lambda eid, policy: eid == 'calendar.shared'
    hass=types.SimpleNamespace(data={'domosenso_frame':{'saved':{'installationId':'0123456789abcdef0123456789abcdef'},'settings':{'stale_seconds':900}}},states=types.SimpleNamespace(get=lambda _:None),services=types.SimpleNamespace(async_call=call),config=types.SimpleNamespace(time_zone='UTC'))
    req={'id':2,'endpoint':'frame','version':1,'capability':'calendar.agenda','revision':1,'answerType':'calendar.v1','arguments':{'period':'today'}}
    asyncio.run(api.query_async(hass,conn,req,lambda *_: ({},endpoint),'read',lambda:now))
    assert calls[0][:2] == ('calendar','get_events')
    assert calls[0][2]['entity_id'] == ['calendar.shared']
    assert conn.results[0]['facts'][0]['title'] == 'Visit'
    assert 'private' not in str(conn.results)
    assert 'calendar.secret' not in str(conn.results)


def test_calendar_revocation_during_await_suppresses_response():
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    endpoint={'id':'frame','temperature':[],'calendars':['calendar.shared']}
    active=True
    def grant(*_):
        if not active:raise ValueError('revoked')
        return {},endpoint
    async def call(*args,**kwargs):
        nonlocal active
        active=False
        return {'calendar.shared':{'events':[]}}
    conn=Connection();conn.user.permissions.check_entity=lambda *_:True
    hass=types.SimpleNamespace(data={'domosenso_frame':{'saved':{'installationId':'0123456789abcdef0123456789abcdef'},'settings':{'stale_seconds':900}}},states=types.SimpleNamespace(get=lambda _:None),services=types.SimpleNamespace(async_call=call),config=types.SimpleNamespace(time_zone='UTC'))
    req={'id':2,'endpoint':'frame','version':1,'capability':'calendar.agenda','revision':1,'answerType':'calendar.v1','arguments':{}}
    asyncio.run(api.query_async(hass,conn,req,grant,'read',lambda:now))
    assert not conn.results
    assert conn.errors[-1][0]=='frame_denied'


def test_inventory_diagnostics_distinguish_unmatched_without_activation():
    from test_assistant_contract import bundle
    lights=bundle()
    record={'id':'lights','version':'1.0.0','commit':'abcdefabcdef','dev':False,'package':''}
    verified, errors=api.inventory_diagnostics({'lights':lights},[record])
    assert verified=={}
    assert errors=={'lights':'contract_mismatch'}
    record['commit']='123456789abc'
    verified, errors=api.inventory_diagnostics({'lights':lights},[record])
    assert set(verified)=={'lights'}
    assert errors=={}


def test_next_calendar_event_ignores_already_finished_events():
    now=datetime(2026,10,31,12,tzinfo=timezone.utc)
    endpoint={'id':'frame','temperature':[],'calendars':['calendar.shared']}
    async def call(*a,**k):
        return {'calendar.shared':{'events':[{'summary':'Earlier','start':'2026-10-31T08:00:00+00:00','end':'2026-10-31T09:00:00+00:00'}, {'summary':'Next','start':'2026-10-31T13:00:00+00:00','end':'2026-10-31T14:00:00+00:00'}]}}
    conn=Connection();conn.user.permissions.check_entity=lambda *_:True
    hass=types.SimpleNamespace(data={'domosenso_frame':{'saved':{'installationId':'0123456789abcdef0123456789abcdef'},'settings':{'stale_seconds':900}}},states=types.SimpleNamespace(get=lambda _:None),services=types.SimpleNamespace(async_call=call),config=types.SimpleNamespace(time_zone='UTC'))
    req={'id':2,'endpoint':'frame','version':1,'capability':'calendar.agenda','revision':1,'answerType':'calendar.v1','arguments':{'nextOnly':True}}
    asyncio.run(api.query_async(hass,conn,req,lambda *_:({},endpoint),'read',lambda:now))
    assert conn.results[0]['facts'][0]['title']=='Next'


def test_invalid_reload_removes_previously_loaded_artifact(tmp_path, monkeypatch):
    ws=types.ModuleType('homeassistant.components.websocket_api');ws.websocket_command=lambda _:lambda fn:fn;ws.require_admin=lambda fn:fn;ws.async_response=lambda fn:fn
    core=types.ModuleType('homeassistant.core');core.callback=lambda fn:fn
    homeassistant=types.ModuleType('homeassistant');homeassistant.__path__=[]
    components=types.ModuleType('homeassistant.components');components.websocket_api=ws
    helpers=types.ModuleType('homeassistant.helpers');helpers.entity_registry=types.SimpleNamespace(async_get=lambda h:types.SimpleNamespace(async_get=lambda eid:None))
    for name,module in [('homeassistant',homeassistant),('homeassistant.components',components),('homeassistant.core',core),('homeassistant.helpers',helpers)]:monkeypatch.setitem(sys.modules,name,module)
    loads=iter([({'lights':{'sentinel':True}},{}),({}, {'lights.yaml':'Invalid assistant artifact'})]);monkeypatch.setattr(api,'load_directory',lambda _:next(loads))
    async def executor(fn,*args):return fn(*args)
    state={};h=types.SimpleNamespace(data={'domosenso_frame':state},config=types.SimpleNamespace(path=lambda:str(tmp_path)),async_add_executor_job=executor)
    handlers=[];asyncio.run(api.setup(h,lambda *_:None,lambda h,f:handlers.append(f),'read'))
    assert 'lights' in state['assistant_artifacts']
    c=Connection();c.user.is_admin=True
    asyncio.run(next(f for f in handlers if f.__name__=='reload')(h,c,{'id':3}))
    assert state['assistant_artifacts']=={} and 'lights.yaml' in state['assistant_errors']


def test_verified_inventory_activates_only_shared_metadata_and_revokes_on_mismatch():
    from test_assistant_contract import bundle
    b=bundle();b['definitions']['capabilities'][0]['required_facts'][0]['role']='light'
    b['bindings']['subjects']=[{'key':'lamp','kind':'light','label':'Catalog lamp','fields':{'light':{'entity':'light.fixture'}}}]
    inventory={'id':'lights','version':'1.0.0','commit':'123456789abc','dev':False,'package':''}
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    states={'sensor.domosenso_components':types.SimpleNamespace(attributes={'components':[inventory]}),'light.fixture':types.SimpleNamespace(state='on',attributes={},last_reported=now)}
    endpoint={'id':'frame','temperature':[],'air':[],'calendars':[],'studio':{'status':[{'entity':'light.fixture','label':'Mapped status'}]}}
    h=types.SimpleNamespace(data={'domosenso_frame':{'saved':{'installationId':'0123456789abcdef0123456789abcdef'},'settings':{'stale_seconds':900},'assistant_artifacts':{'lights':b}}},states=types.SimpleNamespace(get=states.get))
    c=Connection();c.user.permissions.check_entity=lambda *_:True
    req={'id':1,'endpoint':'frame','version':1,'supported':[{'queryType':'light-query.v1','answerType':'lights.v1'}]}
    api.capabilities(h,c,req,lambda *_:({},endpoint),'read',lambda:now)
    assert c.results[-1]['capabilities'][0]['id']=='lights.status'
    inventory['version']='2.0.0'
    api.capabilities(h,c,req,lambda *_:({},endpoint),'read',lambda:now)
    assert c.results[-1]['capabilities']==[]


def test_calendar_remapping_during_await_does_not_release_old_events():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    endpoint={'id':'frame','temperature':[],'calendars':['calendar.shared']}
    async def call(*args,**kwargs):
        endpoint['calendars'].clear()
        return {'calendar.shared':{'events':[{'summary':'Private','start':'2026-10-31T13:00:00+00:00','end':'2026-10-31T14:00:00+00:00'}]}}
    conn=Connection();conn.user.permissions.check_entity=lambda *_:True
    hass=types.SimpleNamespace(data={'domosenso_frame':{'saved':{'installationId':'0123456789abcdef0123456789abcdef'},'settings':{'stale_seconds':900}}},states=types.SimpleNamespace(get=lambda _:None),services=types.SimpleNamespace(async_call=call),config=types.SimpleNamespace(time_zone='UTC'))
    req={'id':2,'endpoint':'frame','version':1,'capability':'calendar.agenda','revision':1,'answerType':'calendar.v1','arguments':{}}
    asyncio.run(api.query_async(hass,conn,req,lambda *_:({},endpoint),'read',lambda:now))
    assert not conn.results
    assert conn.errors[-1][0]=='frame_denied'


def test_forecast_service_rechecks_grant_and_in_place_mapping_after_await():
    now=datetime(2026,10,31,12,tzinfo=timezone.utc)
    endpoint={'id':'frame','assistant':{'subjects':[{'key':'weather','kind':'weather','label':'Weather','fields':{'weather':{'entity':'weather.one'}}}]}}
    active=True; mutate=False; calls=[]
    def grant(*_):
        if not active: raise ValueError('revoked')
        return {},endpoint
    async def service(domain,name,data,**kwargs):
        nonlocal active
        calls.append((domain,name,data,kwargs))
        if mutate: endpoint['assistant']['subjects'][0]['fields']['weather']['entity']='weather.two'
        else: active=False
        return {'weather.one':{'forecast':[]}}
    conn=Connection();conn.user.permissions.check_entity=lambda *_:True
    hass=types.SimpleNamespace(data={'domosenso_frame':{'saved':{'installationId':'0123456789abcdef0123456789abcdef'},'settings':{'stale_seconds':900}}},states=types.SimpleNamespace(get=lambda _:None),services=types.SimpleNamespace(async_call=service),config=types.SimpleNamespace(time_zone='UTC'))
    req={'id':1,'endpoint':'frame','version':1,'capability':'weather.forecast','revision':1,'answerType':'weather.v1','arguments':{}}
    asyncio.run(api.query_async(hass,conn,req,grant,'read',lambda:now))
    assert calls[0][0:2]==('weather','get_forecasts') and calls[0][2]['entity_id']==['weather.one']
    assert conn.errors and not conn.results
    active=True;mutate=True;conn.errors.clear()
    asyncio.run(api.query_async(hass,conn,req,grant,'read',lambda:now))
    assert conn.errors and not conn.results


def test_calendar_malformed_event_makes_valid_subset_incomplete():
    now=datetime(2026,10,31,12,tzinfo=timezone.utc)
    endpoint={'id':'frame','calendars':['calendar.shared']}
    async def call(*args,**kwargs):
        return {'calendar.shared':{'events':[{'start':'bad','end':'bad'},{'summary':'Visit','start':'2026-10-31T13:00:00+00:00','end':'2026-10-31T14:00:00+00:00'}]}}
    conn=Connection(); conn.user.permissions.check_entity=lambda *_:True
    hass=types.SimpleNamespace(data={'domosenso_frame':{'saved':{'installationId':'0123456789abcdef0123456789abcdef'},'settings':{'stale_seconds':900}}},states=types.SimpleNamespace(get=lambda _:None),services=types.SimpleNamespace(async_call=call),config=types.SimpleNamespace(time_zone='UTC'))
    req={'id':2,'endpoint':'frame','version':1,'capability':'calendar.agenda','revision':1,'answerType':'calendar.v1','arguments':{}}
    asyncio.run(api.query_async(hass,conn,req,lambda *_:({},endpoint),'read',lambda:now))
    result=conn.results[0]
    assert result['availability']=='partial'
    assert result['coverage']['missing']==1
    assert result['facts'][0]['title']=='Visit'
    assert 'missing_source' in result['limitations']


@pytest.mark.parametrize('event', [
    {'start':'bad','end':'bad'},
    {'start':'2026-10-31T13:00:00','end':'2026-10-31T14:00:00'},
    {'start':'2026-10-31T14:00:00+00:00','end':'2026-10-31T13:00:00+00:00'},
])
def test_calendar_invalid_only_is_unknown_not_empty(event):
    now=datetime(2026,10,31,12,tzinfo=timezone.utc)
    endpoint={'id':'frame','calendars':['calendar.shared']}
    async def call(*args,**kwargs): return {'calendar.shared':{'events':[event]}}
    conn=Connection();conn.user.permissions.check_entity=lambda *_:True
    hass=types.SimpleNamespace(data={'domosenso_frame':{'saved':{'installationId':'0123456789abcdef0123456789abcdef'},'settings':{'stale_seconds':900}}},states=types.SimpleNamespace(get=lambda _:None),services=types.SimpleNamespace(async_call=call),config=types.SimpleNamespace(time_zone='UTC'))
    req={'id':2,'endpoint':'frame','version':1,'capability':'calendar.agenda','revision':1,'answerType':'calendar.v1','arguments':{}}
    asyncio.run(api.query_async(hass,conn,req,lambda *_:({},endpoint),'read',lambda:now))
    result=conn.results[0]
    assert result['availability']=='unavailable' and result['summary']['status']=='unknown'
    assert result['coverage']['missing']==1 and 'missing_source' in result['limitations']
