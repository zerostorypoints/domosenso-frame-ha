"""Version 1 controls on the existing HA-authenticated, device-proven connection."""
from pathlib import Path
from .control_actions import Controls, Denied
from .control_profiles import Profiles, active, ACTIONS

DOMAIN='domosenso_frame'


def access(hass,connection,msg,grant,read,control):
    if not connection.user.is_active:raise ValueError('Inactive user')
    state=hass.data[DOMAIN];proof,endpoint=grant(hass,connection)
    if endpoint['id']!=msg['endpoint'] or proof['deviceId']!=msg['deviceId']:raise ValueError('Proof binding mismatch')
    def permitted(entity,policy):
        return connection.user.is_active and connection.user.permissions.check_entity(entity,read if policy=='read' else control)
    return state,endpoint,permitted


def targets(hass,connection,msg,grant,read,control):
    try:
        required={'version','endpoint','deviceId','supported'}
        if (not required<=msg.keys() or set(msg)-required-{'id','type'} or type(msg['version']) is not int or msg['version']!=1
                or not isinstance(msg['supported'],list) or len(msg['supported'])>16
                or any(not isinstance(p,dict) or set(p)!={'actionType','resultType'} or any(not isinstance(v,str) or len(v)>80 for v in p.values()) for p in msg['supported'])):raise ValueError('Invalid negotiation')
        state,endpoint,permitted=access(hass,connection,msg,grant,read,control)
        result=state['controls'].targets(endpoint,msg['deviceId'],permitted)
        for target in result['targets']:
            target['capabilities']=[c for c in target['capabilities'] if hass.services.has_service(*ACTIONS[c['id']][:2])]
        result['targets']=[t for t in result['targets'] if t['capabilities']]
        if {'actionType':'control-action.v1','resultType':'control-result.v1'} not in msg['supported']:result['targets']=[]
        connection.send_result(msg['id'],result)
    except Exception:connection.send_error(msg['id'],'frame_denied','Controls unavailable or access denied')


async def action(hass,connection,msg,grant,read,control):
    try:
        state,_,_=access(hass,connection,msg,grant,read,control);engine=state['controls']
        def authorize():
            if hass.data.get(DOMAIN) is not state or state.get('controls') is not engine:raise ValueError('Bridge unloaded')
            _,endpoint,permitted=access(hass,connection,msg,grant,read,control)
            if msg.get('capability') not in ACTIONS or not hass.services.has_service(*ACTIONS[msg['capability']][:2]):raise Denied('unsupported')
            return endpoint,permitted
        context=connection.context(msg)
        async def service(domain,name,data,_):
            await hass.services.async_call(domain,name,data,blocking=True,context=context)
        result=await engine.execute(msg,authorize,service,context.id)
        connection.send_result(msg['id'],result)
    except Exception:connection.send_error(msg['id'],'frame_denied','Control unavailable or access denied')


async def setup(hass,grant,register_command):
    import voluptuous as vol
    from homeassistant.auth.permissions.const import POLICY_READ,POLICY_CONTROL
    from homeassistant.components import websocket_api
    from homeassistant.core import callback
    from homeassistant.helpers import entity_registry as er, device_registry as dr, area_registry as ar
    from homeassistant.helpers.storage import Store
    from .layouts import save_verified
    state=hass.data[DOMAIN];entities=er.async_get(hass);devices=dr.async_get(hass);areas=ar.async_get(hass)
    def registry(eid):
        entry=entities.async_get(eid)
        if not entry or entry.disabled_by:return None
        device=devices.async_get(entry.device_id) if entry.device_id else None
        return {'id':entry.id,'platform':entry.platform,'unique_id':entry.unique_id,'area_id':entry.area_id,
                'device_id':entry.device_id,'device_area_id':device.area_id if device else None,'aliases':list(entry.aliases)}
    def artifacts():
        inventory=hass.states.get('sensor.domosenso_components')
        return active(state.get('control_artifacts',{}),inventory.attributes.get('components') if inventory else None)
    profiles=Profiles(hass.states.get,hass.states.async_all,artifacts,registry,
                      lambda:{a.id:{'name':a.name,'aliases':list(a.aliases)} for a in areas.async_list_areas()})
    store=Store(hass,1,DOMAIN+'_controls')
    async def persist(value):
        await save_verified(store,value,Path(hass.config.path('.storage',DOMAIN+'_controls')),DOMAIN+'_controls',hass.async_add_executor_job)
    state['controls']=Controls(state['saved']['installationId'],profiles,persist,await store.async_load())
    def version(value):
        if type(value) is not int or value!=1:raise vol.Invalid('Unsupported controls version')
        return value
    base={vol.Required('version'):version,vol.Required('endpoint'):vol.All(str,vol.Length(min=1,max=40)),vol.Required('deviceId'):vol.All(str,vol.Length(min=1,max=256))}
    @websocket_api.websocket_command({vol.Required('type'):DOMAIN+'/control_targets',**base,vol.Required('supported'):list})
    @callback
    def negotiate(h,c,msg):targets(h,c,msg,grant,POLICY_READ,POLICY_CONTROL)
    @websocket_api.websocket_command({vol.Required('type'):DOMAIN+'/control_action',**base,
        **{vol.Required(k):str for k in ('capability','targetId','targetRevision','requestId')},vol.Required('revision'):version,
        vol.Optional('scopeChoice'):vol.In(['configured'])})
    @websocket_api.async_response
    async def execute(h,c,msg):await action(h,c,msg,grant,POLICY_READ,POLICY_CONTROL)
    for handler in (negotiate,execute):register_command(hass,handler)
