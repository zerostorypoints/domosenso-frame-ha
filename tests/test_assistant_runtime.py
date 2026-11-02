"""Runtime authorization tests use fictional entity ids and no HA services."""
import importlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import sys
import types
package = types.ModuleType('frame_runtime_package')
package.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[package.__name__] = package
runtime = importlib.import_module('frame_runtime_package.assistant_data')


def test_unitless_air_indexes_use_typed_wire_units_without_accepting_unitless_co2():
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    for metric, value, expected_unit in [('voc', 100, 'index'), ('nox', 1, 'index'), ('score', 90, '%'), ('co2', 800, None)]:
        sample = SimpleNamespace(state=str(value), attributes={}, last_reported=now)
        endpoint = {'id':'frame', 'air':[{'entity':'sensor.air', 'label':'Living room', 'metric':metric}]}
        api = importlib.import_module('frame_runtime_package.assistant_api')
        key = {'voc':'voc_index','nox':'nox_index','score':'iaqs','co2':'carbon_dioxide'}[metric]
        entry = SimpleNamespace(platform='ruuvitag_ble', entity_id='sensor.air', unique_id='AA:BB:CC:DD:EE:FF-'+key)
        engine = runtime.Runtime('0123456789abcdef0123456789abcdef', lambda _:api.state_for_assistant(sample, entry), lambda _:True, lambda:now, verified_source=lambda _:True)
        result = engine.query(endpoint, 'climate-air.air_readings', 1, 'air.v1', {})
        if expected_unit:
            assert result['availability'] == 'ready'
            assert result['facts'][0]['unit'] == expected_unit
            assert result['facts'][0]['value'] == value
            assert sample.attributes == {}  # never mutate HA state or its settings
            entry.platform = 'template'
            assert engine.query(endpoint, 'climate-air.air_readings', 1, 'air.v1', {})['availability'] == 'unavailable'
            entry.platform = 'ruuvitag_ble'
            sample.attributes['restored'] = True
            assert engine.query(endpoint, 'climate-air.air_readings', 1, 'air.v1', {})['availability'] == 'unavailable'
            sample.attributes.clear()
            sample.last_reported = now.replace(day=30)
            assert engine.query(endpoint, 'climate-air.air_readings', 1, 'air.v1', {})['availability'] == 'unavailable'
        else:
            assert result['availability'] == 'unavailable'
            assert 'invalid_unit' in result['limitations']


def test_capabilities_filter_unmapped_and_denied_sources_before_state_reads():
    subjects = [{'key': 'allowed', 'kind': 'room', 'label': 'Living', 'fields': {'temperature': {'entity': 'sensor.living'}}},
                {'key': 'hidden', 'kind': 'room', 'label': 'Private', 'fields': {'temperature': {'entity': 'sensor.private'}}},
                {'key': 'unmapped', 'kind': 'room', 'label': 'Other', 'fields': {'temperature': {'entity': 'sensor.other'}}}]
    reads = []
    def state(eid):
        reads.append(eid)
        return SimpleNamespace(state='21', attributes={'unit_of_measurement': '°C'}, last_reported=datetime(2026, 10, 31, tzinfo=timezone.utc))
    result = runtime.authorized_subjects(subjects, {'sensor.living', 'sensor.private'}, lambda e: e != 'sensor.private', state, '0123456789abcdef0123456789abcdef', 'frame')
    assert [s['label'] for s in result] == ['Living']
    assert reads == []  # discovery performs no entity reads
    assert result[0]['id'] != 'sensor.living'


def test_no_implicit_light_group_child_or_sensitive_exposure():
    subjects = [{'key': 'group', 'kind': 'light', 'label': 'Group', 'fields': {'state': {'entity': 'light.group'}}},
                {'key': 'child', 'kind': 'light', 'label': 'Child', 'fields': {'state': {'entity': 'light.child'}}},
                {'key': 'phone', 'kind': 'presence_source', 'label': 'Phone', 'fields': {'state': {'entity': 'sensor.phone'}}}]
    result = runtime.authorized_subjects(subjects, {'light.group', 'sensor.phone'}, lambda _: True, lambda _: None, '0123456789abcdef0123456789abcdef', 'frame')
    assert [s['label'] for s in result] == ['Group']
    exposed = runtime.authorized_subjects(subjects, {'light.group', 'sensor.phone'}, lambda _: True, lambda _: None, '0123456789abcdef0123456789abcdef', 'frame', sensitive=True)
    assert [s['label'] for s in exposed] == ['Group', 'Phone']


