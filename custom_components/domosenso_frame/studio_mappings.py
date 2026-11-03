"""Admin-selected endpoint overrides. Discovery is evidence, never a binding or grant."""
import asyncio
import copy
import hashlib
import json
import re
import os
import tempfile
import uuid
from .layouts import Conflict,save_verified
from .assistant_selection import validate_policy, validate_change
from .assistant_bindings import validate_selections, validate_new_selections
from .timers import validate_mapping as validate_timers, eligible as timer_eligible
from .radar import validate as validate_radar, eligible as radar_eligible
from .control_profiles import validate_mapping as validate_controls

GROUPS={'rooms','air','weather','calendars','media','lights','covers','status','mains','radar','assistant','timers'}
CLASSES={'temperature':{'temperature'},'humidity':{'humidity'},'co2':{'carbon_dioxide'},'pm2_5':{'pm25','pm2_5'},'voc':{'volatile_organic_compounds','volatile_organic_compounds_parts'},'nox':{'nitrogen_dioxide'},'power':{'power'},'voltage':{'voltage'}}
MEDIA_BITS={'play_pause':1|16384,'previous':16,'next':32,'power':128|256,'volume':4,'source':2048}

def media_controls(state):
    features=state.attributes.get('supported_features',0) if state else 0
    if type(features) is not int:features=0
    # A toggle requires both directions; play/pause also needs both transitions.
    return {key:(features&bits)==bits for key,bits in MEDIA_BITS.items()}

def eligible(state,kind):
    if kind == 'timers': return timer_eligible(state)
    if kind in ('radar_presence','radar_distance'):return radar_eligible(state,kind)
    if not state:return False
    domain=state.entity_id.partition('.')[0]
    if kind in CLASSES:return domain=='sensor' and isinstance(state.attributes.get('device_class'),str) and state.attributes['device_class'] in CLASSES[kind]
    if kind=='assistant':return domain in {'sensor','binary_sensor','plant','media_player','light','calendar','weather','cover','person','device_tracker','input_boolean','input_number','input_select','input_datetime','number','select','switch'}
    if kind=='status':return domain in {'sensor','binary_sensor','input_boolean','light','cover','switch','person','device_tracker'}
    return domain=={'media':'media_player','calendars':'calendar','lights':'light','covers':'cover'}.get(kind,kind)

def discover(states):
    kinds=['timers','radar_presence','radar_distance',*CLASSES,'weather','calendars','media','lights','covers','status','assistant']
    result={key:[] for key in kinds}
    values=states.values() if isinstance(states,dict) else states.async_all()
    for state in sorted(values,key=lambda s:(not bool(s.attributes.get('component')),s.entity_id)):
        for kind in kinds:
            if len(result[kind])>=256 or not eligible(state,kind):continue
            a=state.attributes
            result[kind].append({'entity':state.entity_id,'label':str(a.get('friendly_name') or state.entity_id)[:80], 'unit':str(a.get('unit_of_measurement') or '')[:30], 'deviceClass':str(a.get('device_class') or '')[:80], 'suggested':bool(a.get('component')),**({'controls':media_controls(state)} if kind=='media' else {})})
    return result

def editable(endpoint):
    s=endpoint.get('studio',{});d=endpoint.get('display',{})
    rooms=s.get('rooms',[{'label':r['label'],'temperature':r['entity']} for r in endpoint.get('temperature',[])])
    rows=lambda values,keys:[{k:v for k,v in row.items() if k in keys} for row in values]
    return copy.deepcopy({'timers':endpoint.get('timers',{'writeEnabled':False,'items':[]}),'assistant':endpoint.get('assistant',{'subjects':[],'sensitive':False}),'radar':endpoint.get('radar',{}),'rooms':rows(rooms,{'label','temperature','humidity','roomKey'}),'air':rows(endpoint.get('air',[]),{'label','entity','metric'}),'weather':d.get('weather'),'calendars':endpoint.get('calendars',[]),'media':{'entity':s['media']['entity']} if s.get('media') else {},'lights':rows(s.get('lights',[]),{'label','entity'}),'covers':rows(s.get('covers',[]),{'label','entity'}),'status':rows(s.get('status',d.get('status',[])),{'label','entity'}),'mains':s.get('mains',{})})

