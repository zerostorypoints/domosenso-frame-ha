"""Explicit installation bindings; templates cannot choose entities or services."""
import re
from pathlib import Path

def validate_sources(value):
    if not isinstance(value,dict):raise ValueError('Invalid studio sources')
    allowed={'rooms','plants','media','mains','lights','covers','status','pressure','photos','runtime','wind','outside','lights_all_off'}
    if set(value)-allowed:raise ValueError('Unknown studio source')
    def entity(e,domain=None):
        if not isinstance(e,str) or not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+',e) or (domain and not e.startswith(domain+'.')):raise ValueError('Invalid source entity')
    def label(s):
        if not isinstance(s,str) or not 1<=len(s)<=80:raise ValueError('Invalid source label')
    def shape(v,keys):
        if not isinstance(v,dict) or set(v)-set(keys):raise ValueError('Invalid source fields')
    for key in ('pressure','wind','lights_all_off'):
        if key in value:entity(value[key])
    for key in ('rooms','plants','lights','covers','status'):
        rows=value.get(key,[])
        if not isinstance(rows,list) or len(rows)>32:raise ValueError('Too many sources')
        for row in rows:
            label(row.get('label'))
            if key=='rooms':
                shape(row,['label','temperature','humidity','color'])
                for field in ('temperature','humidity'):
                    if field in row:entity(row[field])
                if 'color' in row and (not isinstance(row['color'],str) or not re.fullmatch(r'#[a-fA-F0-9]{6}',row['color'])):raise ValueError('Invalid colour')
            else:
                shape(row,['label','entity','group','limits'] if key=='plants' else ['label','entity','group']);entity(row.get('entity'),'light' if key=='lights' else 'cover' if key=='covers' else 'plant' if key=='plants' else None)
                if 'group' in row:label(row['group'])
                if 'limits' in row:
                    shape(row['limits'],['moisture','conductivity','brightness'])
                    for bounds in row['limits'].values():
                        if not isinstance(bounds,list) or len(bounds)!=2 or any(type(n) not in (int,float) or not 0<=n<=1000000 for n in bounds) or bounds[0]>=bounds[1]:raise ValueError('Invalid plant limits')
    if 'outside' in value:
        shape(value['outside'],['temperature','humidity','color'])
        for key in ('temperature','humidity'):
            if key in value['outside']:entity(value['outside'][key])
    if 'media' in value:
        shape(value['media'],['entity','source','quality','details','service','no_skip']);entity(value['media'].get('entity'),'media_player')
        for field in ('source','quality','service'):
            if field in value['media']:entity(value['media'][field])
        if 'no_skip' in value['media']:label(value['media']['no_skip'])
        for eid in value['media'].get('details',[]):entity(eid)
        if len(value['media'].get('details',[]))>8:raise ValueError('Too many media details')
    if 'mains' in value:
        shape(value['mains'],['power','voltage'])
        for eid in value['mains'].values():entity(eid)
    if 'photos' in value:
        shape(value['photos'],['directory','permission_entity']);entity(value['photos'].get('permission_entity'))
        path=value['photos'].get('directory','')
        if not isinstance(path,str) or not path or Path(path).is_absolute() or '..' in Path(path).parts:raise ValueError('Photo directory must be relative to www')
    if 'runtime' in value:
        runtime=value['runtime'];shape(runtime,['mode','page','music_view','approach','scripts','legacy_loop','legacy_automations','durations'])
        for key in ('mode','page','music_view','approach','legacy_loop'):
            if key in runtime:entity(runtime[key],'script' if key=='legacy_loop' else None)
        shape(runtime.get('scripts',{}),['controls','ambient','media','calendar'])
        for eid in runtime.get('scripts',{}).values():entity(eid,'script')
        autos=runtime.get('legacy_automations',[])
        if not isinstance(autos,list) or len(autos)>2:raise ValueError('Only loop start and watchdog can be transferred')
        for eid in autos:entity(eid,'automation')
        shape(runtime.get('durations',{}),['climate','air','plants','photos','status'])
        for n in runtime.get('durations',{}).values():
            if type(n) is not int or not 10<=n<=600:raise ValueError('Invalid installed screen duration')
    return value

def source_entities(sources):
    result=set()
    def visit(v,key=''):
        if isinstance(v,dict):
            for k,x in v.items():visit(x,k)
        elif isinstance(v,list):
            for x in v:visit(x,key)
        elif isinstance(v,str) and key not in ('label','group','directory','color','no_skip') and re.fullmatch(r'[a-z_]+\.[a-z0-9_]+',v):result.add(v)
    visit(sources);return result
