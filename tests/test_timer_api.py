"""Wire tests exercise real executor and assistant API without a production HA."""
import asyncio
import importlib
import types
import pytest
from test_timers import Rig, run


def module():
    try: return importlib.import_module('timer_package.timer_api')
    except ModuleNotFoundError: pytest.fail('Timer API missing')


class Connection:
    def __init__(self):
        self.user = types.SimpleNamespace(is_active=True, id='fictional-user', permissions=types.SimpleNamespace(check_entity=lambda *_: True))
        self.results, self.errors = [], []
    def send_result(self, _, result): self.results.append(result)
    def send_error(self, _, code, message): self.errors.append((code, message))
    def context(self, _): return types.SimpleNamespace(id='own-context')


def api_rig():
    r = Rig(); c = Connection()
    async def call(domain, name, data, **kwargs):
        assert domain == 'timer' and kwargs['blocking']
        await r.service(name, data, kwargs['context'].id)
    h = types.SimpleNamespace(data={'domosenso_frame': {'timers': r.engine, 'saved': {'installationId': r.engine.installation, 'devices': r.devices}, 'settings': {'stale_seconds': 900}}}, states=types.SimpleNamespace(get=r.states.get), services=types.SimpleNamespace(async_call=call), config=types.SimpleNamespace(time_zone='Europe/Warsaw'))
    def grant(*_): return {}, r.authorize()
    return r, h, c, grant


def test_actions_negotiate_only_explicit_write_grant_and_read_control_permission():
    api = module(); r,h,c,grant = api_rig()
    req = {'id':1, 'version':1, 'endpoint':'frame','supported':[{'actionType':'timer-action.v1','resultType':'timer-result.v1'}]}
    api.actions(h,c,req,grant,'read','control')
    assert len(c.results[-1]['actions']) == 4
    assert c.results[-1]['actions'][0]['subjects'][0]['id'] == r.fact()['subjectId']
    assert 'timer.demo' not in str(c.results)
    r.endpoint['timers']['writeEnabled'] = False
    api.actions(h,c,req,grant,'read','control'); assert c.results[-1]['actions'] == []
    r.endpoint['timers']['writeEnabled'] = True
    c.user.permissions.check_entity = lambda _, policy: policy == 'read'
    api.actions(h,c,req,grant,'read','control'); assert c.results[-1]['actions'] == []
    api.actions(h,c,req|{'supported':[]},grant,'read','control'); assert c.results[-1]['actions'] == []


def test_read_contract_time_and_timer_uses_existing_envelope_without_entity_ids():
    api = importlib.import_module('timer_package.assistant_api'); r,h,c,grant = api_rig()
    req = {'id':2,'version':1,'endpoint':'frame','supported':[{'queryType':'timer-query.v1','answerType':'timer-status.v1'}, {'queryType':'time-query.v1','answerType':'time.v1'}]}
    api.capabilities(h,c,req,grant,'read',lambda:r.now)
    assert {cap['id'] for cap in c.results[-1]['capabilities']} == {'timers.status','ramka.current_time'}
    for cap, typ in [('timers.status','timer-status.v1'), ('ramka.current_time','time.v1')]:
        asyncio.run(api.query_async(h,c,{'id':3,'version':1,'endpoint':'frame','capability':cap,'revision':1,'answerType':typ,'arguments':{}},grant,'read',lambda:r.now))
        result = c.results[-1]
        assert result['availability'] == 'ready' and result['evidence'][0]['freshnessBasis'] == 'provider_response'
        assert result['facts'][0]['subjectId'] and 'timer.demo' not in str(result)
    assert c.results[-1]['facts'][0]['timezone'] == 'Europe/Warsaw'
    assert c.results[-1]['facts'][0]['instant'] == r.now.isoformat()
    assert c.results[-1]['summary']['status'] == 'reported'


def test_action_and_acknowledgement_validate_scope_and_recheck_revocation():
    async def case():
        api = module(); r,h,c,grant = api_rig(); req = r.request()|{'id':4}
        await api.action(h,c,req,grant,'read','control')
        assert c.results[-1]['status'] == 'applied'
        fact = c.results[-1]['timer']
        await api.acknowledge(h,c,{'id':5,'version':1,'endpoint':'frame','subjectId':fact['subjectId'],'runId':fact['runId']},grant,'read')
        assert c.results[-1]['timer']['acknowledged']
        before = len(c.results); r.allowed = False
        await api.action(h,c,req,grant,'read','control')
        assert len(c.results) == before and c.errors[-1][0] == 'frame_denied'
    run(case)


