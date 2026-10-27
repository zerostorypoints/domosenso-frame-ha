"""Version 2 API remains separate from legacy display and layout clients."""
from pathlib import Path
import time
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers.storage import Store
from homeassistant.core import callback
from .studio import Library,Conflict,validate_config
from .studio_runtime import Runtime,installed_durations
from .studio_data import Data
from .studio_mappings import Mappings,save_mappings_verified
from .studio_actions import validate_action,perform,takeover
from .layouts import save_verified

async def setup(hass,grant,reading,display_snapshot,register_command,register_static_paths):
    state=hass.data['domosenso_frame'];mapping_store=Store(hass,1,'domosenso_frame_mappings')
    async def persist_mappings(value):await save_mappings_verified(mapping_store,value,Path(hass.config.path('.storage','domosenso_frame_mappings')),hass.async_add_executor_job)
    mapping_saved=await mapping_store.async_load() or {}
    store=Store(hass,1,'domosenso_frame_templates')
    async def persist(value):await save_verified(store,value,Path(hass.config.path('.storage','domosenso_frame_templates')),'domosenso_frame_templates',hass.async_add_executor_job)
    lib=state['studio_library']=Library(state['endpoints'],await store.async_load() or {},persist)
    # Preserve every effective v1 layout once, including administrator overrides.
    from homeassistant.helpers import entity_registry as er
    registry=er.async_get(hass);installed={};scripts=hass.data.get('script')
    for eid,endpoint in state['endpoints'].items():
        runtime=endpoint.get('studio',{}).get('runtime',{})
        runtime['legacy_automations']=[requested if hass.states.get(requested) else registry.async_get_entity_id('automation','automation',requested.partition('.')[2]) or requested for requested in runtime.get('legacy_automations',[])]
        script=scripts.get_entity(runtime.get('legacy_loop')) if scripts and runtime.get('legacy_loop') else None
        installed[eid]=runtime.get('durations') or installed_durations(script.raw_config,runtime.get('page')) if script else runtime.get('durations',{})
    # Capture normalized legacy automation IDs as the baseline before any editable overrides.
    mappings=state['studio_mappings']=Mappings(state['endpoints'],mapping_saved,persist_mappings)
    await lib.initialize({id:state['layouts'].get(id) for id,e in state['endpoints'].items() if 'display' in e},installed)
    runtime=state['studio_runtime']=Runtime();data=state['studio_data']=Data(hass,reading,display_snapshot,runtime);state['studio_clients']={}
    def command(name,fields={}):return websocket_api.websocket_command({vol.Required('type'):'domosenso_frame/'+name,**fields})
    def deny(c,msg,code='studio_failed',message='Frame operation unavailable or access denied'):c.send_error(msg['id'],code,message)
    def admin(c):
        if not c.user.is_active or not c.user.is_admin:raise ValueError('Administrator access required')
        if hass.data.get('domosenso_frame') is not state:raise ValueError('Frame disabled')
    def version1(value):
        if type(value) is not int or value!=1:raise vol.Invalid('Unsupported mapping version')
        return value
    mapping_fields={vol.Required('version'):version1,vol.Required('endpoint'):vol.All(str,vol.Length(min=1,max=40))}
    @command('studio_mappings',mapping_fields)
    @websocket_api.require_admin
    @callback
    def mapping_get(h,c,msg):
        try:
            admin(c);c.send_result(msg['id'],mappings.get(msg['endpoint'],h.states))
        except Exception:deny(c,msg)
    @command('studio_mappings_update',{**mapping_fields,vol.Required('revision'):vol.All(str,vol.Match(r'^[a-f0-9]{64}$')),vol.Required('patch'):dict})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def mapping_update(h,c,msg):
        try:
            result=await mappings.update(msg['endpoint'],msg['revision'],msg['patch'],h.states,lambda:admin(c))
            data.cache.clear();runtime.reset(msg['endpoint'])
            if 'radar' in msg['patch'] and state.get('radar'):state['radar'].reset(msg['endpoint'])
            admin(c);c.send_result(msg['id'],result)
        except Conflict:deny(c,msg,'mapping_conflict','Source mappings changed. Reload mappings; your template draft is retained.')
        except ValueError as error:deny(c,msg,'mapping_invalid',str(error))
        except Exception:deny(c,msg,'mapping_save_failed','Could not persist source mappings. Previous live mappings retained; reload before retrying.')
    @command('studio_library')
    @websocket_api.require_admin
    @callback
    def library(h,c,msg):
        if not c.user.is_active:deny(c,msg);return
        c.send_result(msg['id'],lib.get())
    @command('studio_library_update',{vol.Required('revision'):str,vol.Required('operation'):vol.In(['save','apply','delete']),vol.Required('templateId'):str,vol.Optional('config'):validate_config,vol.Optional('endpoint'):str,vol.Optional('renderer'):vol.In([2])})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def update(h,c,msg):
        try:
            if not c.user.is_active:raise ValueError('Inactive user')
            eid=msg.get('endpoint');endpoint=state['endpoints'].get(eid)
            if msg['operation']=='apply' and (not endpoint or 'display' not in endpoint):raise ValueError('Unknown frame')
            if msg['operation']=='apply' and time.monotonic()-state['studio_clients'].get(eid,0)>120:raise ValueError('Open the updated Android app before applying to this frame')
            async def before():return await takeover(h,c,endpoint,c.context(msg))
            result=await lib.mutate(msg['revision'],msg['operation'],msg['templateId'],msg.get('config'),eid,msg.get('renderer'),before)
            if msg['operation']=='apply':runtime.reset(eid)
            c.send_result(msg['id'],result)
        except Conflict:deny(c,msg,'studio_conflict','Library changed. Reload the library; your draft is retained.')
        except ValueError as error:deny(c,msg,'studio_invalid',str(error))
        except Exception:deny(c,msg,'studio_save_failed','Could not persist the template library. Previous layout retained.')
    @command('studio_dashboard',{vol.Required('endpoint'):str,vol.Optional('capabilitiesVersion'):version1,vol.Optional('offset',default=0):vol.All(int,vol.Range(min=-30,max=30))})
    @websocket_api.async_response
    async def dashboard(h,c,msg):
        try:
            result=await data.snapshot(c,state['endpoints'][msg['endpoint']],msg['offset'],c.context(msg),msg.get('capabilitiesVersion')==1);result['readOnly']=True;c.send_result(msg['id'],result)
        except Exception:deny(c,msg)
    @command('studio_display',{vol.Optional('capabilitiesVersion'):version1,vol.Optional('offset',default=0):vol.All(int,vol.Range(min=-30,max=30))})
    @websocket_api.async_response
    async def display(h,c,msg):
        try:
            _,endpoint=grant(h,c);result=await data.snapshot(c,endpoint,msg['offset'],c.context(msg),msg.get('capabilitiesVersion')==1);grant(h,c);state['studio_clients'][endpoint['id']]=time.monotonic();c.send_result(msg['id'],result)
        except Exception:deny(c,msg)
    @command('studio_action',{vol.Required('action'):validate_action})
    @websocket_api.async_response
    async def action(h,c,msg):
        try:
            _,endpoint=grant(h,c);rt=runtime.endpoints.get(endpoint['id']);now=time.monotonic()
            if rt and now<rt['shield']:raise ValueError('Touch shield active')
            await perform(h,c,endpoint,msg['action'],c.context(msg),lambda:grant(h,c));c.send_result(msg['id'],{'ok':True})
        except Exception:deny(c,msg)
    for handler in (mapping_get,mapping_update,library,update,dashboard,display,action):register_command(hass,handler)
    await register_static_paths(hass,[StaticPathConfig('/domosenso_frame/ui/0.2.0',str(Path(__file__).parent/'ui/0.2.0'),False)])