def test_manual_temperature_query_rechecks_permissions_and_reports_partial_coverage():
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    endpoint = {'id': 'frame', 'temperature': [{'entity': 'sensor.one', 'label': 'One'}, {'entity': 'sensor.two', 'label': 'Two'}], 'air': [], 'calendars': []}
    states = {'sensor.one': SimpleNamespace(state='-5', attributes={'unit_of_measurement': '°C'}, last_reported=now), 'sensor.two': SimpleNamespace(state='unknown', attributes={}, last_reported=now)}
    engine = runtime.Runtime('0123456789abcdef0123456789abcdef', lambda e: states.get(e), lambda e: True, lambda: now, verified_source=lambda _:True)
    caps = engine.capabilities(endpoint, [{'queryType': 'temperature-query.v1', 'answerType': 'temperature.v1'}])
    assert [c['id'] for c in caps] == ['climate-air.temperature']
    answer = engine.query(endpoint, 'climate-air.temperature', 1, 'temperature.v1', {})
    assert answer['coverage'] == {'eligible': 2, 'evaluated': 1, 'missing': 1}
    assert answer['availability'] == 'partial'
    assert answer['facts'][0]['value'] == -5
    assert 'sensor.one' not in str(answer)
    engine.permitted = lambda e: e != 'sensor.one'
    answer = engine.query(endpoint, 'climate-air.temperature', 1, 'temperature.v1', {})
    assert answer['coverage']['eligible'] == 1
    assert answer['availability'] == 'unavailable'


def test_client_cannot_select_arbitrary_entity_or_request_an_action():
    import pytest
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    engine = runtime.Runtime('0123456789abcdef0123456789abcdef', lambda e: None, lambda e: True, lambda: now, verified_source=lambda _:True)
    endpoint = {'id': 'frame', 'temperature': [{'entity': 'sensor.one', 'label': 'One'}], 'air': [], 'calendars': []}
    for args in ({'entity': 'sensor.secret'}, {'roomId': 'sensor.one'}, {'action': 'turn_on'}):
        with pytest.raises(ValueError):
            engine.query(endpoint, 'climate-air.temperature', 1, 'temperature.v1', args)


def test_manual_light_mapping_reads_group_as_group_only_without_children():
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    state = SimpleNamespace(state='on', attributes={'entity_id': ['light.hidden'], 'brightness': 128}, last_reported=now)
    reads = []
    def get(eid):
        reads.append(eid)
        return state
    endpoint = {'id': 'frame', 'temperature': [], 'assistant': {'sensitive': False, 'subjects': [{'key': 'group', 'kind': 'light', 'label': 'Group', 'fields': {'state': {'entity': 'light.group'}}}]}}
    engine = runtime.Runtime('0123456789abcdef0123456789abcdef', get, lambda e: e == 'light.group', lambda: now, verified_source=lambda _:True)
    caps = engine.capabilities(endpoint, [{'queryType': 'light-query.v1', 'answerType': 'lights.v1'}])
    assert caps[0]['id'] == 'lights.status'
    answer = engine.query(endpoint, 'lights.status', 1, 'lights.v1', {})
    assert answer['facts'][0]['countUnit'] == 'group'
    assert answer['facts'][0]['state'] == 'on'
    assert 'light.hidden' not in str(answer)
    assert set(reads) == {'light.group'}


def test_media_stopped_metadata_is_last_known_and_optional_details_are_bounded():
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    states = {'media_player.player': SimpleNamespace(state='idle', attributes={'media_title':'Old song', 'media_artist':'Artist', 'volume_level':0.5, 'is_volume_muted':False}, last_reported=now)}
    endpoint = {'id':'frame', 'temperature':[], 'studio':{'media':{'entity':'media_player.player'}}}
    engine = runtime.Runtime('0123456789abcdef0123456789abcdef', states.get, lambda _: True, lambda:now, verified_source=lambda _:True)
    result = engine.query(endpoint, 'media.now_playing', 1, 'media.v1', {})
    assert result['facts'][0]['metadataStatus'] == 'last_known'
    assert result['facts'][0]['title'] == 'Old song'
    assert result['facts'][0]['volumePercent'] == 50
    assert 'media_player.player' not in str(result)


def test_explicit_manual_temperature_mapping_is_usable_without_legacy_settings():
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    subject = {'key':'room-1','kind':'room','label':'Room','fields':{'temperature':{'entity':'sensor.temp'}}}
    endpoint = {'id':'frame','assistant':{'subjects':[subject], 'sensitive':False}}
    state = SimpleNamespace(state='22',attributes={'unit_of_measurement':'°C'},last_reported=now)
    engine = runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now, verified_source=lambda _:True)
    assert engine.query(endpoint,'climate-air.temperature',1,'temperature.v1',{})['facts'][0]['value'] == 22


