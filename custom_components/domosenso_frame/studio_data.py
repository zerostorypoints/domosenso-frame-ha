"""Permission-checked, bounded adapters for the portable display contract."""
import asyncio
import base64
import io
import math
import random
import json
import os
import time as clock
from datetime import datetime,time,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.util import dt as dt_util
from .studio_sources import source_entities
from .studio_mappings import readiness,media_controls

DOMAIN='domosenso_frame'

def finite(v):return float(v) if type(v) in (float,int) and math.isfinite(v) else None

def image_data(data):
    """Decode and bound raster bytes; no SVG, active content or remote URLs in the client."""
    if not data or len(data)>8*1024*1024:return None
    from PIL import Image,ImageOps
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width*im.height>24000000:return None
            im=ImageOps.exif_transpose(im);im.thumbnail((1280,1280));out=io.BytesIO();im.convert('RGB').save(out,format='JPEG',quality=80)
            result=out.getvalue()
            return 'data:image/jpeg;base64,'+base64.b64encode(result).decode() if len(result)<1400000 else None
    except Exception:return None

def photos(directory,root,index,count=5,seed=0):
    root=Path(root).resolve();folder=(root/directory).resolve()
    if not folder.is_relative_to(root) or not folder.is_dir():return None
    files=[];scanned=0
    # Do not follow directory symlinks; bound both traversal work and indexed files.
    for current,dirs,names in os.walk(folder,followlinks=False):
        scanned+=1
        dirs[:]=sorted(d for d in dirs if not (Path(current)/d).is_symlink())[:100]
        for name in sorted(names):
            scanned+=1
            if scanned>5000 or len(files)>=1000:break
            path=Path(current)/name;resolved=path.resolve()
            if resolved.is_relative_to(folder) and path.suffix.lower() in ('.jpg','.jpeg','.png','.webp') and path.is_file() and path.stat().st_size<=8*1024*1024:files.append(path)
        if scanned>5000 or len(files)>=1000:break
    if not files:return None
    selected=random.Random(str(seed)).sample(files,min(count,len(files)));path=selected[index%len(selected)]
    image=image_data(path.read_bytes());caption=''
    metadata=folder/'index.json'
    if metadata.is_file() and metadata.resolve().is_relative_to(folder) and metadata.stat().st_size<524288:
        try:
            entries=json.loads(metadata.read_text()).get('photos',[])
            relative=path.relative_to(folder).as_posix()
            for entry in entries[:2000]:
                if isinstance(entry,dict) and entry.get('path') in (relative,str(path)):
                    caption=' · '.join(str(entry[k]) for k in ('event','date','camera','lens') if entry.get(k))[:512];break
        except (OSError,ValueError,AttributeError):pass
    return {'image':image,'caption':caption,'index':index%len(selected),'count':len(selected)} if image else None

