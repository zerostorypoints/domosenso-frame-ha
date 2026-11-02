"""Fictional compiler bindings, explicit source grants, and stable identities."""
import importlib, sys, types
from pathlib import Path
from datetime import datetime, timezone

pkg=types.ModuleType('frame_activation_package');pkg.__path__=[str(Path(__file__).parents[1]/'custom_components/domosenso_frame')];sys.modules[pkg.__name__]=pkg
mod=importlib.import_module(pkg.__name__+'.assistant_data')


def test_metadata_only_adds_explicitly_shared_roles_and_preserves_canonical_keys():
    bundle={'bindings':{'subjects':[{'key':'device','kind':'player','label':'Metadata','fields':{'player':{'entity':'media_player.one'},'quality':{'entity':'sensor.private'}}}],'policies':[]}}
    endpoint={'id':'frame','assistant':{'subjects':[{'key':'chosen','kind':'player','label':'Chosen','fields':{'player':{'entity':'media_player.one'}}}]}}
    result=mod.merge_verified_bindings(endpoint,{'media':bundle})
    assert result[0]['label']=='Chosen'
    assert set(result[0]['fields'])=={'player'}
    assert result[0]['fields']['player']['entity']=='media_player.one'
    assert mod.merge_verified_bindings({'id':'frame','assistant':{'subjects':[]}}, {'media':bundle})==[]


def test_registry_identity_and_explicit_room_id_survive_entity_rename():
    install='0123456789abcdef0123456789abcdef'
    a={'key':'old','kind':'player','label':'First','roomKey':'kitchen','fields':{'player':{'entity':'media_player.old'}}}
    b={'key':'new','kind':'player','label':'Renamed','roomKey':'kitchen','fields':{'player':{'entity':'media_player.new'}}}
    old=mod.authorized_subjects([a],{'media_player.old'},lambda _:True,None,install,'frame',registry_id=lambda _: 'stable-registry-id')[0]
    new=mod.authorized_subjects([b],{'media_player.new'},lambda _:True,None,install,'frame',registry_id=lambda _: 'stable-registry-id')[0]
    assert old['id']==new['id']
    assert old['roomId']==new['roomId']


def test_compiler_policy_join_checks_exact_tuple_and_helper_permission():
    policy={'owner':'ramka','metric':'co2','entity':'sensor.co2','unit':'ppm','levels':[800,1200],'higher_is_better':False,'helpers':{'raised':'input_number.first','high':'input_number.second'}}
    states={'input_number.first':types.SimpleNamespace(state='700'),'input_number.second':types.SimpleNamespace(state='1100')}
    selected,reason=mod.resolve_air_policy('co2','sensor.co2','ppm',None,[policy],states.get,lambda _:True)
    assert reason is None and selected['levels']==[700,1100]
    assert mod.resolve_air_policy('co2','sensor.co2','ppm',None,[policy,policy|{'levels':[900,1300]}],states.get,lambda _:True)[1]=='conflicting_policy'
    assert mod.resolve_air_policy('co2','sensor.co2','ppm',None,[policy],states.get,lambda _:False)[1]=='missing_policy'
    assert mod.resolve_air_policy('co2','sensor.other','ppm',None,[policy],states.get,lambda _:True)[1]=='missing_policy'


def test_room_selector_filters_exposed_light_members_without_fallback():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    endpoint={'id':'frame','assistant':{'subjects':[{'key':'one','kind':'light','label':'One','roomKey':'room_a','fields':{'light':{'entity':'light.one'}}},{'key':'two','kind':'light','label':'Two','roomKey':'room_b','fields':{'light':{'entity':'light.two'}}}]}}
    state=types.SimpleNamespace(state='on',attributes={},last_reported=now)
    engine=mod.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now)
    cap=engine.capabilities(endpoint,[{'queryType':'light-query.v1','answerType':'lights.v1'}])[0]
    room=cap['subjects'][0]['roomId']
    result=engine.query(endpoint,'lights.status',1,'lights.v1',{'roomId':room})
    assert len(result['facts'])==1 and result['facts'][0]['label']=='One'