def test_air_assessment_uses_configured_live_policy_not_invented_thresholds():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    policy={'higher_is_better':False,'levels':[[800,'good'],[1200,'raised'],[None,'high']],'source':'fictional'}
    endpoint={'id':'frame','temperature':[],'air':[{'entity':'sensor.co2','label':'Room','metric':'co2','policy':policy}]}
    state=SimpleNamespace(state='1300',attributes={'unit_of_measurement':'ppm'},last_reported=now)
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now, verified_source=lambda _:True)
    answer=engine.query(endpoint,'climate-air.air_assessment',1,'air.v1',{})
    assert answer['summary']['status']=='high'
    endpoint['air'][0]['policy']['levels'][1][0]=1500
    assert engine.query(endpoint,'climate-air.air_assessment',1,'air.v1',{})['summary']['status']=='raised'
    del endpoint['air'][0]['policy']
    answer=engine.query(endpoint,'climate-air.air_assessment',1,'air.v1',{})
    assert answer['summary']['status']=='unknown'
    assert 'missing_policy' in answer['limitations']


def test_awning_status_qualifies_reported_position_not_physical_feedback():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    state=SimpleNamespace(state='open',attributes={'current_position':80},last_reported=now)
    endpoint={'id':'frame','temperature':[],'studio':{'covers':[{'entity':'cover.awning','label':'Patio'}]}}
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now, verified_source=lambda _:True)
    answer=engine.query(endpoint,'awnings.status',1,'awnings.v1',{})
    assert answer['facts'][0]['positionPercent']==80
    assert answer['facts'][0]['positionBasis']=='reported'
    assert answer['summary']['basis']=='reported'


def test_unverified_template_report_does_not_become_current_measurement():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    state=SimpleNamespace(state='20',attributes={'unit_of_measurement':'°C'},last_reported=now)
    endpoint={'id':'frame','temperature':[{'entity':'sensor.template','label':'Room'}]}
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now,verified_source=lambda _:False)
    result=engine.query(endpoint,'climate-air.temperature',1,'temperature.v1',{})
    assert result['availability']=='unavailable'
    assert result['facts'][0]['status']=='unverified'
    assert 'unknown_freshness' in result['limitations']


def test_weather_current_uses_only_allowed_weather_attributes():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    state=SimpleNamespace(state='rainy',attributes={'temperature':11,'temperature_unit':'°C','humidity':75,'secret':'do not reveal'},last_reported=now)
    endpoint={'id':'frame','temperature':[],'display':{'weather':'weather.example'}}
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now,verified_source=lambda _:True)
    answer=engine.query(endpoint,'weather.current',1,'weather.v1',{})
    assert answer['facts'][0]['condition']=='rainy'
    assert answer['facts'][0]['measurements'][0]['value']==11
    assert 'secret' not in str(answer)


def test_presence_requires_explicit_exposure_and_qualifies_device_observation():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    s={'key':'phone','kind':'presence_source','label':'Phone','fields':{'state':{'entity':'device_tracker.example'}},'limits':{'observation_kind':'ble_device'}}
    endpoint={'id':'frame','temperature':[],'assistant':{'subjects':[s],'sensitive':False}}
    state=SimpleNamespace(state='home',attributes={},last_reported=now)
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now,verified_source=lambda _:True)
    assert engine.capabilities(endpoint,[{'queryType':'presence-query.v1','answerType':'presence.v1'}]) == []
    endpoint['assistant']['sensitive']=True
    answer=engine.query(endpoint,'bermuda-presence.observations',1,'presence.v1',{})
    assert answer['facts'][0]['observationKind']=='ble_device'
    assert answer['facts'][0]['detected'] is True
    assert 'device_tracker.example' not in str(answer)


def test_audio_negative_dbfs_is_reported_uncalibrated_without_inference():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    subject={'key':'sound','kind':'audio_source','label':'Sound','fields':{'level':{'entity':'sensor.sound'}},'limits':{'integration_window_seconds':1,'weighting':'unweighted'}}
    endpoint={'id':'frame','temperature':[],'assistant':{'subjects':[subject],'sensitive':False}}
    state=SimpleNamespace(state='-38',attributes={'unit_of_measurement':'dBFS'},last_reported=now)
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now,verified_source=lambda _:True)
    answer=engine.query(endpoint,'audio-sensor.level',1,'audio-level.v1',{})
    assert answer['facts'][0]['level']==-38
    assert answer['facts'][0]['calibrated'] is False
    assert 'uncalibrated' in answer['limitations']


def test_backup_zero_count_does_not_report_sentinel_age_as_real_archive():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    subject={'key':'backup','kind':'backup','label':'Backups','fields':{'archive_count':{'entity':'sensor.count'},'newest_age_hours':{'entity':'sensor.age'}}}
    endpoint={'id':'frame','temperature':[],'assistant':{'subjects':[subject],'sensitive':False}}
    states={'sensor.count':SimpleNamespace(state='0',attributes={},last_reported=now),'sensor.age':SimpleNamespace(state='9999',attributes={'unit_of_measurement':'h'},last_reported=now)}
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',states.get,lambda _:True,lambda:now,verified_source=lambda _:True)
    answer=engine.query(endpoint,'backup.status',1,'backup.v1',{})
    assert answer['summary']['status']=='missing'
    assert answer['facts'][0]['newestAgeHours'] is None
    assert answer['facts'][0]['verification']=='existence_only'


