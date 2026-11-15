"""Version 2 API remains separate from legacy display and layout clients."""
from pathlib import Path
import time
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import STATE_PLAYING
from homeassistant.helpers.storage import Store
from homeassistant.core import callback
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.util import dt as dt_util
from .studio import Library,Conflict,validate_config
from .studio_runtime import Runtime,SessionViews,view_key,installed_durations
from .studio_data import Data
from .studio_mappings import Mappings,save_mappings_verified,merge_patch,readiness
from .assistant_projection import project
from .timers import validate_targets
from .studio_actions import validate_action,perform,takeover
from .hifisync_bridge import add_link_summaries, projection
from .layouts import save_verified
from .assistant_api import inventory_diagnostics
from .assistant_bindings import catalog as assistant_catalog

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
    def assistant_context():
        inventory=hass.states.get('sensor.domosenso_components')
        records=getattr(inventory,'attributes',{}).get('components',[]) if inventory else []
        verified,errors=inventory_diagnostics(state.get('assistant_artifacts',{}),records)
        return verified,{**state.get('assistant_errors',{}),**errors},records
    from .assistant_metadata import active_metadata, source_candidates
    def metadata_candidates():
        records=assistant_context()[2]
        verified=active_metadata(state.get('assistant_metadata_artifacts',{}),records)
        return source_candidates(verified,lambda eid:getattr(registry.async_get(eid),'id',None))
    mappings=state['studio_mappings']=Mappings(state['endpoints'],mapping_saved,persist_mappings,lambda:assistant_context()[0],metadata_candidates)
    def with_assistant_catalog(result):
        verified,errors,records=assistant_context()
        result['timerDevices']=[{'deviceId':d['deviceId'],'name':d['name'], 'endpoint':d['endpoint']} for d in state['grants'].devices() if d['state']=='approved']
        result['candidates']['timers']=[r for r in result['candidates'].get('timers',[]) if state.get('timer_source_verified',lambda _:False)(r['entity'])]
        endpoint=state['endpoints'][result['endpoint']]
        result['widgets']=readiness(project(endpoint,verified,lambda _:True,endpoint.get('display',{}).get('locale','en')),hass.states)
        if engine:=state.get('controls'):result['controlCatalog']=engine.profiles.catalog(endpoint)
        if candidates:=metadata_candidates():result['metadataCatalog']=candidates
        result['assistantCatalog']=assistant_catalog(verified,errors,records,state['endpoints'][result['endpoint']],hass.states)
        return result
    await lib.initialize({id:state['layouts'].get(id) for id,e in state['endpoints'].items() if 'display' in e},installed)
    runtime=state['studio_runtime']=Runtime();data=state['studio_data']=Data(hass,reading,display_snapshot,runtime);state['studio_clients']={};views=SessionViews()
    def command(name,fields={}):return websocket_api.websocket_command({vol.Required('type'):'domosenso_frame/'+name,**fields})
    def deny(c,msg,code='studio_failed',message='Frame operation unavailable or access denied'):c.send_error(msg['id'],code,message)
    def admin(c):
        if not c.user.is_active or not c.user.is_admin:raise ValueError('Administrator access required')
        if hass.data.get('domosenso_frame') is not state:raise ValueError('Frame disabled')
    def version1(value):
        if type(value) is not int or value!=1:raise vol.Invalid('Unsupported mapping version')
        return value
    studio_field = {vol.Optional('hifisyncStudioVersion'): version1}
    mapping_fields={vol.Required('version'):version1,vol.Required('endpoint'):vol.All(str,vol.Length(min=1,max=40)),**studio_field}

    async def with_hifisync_links(h, c, endpoint):
        from homeassistant.loader import IntegrationNotFound, async_get_integration

        installed = 'hifisync' in h.data
        if not installed:
            try:
                await async_get_integration(h, 'hifisync')
                installed = True
            except IntegrationNotFound:
                pass
        # Loading an integration manifest may yield while mappings or permission change.
        admin(c)
        result = mappings.get(endpoint, h.states)
        add_link_summaries(result, installed=installed,
                           provider=h.data.get('hifisync', {}).get('studio_link_v1'),
                           permitted=lambda entity_id: c.user.permissions.check_entity(entity_id, POLICY_READ))
        return result

    @command('studio_mappings',mapping_fields)
    @websocket_api.require_admin
    @websocket_api.async_response
    async def mapping_get(h,c,msg):
        try:
            admin(c)
            eid = msg['endpoint']
            result = await with_hifisync_links(h, c, eid) if msg.get('hifisyncStudioVersion') else mappings.get(eid,h.states)
            c.send_result(msg['id'],with_assistant_catalog(result))
        except Exception:deny(c,msg)
    @command('studio_mappings_update',{**mapping_fields,vol.Required('revision'):vol.All(str,vol.Match(r'^[a-f0-9]{64}$')),vol.Required('patch'):dict})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def mapping_update(h,c,msg):
        try:
            def authorize_mapping():
                admin(c)
                if 'controls' in msg['patch'].get('assistant',{}):
                    engine=state.get('controls')
                    if not engine:raise ValueError('Controls unavailable')
                    engine.profiles.validate_change(state['endpoints'][msg['endpoint']],msg['patch']['assistant']['controls'])
                if 'timers' in msg['patch']:
                    for row in msg['patch']['timers'].get('items',[]):
                        if not state.get('timer_source_verified',lambda _:False)(row.get('entity')): raise ValueError('Select a core timer helper')
                    # Validate the whole projected mapping, both before and after
                    # persistence. This permits a first helper/target on its own
                    # endpoint and catches removals which strand other views.
                    effective=dict(state['endpoints'])
                    effective[msg['endpoint']]=merge_patch(effective[msg['endpoint']],msg['patch'],h.states)
                    validate_targets(effective,state['grants'].records,previous=state['endpoints'])
            result=await mappings.update(msg['endpoint'],msg['revision'],msg['patch'],h.states,authorize_mapping)
            data.cache.clear();runtime.reset(msg['endpoint'])
            if state.get('assistant_metadata_read_enabled'):
                h.bus.async_fire('domosenso_frame_event',{'endpoint':msg['endpoint'],'kind':'assistant_metadata'})
            if 'timers' in msg['patch'] and state.get('timers'):await state['timers'].mapping_changed()
            if 'radar' in msg['patch'] and state.get('radar'):state['radar'].reset(msg['endpoint'])
            admin(c)
            if msg.get('hifisyncStudioVersion'):
                result = await with_hifisync_links(h, c, msg['endpoint'])
            c.send_result(msg['id'],with_assistant_catalog(result))
        except Conflict:deny(c,msg,'mapping_conflict','Source mappings changed. Reload mappings; your template draft is retained.')
        except ValueError as error:deny(c,msg,'mapping_invalid',str(error))
        except Exception:deny(c,msg,'mapping_save_failed','Could not confirm the mapping save. Reload current mappings before retrying.')
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
    @command('studio_dashboard',{vol.Required('endpoint'):str,**studio_field,vol.Optional('capabilitiesVersion'):version1,vol.Optional('hifisyncVersion'):version1,vol.Optional('hifisyncDrawingsVersion'):version1,vol.Optional('offset',default=0):vol.All(int,vol.Range(min=-30,max=30))})
    @websocket_api.async_response
    async def dashboard(h,c,msg):
        try:
            studio = msg.get('hifisyncStudioVersion') == 1
            hifisync = msg.get('hifisyncVersion') == 1
            if studio or hifisync:
                admin(c)
            endpoint = state['endpoints'][msg['endpoint']]
            result = await data.snapshot(
                c, endpoint, msg['offset'], c.context(msg), msg.get('capabilitiesVersion') == 1,
                hifisync, view_key(endpoint['id'], hifisync, preview=True, studio=studio),
                msg.get('hifisyncDrawingsVersion') == 1,
                hifisync_studio_version=studio, preview=True,
            )
            if studio or hifisync:
                admin(c)
            result['readOnly']=True;c.send_result(msg['id'],result)
        except Exception:deny(c,msg)
    @command('studio_display',{**studio_field,vol.Optional('capabilitiesVersion'):version1,vol.Optional('hifisyncVersion'):version1,vol.Optional('hifisyncDrawingsVersion'):version1,vol.Optional('offset',default=0):vol.All(int,vol.Range(min=-30,max=30))})
    @websocket_api.async_response
    async def display(h,c,msg):
        try:
            proof,endpoint=grant(h,c)
            studio = msg.get('hifisyncStudioVersion') == 1
            hifisync = msg.get('hifisyncVersion') == 1
            enabled = endpoint.get('hifisync') is True
            key = view_key(endpoint['id'], hifisync and enabled, studio=studio)
            def disconnected():
                views.forget(c)
                runtime.disconnect(c)
            c.subscriptions.setdefault('domosenso_frame_studio_view', disconnected)
            result = await data.snapshot(
                c, endpoint, msg['offset'], c.context(msg), msg.get('capabilitiesVersion') == 1,
                hifisync, key, msg.get('hifisyncDrawingsVersion') == 1,
                hifisync_studio_version=studio,
            )
            current_proof,current=grant(h,c)
            if current_proof!=proof or current is not endpoint:raise ValueError('Endpoint changed')
            views.remember(c, proof, hifisync, enabled, studio=studio, visit=result.get('roomVisitId'))
            state['studio_clients'][endpoint['id']]=time.monotonic();c.send_result(msg['id'],result)
        except Exception:deny(c,msg)

    @command('lovelace_music', {vol.Required('entity'): vol.All(str, vol.Length(min=1, max=255))})
    @callback
    def lovelace_music(h, c, msg):
        """Return the diagrams available to the paired Lovelace dashboard."""
        try:
            _, endpoint = grant(h, c)
        except ValueError:
            deny(c, msg, 'frame_denied')
            return
        player = msg['entity']
        configured = endpoint.get('studio', {}).get('media', {}).get('entity')
        player_state = h.states.get(player) if player == configured else None
        result = None
        if player_state is not None and player_state.state == STATE_PLAYING:
            provider = h.data.get('hifisync', {}).get('studio_snapshot_v1')
            hifisync = projection(
                endpoint, True, lambda entity: c.user.permissions.check_entity(entity, POLICY_READ),
                provider, dt_util.utcnow(), drawings=True, room_identity=True,
            )
            if hifisync is not None:
                result = {'hifisync': hifisync}
        c.send_result(msg['id'], result)

    @command('studio_action',{vol.Required('action'):validate_action})
    @websocket_api.async_response
    async def action(h,c,msg):
        try:
            proof,endpoint=grant(h,c);key=views.key(c,proof,endpoint.get('hifisync') is True);rt=runtime.endpoints.get(key);now=time.monotonic()
            request = msg['action']
            if request['kind'] == 'room_interaction':
                visit = request['visitId']
                signature = data.room_access(c, endpoint, key) if views.saw_room(c, proof, visit) else None
                if not runtime.permits_room(key, visit, signature) or not runtime.interact_room(
                    key, c, visit, request['action'], request.get('gestureId'), now,
                ):
                    deny(c, msg, 'studio_room_inactive', 'Room visit is no longer active')
                    return
            else:
                if rt and now<rt['shield']:raise ValueError('Touch shield active')
                await perform(h,c,endpoint,request,c.context(msg),lambda:grant(h,c),key)
            c.send_result(msg['id'],{'ok':True})
        except Exception:deny(c,msg)
    for handler in (mapping_get,mapping_update,library,update,dashboard,display,lovelace_music,action):register_command(hass,handler)
    await register_static_paths(hass,[
        StaticPathConfig(f'/domosenso_frame/ui/{version}',str(Path(__file__).parent/'ui'/version),False)
        for version in ('0.2.0','0.2.1','0.2.2','0.2.4','0.2.6','0.2.7')
    ])
