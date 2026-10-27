"""Free, scoped HA bridge. No AI, arbitrary entity queries or actuator services."""
import math
import asyncio
import copy
import logging
import secrets
import time as monotonic_time
from datetime import datetime, time, timedelta
from pathlib import Path
from functools import wraps
from zoneinfo import ZoneInfo
import voluptuous as vol
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.util import dt as dt_util
from .grants import Grants
from .radar import Radar, validate as validate_radar, sources as radar_sources
from .layouts import Layouts, Conflict, save_verified
from .semantics import assess, validate_policy, METRICS
from .display import validate_config, default_config, selection
from .studio_sources import validate_sources
from .studio_api import setup as setup_studio

DOMAIN='domosenso_frame'
LABEL=vol.All(str,vol.Length(min=1,max=80))
SOURCE=vol.Schema({vol.Required('entity'):cv.entity_id,vol.Required('label'):LABEL,
    vol.Optional('metric'):vol.In((*METRICS,'temperature')),vol.Optional('indoor'):cv.boolean,vol.Optional('policy'):validate_policy})
DISPLAY=vol.Schema({vol.Optional('locale',default='en'):vol.In(['en','de','pl']),vol.Optional('config'):validate_config,vol.Optional('weather'):cv.entity_id,vol.Optional('status',default=[]):vol.All([vol.Schema({vol.Required('entity'):cv.entity_id,vol.Required('label'):LABEL})],vol.Length(max=64)),vol.Optional('mode_entity'):cv.entity_id,vol.Optional('page_entity'):cv.entity_id})
ENDPOINT=vol.Schema({vol.Required('id'):vol.Match(r'^[a-z][a-z0-9_-]{0,39}$'),vol.Required('name'):LABEL,
    vol.Optional('semantic_version'):vol.In([1]),vol.Optional('metadata_source'):vol.Schema({'component':LABEL,'revision':vol.All(str,vol.Length(max=160))}),
    vol.Optional('radar'):validate_radar,vol.Optional('display'):DISPLAY,vol.Optional('studio'):validate_sources,
    vol.Required('dashboard'):vol.All(str,vol.Match(r'^/(?!/|auth/|api/)[A-Za-z0-9_/-]+$')),
    vol.Optional('temperature',default=[]):vol.All([SOURCE],vol.Length(max=64)),
    vol.Optional('air',default=[]):vol.All([SOURCE],vol.Length(max=64)),
    vol.Optional('calendars',default=[]):vol.All([cv.entity_id],vol.Length(max=16))})
CONFIG_SCHEMA=vol.Schema({vol.Optional(DOMAIN):vol.Schema({vol.Optional('demo',default=False):cv.boolean,
    vol.Optional('stale_seconds',default=900):vol.All(int,vol.Range(min=60,max=86400)),
    vol.Required('endpoints'):vol.All([ENDPOINT],vol.Length(min=1,max=16))})},extra=vol.ALLOW_EXTRA)

async def async_setup(hass,config):
    if DOMAIN not in config:return True
    if DOMAIN in hass.data:return hass.data[DOMAIN].get('source')=='yaml'
    return await _async_setup_bridge(hass,config[DOMAIN],'yaml')

async def async_setup_entry(hass,entry):
    if DOMAIN in hass.data:
        return hass.data[DOMAIN].get('entry_id')==entry.entry_id
    # Validate persisted shape, but do not rediscover: sources may be offline at startup.
    settings=CONFIG_SCHEMA({DOMAIN:{'endpoints':[dict(entry.data['endpoint'])]}})[DOMAIN]
    try:return await _async_setup_bridge(hass,settings,'ui',entry.entry_id)
    except Exception:
        failed=hass.data.get(DOMAIN,{})
        if radar:=failed.get('radar'):radar.close()
        if cancel:=failed.get('radar_cancel'):cancel()
        hass.data.pop(DOMAIN,None)
        frontend.async_remove_panel(hass,DOMAIN,warn_if_unknown=False)
        raise