class Data:
    def __init__(self,hass,reading,display_snapshot,runtime):self.hass=hass;self.reading=reading;self.display_snapshot=display_snapshot;self.runtime=runtime;self.cache={};self.locks={}
    async def cached(self,key,ttl,fn):
        now=clock.monotonic();entry=self.cache.get(key)
        if entry and now-entry[0]<ttl:return entry[1]
        lock=self.locks.setdefault(key,asyncio.Lock())
        async with lock:
            entry=self.cache.get(key)
            if entry and clock.monotonic()-entry[0]<ttl:return entry[1]
            try:result=await asyncio.wait_for(fn(),8)
            except Exception:result=None
            if len(self.cache)>256:self.cache.clear();self.locks={key:lock}
            self.cache[key]=(clock.monotonic(),result);return result
    def check(self,connection,endpoint):
        if not connection.user.is_active:raise ValueError('Inactive user')
        if self.hass and self.hass.data[DOMAIN].get('studio_mappings') and self.hass.data[DOMAIN]['endpoints'].get(endpoint['id']) is not endpoint:raise ValueError('Sources changed during request')
        entities=source_entities(endpoint.get('studio',{}))|set(endpoint.get('calendars',[]))
        entities.update(r['entity'] for key in ('temperature','air') for r in endpoint.get(key,[]))
        display=endpoint.get('display',{})
        entities.update(r['entity'] for r in display.get('status',[]))
        entities.update(display[k] for k in ('weather','mode_entity','page_entity') if display.get(k))
        for eid in entities:
            if not connection.user.permissions.check_entity(eid,POLICY_READ):raise ValueError('Source not permitted')
    async def snapshot(self,connection,endpoint,offset=0,context=None,capabilities_version=False):
        hass=self.hass;self.check(connection,endpoint);base=self.display_snapshot(hass,connection,endpoint);sources=endpoint.get('studio',{});now=dt_util.utcnow()
        def state(eid):
            if not eid:return None
            if not connection.user.permissions.check_entity(eid,POLICY_READ):raise ValueError('Source not permitted')
            return hass.states.get(eid)
        def available(s):return bool(s and s.state not in ('unknown','unavailable'))
        def stale(s):return not s or (now-s.last_reported).total_seconds()>hass.data[DOMAIN]['settings']['stale_seconds']
        def measure(eid,label,unit=''):
            if not eid:return None
            return self.reading(hass,connection,{'entity':eid,'label':label})
        def attr_measure(s,name,unit):
            value=finite(s.attributes.get(name)) if s else None
            return {'label':name,'value':value,'unit':unit,'available':value is not None and available(s),'stale':bool(stale(s))}
        def plain(eid,default=''):
            s=state(eid);return str(s.state)[:300] if available(s) else default
        room_sources=sources.get('rooms',[{'label':r['label'],'temperature':r['entity']} for r in endpoint.get('temperature',[])])
        rooms=[{'label':r['label'],'temperature':measure(r.get('temperature'),r['label']),'humidity':measure(r.get('humidity'),r['label']),**({'color':r['color']} if 'color'in r else {})} for r in room_sources]
        if not rooms:rooms=[{'label':r['label'],'temperature':r,'humidity':None} for r in base['temperature']]
        statuses=[]
        for r in sources.get('status',[]):
            s=state(r['entity']);statuses.append({'label':r['label'],'group':r.get('group','Home'),'value':str(s.state)[:120] if available(s) else '', 'available':available(s),'stale':bool(stale(s))})
        if not statuses:statuses=[{**r,'group':'Home'} for r in base['status']]
        plants=[]
        for r in sources.get('plants',[]):
            s=state(r['entity']);limits={'moisture':[15,60],'conductivity':[350,2000],'brightness':[500,100000],**r.get('limits',{})}
            plants.append({'label':r['label'],'limits':limits,**{k:attr_measure(s,k,u) for k,u in [('moisture','%'),('conductivity','µS/cm'),('brightness','lx'),('temperature','°C'),('battery','%')]}})
        plant_history=[]
        for row,p in zip(sources.get('plants',[]),plants):
            ps=state(row['entity'])
            if ps:
                if finite(ps.attributes.get('max_brightness')) is not None:p['brightness']=attr_measure(ps,'max_brightness','lx')
                p['problem']=str(ps.attributes.get('problem',''))[:300]
                sensor=(ps.attributes.get('sensors') or {}).get('moisture')
                if sensor:
                    state(sensor);plant_history.append(sensor)
                else:plant_history.append(None)
            else:plant_history.append(None)
        controls={}
        for key in ('lights','covers'):
            controls[key]=[]
            for r in sources.get(key,[]):
                s=state(r['entity']);controls[key].append({'label':r['label'],'state':str(s.state)[:40] if s else 'unavailable','available':available(s),'stale':bool(stale(s)),**({'brightness':finite(s.attributes.get('brightness'))/255 if s and finite(s.attributes.get('brightness')) is not None else None} if key=='lights' else {'position':finite(s.attributes.get('current_position')) if s else None})})
        media=None;ms=state(sources.get('media',{}).get('entity'))
        if sources.get('media'):
            a=ms.attributes if ms else {};position=finite(a.get('media_position'));updated=dt_util.parse_datetime(a.get('media_position_updated_at','')) if isinstance(a.get('media_position_updated_at'),str) else a.get('media_position_updated_at')
            if position is not None and ms.state=='playing' and updated:position+=max(0,(now-updated).total_seconds())
            duration=finite(a.get('media_duration'));position=min(position,duration) if position is not None and duration is not None else position
            media={'available':available(ms),'stale':bool(stale(ms)),'state':ms.state if ms else 'unavailable','title':str(a.get('media_title',''))[:300],'artist':str(a.get('media_artist',''))[:300],'album':str(a.get('media_album_name',''))[:300],'position':position,'duration':duration,'volume':finite(a.get('volume_level')),'source':plain(sources['media'].get('source'),str(a.get('source',''))[:120]),'sources':[str(v)[:120] for v in a.get('source_list',[])[:32]],'quality':plain(sources['media'].get('quality')),'details':[plain(e) for e in sources['media'].get('details',[])],'artwork':None,'canSkip':plain(sources['media'].get('service'))!=sources['media'].get('no_skip','Spotify') if sources['media'].get('service') else True}
        if media:
            controls_available=media_controls(ms)
            media['canSkip']=media['canSkip'] and controls_available['next'] and controls_available['previous']
            if capabilities_version:media['controls']=controls_available
        mains={k:measure(eid,k) for k,eid in sources.get('mains',{}).items()}
        widgets=readiness(endpoint,hass.states);capabilities=[k for k,v in widgets.items() if v['ready'] and k not in ('clock','navigation','media-controls')]
        if widgets['rooms']['ready']:capabilities.append('temperature')
        library=hass.data[DOMAIN]['studio_library'];config=library.config(endpoint['id']);runtime=sources.get('runtime',{});mode=self.runtime.mode(endpoint['id'],plain(runtime.get('mode'),'ambient'));page=plain(runtime.get('page'));music=plain(runtime.get('music_view'))=='on'
        applied=library.saved['applied'].get(endpoint['id']);revision=str(applied['revision'])+applied['templateId'] if applied else 'classic'
        if (applied and applied.get('runtimeOwned')) or not runtime.get('scripts') or endpoint['id'] in self.runtime.navigation:
            active,screen,elapsed,shield=self.runtime.select(endpoint['id'],config,mode,page,music,capabilities,revision)
        else:
            # Until explicit Apply, preserve the old HA-selected layout/page and its scheduler.
            active=base['mode'] if base['mode'] in ('controls','ambient','media') else 'ambient'
            screens=next(m['screens'] for m in config['modes'] if m['id']==active)
            screen=base['screen'] if any(s['id']==base['screen'] for s in screens) else screens[0]['id'];elapsed=shield=0
        uid=connection.user.id;eid=endpoint['id'];generation=id(endpoint)
        async def forecast():
            weather=endpoint.get('display',{}).get('weather')
            if not weather:return []
            result=await hass.services.async_call('weather','get_forecasts',{'entity_id':weather,'type':'daily'},blocking=True,return_response=True,context=context)
            return [{k:v for k,v in item.items() if k in ('datetime','condition','temperature','templow')} for item in (result or {}).get(weather,{}).get('forecast',[])[:4]]
        async def calendar():
            tz=ZoneInfo(hass.config.time_zone);date=datetime.now(tz).date()+timedelta(days=offset);start=datetime.combine(date,time.min,tzinfo=tz);end=start+timedelta(days=1)
            if not endpoint['calendars']:return {'date':date.isoformat(),'offset':offset,'events':[],'available':False}
            result=await hass.services.async_call('calendar','get_events',{'entity_id':endpoint['calendars'],'start_date_time':start.isoformat(),'end_date_time':end.isoformat()},blocking=True,return_response=True,context=context)
            events=[]
            for calendar_id in endpoint['calendars']:
                if calendar_id not in (result or {}):raise ValueError('Unavailable calendar')
                for e in result[calendar_id].get('events',[])[:100]:
                    def point(v):return {'dateTime' if 'T' in v else 'date':v}
                    events.append({'title':str(e.get('summary',''))[:300],'start':point(e['start']),'end':point(e['end'])})
            events.sort(key=lambda e:next(iter(e['start'].values())))
            return {'date':date.isoformat(),'offset':offset,'events':events[:100],'available':True}
        async def history():
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period
            mappings={'temperature':[(r['temperature'],r['label']) for r in room_sources if r.get('temperature')], 'humidity':[(r['humidity'],r['label']) for r in room_sources if r.get('humidity')], 'co2':[(r['entity'],r['label']) for r in endpoint['air'] if r.get('metric')=='co2'], 'pm25':[(r['entity'],r['label']) for r in endpoint['air'] if r.get('metric')=='pm2_5'],'power':[(sources['mains']['power'],'Power')] if sources.get('mains',{}).get('power') else []}
            outside=sources.get('outside',{})
            for metric in ('temperature','humidity'):
                if outside.get(metric):mappings[metric].append((outside[metric],'Outside'))
            result={}
            for metric,rows in mappings.items():
                if not rows:result[metric]=[];continue
                days=7 if metric in ('temperature','humidity') else 1
                response=await get_instance(hass).async_add_executor_job(statistics_during_period,hass,now-timedelta(days=days),now,{e for e,_ in rows},'day' if days==7 else '5minute',None,{'mean'})
                result[metric]=[{'label':label,'dashed':entity in outside.values(),'points':[[v['start']*1000,finite(v.get('mean'))] for v in response.get(entity,[])[:300]]} for entity,label in rows]
            for i,entity in enumerate(plant_history):
                if entity:
                    response=await get_instance(hass).async_add_executor_job(statistics_during_period,hass,now-timedelta(days=7),now,{entity},'hour',None,{'mean'})
                    result['_plant'+str(i)]=[[v['start']*1000,finite(v.get('mean'))] for v in response.get(entity,[])[:168]]
            voltage=sources.get('mains',{}).get('voltage')
            if voltage:
                response=await get_instance(hass).async_add_executor_job(statistics_during_period,hass,now-timedelta(hours=1),now,{voltage},'5minute',None,{'mean'})
                result['_voltage']=[[v['start']*1000,finite(v.get('mean'))] for v in response.get(voltage,[])[:12]]
            return result
        async def artwork():
            from homeassistant.components.media_player import DATA_COMPONENT
            component=hass.data.get(DATA_COMPONENT);player=component.get_entity(sources['media']['entity']) if component and media else None
            if not player:return None
            blob,_=await player.async_get_media_image();return await hass.async_add_executor_job(image_data,blob)
        results=await asyncio.gather(self.cached((uid,eid,generation,'forecast'),600,forecast),self.cached((uid,eid,generation,'calendar',offset,now.date().isoformat()),30,calendar),self.cached((uid,eid,generation,'history'),300,history),self.cached((uid,eid,generation,'art',media.get('title') if media else ''),30,artwork))
        self.check(connection,endpoint)
        weather=base['weather']
        if weather:weather={**weather,'forecast':results[0] or [],'pressure':measure(sources.get('pressure'),'Pressure')}
        if media:media['artwork']=results[3]
        history_data={k:v for k,v in (results[2] or {}).items() if not k.startswith('_')};mains['history']=(results[2] or {}).get('_voltage',[])
        for i,sensor in enumerate(plant_history):
            if sensor:state(sensor)
            plants[i]['history']=(results[2] or {}).get('_plant'+str(i),[])
        photo=None
        if sources.get('photos'):
            current=next(s for m in config['modes'] for s in m['screens'] if m['id']==active and s['id']==screen)
            pw=next((w for w in current['widgets'] if w['type']=='photos'),None);interval=pw.get('options',{}).get('interval',10) if pw else 10;count=pw.get('options',{}).get('count',5) if pw else 5;index=int(elapsed//interval)%count
            exposure=self.runtime.endpoints.get(eid,{}).get('since',0)
            async def loadphoto():return await hass.async_add_executor_job(photos,sources['photos']['directory'],hass.config.path('www'),index,count,exposure)
            photo=await self.cached((uid,eid,generation,'photo',exposure,index,count),60,loadphoto)
        self.check(connection,endpoint)
        for sensor in plant_history:
            if sensor:state(sensor)
        return {'version':2,'config':config,'mode':active,'screen':screen,'connected':True,'fetchedAt':dt_util.utcnow().isoformat(),'timezone':hass.config.time_zone,'capabilities':list(set(capabilities)),'off':mode=='off','wind':plain(sources.get('wind')) in ('on','true'),'readOnly':False,'data':{'weather':weather,'rooms':rooms,'air':base['air'],'assessment':base['assessment'],'status':statuses,'plants':plants,'media':media,'mains':mains,'history':history_data,'calendar':results[1],'photos':photo,**controls,'runtime':{'shieldUntil':(now+timedelta(seconds=shield)).isoformat() if shield else None,'approachEnabled':plain(runtime.get('approach'))=='on' if runtime.get('approach') else None}}}