def readiness(endpoint,states):
    s=endpoint.get('studio',{});m=editable(endpoint);result={}
    def ready(key,entities,reason,extra=None):
        entities=list(entities);missing=sum(not states.get(e) for e in entities)
        result[key]={'ready':bool(entities) and not missing,'reasons':[f'{missing} mapped source(s) no longer exist.'] if missing else [] if entities else [reason],**(extra or {})}
    result['clock']={'ready':True,'reasons':[]}
    ready('rooms',[r[k] for r in m['rooms'] for k in ('temperature','humidity') if r.get(k)],'Select room temperature or humidity sensors.')
    ready('air',[r['entity'] for r in m['air']],'Select air measurement sensors. Assessment requires an explicit installed policy.')
    if result['air']['ready'] and not any(r.get('policy') for r in endpoint.get('air',[])):result['air']['notes']=['Measurements only; no installed assessment policy.']
    ready('weather',[m['weather']] if m['weather'] else [],'Select a weather entity.')
    for key in ('lights','covers','status'):ready(key,[r['entity'] for r in m[key]],f'Select {key} sources.')
    ready('calendar',m['calendars'],'Select one or more calendars.')
    ready('mains',m['mains'].values(),'Select power or voltage sensors.')
    ready('media',[m['media']['entity']] if m['media'] else [],'Select a media player.')
    controls=media_controls(states.get(m['media']['entity']) if m['media'].get('entity') else None);result['media-controls']={'ready':result['media']['ready'] and any(controls.values()),'reasons':result['media']['reasons'] or ([] if any(controls.values()) else ['This media player advertises no supported controls.']),'controls':controls,'notes':['Unsupported controls: '+', '.join(k for k,v in controls.items() if not v)+'.'] if not all(controls.values()) else []}
    metrics={k:any(states.get(r.get(k)) for r in m['rooms'] if r.get(k)) for k in ('temperature','humidity')}
    metrics.update({key:any(r.get('metric')==metric and states.get(r['entity']) for r in m['air']) for key,metric in [('co2','co2'),('pm25','pm2_5')]});metrics['power']=bool(states.get(m['mains'].get('power'))) if m['mains'].get('power') else False
    result['history']={'ready':any(metrics.values()),'reasons':[] if any(metrics.values()) else ['Map the measurement used by this chart. Recorder statistics must be available.'],'metrics':{k:bool(v) for k,v in metrics.items()}}
    scripts=s.get('runtime',{}).get('scripts',{});targets={key:bool(states.get(scripts[key])) if scripts.get(key) else True for key in ('controls','ambient','media','calendar')}
    result['navigation']={'ready':targets['controls'],'reasons':[] if targets['controls'] else ['The mapped controls script no longer exists.'],'targets':targets}
    ready('plants',[r['entity'] for r in s.get('plants',[])],'Requires advanced YAML plant entities and limits; retained without changes.')
    ready('photos',[s['photos']['permission_entity']] if s.get('photos') else [],'Requires an advanced YAML local photo directory and permission entity; retained without changes.')
    if result['photos']['ready']:result['photos']['notes']=['Directory contents are checked when loading photos.']
    return result