async def async_unload_entry(hass,entry):
    state=hass.data.get(DOMAIN)
    if not state or state.get('entry_id')!=entry.entry_id:return True
    # Notify directly before teardown: bus callbacks can run after proofs are gone.
    for notify in list(state.get('lifecycle_listeners',{}).values()):notify()
    # Durable identity, grants and mappings remain on disk; connection proofs do not.
    state['grants'].sessions.clear()
    state['grants'].challenges.clear()
    state['grants'].pending.clear()
    for unsubscribe in list(state.get('subscriptions',())):unsubscribe()
    if radar:=state.get('radar'):radar.close()
    if cancel:=state.get('radar_cancel'):cancel()
    hass.data.pop(DOMAIN)
    frontend.async_remove_panel(hass,DOMAIN)
    return True

async def register_static_paths(hass,paths):
    registered=hass.data.setdefault(DOMAIN+'_static_paths',set())
    for path in paths:
        if path.url_path not in registered:
            await hass.http.async_register_static_paths([path])
            registered.add(path.url_path)

def register_command(hass,handler):
    # Routes remain registered in HA while disabled; they must not use stale closures.
    state=hass.data[DOMAIN]
    @wraps(handler)
    def guarded(h,connection,msg):
        if h.data.get(DOMAIN) is not state:
            connection.send_error(msg['id'],'frame_unavailable','DomoSenso Frame is disabled; enable the integration and reconnect')
            return
        return handler(h,connection,msg)
    websocket_api.async_register_command(hass,guarded)

async def _async_setup_bridge(hass,settings,source,entry_id=None):
    endpoints={e['id']:e for e in settings['endpoints']}
    if len(endpoints)!=len(settings['endpoints']):return False
    store=Store(hass,1,DOMAIN)
    saved=await store.async_load()
    if saved is None:
        saved={'installationId':secrets.token_hex(16),'devices':{}}
        await store.async_save(saved)
    grants=Grants(saved['installationId'],saved['devices'])
    hass.data[DOMAIN]={'source':source,'entry_id':entry_id,'settings':settings,'endpoints':endpoints,'store':store,'saved':saved,'grants':grants,'grants_lock':asyncio.Lock(),'subscriptions':set(),'lifecycle_listeners':{}}
    layout_store=Store(hass,1,DOMAIN+'_layouts')
    async def persist_layouts(value):
        await save_verified(layout_store,value,Path(hass.config.path('.storage',DOMAIN+'_layouts')),DOMAIN+'_layouts',hass.async_add_executor_job)
    hass.data[DOMAIN]['layouts']=Layouts(endpoints,await layout_store.async_load() or {},persist_layouts)
    hass.data[DOMAIN]['studio_ready']=False
    try:
        await setup_studio(hass,grant,reading,display_snapshot,register_command,register_static_paths)
        hass.data[DOMAIN]['studio_ready']=True
    except Exception:
        logging.getLogger(__name__).exception('Frame Studio unavailable; legacy display remains active')
    state=hass.data[DOMAIN]
    def later(delay,fn):
        handle=hass.loop.call_later(delay,fn)
        return handle.cancel
    def emit(eid,payload):
        if hass.data.get(DOMAIN) is state:hass.bus.async_fire(DOMAIN+'_event',{**payload,'endpoint':eid,'radar':True})
    radar=state['radar']=Radar(endpoints,hass.states,emit,later,monotonic_time.monotonic)
    @callback
    def radar_changed(event):
        for eid,endpoint in endpoints.items():
            if event.data.get('entity_id') in radar_sources(endpoint.get('radar',{})):radar.refresh(eid)
    state['radar_cancel']=hass.bus.async_listen('state_changed',radar_changed)
    for eid in endpoints:radar.refresh(eid)
    for command in [layout_get,layout_save,profile,begin,finish,status,data,display,dashboard,subscribe,devices,approve,revoke,remove_device,screen]:register_command(hass,command)
    await register_static_paths(hass,[StaticPathConfig('/domosenso_frame/panel.js',str(Path(__file__).with_name('panel.js')),False),StaticPathConfig('/domosenso_frame/editor.js',str(Path(__file__).with_name('editor.js')),False),StaticPathConfig('/domosenso_frame/editor-model.js',str(Path(__file__).with_name('editor-model.js')),False),StaticPathConfig('/domosenso_frame/ui/0.1.0',str(Path(__file__).parent/'ui/0.1.0'),False)])
    frontend.async_register_built_in_panel(hass,'custom',sidebar_title='DomoSenso Frame',sidebar_icon='mdi:tablet-dashboard',frontend_url_path=DOMAIN,require_admin=True,
      config={'_panel_custom':{'name':'domosenso-frame-panel','module_url':'/domosenso_frame/panel.js','embed_iframe':False,'trust_external':False}})
    return True

