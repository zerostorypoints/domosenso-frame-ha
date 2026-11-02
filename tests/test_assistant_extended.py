from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import importlib, sys, types
from pathlib import Path
package = types.ModuleType('extended_test_package')
package.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_frame')]
sys.modules[package.__name__] = package
ExtendedRuntime = importlib.import_module('extended_test_package.assistant_extended').ExtendedRuntime

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)

def test_watering_band_and_optional_permissions():
    states = {'sensor.moisture': SimpleNamespace(state='10', attributes={'unit_of_measurement':'%'}, last_reported=NOW)}
    runtime = ExtendedRuntime(str(UUID(int=1)), states.get, lambda e: e in states, lambda: NOW, verified_source=lambda _: True)
    endpoint = {'id':'frame','assistant':{'subjects':[{'key':'p','kind':'plant','label':'Fern','fields':{'moisture':{'entity':'sensor.moisture'},'plant':{'entity':'plant.private'}},'limits':{'min_moisture':15,'max_moisture':60}}]}}
    pair = {'queryType':'plant-query.v1','answerType':'plant-watering.v1'}
    cap = next(c for c in runtime.capabilities(endpoint,[pair]) if c['id']=='plants.watering_need')
    result = runtime.query(endpoint,cap['id'],1,cap['answerType'],{})
    assert result['summary']['status']=='needs_water'
    assert result['facts'][0]['value']==10

import asyncio
from datetime import timedelta
from zoneinfo import ZoneInfo
import pytest

def state(value, unit='', age=0, **attrs):
    return SimpleNamespace(state=str(value), attributes={'unit_of_measurement':unit, **attrs}, last_reported=NOW-timedelta(seconds=age))

def make(kind, fields, values, limits=None, denied=()):
    reads=[]
    def get(e):
        assert e not in denied
        reads.append(e)
        return values.get(e)
    rt=ExtendedRuntime(str(UUID(int=1)),get,lambda e:e not in denied,lambda:NOW,verified_source=lambda _:True)
    ep={'id':'f','assistant':{'subjects':[{'key':'x','kind':kind,'label':'X','fields':{k:{'entity':v} for k,v in fields.items()},'limits':limits or {}}]}}
    return rt,ep,reads

def ask(rt,ep,cap,args=None):
    from extended_test_package.assistant_extended import ROUTES
    answer=ROUTES[cap][3]
    return rt.query(ep,cap,1,answer,args or {})

def test_water_band_stale_invalid_and_policy():
    rt,ep,_=make('plant',{'moisture':'m'},{'m':state(70,'%')},{'min_moisture':15,'max_moisture':60})
    assert ask(rt,ep,'plants.watering_need')['summary']['status']=='too_wet'
    ep['assistant']['subjects'][0]['limits']={}
    assert ask(rt,ep,'plants.watering_need')['availability']=='unavailable'
    ep['assistant']['subjects'][0]['limits']={'min_moisture':15,'max_moisture':60}
    rt.state_get=lambda _:state(10,'%',age=21601)
    assert ask(rt,ep,'plants.watering_need')['facts'][0]['status']=='stale'
    rt.state_get=lambda _:state(10,'litres')
    assert 'invalid_unit' in ask(rt,ep,'plants.watering_need')['limitations']

def test_estimate_and_event_history():
    fields={'moisture':'m','drying_rate':'r','water_in_days':'d','last_watered':'w'}
    values={'m':state(20,'%'),'r':state(-0.2,'%/h'),'d':state(4,'d'),'w':state((NOW-timedelta(days=4)).isoformat())}
    rt,ep,_=make('plant',fields,values,{'min_moisture':15})
    subject=rt._extended_subjects(ep,'plants.water_in')[0]['id']
    result=ask(rt,ep,'plants.water_in',{'plantId':subject})
    assert result['facts'][0]['value']==4 and result['facts'][0]['basis']=='estimate'
    assert 'history_coverage_unknown' in result['limitations']
    assert ask(rt,ep,'plants.last_watered',{'plantId':subject})['facts'][0]['basis']=='detected'
    values['w']=state((NOW-timedelta(hours=3)).isoformat())
    assert ask(rt,ep,'plants.water_in',{'plantId':subject})['availability']=='unavailable'
    values['w']=state('unknown');values['r']=state(-0.1)
    assert 'watering_history_unknown' in ask(rt,ep,'plants.water_in',{'plantId':subject})['limitations']
    values['r']=state(0)
    assert 'insufficient_history' in ask(rt,ep,'plants.water_in',{'plantId':subject})['limitations']

