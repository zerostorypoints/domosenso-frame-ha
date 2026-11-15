"""HA-owned playlist timing; there is no Android scheduler."""
import time
from uuid import uuid4

ROOM_HOLD_SECONDS = 30

def view_key(endpoint, negotiated, preview=False, studio=False):
    if studio:
        return (endpoint, 'preview-studio' if preview else 'hifisync-studio')
    return (endpoint, 'preview' if preview else 'hifisync' if negotiated else 'legacy')


class SessionViews:
    """Bind a negotiated cursor to a proved connection, never to a request endpoint."""
    def __init__(self):
        self.connections = {}

    def remember(self, connection, proof, negotiated, enabled, studio=False, visit=None):
        binding = (proof['deviceId'], proof['endpoint'])
        previous = self.connections.get(connection)
        if studio:
            self.connections[connection] = (binding, True, visit)
        elif negotiated and enabled and not (previous and previous[0] == binding and previous[1]):
            self.connections[connection] = (binding, False, None)
        elif previous and (previous[0] != binding or not enabled and not previous[1]):
            self.connections.pop(connection, None)

    def key(self, connection, proof, enabled):
        binding = (proof['deviceId'], proof['endpoint'])
        matched = self.connections.get(connection)
        if matched and matched[0] == binding:
            return view_key(proof['endpoint'], enabled, studio=matched[1])
        return view_key(proof['endpoint'], False)

    def saw_room(self, connection, proof, visit):
        binding = (proof['deviceId'], proof['endpoint'])
        return self.connections.get(connection) == (binding, True, visit)

    def forget(self, connection):
        self.connections.pop(connection, None)


class Runtime:
    def __init__(self):self.endpoints={};self.navigation={}
    def navigate(self,eid,mode):self.navigation[eid]=mode;self.reset(eid)
    def mode(self,eid,fallback):return self.navigation.get(eid,fallback)
    def release_navigation(self,eid):
        if eid in self.navigation:self.navigation.pop(eid);self.reset(eid)
    def select(self,eid,config,mode,page,music,capabilities,revision,now=None,paused=False,*,studio_music=False,room_signature=None):
        now=time.monotonic() if now is None else now
        active='media' if mode=='media' or (mode=='ambient' and music) else 'controls' if mode in ('controls','calendar') else 'ambient'
        if mode=='calendar':
            active=next((m['id'] for m in sorted(config['modes'],key=lambda m:m['id']!='controls') if any(any(w['type']=='calendar' for w in s['widgets']) for s in m['screens'])),'controls')
        screens=next(m['screens'] for m in config['modes'] if m['id']==active)
        visible=[s for s in screens if set(s['requires'])<=set(capabilities)]
        if not visible:visible=[s for s in screens if not s['requires']]
        key=(revision,active,mode,tuple(s['id'] for s in visible),room_signature)
        state=self.endpoints.get(eid)
        if not state or state['key']!=key:
            preferred=next((s['id'] for s in visible if any(w['type']=='calendar' for w in s['widgets'])),None) if mode=='calendar' else page
            if active=='media' and (studio_music or any(set(s['requires']) & {'hifisync-system','hifisync-room'} for s in screens)):preferred=None
            index=next((i for i,s in enumerate(visible) if s['id']==preferred),0)
            state={'key':key,'index':index,'since':now,'shield':now+.7 if active=='controls' else 0};self.endpoints[eid]=state
        hifi_music=active=='media' and any(set(s['requires']) & {'hifisync-system','hifisync-room'} for s in screens)
        paused=bool(paused and hifi_music and mode!='off')
        hold = state.get('room_hold')
        if hold and hold['deadline'] <= now:
            self._release_room_hold(state, hold['deadline'])
            hold = None
        if paused and state.get('paused_at') is None:state['paused_at']=now
        elif not paused and state.get('paused_at') is not None:
            state['since']=now;state['paused_at']=None
            if hold:hold['since']=now
        cursor_time = state['paused_at'] if paused else now
        if hold:
            cursor_time = min(cursor_time, hold['since'])
        elapsed=max(0,cursor_time-state['since']);visited=0
        while not paused and not hold and mode not in ('calendar','off') and visible[state['index']]['duration'] and elapsed>=visible[state['index']]['duration'] and visited<1000:
            state.pop('room_visit', None)
            duration=visible[state['index']]['duration'];elapsed-=duration;state['since']+=duration;state['index']=(state['index']+1)%len(visible);visited+=1
            skipped=0
            while set(visible[state['index']]['requires']) & {'hifisync-system','hifisync-room'} and not visible[state['index']]['duration'] and skipped<len(visible):
                state['index']=(state['index']+1)%len(visible);skipped+=1
        state['count']=len(visible)
        current = visible[state['index']]
        if studio_music and room_signature and active=='media' and 'hifisync-room' in current['requires']:
            if 'room_visit' not in state:
                state['room_visit'] = uuid4().hex
            state['room_signature'] = room_signature
        else:
            state.pop('room_visit', None)
        return active,current['id'],elapsed,max(0,state['shield']-now)

    def room_visit(self, eid):
        return self.endpoints.get(eid, {}).get('room_visit')

    def permits_room(self, eid, visit, signature):
        state = self.endpoints.get(eid, {})
        return bool(visit and signature and state.get('room_visit') == visit and state.get('room_signature') == signature)

    @staticmethod
    def _release_room_hold(state, now):
        hold = state.pop('room_hold', None)
        if hold:
            paused_at = state.get('paused_at')
            elapsed_until = min(now, paused_at) if paused_at is not None else now
            state['since'] += max(0, elapsed_until-hold['since'])

    def interact_room(self, eid, connection, visit, action, gesture=None, now=None):
        state = self.endpoints.get(eid)
        if not visit or not state or state.get('room_visit') != visit:
            return False
        now = time.monotonic() if now is None else now
        hold = state.get('room_hold')
        if hold and hold['deadline'] <= now:
            self._release_room_hold(state, hold['deadline'])
            hold = None
        if action in ('progress', 'end'):
            if not hold or hold['owner'] is not connection or hold['gesture'] != gesture:
                return False
            if action == 'progress' and hold['deadline'] > now+ROOM_HOLD_SECONDS-1:
                return True
        elif hold and hold['gesture'] and (hold['owner'] is not connection or action == 'begin'):
            return False

        if not hold:
            hold = {'since': now, 'gesture': None}
            state['room_hold'] = hold
        hold['owner'] = connection
        hold['deadline'] = now + ROOM_HOLD_SECONDS
        if action == 'begin':
            hold['gesture'] = gesture
        elif action == 'end':
            hold['gesture'] = None
        return True

    def disconnect(self, connection, now=None):
        now = time.monotonic() if now is None else now
        for state in self.endpoints.values():
            hold = state.get('room_hold')
            if hold and hold['owner'] is connection:
                self._release_room_hold(state, min(now, hold['deadline']))
                state.pop('room_visit', None)

    def step(self,eid,delta,now=None):
        state=self.endpoints.get(eid)
        if state:
            state['index']=(state['index']+delta)%state['count'];state['since']=time.monotonic() if now is None else now
            state.pop('room_visit', None)
            state.pop('room_hold', None)
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
