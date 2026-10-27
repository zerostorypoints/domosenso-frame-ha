"""Portable template validation and atomic, revisioned library. No HA side effects."""
import asyncio
import copy
import hashlib
import json
import re
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError
from .layouts import Conflict

ASSETS=Path(__file__).parent/'ui/0.2.0'
SCHEMA=json.loads((ASSETS/'schema.json').read_text())
REGISTRY=json.loads((ASSETS/'registry.json').read_text())
BUILTINS=json.loads((ASSETS/'templates.json').read_text())
STATES=('controls','ambient','media')

def positions(screen,portrait=False):
    widgets=screen['widgets']
    if not portrait:return [w['layout'] for w in widgets]
    total=sum(w['layout']['w']*w['layout']['h'] for w in widgets);n=len(widgets)
    extra=[(24-2*n)*w['layout']['w']*w['layout']['h']//total for w in widgets]
    for i in range(24-2*n-sum(extra)):extra[i%n]+=1
    y=0;result=[]
    for w,e in zip(widgets,extra):
        h=2+e;result.append(w.get('portrait',dict(x=0,y=y,w=12,h=h)));y+=h
    return result

def intersects(a,b):return a['x']<b['x']+b['w'] and a['x']+a['w']>b['x'] and a['y']<b['y']+b['h'] and a['y']+a['h']>b['y']

def validate_config(config):
    try:Draft202012Validator(SCHEMA).validate(config)
    except ValidationError as error:raise ValueError('Invalid template configuration') from error
    if set(m['id'] for m in config['modes'])!=set(STATES):raise ValueError('Missing template states')
    for mode in config['modes']:
        if len({s['id'] for s in mode['screens']})!=len(mode['screens']):raise ValueError('Duplicate screens')
        if not any(not s['requires'] for s in mode['screens']):raise ValueError('State needs an unconditional screen')
        for screen in mode['screens']:
            if len({w['id'] for w in screen['widgets']})!=len(screen['widgets']):raise ValueError('Duplicate widgets')
            for portrait in (False,True):
                rects=positions(screen,portrait)
                for i,(w,r) in enumerate(zip(screen['widgets'],rects)):
                    minimum=[3,2] if portrait else REGISTRY[w['type']]['min']
                    if any(type(r[k]) is not int for k in ('x','y','w','h')) or r['x']<0 or r['y']<0 or r['w']<minimum[0] or r['h']<minimum[1] or r['x']+r['w']>12 or r['y']+r['h']>(24 if portrait else 12) or any(intersects(r,p) for p in rects[:i]):raise ValueError('Widgets overlap or do not fit')
    return config

def migrate(old,name='Imported layout'):
    modes=[]
    for mode in STATES:
        src=next((m for m in old['modes'] if m['id']==mode),old['modes'][0]);screens=[];used=set()
        for sid in src['screens']:
            original=next(s for s in old['screens'] if s['id']==sid);chunks=[[]];x=y=0
            for w in original['widgets']:
                width={'small':4,'wide':8,'full':12}[w['size']]
                if x+width>12:y+=3;x=0
                if y+3>12 or len(chunks[-1])>=12:chunks.append([]);x=y=0
                kind=('air' if w.get('topic')=='air' else 'rooms') if w['type']=='measurements' else w['type']
                chunks[-1].append({'id':w['id'],'type':kind,'layout':dict(x=x,y=y,w=width,h=3)});x+=width
            for i,widgets in enumerate(chunks):
                id=original['id'] if i==0 else original['id'][:30]+'-part'+str(i+1)
                collision=0
                while id in used:
                    collision+=1;id=original['id'][:24]+'-part'+str(i+1)+'-'+str(collision)
                used.add(id)
                screens.append({'id':id,'title':original['title'][:70]+(' · '+str(i+1) if i else ''),'duration':0 if src['rotationSeconds']==0 else max(10,min(600,src['rotationSeconds'])),'requires':original.get('requires',[]),'widgets':widgets})
        modes.append({'id':mode,'screens':screens})
    return validate_config({'version':2,'name':name,'locale':old['locale'],'theme':'modern','modes':modes})

class Library:
    def __init__(self,endpoints,saved,persist):
        self.endpoints=set(endpoints);self.persist=persist;self.lock=asyncio.Lock()
        self.saved=copy.deepcopy(saved or {'version':2,'sequence':0,'templates':{},'applied':{},'legacy':{}})
        if self.saved.get('version')!=2:raise ValueError('Unknown library storage version')
        for entry in [*self.saved['templates'].values(),*self.saved['applied'].values()]:validate_config(entry['config'])
    def get(self):
        result=copy.deepcopy(self.saved)
        result['templates']={**{id:{'config':copy.deepcopy(c),'revision':1,'builtin':True} for id,c in BUILTINS.items()},**result['templates']}
        result['revision']=hashlib.sha256(json.dumps(self.saved,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return result
    def config(self,endpoint):
        entry=self.saved['applied'].get(endpoint)
        return copy.deepcopy(entry['config'] if entry else BUILTINS['classic'])
    async def initialize(self,legacy,installed=None):
        """Once-only verified migration; retain exact v1 backup alongside the applied v2 copy."""
        async with self.lock:
            updated=copy.deepcopy(self.saved)
            for eid,entry in legacy.items():
                if eid in updated['legacy']:continue
                updated['legacy'][eid]=copy.deepcopy(entry)
                try:config=migrate(entry['config'],'Imported '+eid)
                except ValueError:
                    updated.setdefault('migrationErrors',{})[eid]='Legacy layout exceeds Studio limits; legacy display remains available'
                    continue
                tid='imported-'+eid
                updated['templates'][tid]={'config':config,'revision':1,'builtin':False}
                updated['applied'][eid]={'templateId':tid,'revision':1,'config':copy.deepcopy(config)}
            for eid,timings in (installed or {}).items():
                tid='installed-'+eid
                if tid in updated['templates'] or not timings:continue
                classic=copy.deepcopy(BUILTINS['classic']);classic['name']='Ramka Classic · installed';classic['locale']=legacy[eid]['config']['locale']
                for mode in classic['modes']:
                    for screen in mode['screens']:
                        if mode['id']=='ambient' and screen['id'] in timings:screen['duration']=timings[screen['id']]
                        if screen['id']=='photos' and 'photos' in timings:screen['widgets'][0]['options']['interval']=max(5,min(120,timings['photos']//5))
                updated['templates'][tid]={'config':validate_config(classic),'revision':1,'builtin':False}
            if updated!=self.saved:await self.persist(updated);self.saved=updated
    async def mutate(self,revision,operation,template_id,config=None,endpoint=None,renderer=None,before_apply=None):
        if not isinstance(template_id,str) or not re.fullmatch('[a-z][a-z0-9_-]{0,63}',template_id):raise ValueError('Invalid template id')
        async with self.lock:
            current=self.get()
            if revision!=current['revision']:raise Conflict('Template library changed; reload before saving')
            updated=copy.deepcopy(self.saved);entry=current['templates'].get(template_id)
            if operation=='save':
                if template_id in BUILTINS:raise ValueError('Built-in template is read-only; save a copy')
                if not entry and len(updated['templates'])>=64:raise ValueError('Template library is full')
                updated['templates'][template_id]={'config':copy.deepcopy(validate_config(config)),'revision':entry['revision']+1 if entry else 1,'builtin':False}
            elif operation=='apply':
                if renderer!=2:raise ValueError('Renderer upgrade required')
                if endpoint not in self.endpoints or not entry:raise ValueError('Unknown endpoint or template')
                updated['applied'][endpoint]={'templateId':template_id,'revision':entry['revision'],'config':copy.deepcopy(entry['config']),'runtimeOwned':True}
            elif operation=='delete':
                if not entry or entry['builtin'] or any(e['templateId']==template_id for e in updated['applied'].values()):raise ValueError('Template is built-in or in use')
                del updated['templates'][template_id]
            else:raise ValueError('Unknown library operation')
            updated['sequence']+=1
            rollback=await before_apply() if operation=='apply' and before_apply else None
            try:await self.persist(copy.deepcopy(updated))
            except Exception:
                if rollback:await rollback()
                raise
            self.saved=updated
            return self.get()