def test_status_and_permissions():
    rt,ep,reads=make('plant',{'plant':'p','moisture':'m','brightness':'b','conductivity':'private'},
                     {'p':state('problem',problem='brightness low'),'m':state(35,'%'),'b':state(400,'lx')},
                     {'check_days':3},denied=('private',))
    ans=ask(rt,ep,'plants.status')
    assert ans['summary']['status']=='problem'
    assert any(f['metric']=='brightness' and f['problems'][0]['status']=='low' for f in ans['facts'])
    assert 'private' not in reads
    ep['assistant']['subjects'][0]['limits']={}
    assert ask(rt,ep,'plants.status')['summary']['status']=='unknown'

def test_humidity_mains_wind_and_unknown_selector():
    rt,ep,_=make('room',{'humidity':'h','humidity_alert':'a'},{'h':state(55,'%'),'a':state('on')})
    assert ask(rt,ep,'climate-air.humidity')['facts'][0]['value']==55
    assert ask(rt,ep,'climate-air.status')['facts'][0]['alertCode']=='humidity_high'
    with pytest.raises(ValueError): ask(rt,ep,'climate-air.humidity',{'roomId':str(UUID(int=9))})
    rt,ep,reads=make('player',{'player':'p','power':'w','voltage':'v'},{'w':state(15,'W')},denied=('v',))
    assert ask(rt,ep,'media.mains')['facts'][0]['value']==15
    assert 'v' not in reads
    rt,ep,_=make('awning',{'wind':'a','last_heard':'h'},{'a':state('off'),'h':state((NOW-timedelta(hours=3)).isoformat())})
    result=ask(rt,ep,'awnings.wind')
    assert result['summary']['status']=='no_reported_alarm' and 'reported_state_only' in result['limitations']

def test_forecast_bounded_and_recheck():
    rt,ep,_=make('weather',{'weather':'w'},{'w':state('sunny',temperature_unit='°C')})
    calls=[]
    async def service(domain, name, data):
        calls.append((domain,name,data))
        return {'w':{'forecast':[{'datetime':'2026-10-31T13:00:00+00:00','temperature':15,'condition':'sunny'}]}}
    result=asyncio.run(rt.forecast(ep,{},service,ZoneInfo('Europe/Zurich'),lambda:True))
    assert calls[0][:2]==('weather','get_forecasts')
    assert result['facts'][0]['periods'][0]['measurements'][0]['value']==15
    assert 'provider_sync_unknown' in result['limitations']
    with pytest.raises(ValueError): asyncio.run(rt.forecast(ep,{'date':'2026-11-07'},service,ZoneInfo('Europe/Zurich'),lambda:True))
    with pytest.raises(ValueError): asyncio.run(rt.forecast(ep,{},service,ZoneInfo('Europe/Zurich'),lambda:False))

def test_estimate_invalid_values_and_missing_required_grant():
    fields={'moisture':'m','drying_rate':'r','water_in_days':'d'}
    values={'m':state(20,'%'),'r':state(-1),'d':state('nan','d')}
    rt,ep,_=make('plant',fields,values,{'min_moisture':15})
    sid=rt._extended_subjects(ep,'plants.water_in')[0]['id']
    assert ask(rt,ep,'plants.water_in',{'plantId':sid})['availability']=='unavailable'
    rt.permitted=lambda e:e!='r'
    assert not rt._extended_subjects(ep,'plants.water_in')

def test_forecast_truncation_and_provider_failure():
    rt,ep,_=make('weather',{'weather':'w'},{'w':state('sunny',temperature_unit='°C')},{'forecast_type':'hourly'})
    async def many(domain,name,data):
        start=NOW
        return {'w':{'forecast':[{'datetime':(start+timedelta(minutes=i)).isoformat(),'temperature':12} for i in range(200)]}}
    ans=asyncio.run(rt.forecast(ep,{'period':'next_7_days'},many,ZoneInfo('Europe/Zurich'),lambda:True))
    assert len(ans['facts'][0]['periods'])==168 and 'truncated' in ans['limitations']
    async def failed(*args): raise RuntimeError('provider down')
    ans=asyncio.run(rt.forecast(ep,{},failed,ZoneInfo('Europe/Zurich'),lambda:True))
    assert ans['availability']=='unavailable' and 'missing_source' in ans['limitations']