def test_frame_status_reports_only_permitted_explicit_roles_and_unknown_physical_display():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    subject={'key':'display','kind':'frame','label':'Frame','fields':{'mode':{'entity':'input_select.frame_mode'}}}
    endpoint={'id':'frame','temperature':[],'assistant':{'subjects':[subject],'sensitive':False}}
    state=SimpleNamespace(state='ambient',attributes={'secret':'private'},last_reported=now)
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now,verified_source=lambda _:True)
    answer=engine.query(endpoint,'ramka.status',1,'frame.v1',{})
    assert answer['facts'][0]['mode']=='ambient'
    assert answer['facts'][0]['physicalDisplayState']=='unknown'
    assert 'private' not in str(answer)


def test_unclassified_light_is_not_claimed_to_be_individual_bulb():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    state=SimpleNamespace(state='off',attributes={},last_reported=now)
    subject={'key':'template','kind':'light','label':'Room lights','fields':{'state':{'entity':'light.room'}}}
    endpoint={'id':'frame','temperature':[],'assistant':{'subjects':[subject],'sensitive':False}}
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now)
    answer=engine.query(endpoint,'lights.status',1,'lights.v1',{})
    assert answer['facts'][0]['countUnit']=='group'


def test_watering_need_uses_each_plants_explicit_band_and_not_other_problem_flags():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    subject={'key':'fern','kind':'plant','label':'Fern','fields':{'moisture':{'entity':'sensor.moisture'}},'limits':{'min_moisture':20,'max_moisture':60}}
    endpoint={'id':'frame','temperature':[],'assistant':{'subjects':[subject],'sensitive':False}}
    state=SimpleNamespace(state='12',attributes={'unit_of_measurement':'%','problem':'conductivity'},last_reported=now)
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now,verified_source=lambda _:True)
    answer=engine.query(endpoint,'plants.watering_need',1,'plant-watering.v1',{})
    assert answer['summary']['status']=='needs_water'
    assert answer['facts'][0]['problems']==['moisture_low']
    subject['limits'].pop('min_moisture')
    answer=engine.query(endpoint,'plants.watering_need',1,'plant-watering.v1',{})
    assert answer['summary']['status']=='unknown'
    assert 'missing_policy' in answer['limitations']


def test_explanation_requires_matching_artifact_and_explicit_endpoint_exposure():
    import copy
    from test_assistant_contract import bundle
    b=bundle();cap=b['definitions']['capabilities'][0]
    cap.update({'id':'lights.explain','query_type':'component-query.v1','answer_type':'component-description.v1','required_facts':[],'policy':{'evaluator':'lights.explain'},'freshness':{'basis':'installed_metadata','max_age_seconds':None}})
    endpoint={'id':'frame','temperature':[],'assistant':{'subjects':[],'sensitive':False,'explanations':['lights']}}
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:None,lambda _:True,lambda:datetime(2026,10,31,tzinfo=timezone.utc),artifacts={'lights':b})
    supported=[{'queryType':'component-query.v1','answerType':'component-description.v1'}]
    assert engine.capabilities(endpoint,supported)[0]['id']=='lights.explain'
    assert engine.query(endpoint,'lights.explain',1,'component-description.v1',{})['summary']['status']=='explained'
    endpoint['assistant']['explanations']=[]
    assert engine.capabilities(endpoint,supported)==[]


def test_weather_provider_reports_are_qualified_without_verified_sensor_source():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    state=SimpleNamespace(state='rainy',attributes={'temperature':11,'temperature_unit':'°C'},last_reported=now)
    endpoint={'id':'frame','display':{'weather':'weather.example'}}
    engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:state,lambda _:True,lambda:now)
    answer=engine.query(endpoint,'weather.current',1,'weather.v1',{})
    assert answer['facts'][0]['measurements'][0]['value']==11
    assert {'reported_state_only','provider_sync_unknown'} <= set(answer['limitations'])
    assert answer['evidence'][0]['freshness']=='unknown'


def test_weather_restored_or_unknown_is_not_a_provider_report():
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    endpoint={'id':'frame','display':{'weather':'weather.example'}}
    for condition,attrs in [('rainy',{'restored':True}),('unavailable',{}),('unknown',{})]:
        sample=SimpleNamespace(state=condition,attributes=attrs,last_reported=now)
        engine=runtime.Runtime('0123456789abcdef0123456789abcdef',lambda _:sample,lambda _:True,lambda:now)
        answer=engine.query(endpoint,'weather.current',1,'weather.v1',{})
        assert answer['availability']=='unavailable' and not answer['facts']
