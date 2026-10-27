"""Small action surface mapped by the installation, never by a template."""
import math
from .studio_mappings import media_controls
from homeassistant.auth.permissions.const import POLICY_READ,POLICY_CONTROL

KINDS={'media':{'play_pause','previous','next','volume','source','power'},'light':{'toggle','brightness','all_off'},'cover':{'open','close','stop','open_all','close_all','stop_all'},'navigation':{'controls','ambient','media','calendar','next','previous'},'runtime':{'approach'}}

def validate_action(value):
    if not isinstance(value,dict) or set(value)-{'kind','action','index','value'}:raise ValueError('Invalid action')
    kind=value.get('kind');action=value.get('action')
    if kind not in KINDS or action not in KINDS[kind]:raise ValueError('Unsupported action')
    if kind in ('light','cover') and action not in ('all_off','open_all','close_all','stop_all'):
        if type(value.get('index')) is not int or not 0<=value['index']<32:raise ValueError('Invalid control index')
    elif 'index' in value:raise ValueError('Unexpected index')
    if action in ('volume','brightness'):
        if type(value.get('value')) not in (int,float) or not math.isfinite(value['value']) or not 0<=value['value']<=1:raise ValueError('Invalid level')
    elif action=='source':
        if not isinstance(value.get('value'),str) or not 1<=len(value['value'])<=120:raise ValueError('Invalid source')
    elif action=='approach':
        if type(value.get('value')) is not bool:raise ValueError('Invalid approach setting')
    elif 'value' in value:raise ValueError('Unexpected action value')
    return value

def action_call(sources,action,states):
    validate_action(action);kind=action['kind'];name=action['action'];data={}
    if kind=='media':
        entity=sources.get('media',{}).get('entity');domain='media_player'
        if not entity or not media_controls(states.get(entity)).get(name):raise ValueError('Media control unsupported')
        service={'play_pause':'media_play_pause','previous':'media_previous_track','next':'media_next_track','volume':'volume_set','source':'select_source','power':'toggle'}[name]
        if name in ('previous','next') and sources['media'].get('service'):
            service_state=states.get(sources['media']['service'])
            if not service_state or service_state.state in ('unknown','unavailable',sources['media'].get('no_skip','Spotify')):raise ValueError('Skipping is unavailable for this source')
        if name=='volume':data['volume_level']=action['value']
        if name=='source':
            state=states.get(entity)
            if not state or action['value'] not in state.attributes.get('source_list',[]):raise ValueError('Unknown source')
            data['source']=action['value']
    elif kind in ('light','cover'):
        rows=sources.get('lights' if kind=='light' else 'covers',[])
        if name=='all_off':
            entity=sources.get('lights_all_off')
            if entity:
                if not entity.startswith('scene.'):raise ValueError('All-off must be a mapped scene')
                return 'scene','turn_on',{'entity_id':entity}
            if not rows:raise ValueError('Unmapped controls')
            return 'light','turn_off',{'entity_id':[r['entity'] for r in rows]}
        if name in ('open_all','close_all','stop_all'):
            if not rows:raise ValueError('Unmapped controls')
            return 'cover',{'open_all':'open_cover','close_all':'close_cover','stop_all':'stop_cover'}[name],{'entity_id':[r['entity'] for r in rows]}
        if action['index']>=len(rows):raise ValueError('Unmapped control')
        entity=rows[action['index']]['entity'];domain=kind;service={'toggle':'toggle','brightness':'turn_on','open':'open_cover','close':'close_cover','stop':'stop_cover'}[name]
        if name=='brightness':data['brightness_pct']=round(action['value']*100)
    elif kind=='runtime':entity=sources.get('runtime',{}).get('approach');domain='input_boolean';service='turn_on' if action['value'] else 'turn_off'
    elif name in ('next','previous'):return None
    else:entity=sources.get('runtime',{}).get('scripts',{}).get(name);domain='script';service='turn_on'
    if not entity:raise ValueError('Control is not mapped')
    return domain,service,{'entity_id':entity,**data}

async def perform(hass,connection,endpoint,action,context,authorize):
    validate_action(action);authorize()
    if action['kind']=='navigation' and action['action'] not in ('next','previous') and not endpoint.get('studio',{}).get('runtime',{}).get('scripts',{}).get(action['action']):
        hass.data['domosenso_frame']['studio_runtime'].navigate(endpoint['id'],action['action']);return
    call=action_call(endpoint.get('studio',{}),action,hass.states)
    if call is None:
        hass.data['domosenso_frame']['studio_runtime'].step(endpoint['id'],1 if action['action']=='next' else -1);return
    domain,service,data=call;entities=data['entity_id'] if isinstance(data['entity_id'],list) else [data['entity_id']]
    from homeassistant.util import dt as dt_util
    for entity in entities:
        if not connection.user.permissions.check_entity(entity,POLICY_READ) or not connection.user.permissions.check_entity(entity,POLICY_CONTROL):raise ValueError('Control not permitted')
        state=hass.states.get(entity)
        if not state or state.state in ('unknown','unavailable'):raise ValueError('Control unavailable')
        if domain in ('light','cover','media_player') and (dt_util.utcnow()-state.last_reported).total_seconds()>hass.data['domosenso_frame']['settings']['stale_seconds']:raise ValueError('Control data is stale')
    await hass.services.async_call(domain,service,data,blocking=True,context=context);authorize()
    if action['kind']=='navigation':
        runtime=hass.data['domosenso_frame']['studio_runtime']
        # A successful installed script takes state authority back from a local detour.
        runtime.release_navigation(endpoint['id'])
        if action['action'] in ('ambient','media'):runtime.reset(endpoint['id'])

async def takeover(hass,connection,endpoint,context):
    """Transfer only declared ambient loop ownership; roll back if persistence fails."""
    runtime=endpoint.get('studio',{}).get('runtime',{});autos=runtime.get('legacy_automations',[]);loop=runtime.get('legacy_loop');changed=[]
    if loop and len(autos)!=2:raise ValueError('Map both legacy ambient start and watchdog before applying')
    for entity in [*autos,*([loop] if loop else [])]:
        if not connection.user.permissions.check_entity(entity,POLICY_CONTROL):raise ValueError('Scheduler transfer not permitted')
        if not hass.states.get(entity):raise ValueError('Scheduler source unavailable')
    loop_was_running=bool(loop and hass.states.get(loop).state=='on')
    async def restore():
        for entity in changed:await hass.services.async_call('automation','turn_on',{'entity_id':entity},blocking=True,context=context)
        if loop_was_running:await hass.services.async_call('script','turn_on',{'entity_id':loop},blocking=True,context=context)
    try:
        for entity in autos:
            if hass.states.get(entity).state=='on':
                await hass.services.async_call('automation','turn_off',{'entity_id':entity,'stop_actions':True},blocking=True,context=context);changed.append(entity)
        if loop:await hass.services.async_call('script','turn_off',{'entity_id':loop},blocking=True,context=context)
        return restore
    except Exception:
        await restore();raise