def test_standalone_climate_status_sources_and_provider_qualification():
    rt,ep,_=make('room',{'humidity_alert':'a'},{'a':state('on')})
    result=ask(rt,ep,'climate-air.status')
    assert result['summary']['status']=='reported'
    assert result['facts'][0]['metric']=='humidity_alert'
    assert result['facts'][0]['alertCode']=='humidity_high'
    assert result['facts'][0]['value'] is None and result['facts'][0]['status']=='usable'
    assert result['evidence'][0]['freshnessBasis']=='reported_state'
    rt,ep,_=make('room',{'difference':'d'},{'d':state(-4.5,'°C')})
    result=ask(rt,ep,'climate-air.status')
    assert result['summary']['status']=='measured'
    assert result['facts'][0]['metric']=='temperature_difference'
    assert result['facts'][0]['value']==-4.5 and result['facts'][0]['basis']=='reported'
    assert 'reported_state_only' in result['limitations']
    rt.state_get=lambda _:state('off',restored=True)
    rt,ep,_=make('room',{'humidity_alert':'a'},{'a':state('off',restored=True)})
    assert ask(rt,ep,'climate-air.status')['availability']=='unavailable'

def test_derived_estimate_needs_verified_physical_moisture():
    fields={'moisture':'m','drying_rate':'r','water_in_days':'d'}
    values={'m':state(20,'%'),'r':state(-1,'%/h'),'d':state(3,'d')}
    rt,ep,_=make('plant',fields,values,{'min_moisture':15})
    rt.verified_source=lambda eid:eid=='m'
    sid=rt._extended_subjects(ep,'plants.water_in')[0]['id']
    result=ask(rt,ep,'plants.water_in',{'plantId':sid})
    assert result['availability']=='ready'
    assert {e['freshnessBasis'] for e in result['evidence']}=={'provider_assessment','source_report'}
    values['m']=state(20,'%',age=21601)
    assert ask(rt,ep,'plants.water_in',{'plantId':sid})['availability']=='unavailable'
    values['m']=state(20,'%'); values['r']=state(-1,'%/h',restored=True)
    assert ask(rt,ep,'plants.water_in',{'plantId':sid})['availability']=='unavailable'

def response_fixtures(unavailable=False):
    """Produce representative negotiation and query responses through the runtime."""
    from extended_test_package.assistant_extended import ROUTES
    cases={
        'plants.watering_need':('plant',{'moisture':'m'},{'m':state(10,'%')},{'min_moisture':15,'max_moisture':60}),
        'plants.status':('plant',{'plant':'p','brightness':'b'},{'p':state('problem',problem='brightness low'),'b':state(25,'lx')},{'check_days':3}),
        'plants.last_watered':('plant',{'last_watered':'w'},{'w':state((NOW-timedelta(days=3)).isoformat())},{}),
        'plants.water_in':('plant',{'moisture':'m','drying_rate':'r','water_in_days':'d'}, {'m':state(20,'%'),'r':state(-1,'%/h'),'d':state(3,'d')},{'min_moisture':15}),
        'climate-air.humidity':('room',{'humidity':'h'},{'h':state(55,'%')},{}),
        'climate-air.status':('room',{'humidity_alert':'a'},{'a':state('on')},{}),
        'climate-air.status.difference':('room',{'difference':'d'},{'d':state(-4,'°C')},{}),
        'media.mains':('player',{'power':'w'},{'w':state(15,'W')},{}),
        'awnings.wind':('awning',{'wind':'a'},{'a':state('off')},{}),
        'weather.forecast':('weather',{'weather':'w'},{'w':state('sunny',temperature_unit='°C')},{}),
    }
    result={}
    for key,(kind,fields,values,limits) in cases.items():
        if unavailable:
            values = {entity:state('unavailable') for entity in values}
        cap='climate-air.status' if key=='climate-air.status.difference' else key
        rt,ep,_=make(kind,fields,values,limits)
        if kind=='plant': ep['assistant']['subjects'][0].update(label={'en':'Fern','de':'Farn','pl':'Paproć'},roomKey='study',roomLabel={'en':'Study','de':'Arbeitszimmer','pl':'Gabinet'})
        if cap=='plants.water_in': rt.verified_source=lambda eid:eid=='m'
        _,_,query,answer=ROUTES[cap]
        capability=next(c for c in rt.capabilities(ep,[{'queryType':query,'answerType':answer}]) if c['id']==cap)
        args={}
        if cap in ('plants.last_watered','plants.water_in'): args={'plantId':capability['subjects'][0]['id']}
        if cap=='weather.forecast':
            async def service(domain,name,data):
                return {} if unavailable else {'w':{'forecast':[{'datetime':'2026-10-31T13:00:00+00:00','temperature':15,'condition':'sunny'}]}}
            output=asyncio.run(rt.forecast(ep,args,service,ZoneInfo('Europe/Zurich'),lambda:True))
        else: output=rt.query(ep,cap,1,answer,args)
        result[key]={'capability':capability,'arguments':args,'answer':output}
    if not unavailable: result.update(forecast_edge_fixtures())
    return result