def command(command_name,**fields):
    return websocket_api.websocket_command({vol.Required('type'):DOMAIN+'/'+command_name,**{vol.Required(k):v for k,v in fields.items()}})
def reject(connection,msg,error):connection.send_error(msg['id'],'frame_denied',str(error))
def grant(hass,connection):
    if not connection.user.is_active:raise ValueError('Inactive user')
    if DOMAIN not in hass.data:raise ValueError('Frame disabled')
    proof=hass.data[DOMAIN]['grants'].authorized(connection,connection.user.id)
    endpoint=hass.data[DOMAIN]['endpoints'].get(proof['endpoint'])
    if not endpoint:raise ValueError('Endpoint removed')
    return proof,endpoint

@command('profile')
@callback
def profile(hass,connection,msg):
    state=hass.data[DOMAIN]
    connection.send_result(msg['id'],{'protocol':1,'installationId':state['saved']['installationId'],'name':hass.config.location_name,'timezone':hass.config.time_zone,'demo':state['settings']['demo'],
      'endpoints':[{'id':e['id'],'name':e['name'],'dashboard':e['dashboard'],'rooms':[s['label'] for s in e['temperature']],
       **({'capabilitiesVersion':1} if state.get('studio_ready') else {}),'semanticVersion':e.get('semantic_version',0),'displayVersion':1 if 'display' in e else 0,'studioVersion':2 if state.get('studio_ready') and 'display' in e and e['id'] not in state['studio_library'].saved.get('migrationErrors',{}) else 0,
       'topics':[t for t,key in [('temperature','temperature'),('air','air'),('calendar','calendars')] if e[key]]} for e in state['endpoints'].values()]})

@command('begin',publicKey=vol.All(str,vol.Length(max=256)),endpoint=LABEL,name=LABEL)
@callback
def begin(hass,connection,msg):
    state=hass.data[DOMAIN];g=state['grants']
    try:
        if msg['endpoint'] not in state['endpoints']:raise ValueError('Unknown endpoint')
        connection.subscriptions.setdefault(DOMAIN+'_session',lambda:g.close(connection))
        connection.send_result(msg['id'],g.begin(connection,connection.user.id,msg['endpoint'],msg['publicKey'],msg['name']))
    except ValueError as err:reject(connection,msg,err)

@command('finish',signature=vol.All(str,vol.Length(max=256)))
@callback
def finish(hass,connection,msg):
    try:connection.send_result(msg['id'],hass.data[DOMAIN]['grants'].finish(connection,msg['signature']))
    except ValueError as err:reject(connection,msg,err)

@command('status')
@callback
def status(hass,connection,msg):
    try:connection.send_result(msg['id'],hass.data[DOMAIN]['grants'].status(connection,connection.user.id))
    except ValueError as err:reject(connection,msg,err)