def test_metadata_policy_is_used_by_air_query_and_helper_updates_are_live():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    policy={'owner':'ramka','metric':'co2','entity':'sensor.co2','unit':'ppm','levels':[800,1200],'higher_is_better':False,'helpers':{'raised':'input_number.first','high':'input_number.second'}}
    states={'sensor.co2':types.SimpleNamespace(state='900',attributes={'unit_of_measurement':'ppm'},last_reported=now),'input_number.first':types.SimpleNamespace(state='800'),'input_number.second':types.SimpleNamespace(state='1200')}
    engine=mod.Runtime('0123456789abcdef0123456789abcdef',states.get,lambda _:True,lambda:now,verified_source=lambda _:True,artifacts={'ramka':{'bindings':{'subjects':[],'policies':[policy]}}})
    endpoint={'id':'frame','air':[{'entity':'sensor.co2','label':'Room','metric':'co2'}]}
    answer=engine.query(endpoint,'climate-air.air_assessment',1,'air.v1',{})
    assert answer['summary']['status']=='raised'
    states['input_number.first'].state='950'
    assert engine.query(endpoint,'climate-air.air_assessment',1,'air.v1',{})['summary']['status']=='good'


def test_manual_calendar_is_sensitive_by_default_but_legacy_calendar_remains():
    engine=mod.Runtime('0123456789abcdef0123456789abcdef',lambda _:None,lambda _:True,lambda:datetime(2026,10,31,tzinfo=timezone.utc))
    endpoint={'id':'frame','calendars':['calendar.legacy'],'assistant':{'subjects':[{'key':'other','kind':'calendar','label':'Private','fields':{'calendar':{'entity':'calendar.manual'}}}]}}
    assert len(engine._calendars(engine.effective_endpoint(endpoint)))==1
    endpoint['assistant']['calendar_sensitive']=True
    assert len(engine._calendars(engine.effective_endpoint(endpoint)))==2


def test_backup_reported_count_does_not_require_physical_sensor_registry():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    endpoint={'id':'frame','assistant':{'subjects':[{'key':'backup','kind':'backup','label':'Backup','fields':{'count':{'entity':'sensor.backup_count'}}}]}}
    state=types.SimpleNamespace(state='1',attributes={},last_reported=now)
    engine=mod.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now)
    assert engine.query(endpoint,'backup.status',1,'backup.v1',{})['summary']['status']=='present'


def test_air_canonical_metadata_role_is_read_when_explicitly_mapped():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    bundle={'bindings':{'subjects':[{'key':'air','kind':'room','label':'Study','fields':{'co2':{'entity':'sensor.co2'}}}],'policies':[]}}
    endpoint={'id':'frame','studio':{'status':[{'entity':'sensor.co2','label':'Air'}]}}
    state=types.SimpleNamespace(state='550',attributes={'unit_of_measurement':'ppm'},last_reported=now)
    engine=mod.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now,verified_source=lambda _:True,artifacts={'climate-air':bundle})
    answer=engine.query(endpoint,'climate-air.air_readings',1,'air.v1',{})
    assert answer['facts'][0]['value']==550


def test_optional_unauthorized_field_does_not_hide_authorized_primary_source():
    row={'key':'player','kind':'player','label':'Player','fields':{'player':{'entity':'media_player.ok'},'quality':{'entity':'sensor.private'}}}
    result=mod.authorized_subjects([row],{'media_player.ok','sensor.private'},lambda eid:eid!='sensor.private',None,'0123456789abcdef0123456789abcdef','frame')
    assert len(result)==1
    assert set(result[0]['fields'])=={'player'}


def test_air_protocol_canonicalizes_known_equivalent_pm_unit():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    state=types.SimpleNamespace(state='3',attributes={'unit_of_measurement':'µg/m3'},last_reported=now)
    endpoint={'id':'frame','air':[{'entity':'sensor.pm','label':'PM','metric':'pm2_5'}]}
    engine=mod.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now,verified_source=lambda _:True)
    assert engine.query(endpoint,'climate-air.air_readings',1,'air.v1',{})['facts'][0]['unit']=='µg/m³'


def test_unlabeled_air_index_is_not_presented_as_usable():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    state=types.SimpleNamespace(state='10',attributes={},last_reported=now)
    endpoint={'id':'frame','air':[{'entity':'sensor.voc','label':'Air','metric':'voc'}]}
    engine=mod.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now,verified_source=lambda _:True)
    answer=engine.query(endpoint,'climate-air.air_readings',1,'air.v1',{})
    assert answer['availability']=='unavailable'
    assert 'invalid_unit' in answer['limitations']