def forecast_edge_fixtures():
    cases={
        'forecast.time_only': ([{'datetime':'2026-10-31T12:00:00+01:00'}], 'K', {}),
        'forecast.invalid_unit_only': ([{'datetime':'2026-10-31T12:00:00+01:00','temperature':12}], 'K', {}),
        'forecast.unknown_condition_only': ([{'datetime':'2026-10-31T12:00:00+01:00','condition':'unknown'}], 'K', {}),
        'forecast.unsupported_condition_only': ([{'datetime':'2026-10-31T12:00:00+01:00','condition':'invented-weather'}], 'K', {}),
        'forecast.mixed_horizon': ([{'datetime':'2026-10-31T12:00:00+01:00','condition':'sunny'}, {'datetime':'2026-11-01T12:00:00+01:00','condition':'unknown'}, {'datetime':'2026-11-02T12:00:00+01:00','condition':'unknown','temperature':12}], '°C', {'period':'next_7_days'}),
    }
    result={}
    for name,(entries,unit,args) in cases.items():
        rt,ep,_=make('weather',{'weather':'w'},{'w':state('sunny',temperature_unit=unit)})
        capability=next(c for c in rt.capabilities(ep,[{'queryType':'weather-query.v1','answerType':'weather.v1'}]) if c['id']=='weather.forecast')
        async def service(*_): return {'w':{'forecast':entries}}
        answer=asyncio.run(rt.forecast(ep,args,service,ZoneInfo('Europe/Zurich'),lambda:True))
        result[name]={'capability':capability,'arguments':args,'answer':answer}
    return result

def test_response_fixtures_match_actual_routes():
    import json
    path=Path(__file__).parent/'fixtures'/'assistant-extended.json'
    assert json.loads(path.read_text())==response_fixtures()

def test_status_fields_are_independently_authorized_and_invalid_is_unknown():
    rt,ep,reads=make('room',{'humidity_alert':'a','difference':'secret'},
                     {'a':state('off')},denied=('secret',))
    result=ask(rt,ep,'climate-air.status')
    assert len(result['facts'])==1 and result['facts'][0]['alertCode']=='no_reported_alert'
    assert 'secret' not in reads and 'secret' not in str(result)
    rt,ep,_=make('room',{'difference':'d'},{'d':state('nan','°C')})
    assert ask(rt,ep,'climate-air.status')['availability']=='unavailable'
    rt,ep,_=make('room',{'difference':'d'},{'d':state(1,'K')})
    assert 'invalid_unit' in ask(rt,ep,'climate-air.status')['limitations']

def test_derived_estimate_does_not_exempt_provider_age_or_other_measurements():
    fields={'moisture':'m','drying_rate':'r','water_in_days':'d'}
    values={'m':state(20,'%'),'r':state(-1),'d':state(3,'d',age=901)}
    rt,ep,_=make('plant',fields,values,{'min_moisture':15})
    rt.verified_source=lambda eid:eid=='m'
    sid=rt._extended_subjects(ep,'plants.water_in')[0]['id']
    assert ask(rt,ep,'plants.water_in',{'plantId':sid})['availability']=='unavailable'
    rt,ep,_=make('room',{'humidity':'d'},{'d':state(50,'%')})
    rt.verified_source=lambda _:False
    assert ask(rt,ep,'climate-air.humidity')['availability']=='unavailable'

