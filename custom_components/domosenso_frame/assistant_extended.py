"""Additional read-only assistant adapters over normalized component bindings."""
import math
import uuid
from datetime import datetime, timedelta, timezone as utc_timezone

from .assistant_data import Runtime, authorized_subjects, calendar_window


ROUTES = {
    'plants.watering_need': ('plant', ('moisture',), 'plant-query.v1', 'plant-watering.v1'),
    'plants.status': ('plant', ('plant',), 'plant-query.v1', 'plant-status.v1'),
    'plants.last_watered': ('plant', ('last_watered',), 'plant-query.v1', 'plant-event.v1'),
    'plants.water_in': ('plant', ('moisture', 'drying_rate', 'water_in_days'), 'plant-query.v1', 'plant-estimate.v1'),
    'climate-air.humidity': ('room', ('humidity',), 'environment-query.v1', 'environment.v1'),
    'climate-air.status': ('room', (), 'environment-query.v1', 'environment.v1'),
    'media.mains': ('player', (), 'reading-query.v1', 'reading.v1'),
    'awnings.wind': ('awning', ('wind',), 'awning-query.v1', 'awnings.v1'),
    'weather.forecast': ('weather', ('weather',), 'weather-query.v1', 'weather.v1'),
}


# Human-readable fallback for manually mapped endpoints. Compiled metadata can override it.
TEXT = {
 'plants.watering_need': (('Plant watering', 'Pflanzen gießen', 'Podlewanie roślin'), ('Which plants need water?', 'Welche Pflanzen brauchen Wasser?', 'Które rośliny wymagają podlewania?')),
 'plants.status': (('Plant condition', 'Pflanzenzustand', 'Stan roślin'), ('Are any plants reporting problems?', 'Melden Pflanzen Probleme?', 'Czy rośliny zgłaszają problemy?')),
 'plants.last_watered': (('Detected watering', 'Erkannte Bewässerung', 'Wykryte podlewanie'), ('When was watering detected?', 'Wann wurde Bewässerung erkannt?', 'Kiedy wykryto podlewanie?')),
 'plants.water_in': (('Watering estimate', 'Bewässerungsschätzung', 'Szacowany termin podlewania'), ('When might this plant need water?', 'Wann braucht diese Pflanze möglicherweise Wasser?', 'Kiedy ta roślina może wymagać podlewania?')),
 'climate-air.humidity': (('Humidity', 'Luftfeuchtigkeit', 'Wilgotność powietrza'), ('What is the humidity?', 'Wie hoch ist die Luftfeuchtigkeit?', 'Jaka jest wilgotność powietrza?')),
 'climate-air.status': (('Climate status', 'Klimastatus', 'Stan klimatu'), ('Is there a humidity alert?', 'Gibt es einen Feuchtigkeitsalarm?', 'Czy zgłoszono alarm wilgotności?')),
 'media.mains': (('Player power and voltage', 'Leistung und Spannung des Players', 'Moc i napięcie odtwarzacza'), ('What power does the player use?', 'Welche Leistung meldet der Player?', 'Jaką moc zgłasza odtwarzacz?')),
 'awnings.wind': (('Awning wind alarm', 'Windalarm der Markise', 'Alarm wiatrowy markizy'), ('Is an awning wind alarm reported?', 'Wird ein Windalarm für die Markise gemeldet?', 'Czy zgłoszono alarm wiatrowy markizy?')),
 'weather.forecast': (('Weather forecast', 'Wettervorhersage', 'Prognoza pogody'), ('What is the forecast for tomorrow?', 'Wie ist die Vorhersage für morgen?', 'Jaka jest prognoza na jutro?')),
}


def _id(subject, value):
    return str(uuid.uuid5(uuid.UUID(subject['id']), value))


def _number(value):
    try:
        if isinstance(value, bool): return None
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _date(value):
    try:
        if isinstance(value, datetime): result = value
        else: result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo and result.utcoffset() is not None else None
    except (ValueError, TypeError): return None


