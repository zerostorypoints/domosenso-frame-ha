"""HA-owned playlist timing; there is no Android scheduler."""
import time

class Runtime:
    def __init__(self):self.endpoints={};self.navigation={}
    def navigate(self,eid,mode):self.navigation[eid]=mode;self.reset(eid)
    def mode(self,eid,fallback):return self.navigation.get(eid,fallback)
    def release_navigation(self,eid):
        if eid in self.navigation:self.navigation.pop(eid);self.reset(eid)
    def select(self,eid,config,mode,page,music,capabilities,revision,now=None):
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
            index=next((i for i,s in enumerate(visible) if s['id']==preferred),0)
            state={'key':key,'index':index,'since':now,'shield':now+.7 if active=='controls' else 0};self.endpoints[eid]=state
        elapsed=max(0,now-state['since']);visited=0
        while mode not in ('calendar','off') and visible[state['index']]['duration'] and elapsed>=visible[state['index']]['duration'] and visited<1000:
            duration=visible[state['index']]['duration'];elapsed-=duration;state['since']+=duration;state['index']=(state['index']+1)%len(visible);visited+=1
        state['count']=len(visible)
        return active,visible[state['index']]['id'],max(0,now-state['since']),max(0,state['shield']-now)
    def step(self,eid,delta,now=None):
        state=self.endpoints.get(eid)
        if state:state['index']=(state['index']+delta)%state['count'];state['since']=time.monotonic() if now is None else now
    def reset(self,eid):self.endpoints.pop(eid,None)

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