def test_manual_capability_text_is_localized():
    fixtures=response_fixtures()
    for row in fixtures.values():
        cap=row['capability']
        assert set(cap['title'])=={'en','de','pl'}
        assert all('_' not in cap['title'][language] for language in ('en','de','pl'))
        assert all(cap['examples'][language] for language in ('en','de','pl'))


def test_climate_status_with_difference_and_alert_reports_alert_summary():
    rt,ep,_=make('room',{'humidity_alert':'a','difference':'d'},{'a':state('off'),'d':state(-2,'°F')})
    result=ask(rt,ep,'climate-air.status')
    assert result['summary']['status']=='reported'
    assert {f['metric'] for f in result['facts']}=={'humidity_alert','temperature_difference'}


def test_extended_identity_uses_registry_and_room_key_across_rename():
    rt,ep,_=make('plant',{'moisture':'m'},{'m':state(10,'%')},{'min_moisture':15,'max_moisture':60})
    ep['assistant']['subjects'][0]['roomKey']='room-stable'
    rt.registry_id=lambda _: 'registry-stable'
    first=rt.capabilities(ep,[{'queryType':'plant-query.v1','answerType':'plant-watering.v1'}])[0]['subjects'][0]
    ep['assistant']['subjects'][0].update(key='renamed',label='Renamed')
    second=rt.capabilities(ep,[{'queryType':'plant-query.v1','answerType':'plant-watering.v1'}])[0]['subjects'][0]
    assert first['id']==second['id'] and first['roomId']==second['roomId']
    assert ask(rt,ep,'plants.watering_need',{'roomId':first['roomId']})['facts'][0]['subjectId']==first['id']


def test_forecast_short_and_empty_horizon_are_not_complete():
    rt,ep,_=make('weather',{'weather':'w'},{'w':state('sunny')})
    async def short(*_): return {'w':{'forecast':[{'datetime':'2026-11-01T00:00:00+01:00','condition':'sunny'}]}}
    result=asyncio.run(rt.forecast(ep,{'period':'next_7_days'},short,ZoneInfo('Europe/Zurich'),lambda:True))
    assert result['availability']=='partial'
    assert 'missing_source' in result['limitations']
    async def empty(*_): return {'w':{'forecast':[]}}
    result=asyncio.run(rt.forecast(ep,{},empty,ZoneInfo('Europe/Zurich'),lambda:True))
    assert result['availability']=='unavailable' and result['summary']['status']=='unknown'


def test_localized_plant_and_room_labels_survive_capabilities_and_answer():
    rt,ep,_=make('plant',{'moisture':'m'},{'m':state(10,'%')},{'min_moisture':15,'max_moisture':60})
    plant=ep['assistant']['subjects'][0]
    plant.update(label={'en':'Fern','pl':'Paproć'},roomKey='stable',roomLabel={'en':'Study','pl':'Gabinet'})
    cap=next(c for c in rt.capabilities(ep,[{'queryType':'plant-query.v1','answerType':'plant-watering.v1'}]) if c['id']=='plants.watering_need')
    subject=cap['subjects'][0]
    assert subject['label']=={'en':'Fern','pl':'Paproć'}
    assert subject['roomLabel']=={'en':'Study','pl':'Gabinet'}
    assert ask(rt,ep,'plants.watering_need',{'roomId':subject['roomId']})['facts'][0]['name']['pl']=='Paproć'


def test_forecast_coverage_daily_dst_and_hourly_gaps():
    rt,ep,_=make('weather',{'weather':'w'},{'w':state('sunny')})
    async def daily(*_): return {'w':{'forecast':[{'datetime':'2026-10-31T12:00:00+01:00','condition':'sunny'}, {'datetime':'2026-11-01T12:00:00+01:00','condition':'sunny'}]}}
    result=asyncio.run(rt.forecast(ep,{'period':'tomorrow'},daily,ZoneInfo('Europe/Zurich'),lambda:True))
    assert result['availability']=='ready'
    ep['assistant']['subjects'][0]['limits']['forecast_type']='hourly'
    async def gap(*_): return {'w':{'forecast':[{'datetime':'2026-10-31T00:00:00+01:00','condition':'sunny'},{'datetime':'2026-10-31T23:00:00+01:00','condition':'sunny'}]}}
    result=asyncio.run(rt.forecast(ep,{},gap,ZoneInfo('Europe/Zurich'),lambda:True))
    assert result['availability']=='partial' and 'missing_source' in result['limitations']