def reading(hass,connection,source):
    eid=source['entity']
    if not connection.user.permissions.check_entity(eid,POLICY_READ):raise ValueError('Source not permitted')
    s=hass.states.get(eid);value=None
    try:
        value=float(s.state)
        if not math.isfinite(value):value=None
    except (AttributeError,ValueError,TypeError):pass
    stale=not s or (dt_util.utcnow()-s.last_reported).total_seconds()>hass.data[DOMAIN]['settings']['stale_seconds']
    return {**{k:source[k] for k in ('metric','indoor','policy') if k in source},'label':source['label'],'value':value,'available':value is not None,'stale':stale,
      'unit':(s.attributes.get('unit_of_measurement') or '') if s else '',
      'updatedAt':s.last_updated.isoformat() if s else None,'observedAt':s.last_reported.isoformat() if s else None}

@command('data',topic=vol.In(['temperature','air','calendar']))
@websocket_api.async_response
async def data(hass,connection,msg):
    try:
        _,endpoint=grant(hass,connection);topic=msg['topic'];result={'topic':topic,'fetchedAt':dt_util.utcnow().isoformat()}
        if topic=='calendar':
            if not endpoint['calendars']:raise ValueError('Calendar not mapped')
            for eid in endpoint['calendars']:
                if not connection.user.permissions.check_entity(eid,POLICY_READ):raise ValueError('Calendar not permitted')
            tz=ZoneInfo(hass.config.time_zone);today=datetime.now(tz).date();start=datetime.combine(today,time.min,tzinfo=tz);end=datetime.combine(today+timedelta(days=1),time.min,tzinfo=tz)
            response=await hass.services.async_call('calendar','get_events',{'entity_id':endpoint['calendars'],'start_date_time':start.isoformat(),'end_date_time':end.isoformat()},blocking=True,return_response=True,context=connection.context(msg))
            _,current_endpoint=grant(hass,connection)  # Revocation or remapping during an awaited query wins.
            if current_endpoint is not endpoint:raise ValueError('Sources changed')
            events=[]
            for eid in endpoint['calendars']:
                if not connection.user.permissions.check_entity(eid,POLICY_READ):raise ValueError('Calendar not permitted')
                if eid not in (response or {}):raise ValueError('Calendar unavailable')
                for event in response[eid].get('events',[]):
                    def point(v):return {'dateTime':v} if 'T' in v else {'date':v}
                    events.append({'title':str(event.get('summary','(untitled)'))[:300],'start':point(event['start']),'end':point(event['end'])})
            result.update({'timezone':hass.config.time_zone,'date':today.isoformat(),'events':events[:100]})
        else:
            if not endpoint[topic]:raise ValueError('Topic not mapped')
            result['measurements']=[reading(hass,connection,s) for s in endpoint[topic]]
            if endpoint.get('semantic_version')==1 and topic=='air':result['assessment']=assess(result['measurements'])
            for measurement in result['measurements']:measurement.pop('policy',None)
        connection.send_result(msg['id'],result)
    except Exception as err:
        # Do not expose internal entities, service traces or credentials in errors.
        reject(connection,msg,ValueError('Data unavailable or access denied'))


def display_snapshot(hass,connection,endpoint):
    if not connection.user.is_active or 'display' not in endpoint:raise ValueError('Display unavailable')
    settings=endpoint['display'];now=dt_util.utcnow()
    def state(eid):
        if not connection.user.permissions.check_entity(eid,POLICY_READ):raise ValueError('Source not permitted')
        return hass.states.get(eid)
    def stale(value):return not value or (now-value.last_reported).total_seconds()>hass.data[DOMAIN]['settings']['stale_seconds']
    layouts=hass.data[DOMAIN].get('layouts')
    config=layouts.get(endpoint['id'])['config'] if layouts else settings.get('config') or default_config(endpoint,settings['locale'])
    mode=state(settings['mode_entity']) if 'mode_entity' in settings else None
    page=state(settings['page_entity']) if 'page_entity' in settings else None
    capabilities=[key for key in ('temperature','air') if endpoint.get(key)]+[key for key in ('weather','status') if settings.get(key)]
    active,screen_id=selection(config,now.timestamp(),mode.state if mode else None,page.state if page else None,capabilities)
    temperature=[reading(hass,connection,s) for s in endpoint['temperature']]
    air=[reading(hass,connection,s) for s in endpoint['air']]
    assessment=assess(air) if endpoint.get('semantic_version')==1 else None
    for item in temperature+air:item.pop('policy',None)
    statuses=[]
    for source in settings['status']:
        value=state(source['entity']);available=bool(value and value.state not in ('unknown','unavailable'))
        statuses.append({'label':source['label'],'value':value.state[:120] if available else '', 'available':available,'stale':bool(stale(value))})
    weather=None
    if 'weather' in settings:
        value=state(settings['weather']);available=bool(value and value.state not in ('unknown','unavailable'));temp=value.attributes.get('temperature') if value else None
        if isinstance(temp,bool) or not isinstance(temp,(int,float)) or not math.isfinite(temp):temp=None
        weather={'label':str(value.attributes.get('friendly_name','Weather'))[:80] if value else 'Weather','condition':value.state[:80] if available else '',
          'temperature':temp,'unit':str(value.attributes.get('temperature_unit') or '')[:30] if value else '', 'available':available,'stale':bool(stale(value))}
    return {'version':1,'config':config,'mode':active,'screen':screen_id,'connected':True,'fetchedAt':now.isoformat(),'timezone':hass.config.time_zone,
      'temperature':temperature,'air':air,'assessment':assessment,'status':statuses,'weather':weather}

