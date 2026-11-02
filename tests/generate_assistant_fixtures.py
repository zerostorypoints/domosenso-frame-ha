"""Produce reproducible fictional bridge outputs, not hand-built expected objects."""
import asyncio, importlib, json, sys, types, os
from test_assistant_contract import bundle
from datetime import datetime, timezone
from pathlib import Path
p=types.ModuleType('fixture_package');p.__path__=[str(Path(__file__).parents[1]/'custom_components/domosenso_frame')];sys.modules[p.__name__]=p
runtime=importlib.import_module(p.__name__+'.assistant_data');api=importlib.import_module(p.__name__+'.assistant_api')
now=datetime(2026,10,31,12,tzinfo=timezone.utc)
def state(value, **attrs):return types.SimpleNamespace(state=value,attributes=attrs,last_reported=now)
states={'sensor.temp':state('21',unit_of_measurement='°C'),'sensor.co2':state('900',unit_of_measurement='ppm'),'light.lamp':state('on'),'media_player.radio':state('playing',media_title='Song'),'calendar.events':state('on'),'weather.outside':state('sunny',temperature=20,temperature_unit='°C'),'cover.awning':state('open',current_position=80),'sensor.moisture':state('15',unit_of_measurement='%'),'sensor.sound':state('-20',unit_of_measurement='dBFS'),'sensor.count':state('0'),'input_select.mode':state('ambient'),'device_tracker.phone':state('home')}
subjects=[{'key':'fern','kind':'plant','label':{'en':'Fern','de':'Farn','pl':'Paproć'},'roomKey':'study','roomLabel':{'en':'Study','de':'Arbeitszimmer','pl':'Gabinet'},'fields':{'moisture':{'entity':'sensor.moisture'}},'limits':{'min_moisture':20,'max_moisture':60}}, {'key':'audio','kind':'audio_source','label':'Sound','fields':{'level':{'entity':'sensor.sound'}},'limits':{'integration_window_seconds':1,'weighting':'unweighted'}}, {'key':'backup','kind':'backup','label':'Backup','fields':{'count':{'entity':'sensor.count'}}}, {'key':'frame','kind':'frame','label':'Frame','fields':{'mode':{'entity':'input_select.mode'}}}, {'key':'phone','kind':'presence_source','label':'Phone','fields':{'presence':{'entity':'device_tracker.phone'}},'limits':{'observation_kind':'ble_device'}}]
endpoint={'id':'frame','temperature':[{'entity':'sensor.temp','label':'Study'}],'air':[{'entity':'sensor.co2','label':'Study','metric':'co2','policy':{'higher_is_better':False,'levels':[[800,'good'],[1200,'raised'],[None,'high']],'source':'fixture'}}],'calendars':['calendar.events'],'display':{'weather':'weather.outside'},'studio':{'lights':[{'label':'Lamp','entity':'light.lamp'}],'media':{'entity':'media_player.radio'},'covers':[{'entity':'cover.awning','label':'Patio'}]},'assistant':{'sensitive':True,'subjects':subjects}}
if os.getenv('FIXTURE_UNAVAILABLE'):
    for source in states.values(): source.state='unavailable'; source.attributes={}
supported=[{'queryType':q,'answerType':a} for q,a in [('temperature-query.v1','temperature.v1'),('air-query.v1','air.v1'),('plant-query.v1','plant-watering.v1'),('light-query.v1','lights.v1'),('media-query.v1','media.v1'),('weather-query.v1','weather.v1'),('presence-query.v1','presence.v1'),('awning-query.v1','awnings.v1'),('audio-query.v1','audio-level.v1'),('status-query.v1','backup.v1'),('status-query.v1','frame.v1'),('calendar-query.v1','calendar.v1')]]
artifact=bundle()
explain=artifact['definitions']['capabilities'][0]
explain.update({'id':'lights.explain','query_type':'component-query.v1','answer_type':'component-description.v1','required_facts':[],'policy':{'evaluator':'lights.explain'},'freshness':{'basis':'installed_metadata','max_age_seconds':None}})
endpoint['assistant']['explanations']=['lights']
supported.append({'queryType':'component-query.v1','answerType':'component-description.v1'})
engine=runtime.Runtime('0123456789abcdef0123456789abcdef',states.get,lambda _:True,lambda:now,verified_source=lambda _:True,artifacts={'lights':artifact})
caps=engine.capabilities(endpoint,supported); answers={}
for cap in caps:
    if cap['id']!='calendar.agenda':answers[cap['id']]=engine.query(endpoint,cap['id'],1,cap['answerType'],{})
class C:
    user=types.SimpleNamespace(is_active=True,permissions=types.SimpleNamespace(check_entity=lambda *_:True))
    def send_result(self,_,value):self.result=value
    def send_error(self,*args):raise AssertionError(args)
async def call(*a,**kw):return {'calendar.events':{'events':[{'summary':'Appointment','start':'2026-10-31T13:00:00+00:00','end':'2026-10-31T14:00:00+00:00'}]}}
h=types.SimpleNamespace(data={'domosenso_frame':{'saved':{'installationId':'0123456789abcdef0123456789abcdef'},'settings':{'stale_seconds':900},'assistant_source_verified':lambda _:True}},states=types.SimpleNamespace(get=states.get),services=types.SimpleNamespace(async_call=call),config=types.SimpleNamespace(time_zone='UTC'))
c=C();asyncio.run(api.query_async(h,c,{'id':1,'endpoint':'frame','version':1,'capability':'calendar.agenda','revision':1,'answerType':'calendar.v1','arguments':{}},lambda *_:({},endpoint),'read',lambda:now));answers['calendar.agenda']=c.result
Path('tests/fixtures/assistant-unavailable.json' if os.getenv('FIXTURE_UNAVAILABLE') else 'tests/fixtures/assistant-package.json').write_text(json.dumps({'capabilities':{'version':1,'asOf':now.isoformat(),'capabilities':caps},'answers':answers},ensure_ascii=False,indent=2)+'\n')
