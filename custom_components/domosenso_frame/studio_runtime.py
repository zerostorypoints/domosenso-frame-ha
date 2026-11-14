"""HA-owned playlist timing; there is no Android scheduler."""
import time

def view_key(endpoint, negotiated, preview=False):
    return (endpoint, 'preview' if preview else 'hifisync' if negotiated else 'legacy')

class SessionViews:
    """Bind a negotiated cursor to a proved connection, never to a request endpoint."""
    def __init__(self):self.connections={}
    def remember(self,connection,proof,negotiated,enabled):
        binding=(proof['deviceId'],proof['endpoint'])
        if negotiated and enabled:self.connections[connection]=binding
        elif not enabled or self.connections.get(connection)!=binding:self.connections.pop(connection,None)
    def key(self,connection,proof,enabled):
        binding=(proof['deviceId'],proof['endpoint'])
        return view_key(proof['endpoint'],bool(enabled and self.connections.get(connection)==binding))
    def forget(self,connection):self.connections.pop(connection,None)

class Runtime:
    def __init__(self):self.endpoints={};self.navigation={}
    def navigate(self,eid,mode):self.navigation[eid]=mode;self.reset(eid)
    def mode(self,eid,fallback):return self.navigation.get(eid,fallback)
    def release_navigation(self,eid):
        if eid in self.navigation:self.navigation.pop(eid);self.reset(eid)
    def select(self,eid,config,mode,page,music,capabilities,revision,now=None,paused=False):
        now=time.monotonic() if now is None else now
        active='media' if mode=='media' or (mode=='ambient' and music) else 'controls' if mode in ('controls','calendar') else 'ambient'
        if mode=='calendar':
            active=next((m['id'] for m in sorted(config['modes'],key=lambda m:m['id']!='controls') if any(any(w['type']=='calendar' for w in s['widgets']) for s in m['screens'])),'controls')
        screens=next(m['screens'] for m in config['modes'] if m['id']==active)
        visible=[s for s in screens if set(s['requires'])<=set(capabilities)]
        if not visible:visible=[s for s in screens if not s['requires']]
        key=(revision,active,mode,tuple(s['id'] for s in visible))
        state=self.endpoints.get(eid)
        if not state or state['key']!=key:
            preferred=next((s['id'] for s in visible if any(w['type']=='calendar' for w in s['widgets'])),None) if mode=='calendar' else page
            if active=='media' and any(set(s['requires']) & {'hifisync-system','hifisync-room'} for s in screens):preferred=None
            index=next((i for i,s in enumerate(visible) if s['id']==preferred),0)
            state={'key':key,'index':index,'since':now,'shield':now+.7 if active=='controls' else 0};self.endpoints[eid]=state
        hifi_music=active=='media' and any(set(s['requires']) & {'hifisync-system','hifisync-room'} for s in screens)
        paused=bool(paused and hifi_music and mode!='off')
        if paused and state.get('paused_at') is None:state['paused_at']=now
        elif not paused and state.get('paused_at') is not None:
            state['since']+=max(0,now-state['paused_at']);state['paused_at']=None
        elapsed=max(0,(state['paused_at'] if paused else now)-state['since']);visited=0
        while not paused and mode not in ('calendar','off') and visible[state['index']]['duration'] and elapsed>=visible[state['index']]['duration'] and visited<1000:
            duration=visible[state['index']]['duration'];elapsed-=duration;state['since']+=duration;state['index']=(state['index']+1)%len(visible);visited+=1
            skipped=0
            while set(visible[state['index']]['requires']) & {'hifisync-system','hifisync-room'} and not visible[state['index']]['duration'] and skipped<len(visible):
                state['index']=(state['index']+1)%len(visible);skipped+=1
        state['count']=len(visible)
        return active,visible[state['index']]['id'],max(0,(state['paused_at'] if paused else now)-state['since']),max(0,state['shield']-now)
    def step(self,eid,delta,now=None):
        state=self.endpoints.get(eid)
        if state:
            state['index']=(state['index']+delta)%state['count'];state['since']=time.monotonic() if now is None else now
            if state.get('paused_at') is not None:state['paused_at']=state['since']
    def reset(self,eid):
        if isinstance(eid,tuple):self.endpoints.pop(eid,None);return
        for key in list(self.endpoints):
            if key==eid or isinstance(key,tuple) and key[0]==eid:self.endpoints.pop(key,None)

def installed_durations(raw,page_entity,photo_count=5):
    """Read numeric delays from the installed Ramka script; never infer them from labels."""
    result={}
    def delay_seconds(value):
        if isinstance(value,dict) and set(value)<= {'seconds','minutes','hours','milliseconds'}:
            try:return float(value.get('seconds',0))+60*float(value.get('minutes',0))+3600*float(value.get('hours',0))+float(value.get('milliseconds',0))/1000
            except (ValueError,TypeError):return None
        if type(value) in (int,float):return value
        return None
    def delays(value):
        if isinstance(value,list):return [n for v in value for n in delays(v)]
        if isinstance(value,dict):
            if 'delay' in value:
                n=delay_seconds(value['delay']);return [n] if n is not None else []
            return [n for v in value.values() for n in delays(v)]
        return []
    def visit(value):
        if isinstance(value,list):
            for v in value:visit(v)
        elif isinstance(value,dict):
            conditions=value.get('if',[]);conditions=conditions if isinstance(conditions,list) else [conditions]
            for c in conditions:
                if isinstance(c,dict) and c.get('entity_id')==page_entity and c.get('state') in ('climate','air','plants','photos','status'):
                    ds=delays(value.get('then',[]))
                    if ds:
                        n=sum(ds)*(photo_count if c['state']=='photos' else 1)
                        if 10<=n<=600 and float(n).is_integer():result[c['state']]=int(n)
            for v in value.values():visit(v)
    visit(raw);return result