@command('display')
@callback
def display(hass,connection,msg):
    try:
        _,endpoint=grant(hass,connection)
        connection.send_result(msg['id'],display_snapshot(hass,connection,endpoint))
    except Exception:reject(connection,msg,ValueError('Display unavailable or access denied'))

@command('dashboard',endpoint=LABEL)
@callback
def dashboard(hass,connection,msg):
    # Standard HA dashboard access is deliberately independent of Android device proof.
    # Every source is checked with the same authenticated HA user's read permissions.
    try:connection.send_result(msg['id'],display_snapshot(hass,connection,hass.data[DOMAIN]['endpoints'][msg['endpoint']]))
    except Exception:reject(connection,msg,ValueError('Display unavailable or access denied'))

@command('subscribe')
@callback
def subscribe(hass,connection,msg):
    try:proof,_=grant(hass,connection)
    except ValueError as err:reject(connection,msg,err);return
    def event(event):
        if event.data.get('deviceId')==proof['deviceId'] and event.data['kind']=='revoked':
            connection.send_event(msg['id'],{'kind':'revoked'});return
        try:current,endpoint=grant(hass,connection)
        except ValueError:return
        if event.data.get('endpoint')!=current['endpoint']:return
        if event.data.get('radar') and not can_read(endpoint):return
        payload={'kind':'screen','action':event.data['action']}
        if event.data.get('radar'):
            if endpoint.get('radar'):
                radar=hass.data[DOMAIN].get('radar')
                payload=radar.snapshot(current['endpoint']) if radar else None
                if payload is None:return
            else:payload={'kind':'screen','action':'release','mode':'ambient'}
        connection.send_event(msg['id'],payload)
    def can_read(endpoint):
        return all(connection.user.permissions.check_entity(eid,POLICY_READ) for eid in radar_sources(endpoint.get('radar',{})))
    subscriptions=hass.data[DOMAIN]['subscriptions']
    lifecycle=hass.data[DOMAIN]['lifecycle_listeners']
    cancel=hass.bus.async_listen(DOMAIN+'_event',event)
    def unsubscribe():
        if unsubscribe in subscriptions:
            subscriptions.remove(unsubscribe);lifecycle.pop(unsubscribe,None);cancel()
    lifecycle[unsubscribe]=lambda:connection.send_event(msg['id'],{'kind':'bridge_unavailable','reason':'integration_unloaded'})
    subscriptions.add(unsubscribe)
    connection.subscriptions[msg['id']]=unsubscribe
    connection.send_result(msg['id'])
    if radar:=hass.data[DOMAIN].get('radar'):
        try:
            current,endpoint=grant(hass,connection)
            if can_read(endpoint) and (payload:=radar.snapshot(current['endpoint'])):connection.send_event(msg['id'],payload)
        except ValueError:pass

