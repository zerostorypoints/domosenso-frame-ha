"""Bounded opt-in grants for future subjects, never future capabilities."""
import copy
import re

READS = {
 'plants.watering_need':'plant','plants.status':'plant','plants.last_watered':'plant','plants.water_in':'plant',
 'climate-air.temperature':'room','climate-air.humidity':'room','climate-air.status':'room',
 'climate-air.air_readings':'room','climate-air.air_assessment':'room',
 'lights.status':'light','media.now_playing':'player','media.status':'player','media.mains':'player',
 'calendar.agenda':'calendar','weather.current':'weather','weather.forecast':'weather',
 'awnings.status':'awning','awnings.wind':'awning','bermuda-presence.observations':'presence_source',
 'audio-sensor.level':'audio_source','backup.status':'backup','ramka.status':'frame',
}


def proposals(component, bundle):
    result=[]
    for kind in sorted({s['kind'] for s in bundle['bindings']['subjects']}):
        definitions=[c for c in bundle['definitions']['capabilities'] if c.get('operation')=='read'
                     and c.get('revision')==1 and READS.get(c['id'])==kind]
        roles={f['role'] for c in definitions for f in c.get('required_facts',[])+c.get('optional_facts',[])}
        fields={r for s in bundle['bindings']['subjects'] if s['kind']==kind for r in s['fields']}
        roles &= fields
        if definitions and roles:
            result.append({'component':component,'kind':kind,'roles':sorted(roles),
                           'capabilities':{c['id']:c['revision'] for c in definitions},'exclude':[]})
    return result


def validate_policy(value):
    if not isinstance(value,dict) or set(value)!={'version','scopes'} or type(value['version']) is not int or value['version']!=1:
        raise ValueError('Invalid source policy version')
    scopes=value['scopes']
    if not isinstance(scopes,list) or len(scopes)>64:raise ValueError('Invalid source scopes')
    seen=set()
    for s in scopes:
        if not isinstance(s,dict) or set(s)!={'component','kind','roles','capabilities','exclude'}:raise ValueError('Invalid source scope')
        if not isinstance(s['component'],str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',s['component']) or s['kind'] not in set(READS.values()):raise ValueError('Invalid source provider')
        key=(s['component'],s['kind'])
        if key in seen:raise ValueError('Duplicate source scope')
        seen.add(key)
        if not isinstance(s['roles'],list) or not 1<=len(s['roles'])<=32 or any(not isinstance(r,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,79}',r) for r in s['roles']) or len(set(s['roles']))!=len(s['roles']):raise ValueError('Invalid source roles')
        if not isinstance(s['capabilities'],dict) or not s['capabilities'] or len(s['capabilities'])>32 or any(READS.get(c)!=s['kind'] or type(v) is not int or v!=1 for c,v in s['capabilities'].items()):raise ValueError('Unsupported source capability')
        if not isinstance(s['exclude'],list) or len(s['exclude'])>256 or any(not isinstance(k,str) or not 1<=len(k)<=160 or any(ord(c)<32 for c in k) for k in s['exclude']) or len(set(s['exclude']))!=len(s['exclude']):raise ValueError('Invalid exclusions')
    return value


def validate_change(previous, proposed, artifacts):
    validate_policy(proposed)
    old=(previous or {}).get('scopes',[])
    for scope in proposed['scopes']:
        # Revocation remains possible while a provider is offline.
        earlier=next((s for s in old if (s['component'],s['kind'])==(scope['component'],scope['kind'])),None)
        if earlier and set(scope['roles'])<=set(earlier['roles']) and all(earlier['capabilities'].get(c)==v for c,v in scope['capabilities'].items()) and set(scope['exclude'])>=set(earlier['exclude']):continue
        choices=proposals(scope['component'],artifacts[scope['component']]) if scope['component'] in artifacts else []
        candidate=next((p for p in choices if p['kind']==scope['kind']),None)
        if not candidate or not set(scope['roles'])<=set(candidate['roles']) or any(candidate['capabilities'].get(c)!=v for c,v in scope['capabilities'].items()):
            raise ValueError('Source scope changed or metadata unavailable. Reload sources.')


def dynamic_subjects(endpoint, artifacts, capability=None):
    result=[]
    for scope in endpoint.get('assistant',{}).get('source_policy',{}).get('scopes',[]):
        bundle=artifacts.get(scope['component'])
        if not bundle:continue
        definitions={c['id']:c for c in bundle['definitions']['capabilities']}
        active=[c for c,v in scope['capabilities'].items() if READS.get(c)==scope['kind'] and v==1
                and definitions.get(c,{}).get('revision')==v and definitions[c].get('operation')=='read']
        if not active or capability is not None and capability not in active:continue
        # Newly declared roles are never included by an existing scope.
        live_roles={f['role'] for c in active for f in definitions[c].get('required_facts',[])+definitions[c].get('optional_facts',[])}
        roles=set(scope['roles']) & live_roles
        for subject in bundle['bindings']['subjects']:
            if subject['kind']!=scope['kind'] or subject['key'] in scope['exclude']:continue
            fields={r:copy.deepcopy(f) for r,f in subject['fields'].items() if r in roles}
            if fields:result.append({**copy.deepcopy(subject),'fields':fields,'_component':scope['component']})
    return result