def test_unassociated_plant_does_not_gain_room_identity():
    rt,ep,_=make('plant',{'moisture':'m'},{'m':state(10,'%')},{'min_moisture':15,'max_moisture':60})
    ep['assistant']['subjects'][0]['roomLabel']={'en':'Study','pl':'Gabinet'}
    cap=next(c for c in rt.capabilities(ep,[{'queryType':'plant-query.v1','answerType':'plant-watering.v1'}]) if c['id']=='plants.watering_need')
    assert 'roomId' not in cap['subjects'][0]
    assert cap['subjects'][0]['roomLabel']['pl']=='Gabinet'


def test_forecast_complete_hourly_coverage_across_dst_fallback():
    rt,ep,_=make('weather',{'weather':'w'},{'w':state('sunny')},{'forecast_type':'hourly'})
    rt.now=lambda:datetime(2026,10,25,12,tzinfo=timezone.utc)
    start=datetime(2026,10,24,22,tzinfo=timezone.utc)
    async def service(*_):
        return {'w':{'forecast':[{'datetime':(start+timedelta(hours=i)).isoformat(),'condition':'sunny'} for i in range(25)]}}
    result=asyncio.run(rt.forecast(ep,{},service,ZoneInfo('Europe/Zurich'),lambda:True))
    assert result['availability']=='ready'
    assert len(result['facts'][0]['periods'])==25


@pytest.mark.parametrize('entry', [
    {'datetime':'2026-10-31T12:00:00+01:00'},
    {'datetime':'2026-10-31T12:00:00+01:00','temperature':12},
    {'datetime':'2026-10-31T12:00:00+01:00','condition':'unknown'},
    {'datetime':'2026-10-31T12:00:00+01:00','condition':'invented-weather'},
], ids=['time-only','invalid-unit-only','unknown-condition-only','unsupported-condition-only'])
def test_forecast_unusable_period_does_not_cover_interval(entry):
    rt,ep,_=make('weather',{'weather':'w'},{'w':state('sunny',temperature_unit='K')})
    async def service(*_): return {'w':{'forecast':[entry]}}
    result=asyncio.run(rt.forecast(ep,{},service,ZoneInfo('Europe/Zurich'),lambda:True))
    assert result['availability']=='unavailable'
    assert result['coverage']['missing']==1 and 'missing_source' in result['limitations']
    assert result['summary']=={'status':'unknown','affected':0}
    assert not result['facts'][0]['periods']

def test_forecast_mixed_horizon_retains_only_usable_periods():
    rt,ep,_=make('weather',{'weather':'w'},{'w':state('sunny',temperature_unit='°C')})
    async def service(*_): return {'w':{'forecast':[
        {'datetime':'2026-10-31T12:00:00+01:00','condition':'sunny'},
        {'datetime':'2026-11-01T12:00:00+01:00','condition':'unknown'},
        {'datetime':'2026-11-02T12:00:00+01:00','condition':'unknown','temperature':12},
    ]}}
    result=asyncio.run(rt.forecast(ep,{'period':'next_7_days'},service,ZoneInfo('Europe/Zurich'),lambda:True))
    assert result['availability']=='partial' and 'missing_source' in result['limitations']
    periods=result['facts'][0]['periods']
    assert len(periods)==2 and periods[0]['condition']=='sunny'
    assert 'condition' not in periods[1] and periods[1]['measurements']==[{'metric':'temperature','value':12,'unit':'°C'}]


def test_forecast_skipped_period_marks_fully_covered_interval_incomplete():
    rt,ep,_=make('weather',{'weather':'w'},{'w':state('sunny')})
    async def service(*_): return {'w':{'forecast':[
        {'datetime':'2026-10-31T12:00:00+01:00','condition':'sunny'},
        {'datetime':'2026-10-31T13:00:00+01:00'},
    ]}}
    result=asyncio.run(rt.forecast(ep,{},service,ZoneInfo('Europe/Zurich'),lambda:True))
    assert result['availability']=='partial' and 'missing_source' in result['limitations']
    assert len(result['facts'][0]['periods'])==1