@command('devices')
@websocket_api.require_admin
@callback
def devices(hass,connection,msg):connection.send_result(msg['id'],hass.data[DOMAIN]['grants'].devices())

@command('approve',deviceId=str,code=str)
@websocket_api.require_admin
@websocket_api.async_response
async def approve(hass,connection,msg):
    state=hass.data[DOMAIN]
    try:
        async with state['grants_lock']:
            if not connection.user.is_active or hass.data.get(DOMAIN) is not state:raise ValueError('Frame unavailable')
            state['grants'].approve(msg['deviceId'],msg['code']);await state['store'].async_save(state['saved']);connection.send_result(msg['id'],{'approved':True})
    except ValueError as err:reject(connection,msg,err)

@command('revoke',deviceId=str)
@websocket_api.require_admin
@websocket_api.async_response
async def revoke(hass,connection,msg):
    state=hass.data[DOMAIN]
    try:
        async with state['grants_lock']:
            if not connection.user.is_active or hass.data.get(DOMAIN) is not state:raise ValueError('Frame unavailable')
            state['grants'].revoke(msg['deviceId']);hass.bus.async_fire(DOMAIN+'_event',{'kind':'revoked','deviceId':msg['deviceId']});await state['store'].async_save(state['saved']);connection.send_result(msg['id'],{'revoked':True})
    except ValueError as err:reject(connection,msg,err)

@command('remove_device',deviceId=str)
@websocket_api.require_admin
@websocket_api.async_response
async def remove_device(hass,connection,msg):
    state=hass.data[DOMAIN]
    try:
        async with state['grants_lock']:
            if not connection.user.is_active or hass.data.get(DOMAIN) is not state:raise ValueError('Frame unavailable')
            device=msg['deviceId']
            if state['grants'].records.get(device,{}).get('state')!='revoked':raise ValueError('Revoke the device before removing its registration')
            previous=copy.deepcopy(state['saved']);updated=copy.deepcopy(previous)
            del updated['devices'][device]
            try:
                await state['store'].async_save(updated)
                if not connection.user.is_active or hass.data.get(DOMAIN) is not state:raise ValueError('Frame unavailable')
            except Exception:
                await state['store'].async_save(previous)
                raise
            state['grants'].remove_revoked(device)
            connection.send_result(msg['id'],{'removed':True})
    except Exception:reject(connection,msg,ValueError('Registration was not removed; reload and retry'))

@command('screen',endpoint=str,action=vol.In(['wake','dim','release']))
@websocket_api.require_admin
@callback
def screen(hass,connection,msg):
    if msg['endpoint'] not in hass.data[DOMAIN]['endpoints']:reject(connection,msg,ValueError('Unknown endpoint'));return
    hass.bus.async_fire(DOMAIN+'_event',{'kind':'screen','endpoint':msg['endpoint'],'action':msg['action']});connection.send_result(msg['id'])

@command('layout_get',endpoint=LABEL)
@websocket_api.require_admin
@callback
def layout_get(hass,connection,msg):
    try:
        if not connection.user.is_active:raise ValueError('Inactive user')
        connection.send_result(msg['id'],hass.data[DOMAIN]['layouts'].get(msg['endpoint']))
    except Exception:reject(connection,msg,ValueError('Layout unavailable'))

@command('layout_save',endpoint=LABEL,revision=vol.All(str,vol.Length(min=64,max=64)),config=validate_config)
@websocket_api.require_admin
@websocket_api.async_response
async def layout_save(hass,connection,msg):
    try:
        if not connection.user.is_active:raise ValueError('Inactive user')
        result=await hass.data[DOMAIN]['layouts'].save(msg['endpoint'],msg['revision'],msg['config'])
        connection.send_result(msg['id'],result)
    except Conflict:connection.send_error(msg['id'],'layout_conflict','Layout changed; reload before saving')
    except Exception:connection.send_error(msg['id'],'layout_save_failed','Could not save layout; keep the draft and check configuration')