@pytest.mark.parametrize('field,value', [('version',True), ('arguments',{'service':'turn_on'}),('endpoint','other'),('capability','timer.start')])
def test_action_rejects_invalid_wire(field,value):
    async def case():
        api = module(); r,h,c,grant = api_rig()
        await api.action(h,c,r.request()|{'id':6,field:value},grant,'read','control')
        assert c.errors and not c.results and not r.calls
    run(case)


def test_verified_command_metadata_localizes_only_mapped_helpers_and_never_grants():
    from test_timer_contract import actions_bundle
    api=module();r,h,c,grant=api_rig();b=actions_bundle()
    for lang in ('en','de','pl'): b['translations'][lang]['meta']['assistant']['status']['title']='Catalog timer'
    h.data['domosenso_frame']['assistant_action_artifacts']={'timers':b}
    r.states['sensor.domosenso_components']=types.SimpleNamespace(attributes={'components':[dict(b['component'],package='')]})
    req={'id':1,'version':1,'endpoint':'frame','supported':[{'actionType':'timer-action.v1','resultType':'timer-result.v1'}]}
    api.actions(h,c,req,grant,'read','control')
    assert c.results[-1]['actions'][0]['title']['en']=='Catalog timer'
    r.endpoint['timers']['items']=[]
    api.actions(h,c,req,grant,'read','control')
    assert c.results[-1]['actions']==[]


def test_shared_fixture_matches_actual_wire_and_contains_no_ha_entity_ids():
    import json
    from pathlib import Path
    from generate_timer_fixture import build
    raw=Path(__file__).with_name('fixtures').joinpath('shared-timers.json').read_text()
    assert json.loads(raw)==build()
    assert 'timer.demo' not in raw and 'entity_id' not in raw
    assert json.loads(raw)['completed']['facts'][0]['outcome']=='completed'


def test_late_finish_event_cannot_turn_a_later_cancelled_run_into_completion():
    api=module();r,h,c,grant=api_rig()
    run(lambda:r.execute(r.request()))
    run(lambda:r.execute(r.request('cancel',requestId='cancel')))
    r.states['timer.demo'].attributes['last_transition']='cancelled'
    before=r.fact()
    assert hasattr(api,'observe_event'), 'Lifecycle adapter missing'
    event=types.SimpleNamespace(event_type='timer.finished',data={'entity_id':'timer.demo','finished_at':before['deadline']},context=types.SimpleNamespace(id='old'))
    api.observe_event(r.engine,event,h.states.get)
    assert r.fact()==before


def test_unknown_timer_summary_never_counts_more_affected_than_evaluated():
    api = module(); r,h,c,grant = api_rig()
    r.states['timer.demo'].state = 'unavailable'
    msg = {'capability':'timers.status','revision':1,'answerType':'timer-status.v1','arguments':{},'endpoint':'frame'}
    result = api.read_query(h,c,msg,grant,'read',lambda:r.now)
    assert result['summary'] == {'status':'unknown','affected':0}
    assert result['availability'] == 'unavailable' and result['coverage']['evaluated'] == 0
    r.endpoint['timers']['items'].append({'entity':'timer.second','label':'Second','alarmDeviceId':None})
    r.states['timer.second'] = r.state('idle'); r.states['timer.second'].entity_id = 'timer.second'
    result = api.read_query(h,c,msg,grant,'read',lambda:r.now)
    assert result['summary'] == {'status':'mixed','affected':1}
    assert result['availability'] == 'partial' and result['coverage']['evaluated'] == 1


def test_runtime_localization_uses_separate_identity_checked_store_and_is_optional():
    from test_timer_contract import runtime_bundle
    api = module(); r,h,c,grant = api_rig(); b = runtime_bundle()
    for lang in ('en','de','pl'): b['translations'][lang]['meta']['assistant']['status']['title'] = 'Catalog clock'
    r.states['sensor.domosenso_components'] = types.SimpleNamespace(attributes={'components':[dict(b['component'],package='')]})
    state = h.data['domosenso_frame']; state['assistant_artifacts'] = {'ramka':b}
    assert api.localized_metadata(h,'ramka.current_time')['title']['en'] == 'Current time'
    state['assistant_runtime_artifacts'] = {'ramka':b}
    assert api.localized_metadata(h,'ramka.current_time')['title']['en'] == 'Catalog clock'
    r.states['sensor.domosenso_components'].attributes['components'][0]['dev'] = True
    assert api.localized_metadata(h,'ramka.current_time')['title']['en'] == 'Current time'
    r.endpoint['timers']['items'] = []
    caps = api.read_capabilities(h,c,{'endpoint':'frame','supported':[{'queryType':'time-query.v1','answerType':'time.v1'}]},grant,'read',lambda:r.now)
    assert [cap['id'] for cap in caps] == ['ramka.current_time']