def merge_patch(endpoint,patch,states=None):
    if not isinstance(patch,dict) or not patch or set(patch)-GROUPS:raise ValueError('Invalid mapping groups')
    out=copy.deepcopy(endpoint);s=out.setdefault('studio',{});d=out.setdefault('display',{'locale':'en','status':[]});old=editable(endpoint)
    def shape(v,keys):
        if not isinstance(v,dict) or set(v)-set(keys):raise ValueError('Invalid mapping fields')
    def label(v):
        if not isinstance(v,str) or not v.strip() or len(v)>80 or any(ord(c)<32 for c in v):raise ValueError('Invalid mapping label')
    def localized_label(v):
        if isinstance(v,dict):
            if 'en' not in v or set(v)-{'en','de','pl'}: raise ValueError('Invalid localized label')
            for text in v.values(): label(text)
        else: label(v)
    def entity(v,kind,previous=()):
        if not isinstance(v,str) or not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+',v):raise ValueError('Invalid mapping entity')
        if states is not None and v not in previous and not eligible(states.get(v),kind):raise ValueError('Select an existing eligible entity')
    for key,value in patch.items():
        if key=='timers':
            out['timers']=validate_timers(value,states,endpoint)
        elif key=='assistant':
            shape(value,['subjects','sensitive','explanations','calendar_sensitive','presence_sensitive','component_sources','source_policy','controls'])
            if 'controls' in value:validate_controls(value['controls'])
            # Controls-only changes preserve every existing read grant. Legacy read
            # saves preserve independent actuator consent.
            if set(value)=={'controls'}:value={**copy.deepcopy(out.get('assistant',{'subjects':[],'sensitive':False})),**value}
            elif 'controls' not in value and 'controls' in out.get('assistant',{}):value={**value,'controls':copy.deepcopy(out['assistant']['controls'])}
            if 'source_policy' in value:validate_policy(value['source_policy'])
            elif 'source_policy' in out.get('assistant',{}):value={**value,'source_policy':copy.deepcopy(out['assistant']['source_policy'])}
            validate_selections(value.get('component_sources',[]))
            if not {'subjects','sensitive'}<=set(value) or type(value['sensitive']) is not bool or not isinstance(value['subjects'],list) or len(value['subjects'])>100:raise ValueError('Invalid assistant mapping')
            if any(k in value and type(value[k]) is not bool for k in ('calendar_sensitive','presence_sensitive')):raise ValueError('Invalid sensitive exposure')
            explanations=value.get('explanations',[])
            if not isinstance(explanations,list) or len(explanations)>64 or any(not isinstance(cid,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,79}',cid) for cid in explanations) or len(set(explanations))!=len(explanations):raise ValueError('Invalid component explanations')
            kinds={'room','plant','player','light','calendar','weather','presence_source','awning','audio_source','backup','frame'}
            primary={'light':'light','player':'media_player','calendar':'calendar','weather':'weather','awning':'cover'}
            seen=set()
            for row in value['subjects']:
                shape(row,['key','kind','label','fields','roomKey','roomLabel','indoor','limits'])
                localized_label(row.get('label'))
                if not isinstance(row.get('key'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',row['key']) or row['key'] in seen or row.get('kind') not in kinds:raise ValueError('Invalid assistant subject')
                seen.add(row['key'])
                if 'roomKey' in row and (not isinstance(row['roomKey'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',row['roomKey'])):raise ValueError('Invalid room key')
                if 'roomLabel' in row:
                    localized_label(row['roomLabel'])
                if 'indoor' in row and type(row['indoor']) is not bool:raise ValueError('Invalid indoor flag')
                fields=row.get('fields')
                if not isinstance(fields,dict) or not fields or len(fields)>32:raise ValueError('Invalid assistant fields')
                for role,source in fields.items():
                    if not isinstance(role,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,79}',role):raise ValueError('Invalid role')
                    shape(source,['entity','attribute','maxAgeSeconds'])
                    eid=source.get('entity')
                    if not isinstance(eid,str) or not re.fullmatch(r'[a-z_]+\.[a-z0-9_]+',eid) or eid.split('.')[0] in {'script','automation','button','scene','service'}:raise ValueError('Invalid assistant entity')
                    if role=='state' and row['kind'] in primary and not eid.startswith(primary[row['kind']]+'.'):raise ValueError('Invalid subject domain')
                    if 'attribute' in source and (not isinstance(source['attribute'],str) or not re.fullmatch(r'[A-Za-z0-9_]{1,80}',source['attribute'])):raise ValueError('Invalid assistant attribute')
                    if 'maxAgeSeconds' in source and (type(source['maxAgeSeconds']) is not int or not 60<=source['maxAgeSeconds']<=86400):raise ValueError('Invalid freshness')
                    if states is not None and not states.get(eid) and not any(eid==f.get('entity') for oldrow in old['assistant']['subjects'] for f in oldrow['fields'].values()):raise ValueError('Select an existing assistant entity')
                if 'limits' in row and (not isinstance(row['limits'],dict) or len(row['limits'])>32 or any(not isinstance(k,str) or type(v) not in (int,float,str,bool) for k,v in row['limits'].items())):raise ValueError('Invalid assistant limits')
            out['assistant']=copy.deepcopy(value)
        elif key=='radar':
            radar=validate_radar(value,states)
            if radar:out['radar']=radar
            else:out.pop('radar',None)
        elif key=='weather':
            if value is None:d.pop('weather',None)
            else:entity(value,'weather',[old[key]]);d['weather']=value
        elif key in ('media','mains'):
            shape(value,['entity'] if key=='media' else ['power','voltage'])
            if not value:s.pop(key,None);continue
            current=copy.deepcopy(s.get(key,{}))
            for field,eid in value.items():
                if eid is None:
                    if key=='media':raise ValueError('Clear media with an empty mapping')
                    current.pop(field,None);continue
                entity(eid,'media' if key=='media' else field,[old[key].get(field)]);current[field]=eid
            if key=='media' and not current.get('entity'):raise ValueError('Select a media player')
            s[key]=current
        else:
            if not isinstance(value,list) or len(value)>(16 if key=='calendars' else 32):raise ValueError('Too many mappings')
            if key=='calendars':
                for eid in value:entity(eid,key,old[key])
                if len(set(value))!=len(value):raise ValueError('Duplicate calendars')
                out[key]=copy.deepcopy(value);continue
            rows=[];seen=set()
            existing=s.get(key,[]) if key!='air' else endpoint.get('air',[])
            for row in value:
                shape(row,['label','temperature','humidity','roomKey'] if key=='rooms' else ['label','entity','metric'] if key=='air' else ['label','entity']);label(row.get('label'))
                if key=='rooms':
                    if not any(row.get(k) for k in ('temperature','humidity')):raise ValueError('Select at least one room measurement')
                    for field in ('temperature','humidity'):
                        if field in row:entity(row[field],field,[r.get(field) for r in old[key]])
                    # Metadata belongs to a source row, never its current position in the list.
                    exact=[r for r in existing if all(r.get(k)==row.get(k) for k in ('temperature','humidity'))]
                    shared=[r for r in existing if any(row.get(k) and r.get(k)==row[k] for k in ('temperature','humidity'))]
                    candidates=exact or shared
                    if row.get('roomKey'):
                        try: uuid.UUID(row['roomKey'])
                        except (ValueError, TypeError, AttributeError): raise ValueError('Invalid room key')
                        candidates=[r for r in existing if r.get('roomKey')==row['roomKey']]
                    previous=candidates[0] if len(candidates)==1 else {}
                    merged={k:v for k,v in previous.items() if k not in ('temperature','humidity','label')}
                    merged['roomKey'] = previous.get('roomKey') or row.get('roomKey') or str(uuid.uuid4())
                else:
                    metric=row.get('metric');previous=next((r for r in existing if r.get('entity')==row.get('entity')),{});merged=copy.deepcopy(previous)
                    if key=='air' and metric not in {'co2','pm2_5','voc','nox','score'}:raise ValueError('Select an air metric')
                    # Existing explicit score sources remain editable; arbitrary sensors cannot become scores.
                    entity(row.get('entity'),metric if key=='air' else key,[r['entity'] for r in old[key] if key!='air' or r.get('metric')==metric])
                    if row['entity'] in seen:raise ValueError('Duplicate sources')
                    seen.add(row['entity'])
                merged.update(copy.deepcopy(row));rows.append(merged)
            if key=='air':out['air']=rows
            else:s[key]=rows
            if key=='rooms':
                original={r['entity']:r for r in endpoint.get('temperature',[])}
                out['temperature']=[{**original.get(r['temperature'],{}),'entity':r['temperature'],'label':r['label'],'metric':'temperature','roomKey':r['roomKey']} for r in rows if r.get('temperature')]
            if key=='status':d['status']=[{'label':r['label'],'entity':r['entity']} for r in rows]
    return out

class Mappings:
    def __init__(self,endpoints,saved,persist,assistant_artifacts=None):
        self.assistant_artifacts=assistant_artifacts
        self.endpoints=endpoints;self.defaults=copy.deepcopy(endpoints);self.saved=copy.deepcopy(saved);self.persist=persist;self.lock=asyncio.Lock()
        if not isinstance(saved,dict):raise ValueError('Invalid saved mappings')
        for eid,entry in saved.items():
            if eid in endpoints:
                if not isinstance(entry,dict) or set(entry)!={'patch','sequence'} or type(entry['sequence']) is not int or entry['sequence']<1:raise ValueError('Invalid mapping record')
                endpoints[eid]=merge_patch(self.defaults[eid],entry['patch'])
    def get(self,eid,states):
        e=self.endpoints.get(eid)
        if not e or 'display' not in e:raise ValueError('Unknown frame')
        revision=hashlib.sha256(json.dumps([self.defaults[eid],self.saved.get(eid,{})],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return {'version':1,'endpoint':eid,'revision':revision,'mappings':editable(e),'candidates':discover(states),'widgets':readiness(e,states)}
    async def update(self,eid,revision,patch,states,authorize):
        async with self.lock:
            authorize()
            if revision!=self.get(eid,states)['revision']:raise Conflict('Source mappings changed. Reload mappings; your template draft is retained.')
            def validate_component_change():
                if self.assistant_artifacts is not None and 'assistant' in patch:
                    if 'source_policy' in patch['assistant']:validate_change(self.endpoints[eid].get('assistant',{}).get('source_policy'),patch['assistant']['source_policy'],self.assistant_artifacts())
                    validate_new_selections(self.endpoints[eid].get('assistant',{}).get('component_sources',[]), patch['assistant'].get('component_sources',[]), self.assistant_artifacts())
            validate_component_change()
            effective=merge_patch(self.endpoints[eid],patch,states);updated=copy.deepcopy(self.saved)
            # Store only explicitly edited groups, separate from YAML and templates. Nested keys merge.
            combined=copy.deepcopy(updated.get(eid,{}).get('patch',{}))
            for key,value in patch.items():
                if key=='mains' and value:combined[key]={field:effective.get('studio',{}).get('mains',{}).get(field) for field in ('power','voltage')}
                elif key=='assistant':combined[key]=copy.deepcopy(effective['assistant'])
                elif key=='rooms':combined[key]=copy.deepcopy(effective['studio']['rooms'])
                else:combined[key]=copy.deepcopy(value)
            updated[eid]={'patch':combined,'sequence':updated.get(eid,{}).get('sequence',0)+1}
            # Recompute from defaults so restart behavior is identical to the live endpoint.
            effective=merge_patch(self.defaults[eid],combined)
            try:
                await self.persist(copy.deepcopy(updated));authorize()
                merge_patch(self.endpoints[eid],patch,states)
                validate_component_change()
            except Exception:
                await self.persist(copy.deepcopy(self.saved));raise
            self.saved=updated;self.endpoints[eid]=effective
            return self.get(eid,states)

async def save_mappings_verified(store,value,path,executor):
    """Retain the previous envelope even when Store writes bytes but verification fails."""
    def backup():return path.read_bytes() if path.exists() else None
    previous=await executor(backup)
    if any('source_policy' in entry.get('patch',{}).get('assistant',{}) for entry in value.values()):
        def retain_pre_policy():
            destination=path.with_name(path.name+'.pre-source-policy-v1')
            if destination.is_symlink():raise OSError('Invalid source policy backup')
            try:fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            except FileExistsError:return
            try:
                with os.fdopen(fd,'wb') as file:
                    file.write(previous if previous is not None else json.dumps({'version':1,'minor_version':1,'key':'domosenso_frame_mappings','data':{}}).encode())
                    file.flush();os.fsync(file.fileno())
            except Exception:
                destination.unlink(missing_ok=True);raise
        await executor(retain_pre_policy)
    try:await save_verified(store,value,path,'domosenso_frame_mappings',executor)
    except Exception:
        def restore():
            if previous is None:path.unlink(missing_ok=True);return
            fd,name=tempfile.mkstemp(prefix='.frame-mapping-',dir=path.parent)
            try:
                with os.fdopen(fd,'wb') as file:file.write(previous);file.flush();os.fsync(file.fileno())
                os.replace(name,path)
                if path.read_bytes()!=previous:raise OSError('Mapping rollback verification failed')
            finally:
                if os.path.exists(name):os.unlink(name)
        await executor(restore)
        raise
