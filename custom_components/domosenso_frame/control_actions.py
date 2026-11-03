"""Bounded control negotiation and durable at-most-once dispatch attempts."""
import asyncio
import copy
import re
import uuid
from datetime import datetime, timedelta, timezone
from .control_profiles import ACTIONS, digest


class Denied(ValueError):
    pass


class Controls:
    TTL_SECONDS = 600
    SERVICE_TIMEOUT = 10

    def __init__(self, installation, profiles, persist, saved=None, *, now=None, capacity=1024):
        self.installation=uuid.UUID(installation);self.profiles=profiles;self.persist=persist
        self.now=now or (lambda:datetime.now(timezone.utc));self.capacity=capacity
        self.lock=asyncio.Lock();self.busy=False;self.closed=False
        self.data=copy.deepcopy(saved) if saved else self.fresh()
        if (not isinstance(self.data,dict) or set(self.data)!={'epoch','expiresAt','requests'}
                or not isinstance(self.data['requests'],dict) or len(self.data['requests'])>capacity):
            raise ValueError('Invalid control ledger')
        uuid.UUID(self.data['epoch']);self.expiry()

    def fresh(self):
        return {'epoch':str(uuid.uuid4()),'expiresAt':(self.now()+timedelta(seconds=self.TTL_SECONDS)).isoformat(),'requests':{}}

    def expiry(self): return datetime.fromisoformat(self.data['expiresAt'])

    def epoch(self):
        if self.now()>=self.expiry() and not self.busy: self.data=self.fresh()
        return self.data['epoch']

    def opaque(self, value): return str(uuid.uuid5(self.installation,value))

    def resolved(self, endpoint, device, permitted):
        epoch=self.epoch();rows=self.profiles.authorized(endpoint,permitted);targets=[]
        def make(selected,scope,area=None,kind=None,partial=False):
            if not selected:return
            kind=kind or selected[0]['kind']
            identity=('room:'+area+':'+kind) if scope=='room' else selected[0]['source']
            ident=self.opaque(endpoint['id']+':'+identity)
            room=copy.deepcopy(selected[0]['room'])
            if room: room={'id':self.opaque('area:'+room.pop('key')),**room}
            caps=sorted(set.intersection(*(set(r['capabilities']) for r in selected)))
            if not caps:return
            revision=digest({'epoch':epoch,'device':device,'endpoint':endpoint,'rows':[r['grant'] for r in selected],
                             'room':room,'partial':partial,'known':sorted(self.profiles.known_room_members(area,kind)) if area else []})
            targets.append({'id':ident,'revision':revision,'kind':kind,'scope':scope,'label':room['label'] if scope=='room' else selected[0]['label'],
                            'aliases':room['aliases'] if scope=='room' else selected[0]['aliases'],'room':room,
                            'capabilities':[{'id':c,'revision':1} for c in caps],'partialScope':partial,
                            'memberIds':[self.opaque(endpoint['id']+':'+r['source']) for r in selected],
                            'limitations':['partial_scope'] if partial else [],'_rows':selected})
        for row in rows: make([row],'subject')
        for area,kind in sorted({(r['area'],r['kind']) for r in rows if r['area']}):
            room_rows=[r for r in rows if r['area']==area and r['kind']==kind]
            # Prefer a granted proxy over separately mapped descendants, preserving
            # its actual service target. Incomparable overlap is ambiguous and denied.
            chosen=[];ambiguous=False
            for row in sorted(room_rows,key=lambda r:(-len(r['graph']),r['source'])):
                members=set(row['members'])
                if any(members<=set(old['members']) for old in chosen): continue
                if any(members & set(old['members']) for old in chosen): ambiguous=True;break
                chosen.append(row)
            if ambiguous:continue
            covered={e for r in chosen for e in r['members']}
            known=self.profiles.known_room_members(area,kind)
            for r in room_rows:
                known.difference_update(set(r['graph'])-set(r['members']))
            make(chosen,'room',area,kind,bool(known-covered))
        return targets

    def targets(self, endpoint, device, permitted):
        epoch=self.epoch()
        targets=[] if self.closed or self.now()>=self.expiry() else self.resolved(endpoint,device,permitted)
        return {'version':1,'actionType':'control-action.v1','resultType':'control-result.v1',
                'requestIdPrefix':epoch+':','expiresAt':self.data['expiresAt'],
                'targets':[{k:v for k,v in t.items() if k!='_rows'} for t in targets]}

    def validate(self, msg):
        fields={'version','endpoint','deviceId','capability','revision','targetId','targetRevision','requestId'}
        if (not isinstance(msg,dict) or not fields<=msg.keys() or set(msg)-fields-{'id','type','scopeChoice'}
                or type(msg['version']) is not int or msg['version']!=1 or type(msg['revision']) is not int or msg['revision']!=1
                or msg['capability'] not in ACTIONS): raise ValueError('Invalid control action')
        for key in fields-{'version','revision'}:
            if not isinstance(msg[key],str) or not 1<=len(msg[key])<=256 or any(ord(c)<32 for c in msg[key]): raise ValueError('Invalid action field')
        if not re.fullmatch('[a-f0-9]{64}',msg['targetRevision']): raise ValueError('Invalid target revision')
        if 'scopeChoice' in msg and msg['scopeChoice']!='configured':raise ValueError('Invalid scope choice')

    def result(self,msg,status,reason,outcomes=None):
        return {'version':1,'resultType':'control-result.v1',**{k:msg[k] for k in ('requestId','capability','revision','targetId','targetRevision')},
                'status':status,'reason':reason,'outcomes':outcomes or []}

    def check(self,msg,authorize):
        if self.closed: raise Denied('access_denied')
        try: endpoint,permitted=authorize()
        except Denied:raise
        except Exception as error:raise Denied('access_denied') from error
        if endpoint['id']!=msg['endpoint']: raise Denied('access_denied')
        if self.now()>=self.expiry() or not msg['requestId'].startswith(self.data['epoch']+':') or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',msg['requestId'].partition(':')[2]):raise Denied('stale_revision')
        target=next((t for t in self.resolved(endpoint,msg['deviceId'],permitted) if t['id']==msg['targetId']),None)
        if not target or target['revision']!=msg['targetRevision']:raise Denied('stale_revision')
        if {'id':msg['capability'],'revision':1} not in target['capabilities']:raise Denied('unsupported')
        if target['partialScope'] and msg.get('scopeChoice')!='configured':raise Denied('partial_scope')
        # All effective members, the proxy, and all current safety inputs preflight
        # before the first call and again after each awaited operation.
        for row in target['_rows']:
            for eid in row['graph']:
                state=self.profiles.state_get(eid)
                if not state or state.state in ('unknown','unavailable'):raise Denied('unavailable')
                if not permitted(eid,'read') or not permitted(eid,'control'):raise Denied('access_denied')
            if msg['capability']=='awnings.extend':self.wind(row,permitted)
        return target

    def wind(self,row,permitted):
        guard=row.get('guard')
        if not guard:raise Denied('wind_block')
        wind_id=guard['wind']['entity'];heard_id=guard['last_heard']['entity']
        if not permitted(wind_id,'read') or not permitted(heard_id,'read'):raise Denied('wind_block')
        wind=self.profiles.state_get(wind_id);heard=self.profiles.state_get(heard_id)
        if not wind or wind.state!='off' or not heard:raise Denied('wind_block')
        try:
            timestamp=datetime.fromisoformat(heard.state.replace('Z','+00:00'))
            # HA input_datetime is a local wall-clock string. Its authoritative
            # timestamp attribute avoids guessing the installation's timezone.
            if timestamp.tzinfo is None:
                seconds=heard.attributes.get('timestamp')
                if type(seconds) not in (int,float):raise ValueError('Missing timezone')
                timestamp=datetime.fromtimestamp(seconds,timezone.utc)
            age=(self.now()-timestamp).total_seconds()
            if not 0<=age<=guard['max_age_seconds']:raise ValueError('Stale wind')
        except (ValueError,TypeError,OverflowError):raise Denied('wind_block') from None

    def observed(self,row,capability,context,started):
        state=self.profiles.state_get(row['entity']);assumed=bool(state and state.attributes.get('assumed_state'))
        result={'targetId':self.opaque(self._endpoint+':'+row['source']),'status':'accepted','reason':'command_sent','assumedState':assumed}
        if not state or assumed or getattr(getattr(state,'context',None),'id',None)!=context or getattr(state,'last_updated',started)<started:return result
        wanted={'lights.turn_on':'on','lights.turn_off':'off','covers.open':'open','covers.close':'closed','awnings.extend':'open','awnings.retract':'closed'}.get(capability)
        if state.state==wanted:result.update(status='confirmed',reason='state_observed')
        elif state.state in ('opening','closing'):result.update(status='moving',reason='motion_observed')
        return result

    async def execute(self,msg,authorize,service,context):
        self.validate(msg)
        async with self.lock:
            self.epoch();self.busy=True
            try:return await self._execute(msg,authorize,service,context)
            finally:self.busy=False

    async def _execute(self,msg,authorize,service,context):
        # Authentication must hold even when reading a previous ledger outcome.
        try:authorize()
        except Denied as error:return self.result(msg,'failed',str(error))
        except Exception:return self.result(msg,'failed','access_denied')
        if not msg['requestId'].startswith(self.data['epoch']+':') or self.now()>=self.expiry():return self.result(msg,'failed','stale_revision')
        payload=digest({k:v for k,v in msg.items() if k not in ('id','type')})
        prior=self.data['requests'].get(msg['requestId'])
        if prior:
            if prior['payload']!=payload:return self.result(msg,'failed','request_conflict')
            return copy.deepcopy(prior['result'] or prior.get('unknownResult') or self.result(msg,'unknown','outcome_unknown'))
        try:target=self.check(msg,authorize)
        except Denied as error:return self.result(msg,'failed',str(error))
        if len(self.data['requests'])>=self.capacity:return self.result(msg,'failed','ledger_full')
        uncertain=[{'targetId':self.opaque(msg['endpoint']+':'+r['source']),'status':'unknown','reason':'outcome_unknown',
                    'assumedState':bool(self.profiles.state_get(r['entity']).attributes.get('assumed_state'))} for r in target['_rows']]
        record={'payload':payload,'result':None,'unknownResult':self.result(msg,'unknown','outcome_unknown',uncertain)}
        self.data['requests'][msg['requestId']]=record
        try:await self.persist(copy.deepcopy(self.data))
        except Exception:return self.result(msg,'failed','storage_failed')
        outcomes=[];self._endpoint=msg['endpoint']
        for row in target['_rows']:
            try:self.check(msg,authorize)
            except Denied as error:
                outcomes.append({'targetId':self.opaque(msg['endpoint']+':'+row['source']),'status':'failed','reason':str(error),'assumedState':False})
                # Include every not-dispatched target so partial scope is explicit.
                for remaining in target['_rows'][len(outcomes):]:outcomes.append({'targetId':self.opaque(msg['endpoint']+':'+remaining['source']),'status':'failed','reason':str(error),'assumedState':False})
                break
            domain,name,_=ACTIONS[msg['capability']];started=self.now()
            try:
                async with asyncio.timeout(self.SERVICE_TIMEOUT):
                    await service(domain,name,{'entity_id':row['entity']},context)
                outcomes.append(self.observed(row,msg['capability'],context,started))
            except Exception:
                outcomes.append({'targetId':self.opaque(msg['endpoint']+':'+row['source']),'status':'unknown','reason':'service_error','assumedState':bool(self.profiles.state_get(row['entity']) and self.profiles.state_get(row['entity']).attributes.get('assumed_state'))})
            # Recheck even after the last dispatch; never claim an unqualified
            # success if permission or safety changed while a service was awaited.
            try:self.check(msg,authorize)
            except Denied:
                if len(outcomes)==len(target['_rows']):
                    outcomes[-1].update(status='unknown',reason='outcome_unknown')
        statuses={o['status'] for o in outcomes}
        if 'failed' in statuses:status='failed';reason='partial_failure' if len(statuses)>1 else outcomes[0]['reason']
        elif 'unknown' in statuses:status='unknown';reason='partial_failure' if len(statuses)>1 else 'outcome_unknown'
        elif statuses=={'confirmed'}:status='confirmed';reason='state_observed'
        elif statuses=={'moving'}:status='moving';reason='motion_observed'
        else:status='accepted';reason='command_sent'
        result=self.result(msg,status,reason,outcomes);record['result']=result
        try:await self.persist(copy.deepcopy(self.data))
        except Exception:
            record['result']=None
            return self.result(msg,'unknown','outcome_unknown',[{**o,'status':'unknown','reason':'outcome_unknown'} for o in outcomes])
        # No dispatch after this await. A revocation suppresses optimistic output.
        try:self.check(msg,authorize)
        except Denied:
            if status in ('accepted','confirmed','moving'):
                uncertain=self.result(msg,'unknown','outcome_unknown',[{**o,'status':'unknown','reason':'outcome_unknown'} for o in outcomes])
                record['result']=uncertain
                try:await self.persist(copy.deepcopy(self.data))
                except Exception:pass  # The durable pending/terminal record still prevents replay.
                return uncertain
        return copy.deepcopy(result)