class ExtendedRuntime(Runtime):
    def _extended_subjects(self, endpoint, capability):
        kind, required, _, _ = ROUTES[capability]
        rows = []
        for original in endpoint.get('assistant', {}).get('subjects', []):
            if original.get('kind') != kind: continue
            fields = original.get('fields', {})
            if capability in ('media.mains', 'climate-air.status'):
                roles = ('power', 'voltage') if capability == 'media.mains' else ('humidity_alert', 'difference')
                if not any(role in fields and self.permitted(fields[role]['entity']) for role in roles): continue
            elif any(role not in fields or not self.permitted(fields[role]['entity']) for role in required): continue
            # Filter before invoking the shared identity/authorization utility. Optional grants do
            # not gate a subject, and no denied entity is read or exposed.
            permitted = {k: v for k, v in fields.items() if self.permitted(v['entity'])}
            label=original.get('label', original['key'])
            rows.append({**original, 'label':label, 'fields': permitted})
        mapped = {v['entity'] for row in rows for v in row['fields'].values()}
        return authorized_subjects(rows, mapped, self.permitted, self.state_get, self.installation_id, endpoint['id'], registry_id=self.registry_id, required_roles=tuple({role for row in rows for role in row['fields']}))

    def capabilities(self, endpoint, supported):
        result = [c for c in super().capabilities(endpoint, supported) if c['id'] not in ROUTES]
        original_endpoint = endpoint
        for cap, (_, _, query, answer) in ROUTES.items():
            if {'queryType': query, 'answerType': answer} not in supported: continue
            endpoint = self.effective_endpoint(original_endpoint,cap)
            subjects = self._extended_subjects(endpoint, cap)
            if not subjects: continue
            if cap == 'weather.forecast':
                availability, limits, basis, asof = 'unavailable', ['provider_sync_unknown'], 'configuration_only', self.now().isoformat()
            else:
                response = self._answer(cap, subjects)
                availability, limits, basis, asof = response['availability'], response['limitations'], 'live_states', response['asOf']
            titles, examples = TEXT[cap]
            title = dict(zip(('en','de','pl'), titles))
            example = dict(zip(('en','de','pl'), ([x] for x in examples)))
            result.append({'id':cap, 'revision':1, 'queryType':query, 'answerType':answer,
                           'title':title, 'description':title, 'examples':example,
                           'configuration':'configured','availability':availability,'limitations':limits,
                           'availabilityBasis':basis,'availabilityAsOf':asof,
                           'subjects':[{'id':s['id'],'kind':s['kind'],'label':s['label'], **({'roomId':s['roomId']} if s.get('roomId') else {}), **({'roomLabel':s['roomLabel']} if s.get('roomLabel') else {})} for s in subjects]})
        for record in result:
            bundle = self.artifacts.get(record['id'].partition('.')[0])
            definition = next((c for c in bundle['definitions']['capabilities'] if c['id'] == record['id']), None) if bundle else None
            if definition:
                for name in ('title', 'description', 'examples'):
                    record[name] = self._localized(bundle, definition[name + '_key'])
        if len(result) > 64: raise ValueError('Too many assistant capabilities')
        return result

    def _select(self, endpoint, cap, args):
        subjects = self._extended_subjects(endpoint, cap)
        allowed = {'plantId','roomId'} if cap.startswith('plants.') else {'roomId'} if cap.startswith('climate-air.') else {'subjectId','metric'} if cap == 'media.mains' else {'subjectId','roomId'}
        if not isinstance(args, dict) or set(args)-allowed or sum(k in args for k in ('roomId','plantId','subjectId')) > 1: raise ValueError('Invalid arguments')
        if cap == 'media.mains' and args.get('metric') not in (None, 'power', 'voltage'): raise ValueError('Invalid metric')
        if cap in ('plants.last_watered','plants.water_in') and 'plantId' not in args: raise ValueError('Plant required')
        if 'roomId' in args:
            subjects = [s for s in subjects if s['id'] == args['roomId'] or s.get('roomId') == args['roomId']]
        for key in ('plantId','subjectId'):
            if key in args: subjects = [s for s in subjects if s['id'] == args[key]]
        if not subjects: raise ValueError('Unknown subject')
        return subjects

    def query(self, endpoint, capability, revision, answer_type, arguments):
        if capability not in ROUTES: return super().query(endpoint, capability, revision, answer_type, arguments)
        if capability == 'weather.forecast' or type(revision) is not int or revision != 1 or answer_type != ROUTES[capability][3]: raise ValueError('Unsupported capability')
        return self._answer(capability, self._select(self.effective_endpoint(endpoint,capability), capability, arguments), arguments)

    def _read(self, s, role, units=None, bounds=None, default_age=None, *, provider_derived=False):
        field = s['fields'].get(role)
        eid = field.get('entity') if field else None
        state = self.state_get(eid) if eid and self.permitted(eid) else None
        attrs = getattr(state, 'attributes', {}) or {}
        raw = attrs.get(field['attribute']) if state and field and 'attribute' in field else getattr(state, 'state', None)
        number = _number(raw); unit = attrs.get('unit_of_measurement', '')
        reported = _date(getattr(state, 'last_reported', None)); age = field.get('maxAgeSeconds', default_age or self.max_age) if field else (default_age or self.max_age)
        status, fresh, reason = 'usable', 'current', None
        if number is None: status, fresh, reason = 'missing', 'unknown', 'missing_source'
        elif units is not None and unit not in units: status, fresh, reason = 'invalid', 'unknown', 'invalid_unit'
        elif bounds and not bounds[0] <= number <= bounds[1]: status, fresh, reason = 'invalid', 'unknown', 'missing_source'
        elif attrs.get('restored') or not eid or (not provider_derived and not self.verified_source(eid)) or not reported: status, fresh, reason = 'unverified', 'unknown', 'unknown_freshness'
        elif not 0 <= (self.now()-reported).total_seconds() <= age: status, fresh, reason = 'stale', 'stale', 'stale'
        ev = {'id':_id(s, role+'-evidence'), 'sourceRef':_id(s, role+'-source'), 'observedAt':None,
              'reportedAt':reported.isoformat() if reported else None, 'freshnessBasis':('provider_assessment' if provider_derived else 'source_report') if fresh != 'unknown' else 'unknown',
              'maxAgeSeconds':age, 'freshness':fresh}
        return number, unit, status, reason, ev

    def _event(self, s, role):
        field = s['fields'].get(role); eid = field.get('entity') if field else None
        state = self.state_get(eid) if eid and self.permitted(eid) else None
        attrs = getattr(state, 'attributes', {}) or {}
        raw = attrs.get(field['attribute']) if state and 'attribute' in field else getattr(state, 'state', None)
        timestamp = _date(raw)
        if attrs.get('restored') or not state or timestamp and timestamp > self.now(): timestamp = None
        ev = {'id':_id(s, role+'-evidence'),'sourceRef':_id(s, role+'-source'),'observedAt':None,'reportedAt':None,
              'freshnessBasis':'historical_event','maxAgeSeconds':None,'freshness':'not_applicable'}
        return timestamp, ev

    def _answer(self, cap, subjects, args=None):
        args = args or {}; now = self.now(); facts=[]; evidence=[]; limits=set(); evaluated=0; classifications=[]
        if cap == 'plants.status': return self._plant_status(subjects)
        for s in subjects:
            common={'subjectId':s['id']}; good=False; classification='unknown'
            if cap == 'plants.last_watered':
                event, ev = self._event(s,'last_watered'); good=bool(event); evidence.append(ev)
                facts.append({**common,'id':_id(s,'event'),'plantId':s['id'],'name':s['label'],'eventAt':event.isoformat() if event else None,'basis':'detected','evidenceRefs':[ev['id']]})
                classification='detected' if good else 'unknown'
                if not good: limits.add('missing_source')
            elif cap == 'awnings.wind':
                field=s['fields']['wind']; eid=field['entity']; state=self.state_get(eid) if self.permitted(eid) else None
                raw=getattr(state,'state',None); attrs=getattr(state,'attributes',{}) or {}
                alarm=raw if raw in ('on','off') and not attrs.get('restored') else 'unknown'; good=alarm!='unknown'
                heard, _ = self._event(s,'last_heard'); classification='wind_alarm' if alarm=='on' else 'no_reported_alarm' if good else 'unknown'
                ev={'id':_id(s,'wind-evidence'),'sourceRef':_id(s,'wind-source'),'observedAt':None,'reportedAt':None,'freshnessBasis':'reported_state','maxAgeSeconds':None,'freshness':'unknown'}; evidence.append(ev)
                facts.append({**common,'id':_id(s,'wind'),'label':s['label'],'state':'unknown','stateBasis':'reported','positionBasis':'reported','alarm':alarm,'alarmBasis':'reported' if good else 'unknown','lastHeardAt':heard.isoformat() if heard else None,'evidenceRefs':[ev['id']]}); limits.add('reported_state_only')
            elif cap == 'climate-air.status':
                measured = reported = False
                for role in ('difference', 'humidity_alert'):
                    field = s['fields'].get(role)
                    if not field or not self.permitted(field['entity']): continue
                    state = self.state_get(field['entity'])
                    attrs = getattr(state, 'attributes', {}) or {}
                    raw = attrs.get(field['attribute']) if state and 'attribute' in field else getattr(state, 'state', None)
                    available = bool(state and not attrs.get('restored') and raw not in (None, 'unknown', 'unavailable'))
                    ev = {'id':_id(s,role+'-evidence'), 'sourceRef':_id(s,role+'-source'),
                          'observedAt':None, 'reportedAt':None, 'freshnessBasis':'reported_state',
                          'maxAgeSeconds':None, 'freshness':'unknown'}
                    row = {**common,'id':_id(s,role),'label':s['label'],'roomId':s.get('roomId',s['id']),
                           'indoor':s.get('indoor', True),'evidenceRefs':[ev['id']], 'basis':'reported'}
                    if role == 'difference':
                        value = _number(raw); unit = attrs.get('unit_of_measurement')
                        valid = available and value is not None and unit in ('°C','°F')
                        row.update(metric='temperature_difference', value=value if valid else None,
                                   unit=unit if unit in ('°C','°F') else '°C',
                                   status='usable' if valid else 'invalid' if available and value is not None else 'missing')
                        if available and value is not None and not valid: limits.add('invalid_unit')
                        measured |= valid
                    else:
                        valid = available and raw in ('on','off')
                        row.update(metric='humidity_alert',value=None,unit='%',status='usable' if valid else 'missing',
                                   alertCode='humidity_high' if valid and raw=='on' else 'no_reported_alert' if valid else 'unknown')
                        reported |= valid
                    if not valid: limits.add('missing_source')
                    evidence.append(ev); facts.append(row); limits.add('reported_state_only')
                good = measured or reported
                classification = 'reported' if reported else 'measured' if measured else 'unknown'
            else:
                roles = ['moisture'] if cap in ('plants.watering_need','plants.status') else ['water_in_days'] if cap=='plants.water_in' else ['humidity'] if cap=='climate-air.humidity' else [x for x in ('power','voltage') if x in s['fields'] and (not args.get('metric') or args['metric']==x)]
                subject_good=False
                for role in roles:
                    units={'moisture':{'%'},'humidity':{'%'},'power':{'W'},'voltage':{'V'},'water_in_days':{'d','days','day'}}[role]
                    value,unit,status,reason,ev=self._read(s,role,units,(0,100) if role in ('moisture','humidity') else (0,float('inf')),21600 if role in ('moisture','humidity') else None, provider_derived=cap=='plants.water_in' and role=='water_in_days')
                    if reason: limits.add(reason)
                    row={**common,'id':_id(s,role),'label':s['label'],'metric':'water_in' if role=='water_in_days' else role,'value':value if status=='usable' else None,'unit':'d' if role=='water_in_days' else unit if unit in units else sorted(units)[0], 'status':status,'evidenceRefs':[ev['id']]}
                    if cap.startswith('plants.'):
                        row.update(plantId=s['id'],name=s['label'])
                    if cap=='climate-air.humidity': row.update(roomId=s.get('roomId',s['id']),indoor=s.get('indoor',True))
                    if cap=='media.mains': row['scope']='individual_meter'
                    if cap=='plants.watering_need':
                        lo=_number(s.get('limits',{}).get('min_moisture')); hi=_number(s.get('limits',{}).get('max_moisture'))
                        policy=lo is not None and hi is not None and 0<=lo<hi<=100
                        if not policy: limits.add('missing_policy'); row['status']='unverified'; row['value']=None
                        elif status=='usable':
                            classification='needs_water' if value<lo else 'too_wet' if value>hi else 'within_band'
                            row['wateringAssessment']=classification
                            ev['policy']={'id':'plant-moisture','revision':1,'bounds':{'min':lo,'max':hi},'direction':'band','source':'configured'}
                    elif cap=='plants.water_in':
                        moisture,_,ms,mr,me=self._read(s,'moisture',{'%'},(0,100),21600)
                        rate,_,rs,rr,re=self._read(s,'drying_rate',None,None,172800,provider_derived=True)
                        event,_=self._event(s,'last_watered')
                        if event is None: limits.add('watering_history_unknown')
                        minimum=_number(s.get('limits',{}).get('min_moisture'))
                        if status!='usable' or ms!='usable' or rs!='usable' or minimum is None or not 0 <= minimum <= 100 or rate is None or rate>=0 or value is None or value<0 or not value.is_integer() or event and now-event<timedelta(hours=48):
                            row['status']='unverified'; row['value']=None; limits.add('insufficient_history')
                        else:
                            ev['history']={'historyBasis':'provider_estimate','windowHours':48,'coverage':'unknown'}; limits.add('history_coverage_unknown'); classification='estimated'
                            evidence.extend([me,re]); row['evidenceRefs'].extend([me['id'],re['id']])
                        row['basis']='estimate'
                    elif cap=='plants.status':
                        # Only independently supported moisture band conclusions are exposed here.
                        plant=s['fields'].get('plant'); ps=self.state_get(plant['entity']) if plant and self.permitted(plant['entity']) else None
                        attrs=getattr(ps,'attributes',{}) or {}; lo=_number(s.get('limits',{}).get('min_moisture')); hi=_number(s.get('limits',{}).get('max_moisture'))
                        if not ps or attrs.get('restored') or lo is None or hi is None or lo>=hi or status!='usable': row['status']='unverified';row['value']=None;limits.add('missing_source')
                        else:
                            problem='low' if value<lo else 'high' if value>hi else 'normal';row['problems']=[{'metric':'moisture','status':problem}];classification='problem' if problem!='normal' else 'no_reported_problem';limits.add('provider_history_unknown')
                    else: classification='measured' if status=='usable' else 'unknown'
                    subject_good |= row['status']=='usable' and (cap!='plants.watering_need' or classification!='unknown')
                    facts.append(row); evidence.append(ev)
                good=subject_good
            evaluated += int(good); classifications.append(classification if good else 'unknown')
        known={x for x in classifications if x!='unknown'}
        if not known: summary='unknown'
        elif len(known)==1: summary=next(iter(known))
        elif cap=='climate-air.status': summary='reported' if 'reported' in known else 'measured'
        elif cap=='plants.watering_need': summary='mixed'
        elif cap=='plants.status': summary='problem' if 'problem' in known else 'no_reported_problem'
        elif cap=='awnings.wind': summary='wind_alarm' if 'wind_alarm' in known else 'no_reported_alarm'
        else: summary=next(iter(sorted(known)))
        affected=sum(x==summary for x in classifications) if summary not in ('unknown','mixed') else sum(x!='unknown' for x in classifications) if summary=='mixed' else 0
        if evaluated == len(subjects) and evaluated:
            stale_ids={e['id'] for e in evidence if e['freshness']=='stale'}
            facts=[f for f in facts if not stale_ids.intersection(f['evidenceRefs'])]
            evidence=[e for e in evidence if e['id'] not in stale_ids]
        summary_obj={'status':summary,'affected':affected}
        if cap=='awnings.wind': summary_obj['basis']='reported'
        return {'version':1,'capability':cap,'revision':1,'answerType':ROUTES[cap][3],'asOf':now.isoformat(),
                'availability':'ready' if evaluated==len(subjects) and evaluated else 'partial' if evaluated else 'unavailable',
                'coverage':{'eligible':len(subjects),'evaluated':evaluated,'missing':len(subjects)-evaluated},'summary':summary_obj,
                'facts':facts,'evidence':evidence,'limitations':sorted(limits)}

    def _plant_status(self, subjects):
        facts=[]; evidence=[]; limitations={'provider_history_unknown'}; evaluated=0; classifications=[]
        for s in subjects:
            plant=s['fields']['plant']['entity']; state=self.state_get(plant) if self.permitted(plant) else None
            attrs=getattr(state,'attributes',{}) or {}; raw=attrs.get('problem','')
            problems=raw.lower() if isinstance(raw,str) else ' '.join(str(x).lower() for x in raw) if isinstance(raw,list) else ''
            provider_ok=bool(state and getattr(state,'state',None) not in ('unknown','unavailable',None) and not attrs.get('restored'))
            known=[]; flagged=[]
            for role, units, bounds in [('moisture',{'%'},(0,100)),('conductivity',{'µS/cm','μS/cm','uS/cm'},(0,float('inf'))),('temperature',{'°C','°F'},None),('brightness',{'lx'},(0,float('inf')))]:
                value,unit,status,reason,ev=self._read(s,role,units,bounds,21600)
                if reason: limitations.add(reason)
                if not provider_ok and status=='usable': status='unverified'; limitations.add('unknown_freshness')
                flag='low' if role+' low' in problems else 'high' if role+' high' in problems else 'normal'
                window=_number(s.get('limits',{}).get('check_days'))
                if role=='brightness' and (window is None or window<=0): status='unverified'; limitations.add('insufficient_history')
                # Provider brightness flag requires a current underlying light report and a configured window.
                if status=='usable': known.append(role); flagged.append(flag!='normal')
                else: flag='unknown'
                row={'id':_id(s,'status-'+role),'subjectId':s['id'],'plantId':s['id'],'name':s['label'],
                     'metric':role,'value':value if status=='usable' else None,'unit':unit if unit in units else sorted(units)[0],
                     'status':status,'problems':[{'metric':role,'status':flag}],'evidenceRefs':[ev['id']]}
                if role=='brightness' and status=='usable': row['basis']='provider_assessment'; row['checkDays']=window
                evidence.append(ev); facts.append(row)
            good=bool(known); evaluated+=int(good)
            classifications.append('problem' if any(flagged) else 'unknown')
        if evaluated == len(subjects) and evaluated:
            stale_ids={e['id'] for e in evidence if e['freshness']=='stale'}
            facts=[f for f in facts if not stale_ids.intersection(f['evidenceRefs'])]
            evidence=[e for e in evidence if e['id'] not in stale_ids]
        status='problem' if 'problem' in classifications else 'unknown'; affected=classifications.count('problem')
        return {'version':1,'capability':'plants.status','revision':1,'answerType':'plant-status.v1','asOf':self.now().isoformat(),
                'availability':'ready' if evaluated==len(subjects) and evaluated else 'partial' if evaluated else 'unavailable',
                'coverage':{'eligible':len(subjects),'evaluated':evaluated,'missing':len(subjects)-evaluated},
                'summary':{'status':status,'affected':affected},'facts':facts,'evidence':evidence,'limitations':sorted(limitations)}

    async def forecast(self, endpoint, arguments, service_call, timezone, recheck):
        """Fetch one authorized provider forecast; callbacks must enforce the caller's grant.

        service_call(domain, service, data) returns the HA response. recheck() must
        revalidate the endpoint mapping and user grant after the awaited service call.
        """
        if not isinstance(arguments,dict) or set(arguments)-{'subjectId','period','date'}: raise ValueError('Invalid forecast arguments')
        start,end=calendar_window(arguments,self.now().astimezone(timezone).date(),timezone)
        subjects=self._extended_subjects(self.effective_endpoint(endpoint,'weather.forecast'),'weather.forecast')
        if 'subjectId' in arguments: subjects=[s for s in subjects if s['id']==arguments['subjectId']]
        if len(subjects)!=1: raise ValueError('Unknown or ambiguous weather source')
        s=subjects[0]; eid=s['fields']['weather']['entity']
        forecast_type=s.get('limits',{}).get('forecast_type','daily')
        if forecast_type not in ('daily','hourly'): raise ValueError('Invalid forecast type')
        try:
            response=await service_call('weather','get_forecasts',{'entity_id':[eid],'type':forecast_type})
        except Exception:
            response=None
        checked=recheck()
        if hasattr(checked,'__await__'): checked=await checked
        if not checked or not self.permitted(eid): raise ValueError('Authorization changed')
        current=self._extended_subjects(self.effective_endpoint(endpoint,'weather.forecast'),'weather.forecast')
        if not any(x['id']==s['id'] and x['fields']['weather']['entity']==eid for x in current): raise ValueError('Mapping changed')
        item=response.get(eid) if isinstance(response,dict) else None
        entries=item.get('forecast') if isinstance(item,dict) else None
        successful=isinstance(entries,list); periods=[]; limits={'provider_sync_unknown'}
        if not successful: limits.add('missing_source'); entries=[]
        for index,entry in enumerate(entries):
            if not isinstance(entry,dict): limits.add('missing_source'); continue
            dt=_date(entry.get('datetime'))
            if not dt: limits.add('missing_source'); continue
            if forecast_type=='hourly': valid_to=dt+timedelta(hours=1)
            else:
                local=dt.astimezone(timezone)
                dt=datetime.combine(local.date(), datetime.min.time(), tzinfo=timezone)
                valid_to=datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), tzinfo=timezone)
            if dt>=end or valid_to<=start: continue
            measurements=[]
            # HA weather entity attributes declare the unit for provider forecast values.
            state=self.state_get(eid) if self.permitted(eid) else None
            attrs=getattr(state,'attributes',{}) or {}
            for key,metric,unit_key,allowed in [('temperature','temperature','temperature_unit',{'°C','°F'}),('humidity','humidity',None,{'%'}),('precipitation_probability','precipitation_probability',None,{'%'}),('precipitation','precipitation','precipitation_unit',{'mm','in'}),('wind_speed','wind_speed','wind_speed_unit',{'km/h','m/s','mph','kn'})]:
                value=_number(entry.get(key)); unit=attrs.get(unit_key) if unit_key else '%'
                if value is not None and unit in allowed and (metric not in ('humidity','precipitation_probability') or 0<=value<=100) and (metric not in ('precipitation','wind_speed') or value>=0): measurements.append({'metric':metric,'value':value,'unit':unit})
                elif key in entry: limits.add('invalid_unit')
            period={'validFrom':dt.isoformat(),'validTo':valid_to.isoformat(),'issueAt':None,'measurements':measurements}
            condition=entry.get('condition')
            if isinstance(condition,str) and condition in {'clear-night','cloudy','fog','hail','lightning','lightning-rainy','partlycloudy','pouring','rainy','snowy','snowy-rainy','sunny','windy','windy-variant','exceptional'}: period['condition']=condition
            if 'condition' not in period and not measurements:
                limits.add('missing_source'); continue
            periods.append(period)
        periods.sort(key=lambda x:_date(x['validFrom']).timestamp())
        if len(periods)>168: periods=periods[:168];limits.add('truncated')
        cursor=start.astimezone(utc_timezone.utc)
        for period in periods:
            begin=_date(period['validFrom']).astimezone(utc_timezone.utc)
            finish=_date(period['validTo']).astimezone(utc_timezone.utc)
            if begin>cursor: break
            cursor=max(cursor,finish)
        complete=successful and bool(periods) and cursor>=end.astimezone(utc_timezone.utc) and 'missing_source' not in limits and 'truncated' not in limits
        if not complete: limits.add('missing_source')
        ev={'id':_id(s,'forecast-evidence'),'sourceRef':_id(s,'forecast-source'),'observedAt':None,'reportedAt':None,'freshnessBasis':'provider_response','maxAgeSeconds':None,'freshness':'current'}
        facts=[{'id':_id(s,'forecast'),'subjectId':s['id'],'condition':'unknown','forecastType':forecast_type,'providerSyncAt':None,'periods':periods,'evidenceRefs':[ev['id']]}] if successful else []
        if successful and not periods: limits.add('missing_source')
        return {'version':1,'capability':'weather.forecast','revision':1,'answerType':'weather.v1','asOf':self.now().isoformat(),
                'availability':'ready' if complete else 'partial' if periods else 'unavailable','coverage':{'eligible':1,'evaluated':int(complete),'missing':int(not complete),'unit':'forecast_source'},
                'summary':{'status':'forecast' if periods else 'unknown','affected':len(periods)},'facts':facts,'evidence':[ev] if successful else [],
                'limitations':sorted(limits),'intervalStart':start.isoformat(),'intervalEnd':end.isoformat(),'timezone':str(timezone)}
