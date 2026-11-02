"""Pure bounded request helpers for the assistant bridge."""
from datetime import date, datetime, time, timedelta


def calendar_window(arguments, today, tz):
    """Return local midnight boundaries, preserving DST and the seven-date horizon."""
    if not isinstance(arguments, dict) or set(arguments) - {'subjectId', 'period', 'date', 'nextOnly'}:
        raise ValueError('Invalid calendar arguments')
    if 'subjectId' in arguments and (not isinstance(arguments['subjectId'], str) or not 1 <= len(arguments['subjectId']) <= 80):
        raise ValueError('Invalid subject')
    if 'nextOnly' in arguments and type(arguments['nextOnly']) is not bool:
        raise ValueError('Invalid nextOnly')
    if 'date' in arguments and 'period' in arguments:
        raise ValueError('Choose date or period')
    if 'date' in arguments:
        raw = arguments['date']
        if not isinstance(raw, str):
            raise ValueError('Invalid date')
        try:
            selected = date.fromisoformat(raw)
        except ValueError as err:
            raise ValueError('Invalid date') from err
        if selected.isoformat() != raw or not today <= selected <= today + timedelta(days=6):
            raise ValueError('Date outside supported range')
        days = 1
    else:
        period = arguments.get('period', 'today')
        if period not in ('today', 'tomorrow', 'next_7_days'):
            raise ValueError('Invalid period')
        selected = today + timedelta(days=1 if period == 'tomorrow' else 0)
        days = 7 if period == 'next_7_days' else 1
    return (datetime.combine(selected, time.min, tzinfo=tz),
            datetime.combine(selected + timedelta(days=days), time.min, tzinfo=tz))


def merge_verified_bindings(endpoint, artifacts):
    """Intersect every artifact field with explicitly selected endpoint fields."""
    import copy
    manual = endpoint.get('assistant', {}).get('subjects', [])
    allowed = {f['entity'] for row in manual for f in row.get('fields', {}).values()}
    allowed.update(row['entity'] for group in ('temperature','air') for row in endpoint.get(group, []))
    allowed.update(endpoint.get('calendars', []))
    studio = endpoint.get('studio', {})
    for group in ('rooms','lights','covers','status','plants'):
        for row in studio.get(group, []):
            allowed.update(v for k,v in row.items() if k in ('entity','temperature','humidity') and isinstance(v,str))
    allowed.update(v for v in studio.get('mains',{}).values() if isinstance(v,str))
    if studio.get('media',{}).get('entity'):allowed.add(studio['media']['entity'])
    if endpoint.get('display',{}).get('weather'):allowed.add(endpoint['display']['weather'])
    result=copy.deepcopy(manual)
    for cid,bundle in artifacts.items():
        for subject in bundle['bindings']['subjects']:
            fields={role:copy.deepcopy(field) for role,field in subject['fields'].items() if field['entity'] in allowed}
            if not fields:continue
            selected=set(f['entity'] for f in fields.values())
            matches=[r for r in result if r.get('kind')==subject['kind'] and selected & {f['entity'] for f in r.get('fields',{}).values()}]
            if len(matches)>1:continue
            if matches:
                row=matches[0]
                for name in ('roomKey','roomLabel'):
                    if name in subject: row.setdefault(name,copy.deepcopy(subject[name]))
                for role,field in fields.items():row.setdefault('fields',{}).setdefault(role,field)
            else:
                row={**copy.deepcopy(subject),'fields':fields,'_component':cid}
                result.append(row)
    return result


def resolve_air_policy(metric, entity, unit, override, policies, state_get, permitted):
    """Resolve an exact scoped tuple; never read helpers lacking user permission."""
    import math
    if override:
        return override, None
    candidates=[p for p in policies if (p.get('metric'),p.get('entity'),p.get('unit'))==(metric,entity,unit)]
    if not candidates:return None,'missing_policy'
    fingerprints={(tuple(p['levels']),p['higher_is_better'],tuple(sorted(p.get('helpers',{}).items()))) for p in candidates}
    if len(fingerprints)!=1:return None,'conflicting_policy'
    policy=dict(candidates[0]); levels=list(policy['levels']); helpers=policy.get('helpers',{})
    role_order=('fair','poor') if metric=='score' else ('raised','high')
    if any(not permitted(eid) for eid in helpers.values()):return None,'missing_policy'
    fallback=False
    for i,role in enumerate(role_order):
        if role not in helpers:continue
        state=state_get(helpers[role])
        try:
            n=float(state.state)
            if not math.isfinite(n):raise ValueError()
            levels[i]=n
        except (AttributeError,TypeError,ValueError):fallback=True
    if (policy['higher_is_better'] and not levels[0]>levels[1]) or (not policy['higher_is_better'] and not levels[0]<levels[1]):return None,'conflicting_policy'
    policy['levels']=levels;policy['source']='fallback' if fallback or not helpers else 'configured'
    return policy,None


def authorized_subjects(subjects, mapped_entities, permitted, state_get, installation_id, endpoint_id, *, sensitive=False, registry_id=None, required_roles=None):
    """Filter mappings before reading state; never expand groups into children.

    YAML binding keys are the fallback stable identity. Registry-backed identity
    needs to be supplied by the caller before exposing those subjects.
    """
    import json
    import uuid

    namespace = uuid.UUID(installation_id)
    result = []
    for subject in subjects:
        if subject['kind'] == 'presence_source' and not sensitive:
            continue
        fields = {role: source for role,source in subject['fields'].items() if source['entity'] in mapped_entities and permitted(source['entity'])}
        required={'player':('player','state'),'light':('light','state'),'calendar':('calendar','state'),
                  'weather':('weather','state'),'awning':('cover','state','wind'),'presence_source':('presence','state'),
                  'audio_source':('level',),'plant':('moisture','plant'),'backup':('count','archive_count'),
                  'room':('temperature','humidity','measurement','co2','score','pm2_5','voc','nox')}.get(subject['kind'])
        required = required_roles or required
        if not fields or required and not any(role in fields for role in required):
            continue
        identity_key = subject['key']
        if registry_id:
            for role in ('plant','player','light','calendar','weather','cover','presence','level','state','temperature','moisture','measurement','humidity','count','archive_count'):
                if role in fields and (rid := registry_id(fields[role]['entity'])):
                    identity_key = rid;break
        identity = json.dumps([endpoint_id, subject['kind'], identity_key], ensure_ascii=False, separators=(',', ':'))
        identifier = str(uuid.uuid5(namespace, identity))
        room_key = subject.get('roomKey')
        room_id = str(uuid.uuid5(namespace, json.dumps([endpoint_id, 'room', room_key], ensure_ascii=False, separators=(',', ':')))) if room_key else identifier if subject['kind']=='room' else None
        result.append({**subject, 'fields':fields, 'id': identifier, **({'roomId':room_id} if room_id else {})})
    return result


class Runtime:
    """Resolve manual endpoint sources on every request, without retaining readings."""
    def __init__(self, installation_id, state_get, permitted, now, max_age=900, verified_source=None, artifacts=None, registry_id=None):
        self.installation_id = installation_id
        self.state_get = state_get
        self.permitted = permitted
        self.now = now
        self.max_age = max_age
        self.verified_source = verified_source or (lambda _: False)
        self.artifacts = artifacts or {}
        self.registry_id = registry_id

    def effective_endpoint(self, endpoint):
        """Build a request-local view; verified metadata never becomes a saved mapping."""
        import copy
        out=copy.deepcopy(endpoint)
        config=out.setdefault('assistant',{})
        config['subjects']=merge_verified_bindings(endpoint,self.artifacts)
        aliases={'player':{'player':'state'},'light':{'light':'state'},'calendar':{'calendar':'state'},
                 'weather':{'weather':'state'},'awning':{'cover':'state'},'presence_source':{'presence':'state'},
                 'backup':{'count':'archive_count','age':'newest_age_hours'},'frame':{'radar_wake':'radar_wake_enabled'}}
        for row in config['subjects']:
            for source,target in aliases.get(row.get('kind'),{}).items():
                if source in row.get('fields',{}):row['fields'].setdefault(target,row['fields'][source])
        return out

    def capabilities(self, endpoint, supported):
        endpoint=self.effective_endpoint(endpoint)
        results=self._capabilities(endpoint,supported)
        for result in results:
            cid=result['id'].partition('.')[0]
            bundle=self.artifacts.get(cid)
            cap=next((c for c in bundle['definitions']['capabilities'] if c['id']==result['id']),None) if bundle else None
            if cap:
                for name in ('title','description','examples'):
                    result[name]=self._localized(bundle,cap[name+'_key'])
            unique = {}
            for subject in result['subjects']:
                unique.setdefault(subject['id'],subject)
            if len(unique)>164:raise ValueError('Too many assistant subjects')
            result['subjects']=list(unique.values())
        if len(results)>64:raise ValueError('Too many assistant capabilities')
        return results

    def query(self, endpoint, capability, revision, answer_type, arguments):
        return self._query(self.effective_endpoint(endpoint),capability,revision,answer_type,arguments)

    def _temperatures(self, endpoint):
        rows = [r for r in endpoint.get('assistant', {}).get('subjects', []) if r.get('kind') == 'room' and 'temperature' in r.get('fields', {})]
        used = {r['fields']['temperature']['entity'] for r in rows}
        rows += [{'key': row['entity'], 'kind': 'room', 'label': row['label'],
                  'indoor': row.get('indoor', True), **({'roomKey':row['roomKey']} if row.get('roomKey') else {}), 'fields': {'temperature': {'entity': row['entity']}}}
                 for row in endpoint.get('temperature', []) if row['entity'] not in used]
        return authorized_subjects(rows, {f['entity'] for r in rows for f in r['fields'].values()},
                                   self.permitted, self.state_get, self.installation_id, endpoint['id'],registry_id=self.registry_id,required_roles=('temperature',))

    def _capabilities(self, endpoint, supported):
        pair = {'queryType': 'temperature-query.v1', 'answerType': 'temperature.v1'}
        light_pair = {'queryType': 'light-query.v1', 'answerType': 'lights.v1'}
        results = []
        if {'queryType':'component-query.v1','answerType':'component-description.v1'} in supported:
            for cid in endpoint.get('assistant',{}).get('explanations',[]):
                entry=self._explanation(endpoint,cid)
                if entry:
                    cap, _, subject = entry
                    results.append({'id':cap['id'],'revision':1,'queryType':cap['query_type'],'answerType':cap['answer_type'],
                                    'title':self._localized(self.artifacts[cid],cap['title_key']),
                                    'description':self._localized(self.artifacts[cid],cap['description_key']),
                                    'examples':self._localized(self.artifacts[cid],cap['examples_key']),
                                    'configuration':'configured','availability':'ready','limitations':cap['limitations'],
                                    'availabilityBasis':'live_states','availabilityAsOf':self.now().isoformat(),'subjects':[subject]})
        if {'queryType':'plant-query.v1','answerType':'plant-watering.v1'} in supported:
            plants=self._plants(endpoint)
            if plants:
                answer=self._watering_answer(plants)
                results.append({'id':'plants.watering_need','revision':1,'queryType':'plant-query.v1','answerType':'plant-watering.v1',
                                'title':{'en':'Plant watering','de':'Pflanzen gießen','pl':'Podlewanie roślin'},
                                'description':{'en':'Compare plant moisture with its configured band.','de':'Pflanzenfeuchte mit ihrem eingestellten Bereich vergleichen.','pl':'Porównanie wilgotności z ustawionym zakresem.'},
                                'examples':{'en':['Which plants need water?'],'de':['Welche Pflanzen brauchen Wasser?'],'pl':['Które rośliny wymagają podlewania?']},
                                'configuration':'configured','availability':answer['availability'],'limitations':answer['limitations'],'availabilityBasis':'live_states','availabilityAsOf':answer['asOf'],
                                'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in plants]})
        if {'queryType':'status-query.v1','answerType':'frame.v1'} in supported:
            frames=self._frames(endpoint)
            if frames:
                answer=self._frame_answer(frames)
                results.append({'id':'ramka.status','revision':1,'queryType':'status-query.v1','answerType':'frame.v1',
                                'title':{'en':'Frame','de':'Rahmen','pl':'Ramka'},
                                'description':{'en':'Read the reported frame configuration.','de':'Gemeldete Rahmenkonfiguration lesen.','pl':'Odczyt zgłaszanej konfiguracji ramki.'},
                                'examples':{'en':['What is the frame showing?'],'de':['Was zeigt der Rahmen?'],'pl':['Co pokazuje ramka?']},
                                'configuration':'configured','availability':answer['availability'],'limitations':answer['limitations'],'availabilityBasis':'live_states','availabilityAsOf':answer['asOf'],
                                'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in frames]})
        if {'queryType':'status-query.v1','answerType':'backup.v1'} in supported:
            subjects=self._backup(endpoint)
            if subjects:
                answer=self._backup_answer(subjects)
                results.append({'id':'backup.status','revision':1,'queryType':'status-query.v1','answerType':'backup.v1',
                                'title':{'en':'Backups','de':'Sicherungen','pl':'Kopie zapasowe'},
                                'description':{'en':'Read reported local backup information.','de':'Gemeldete lokale Sicherungen lesen.','pl':'Odczyt informacji o lokalnych kopiach.'},
                                'examples':{'en':['When was the last backup?'],'de':['Wann war die letzte Sicherung?'],'pl':['Kiedy była ostatnia kopia?']},
                                'configuration':'configured','availability':answer['availability'],'limitations':answer['limitations'],'availabilityBasis':'live_states','availabilityAsOf':answer['asOf'],
                                'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in subjects]})
        if {'queryType':'audio-query.v1','answerType':'audio-level.v1'} in supported:
            subjects=self._audio(endpoint)
            if subjects:
                answer=self._audio_answer(subjects)
                results.append({'id':'audio-sensor.level','revision':1,'queryType':'audio-query.v1','answerType':'audio-level.v1',
                                'title':{'en':'Sound level','de':'Schallpegel','pl':'Poziom dźwięku'},
                                'description':{'en':'Read the configured sound sensor.','de':'Den konfigurierten Schallsensor lesen.','pl':'Odczyt skonfigurowanego czujnika dźwięku.'},
                                'examples':{'en':['What level does the sensor report?'],'de':['Welchen Pegel meldet der Sensor?'],'pl':['Jaki poziom zgłasza czujnik?']},
                                'configuration':'configured','availability':answer['availability'],'limitations':answer['limitations'],'availabilityBasis':'live_states','availabilityAsOf':answer['asOf'],
                                'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in subjects]})
        if {'queryType':'presence-query.v1','answerType':'presence.v1'} in supported:
            presence=self._presence(endpoint)
            if presence:
                answer=self._presence_answer(presence)
                results.append({'id':'bermuda-presence.observations','revision':1,'queryType':'presence-query.v1','answerType':'presence.v1',
                                'title':{'en':'Presence observations','de':'Anwesenheitsbeobachtungen','pl':'Obserwacje obecności'},
                                'description':{'en':'Read explicitly shared device observations.','de':'Freigegebene Gerätebeobachtungen lesen.','pl':'Odczyt udostępnionych obserwacji urządzeń.'},
                                'examples':{'en':['Which shared devices are detected?'],'de':['Welche freigegebenen Geräte werden erkannt?'],'pl':['Które udostępnione urządzenia wykryto?']},
                                'configuration':'configured','availability':answer['availability'],'limitations':answer['limitations'],'availabilityBasis':'live_states','availabilityAsOf':answer['asOf'],
                                'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in presence]})
        if {'queryType':'weather-query.v1','answerType':'weather.v1'} in supported:
            weather=self._weather(endpoint)
            if weather:
                answer=self._weather_answer(weather)
                results.append({'id':'weather.current','revision':1,'queryType':'weather-query.v1','answerType':'weather.v1',
                                'title':{'en':'Weather','de':'Wetter','pl':'Pogoda'},
                                'description':{'en':'Read reported current weather.','de':'Gemeldetes Wetter lesen.','pl':'Odczyt zgłaszanej pogody.'},
                                'examples':{'en':['What is the weather?'],'de':['Wie ist das Wetter?'],'pl':['Jaka jest pogoda?']},
                                'configuration':'configured','availability':answer['availability'],'limitations':answer['limitations'],'availabilityBasis':'live_states','availabilityAsOf':answer['asOf'],
                                'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in weather]})
        if {'queryType':'awning-query.v1','answerType':'awnings.v1'} in supported:
            covers=self._awnings(endpoint)
            if covers:
                answer=self._awning_answer(covers)
                results.append({'id':'awnings.status','revision':1,'queryType':'awning-query.v1','answerType':'awnings.v1',
                                'title':{'en':'Awnings','de':'Markisen','pl':'Markizy'},
                                'description':{'en':'Read reported awning status.','de':'Gemeldeten Markisenstatus lesen.','pl':'Odczyt zgłaszanego stanu markiz.'},
                                'examples':{'en':['Are the awnings extended?'],'de':['Sind die Markisen ausgefahren?'],'pl':['Czy markizy są rozwinięte?']},
                                'configuration':'configured','availability':answer['availability'],'limitations':answer['limitations'],'availabilityBasis':'live_states','availabilityAsOf':answer['asOf'],
                                'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in covers]})
        if {'queryType':'air-query.v1','answerType':'air.v1'} in supported:
            air = self._air(endpoint)
            if air:
                for cap in ('climate-air.air_readings','climate-air.air_assessment'):
                    if cap.endswith('air_assessment') and not any(s.get('policy') for s in air) and not any(b['bindings']['policies'] for b in self.artifacts.values()):continue
                    answer = self._air_answer(air,cap)
                    results.append({'id':cap,'revision':1,'queryType':'air-query.v1','answerType':'air.v1',
                                    'title':{'en':'Air','de':'Luft','pl':'Powietrze'},
                                    'description':{'en':'Read mapped air measurements.','de':'Zugeordnete Luftmesswerte lesen.','pl':'Odczyt pomiarów powietrza.'},
                                    'examples':{'en':['What is the air quality?'],'de':['Wie ist die Luftqualität?'],'pl':['Jaka jest jakość powietrza?']},
                                    'configuration':'configured','availability':answer['availability'],'limitations':answer['limitations'],
                                    'availabilityBasis':'live_states','availabilityAsOf':answer['asOf'],
                                    'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in air]})
        if {'queryType':'calendar-query.v1','answerType':'calendar.v1'} in supported:
            calendars = self._calendars(endpoint)
            if calendars:
                results.append({'id':'calendar.agenda','revision':1,'queryType':'calendar-query.v1','answerType':'calendar.v1',
                                'title':{'en':'Calendar','de':'Kalender','pl':'Kalendarz'},
                                'description':{'en':'Read upcoming events in the next seven days.','de':'Termine der nächsten sieben Tage lesen.','pl':'Odczyt wydarzeń w ciągu siedmiu dni.'},
                                'examples':{'en':['What is on my calendar tomorrow?'],'de':['Was steht morgen im Kalender?'],'pl':['Co mam jutro w kalendarzu?']},
                                'configuration':'configured','availability':'unavailable','limitations':['provider_sync_unknown'],
                                'availabilityBasis':'configuration_only','availabilityAsOf':self.now().isoformat(),
                                'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in calendars]})
        if {'queryType':'media-query.v1','answerType':'media.v1'} in supported:
            players = self._players(endpoint)
            if players:
                for capability in ('media.now_playing','media.status'):
                    answer = self._media_answer(players, capability)
                    results.append({'id':capability,'revision':1,'queryType':'media-query.v1','answerType':'media.v1',
                                    'title':{'en':'Media','de':'Medien','pl':'Media'},
                                    'description':{'en':'Read reported player information.','de':'Gemeldete Wiedergabeinformationen lesen.','pl':'Odczyt stanu odtwarzacza.'},
                                    'examples':{'en':['What is playing?'],'de':['Was läuft?'],'pl':['Co jest odtwarzane?']},
                                    'configuration':'configured','availability':answer['availability'],'limitations':answer['limitations'],
                                    'availabilityBasis':'live_states','availabilityAsOf':answer['asOf'],
                                    'subjects':[{'id':x['id'],'kind':x['kind'],'label':x['label'],**({'roomId':x['roomId']} if x.get('roomId') else {}),**({'roomLabel':x['roomLabel']} if x.get('roomLabel') else {})} for x in players]})
        if light_pair in supported:
            lights = self._lights(endpoint)
            if lights:
                answer = self._light_answer(lights)
                results.append({'id': 'lights.status', 'revision': 1, **light_pair,
                                'title': {'en': 'Lights', 'de': 'Licht', 'pl': 'Światła'},
                                'description': {'en': 'Read reported light states.', 'de': 'Gemeldete Lichtzustände lesen.', 'pl': 'Odczyt zgłaszanego stanu świateł.'},
                                'examples': {'en': ['Which lights are on?'], 'de': ['Welche Lichter sind an?'], 'pl': ['Które światła są włączone?']},
                                'configuration': 'configured', 'availability': answer['availability'], 'limitations': answer['limitations'],
                                'availabilityBasis': 'live_states', 'availabilityAsOf': answer['asOf'],
                                'subjects': [{'id': s['id'], 'kind': s['kind'], 'label': s['label'], **({'roomId':s['roomId']} if s.get('roomId') else {}), **({'roomLabel':s['roomLabel']} if s.get('roomLabel') else {})} for s in lights]})
        if pair not in supported:
            return results
        subjects = self._temperatures(endpoint)
        if not subjects:
            return results
        answer = self._temperature(endpoint, subjects, {})
        return [{'id': 'climate-air.temperature', 'revision': 1, **pair,
                 'title': {'en': 'Temperature', 'de': 'Temperatur', 'pl': 'Temperatura'},
                 'description': {'en': 'Read mapped room temperatures.', 'de': 'Zugeordnete Raumtemperaturen lesen.', 'pl': 'Odczyt temperatury przypisanych pomieszczeń.'},
                 'examples': {'en': ['What is the temperature?'], 'de': ['Wie ist die Temperatur?'], 'pl': ['Jaka jest temperatura?']},
                 'configuration': 'configured', 'availability': answer['availability'], 'limitations': answer['limitations'],
                 'availabilityBasis': 'live_states', 'availabilityAsOf': answer['asOf'],
                 'subjects': [{'id': s['id'], 'kind': s['kind'], 'label': s['label'], **({'roomId':s['roomId']} if s.get('roomId') else {}), **({'roomLabel':s['roomLabel']} if s.get('roomLabel') else {})} for s in subjects]}] + results

    def _query(self, endpoint, capability, revision, answer_type, arguments):
        if isinstance(capability,str) and capability.endswith('.explain') and type(revision) is int and revision == 1 and answer_type == 'component-description.v1':
            if arguments != {}:raise ValueError('Invalid explanation arguments')
            cid=capability[:-8]; entry=self._explanation(endpoint,cid)
            if not entry:raise ValueError('Unknown explanation')
            cap,bundle,subject=entry; now=self.now().isoformat()
            return {'version':1,'capability':capability,'revision':1,'answerType':answer_type,'asOf':now,'availability':'ready',
                    'coverage':{'eligible':1,'evaluated':1,'missing':0,'unit':'subject'},'summary':{'status':'explained','affected':1},
                    'facts':[{'id':subject['id'],'subjectId':subject['id'],'componentId':cid,'componentVersion':bundle['component']['version'],
                              'purpose':self._localized(bundle,cap['description_key']),'capabilities':[],
                              'basis':'installed_metadata','liveStatus':'not_assessed','evidenceRefs':[subject['id']]}],
                    'evidence':[{'id':subject['id'],'sourceRef':subject['id'],'observedAt':None,'reportedAt':None,'freshnessBasis':'installed_metadata','maxAgeSeconds':None,'freshness':'not_applicable'}],
                    'limitations':cap['limitations']}
        if capability == 'plants.watering_need' and type(revision) is int and revision == 1 and answer_type == 'plant-watering.v1':
            if not isinstance(arguments,dict) or set(arguments)-{'roomId','plantId'} or 'roomId' in arguments and 'plantId' in arguments:raise ValueError('Invalid plant arguments')
            subjects=self._plants(endpoint)
            if 'roomId' in arguments:subjects=[s for s in subjects if s.get('roomId')==arguments['roomId']]
            if 'plantId' in arguments:subjects=[s for s in subjects if s['id']==arguments['plantId']]
            if not subjects:raise ValueError('Unknown plant')
            return self._watering_answer(subjects)
        if capability == 'ramka.status' and type(revision) is int and revision == 1 and answer_type == 'frame.v1':
            if not isinstance(arguments,dict) or set(arguments)-{'subjectId'}:raise ValueError('Invalid frame arguments')
            subjects=self._frames(endpoint)
            if 'subjectId' in arguments:subjects=[s for s in subjects if s['id']==arguments['subjectId']]
            if len(subjects)!=1:raise ValueError('Unknown or ambiguous frame')
            return self._frame_answer(subjects)
        if capability == 'backup.status' and type(revision) is int and revision == 1 and answer_type == 'backup.v1':
            if not isinstance(arguments,dict) or set(arguments)-{'subjectId'}:raise ValueError('Invalid backup arguments')
            subjects=self._backup(endpoint)
            if 'subjectId' in arguments:subjects=[s for s in subjects if s['id']==arguments['subjectId']]
            if len(subjects)!=1:raise ValueError('Unknown or ambiguous backup')
            return self._backup_answer(subjects)
        if capability == 'audio-sensor.level' and type(revision) is int and revision == 1 and answer_type == 'audio-level.v1':
            if not isinstance(arguments,dict) or set(arguments)-{'subjectId','roomId'} or 'subjectId' in arguments and 'roomId' in arguments:raise ValueError('Invalid audio arguments')
            subjects=self._audio(endpoint)
            if 'roomId' in arguments:subjects=[s for s in subjects if s.get('roomId')==arguments['roomId']]
            if 'subjectId' in arguments:subjects=[s for s in subjects if s['id']==arguments['subjectId']]
            if not subjects:raise ValueError('Unknown source')
            return self._audio_answer(subjects)
        if capability == 'bermuda-presence.observations' and type(revision) is int and revision == 1 and answer_type == 'presence.v1':
            if not isinstance(arguments,dict) or set(arguments)-{'subjectId','roomId'} or 'subjectId' in arguments and 'roomId' in arguments:raise ValueError('Invalid presence arguments')
            subjects=self._presence(endpoint)
            if 'roomId' in arguments:subjects=[s for s in subjects if s.get('roomId')==arguments['roomId']]
            if 'subjectId' in arguments:subjects=[s for s in subjects if s['id']==arguments['subjectId']]
            if not subjects:raise ValueError('Unknown subject')
            return self._presence_answer(subjects)
        if capability == 'weather.current' and type(revision) is int and revision == 1 and answer_type == 'weather.v1':
            if not isinstance(arguments,dict) or set(arguments)-{'subjectId'}:raise ValueError('Invalid weather arguments')
            subjects=self._weather(endpoint)
            if 'subjectId' in arguments:subjects=[s for s in subjects if s['id']==arguments['subjectId']]
            if len(subjects)!=1:raise ValueError('Unknown or ambiguous weather source')
            return self._weather_answer(subjects)
        if capability == 'awnings.status' and type(revision) is int and revision == 1 and answer_type == 'awnings.v1':
            if not isinstance(arguments,dict) or set(arguments)-{'roomId','subjectId'} or 'roomId' in arguments and 'subjectId' in arguments:
                raise ValueError('Invalid awning arguments')
            subjects=self._awnings(endpoint)
            if 'roomId' in arguments:subjects=[s for s in subjects if s.get('roomId')==arguments['roomId']]
            if 'subjectId' in arguments:subjects=[s for s in subjects if s['id']==arguments['subjectId']]
            if not subjects:raise ValueError('Unknown subject')
            return self._awning_answer(subjects)
        if capability in ('climate-air.air_readings','climate-air.air_assessment') and type(revision) is int and revision == 1 and answer_type == 'air.v1':
            if not isinstance(arguments,dict) or set(arguments)-{'roomId','metric'} or arguments.get('metric') not in (None,'score','co2','pm2_5','voc','nox'):
                raise ValueError('Invalid air arguments')
            subjects=self._air(endpoint)
            if 'roomId' in arguments:subjects=[s for s in subjects if s.get('roomId')==arguments['roomId']]
            if arguments.get('metric'):subjects=[s for s in subjects if s['metric']==arguments['metric']]
            if not subjects:raise ValueError('Unknown source')
            return self._air_answer(subjects,capability)
        if capability in ('media.now_playing','media.status') and type(revision) is int and revision == 1 and answer_type == 'media.v1':
            if not isinstance(arguments,dict) or set(arguments)-{'roomId','subjectId'} or 'roomId' in arguments and 'subjectId' in arguments:
                raise ValueError('Invalid media arguments')
            subjects = self._players(endpoint)
            if 'roomId' in arguments:subjects=[s for s in subjects if s.get('roomId')==arguments['roomId']]
            if 'subjectId' in arguments:
                subjects = [s for s in subjects if s['id'] == arguments['subjectId']]
            if len(subjects) != 1:
                raise ValueError('Unknown or ambiguous subject')
            return self._media_answer(subjects,capability)
        if capability == 'lights.status' and type(revision) is int and revision == 1 and answer_type == 'lights.v1':
            if not isinstance(arguments, dict) or set(arguments) - {'roomId','subjectId','filter'} or 'roomId' in arguments and 'subjectId' in arguments or arguments.get('filter','all') not in ('all','on','off','unknown'):
                raise ValueError('Invalid light arguments')
            subjects = self._lights(endpoint)
            if 'roomId' in arguments:
                subjects = [s for s in subjects if s.get('roomId') == arguments['roomId']]
            if 'subjectId' in arguments:
                subjects = [s for s in subjects if s['id'] == arguments['subjectId']]
            if not subjects:
                raise ValueError('Unknown subject')
            return self._light_answer(subjects, arguments.get('filter','all'))
        if capability != 'climate-air.temperature' or type(revision) is not int or revision != 1 or answer_type != 'temperature.v1':
            raise ValueError('Unsupported capability')
        if not isinstance(arguments, dict) or set(arguments) - {'roomId'}:
            raise ValueError('Invalid arguments')
        subjects = self._temperatures(endpoint)
        if 'roomId' in arguments:
            if not isinstance(arguments['roomId'], str):
                raise ValueError('Invalid room')
            subjects = [s for s in subjects if s.get('roomId') == arguments['roomId']]
            if not subjects:
                raise ValueError('Unknown room')
        else:
            subjects = [s for s in subjects if s.get('indoor', True)]
        if not subjects:
            raise ValueError('No authorized sources')
        return self._temperature(endpoint, subjects, arguments)

    @staticmethod
    def _localized(bundle, key):
        values={}
        for lang in ('en','de','pl'):
            value=bundle['translations'][lang]
            for part in key.split('.'):value=value[part]
            values[lang]=value
        return values

    def _explanation(self, endpoint, cid):
        import json
        import uuid
        if cid not in endpoint.get('assistant',{}).get('explanations',[]):return None
        bundle=self.artifacts.get(cid)
        if not bundle:return None
        caps=[c for c in bundle['definitions']['capabilities'] if c['id']==cid+'.explain' and c['revision']==1 and c['query_type']=='component-query.v1' and c['answer_type']=='component-description.v1']
        if len(caps)!=1:return None
        key=json.dumps([endpoint['id'],'component',cid],separators=(',',':'))
        subject={'id':str(uuid.uuid5(uuid.UUID(self.installation_id),key)),'kind':'component','label':self._localized(bundle,caps[0]['title_key'])}
        return caps[0],bundle,subject

    def _plants(self, endpoint):
        rows=[r for r in endpoint.get('assistant',{}).get('subjects',[]) if r.get('kind')=='plant' and 'moisture' in r.get('fields',{})]
        mapped={field['entity'] for row in rows for field in row['fields'].values()}
        return authorized_subjects(rows,mapped,self.permitted,self.state_get,self.installation_id,endpoint['id'],registry_id=self.registry_id,required_roles=('moisture',))

    def _watering_answer(self, subjects):
        import math
        import uuid
        now=self.now(); facts=[]; evidence=[]; usable=0; limitations=set(); classifications=[]
        for s in subjects:
            field=s['fields']['moisture']; eid=field['entity']; state=self.state_get(eid) if self.permitted(eid) else None
            attrs=getattr(state,'attributes',{}) if state else {}; reported=getattr(state,'last_reported',None); unit=attrs.get('unit_of_measurement'); max_age=field.get('maxAgeSeconds',21600)
            value=None
            try:
                n=float(attrs.get(field['attribute']) if 'attribute' in field else state.state)
                if math.isfinite(n) and 0<=n<=100:value=n
            except (ValueError,TypeError,AttributeError):pass
            status='usable' if value is not None else 'missing'; freshness='unknown'
            if status=='usable' and unit!='%':status='invalid'; limitations.add('invalid_unit')
            if status=='usable' and (attrs.get('restored') or not self.verified_source(eid) or not reported or not reported.tzinfo):status='unverified'; limitations.add('unknown_freshness')
            elif status=='usable' and ((now-reported).total_seconds()>max_age or reported>now):status='stale';freshness='stale';limitations.add('stale')
            elif status=='usable':freshness='current'
            if status=='missing':limitations.add('missing_source')
            low=s.get('limits',{}).get('min_moisture'); high=s.get('limits',{}).get('max_moisture')
            valid=type(low) in (int,float) and type(high) in (int,float) and math.isfinite(low) and math.isfinite(high) and 0<=low<high<=100
            if not valid:limitations.add('missing_policy')
            assessment='unknown'
            if status=='usable' and valid:
                assessment='needs_water' if value<low else 'too_wet' if value>high else 'within_band';usable+=1
            classifications.append(assessment)
            fid=str(uuid.uuid5(uuid.UUID(s['id']),'moisture'));evid=str(uuid.uuid5(uuid.UUID(fid),'evidence'))
            item={'id':evid,'sourceRef':str(uuid.uuid5(uuid.UUID(s['id']),'moisture-source')),'observedAt':None,'reportedAt':reported.isoformat() if reported and reported.tzinfo else None,'freshnessBasis':'source_report' if freshness!='unknown' else 'unknown','maxAgeSeconds':max_age,'freshness':freshness}
            if valid:item['policy']={'id':'plant-moisture','revision':1,'bounds':{'minimum':low,'maximum':high},'direction':'within_band','source':'configured'}
            evidence.append(item)
            facts.append({'id':fid,'subjectId':s['id'],'plantId':s['id'],'name':s['label'],'metric':'moisture','value':value if status=='usable' else None,'unit':'%','status':status,'problems':['moisture_low'] if assessment=='needs_water' else ['moisture_high'] if assessment=='too_wet' else [],'evidenceRefs':[evid]})
        known={v for v in classifications if v!='unknown'}
        summary=next(iter(known)) if len(known)==1 else 'mixed' if known else 'unknown'
        affected=sum(v==summary for v in classifications) if summary not in ('mixed','unknown') else sum(v in ('needs_water','too_wet') for v in classifications) if summary=='mixed' else 0
        return {'version':1,'capability':'plants.watering_need','revision':1,'answerType':'plant-watering.v1','asOf':now.isoformat(),'availability':'ready' if usable==len(facts) and facts else 'partial' if usable else 'unavailable',
                'coverage':{'eligible':len(facts),'evaluated':usable,'missing':len(facts)-usable},'summary':{'status':summary,'affected':affected},'facts':facts,'evidence':evidence,'limitations':sorted(limitations)}

    def _frames(self, endpoint):
        rows=[r for r in endpoint.get('assistant',{}).get('subjects',[]) if r.get('kind')=='frame' and set(r.get('fields',{})) & {'mode','page','theme','radar_wake_enabled'}]
        mapped={v['entity'] for r in rows for v in r['fields'].values()}
        return authorized_subjects(rows,mapped,self.permitted,self.state_get,self.installation_id,endpoint['id'],registry_id=self.registry_id)

    def _frame_answer(self, subjects):
        import uuid
        now=self.now(); facts=[]; evidence=[]; usable=0
        for s in subjects:
            fid=str(uuid.uuid5(uuid.UUID(s['id']),'frame')); row={'id':fid,'subjectId':s['id'],'physicalDisplayState':'unknown','evidenceRefs':[]}
            known=0
            for role in ('mode','page','theme','radar_wake_enabled'):
                field=s['fields'].get(role)
                if not field:continue
                eid=field['entity']; state=self.state_get(eid) if self.permitted(eid) else None; attrs=getattr(state,'attributes',{}) if state else {}; value=getattr(state,'state',None)
                if not state or attrs.get('restored') or not isinstance(value,str) or value in ('unknown','unavailable'):continue
                if role=='radar_wake_enabled':
                    if value not in ('on','off'):continue
                    row['radarWakeEnabled']=value=='on'
                else:row[role]=value[:120]
                known+=1; evid=str(uuid.uuid5(uuid.UUID(fid),role));row['evidenceRefs'].append(evid)
                evidence.append({'id':evid,'sourceRef':str(uuid.uuid5(uuid.UUID(s['id']),role+'-source')),'observedAt':None,'reportedAt':None,'freshnessBasis':'reported_state','maxAgeSeconds':None,'freshness':'unknown'})
            usable+=int(known>0);
            if row['evidenceRefs']:facts.append(row)
        return {'version':1,'capability':'ramka.status','revision':1,'answerType':'frame.v1','asOf':now.isoformat(),'availability':'ready' if usable==len(subjects) and subjects else 'partial' if usable else 'unavailable',
                'coverage':{'eligible':len(subjects),'evaluated':usable,'missing':len(subjects)-usable,'unit':'subject'},'summary':{'status':'reported' if usable else 'unknown','affected':usable},'facts':facts,'evidence':evidence,'limitations':['reported_state_only']}

    def _backup(self, endpoint):
        rows=[r for r in endpoint.get('assistant',{}).get('subjects',[]) if r.get('kind')=='backup' and 'archive_count' in r.get('fields',{})]
        mapped={v['entity'] for r in rows for v in r['fields'].values()}
        return authorized_subjects(rows,mapped,self.permitted,self.state_get,self.installation_id,endpoint['id'],registry_id=self.registry_id,required_roles=('archive_count',))

    def _backup_answer(self, subjects):
        import math
        import uuid
        now=self.now(); facts=[]; evidence=[]; usable=0; limitations=set()
        for s in subjects:
            values={}; refs=[]
            for role in ('archive_count','newest_age_hours','size'):
                field=s['fields'].get(role)
                if not field:continue
                eid=field['entity']; state=self.state_get(eid) if self.permitted(eid) else None; attrs=getattr(state,'attributes',{}) if state else {}
                value=None
                try:
                    n=float(attrs.get(field['attribute']) if 'attribute' in field else state.state)
                    if math.isfinite(n) and n>=0 and not attrs.get('restored'):value=n
                except (ValueError,TypeError,AttributeError):pass
                if role=='archive_count' and value is not None and not value.is_integer():value=None
                if role=='newest_age_hours' and attrs.get('unit_of_measurement') not in ('h','hours'):value=None
                if role=='size' and attrs.get('unit_of_measurement') not in ('MiB','MB'):value=None
                values[role]=(value,attrs.get('unit_of_measurement'))
                evid=str(uuid.uuid5(uuid.UUID(s['id']),role+'-evidence'));refs.append(evid)
                evidence.append({'id':evid,'sourceRef':str(uuid.uuid5(uuid.UUID(s['id']),role+'-source')),'observedAt':None,'reportedAt':None,'freshnessBasis':'historical_event','maxAgeSeconds':None,'freshness':'not_applicable'})
            count=values.get('archive_count',(None,None))[0]; age=values.get('newest_age_hours',(None,None))[0]
            if count==0:age=None
            elif age==9999:age=None; limitations.add('missing_source')
            if count is None:limitations.add('missing_source')
            else:usable+=1
            fid=str(uuid.uuid5(uuid.UUID(s['id']),'backup'))
            row={'id':fid,'subjectId':s['id'],'archiveCount':int(count) if count is not None else None,'newestAgeHours':age,'reportedAt':None,'verification':'existence_only','evidenceRefs':refs}
            if 'size' in values and values['size'][0] is not None:row['size']=values['size'][0]; row['sizeUnit']=values['size'][1]
            facts.append(row)
        status='unknown' if not usable else 'missing' if all(f['archiveCount']==0 for f in facts) else 'present' if all(f['archiveCount'] and f['archiveCount']>0 for f in facts) else 'unknown'
        return {'version':1,'capability':'backup.status','revision':1,'answerType':'backup.v1','asOf':now.isoformat(),'availability':'ready' if usable==len(facts) and facts else 'partial' if usable else 'unavailable',
                'coverage':{'eligible':len(facts),'evaluated':usable,'missing':len(facts)-usable,'unit':'subject'},'summary':{'status':status,'affected':usable if status!='unknown' else 0},'facts':[f for f in facts if f['archiveCount'] is not None],'evidence':evidence,'limitations':sorted(limitations)}

    def _audio(self, endpoint):
        rows=[r for r in endpoint.get('assistant',{}).get('subjects',[]) if r.get('kind')=='audio_source' and 'level' in r.get('fields',{})]
        mapped={v['entity'] for r in rows for v in r['fields'].values()}
        return authorized_subjects(rows,mapped,self.permitted,self.state_get,self.installation_id,endpoint['id'],registry_id=self.registry_id,required_roles=('level',))

    def _audio_answer(self, subjects):
        import math
        import uuid
        now=self.now(); facts=[]; evidence=[]; usable=0; limitations=set()
        for s in subjects:
            field=s['fields']['level']; eid=field['entity']; state=self.state_get(eid) if self.permitted(eid) else None
            attrs=getattr(state,'attributes',{}) if state else {}; reported=getattr(state,'last_reported',None); unit=attrs.get('unit_of_measurement'); limits=s.get('limits',{})
            current=bool(state and not attrs.get('restored') and self.verified_source(eid) and reported and reported.tzinfo and 0<=(now-reported).total_seconds()<=field.get('maxAgeSeconds',self.max_age))
            value=None
            try:
                n=float(attrs.get(field['attribute']) if 'attribute' in field else state.state)
                if math.isfinite(n) and unit in ('dBFS','dB_SPL'):value=n
            except (ValueError,TypeError,AttributeError):pass
            calibrated=limits.get('calibrated') is True and unit=='dB_SPL'
            window=limits.get('integration_window_seconds'); weighting=limits.get('weighting')
            if type(window) not in (int,float) or not math.isfinite(window) or window<=0 or weighting!='unweighted':current=False
            if unit=='dB_SPL' and not calibrated:current=False
            if not current:limitations.add('unknown_freshness')
            if not calibrated:limitations.add('uncalibrated')
            if value is not None and current:usable+=1
            fid=str(uuid.uuid5(uuid.UUID(s['id']),'level')); evid=str(uuid.uuid5(uuid.UUID(fid),'evidence'))
            facts.append({'id':fid,'subjectId':s['id'],'label':s['label'],'level':value if current else None,'unit':unit if unit in ('dBFS','dB_SPL') else None,'calibrated':calibrated,'basis':'calibrated' if calibrated else 'estimate','integrationWindowSeconds':window if type(window) in (int,float) else None,'weighting':weighting if weighting=='unweighted' else None,'online':bool(state) if state else None,'evidenceRefs':[evid]})
            evidence.append({'id':evid,'sourceRef':str(uuid.uuid5(uuid.UUID(s['id']),'level-source')),'observedAt':None,'reportedAt':reported.isoformat() if reported and reported.tzinfo else None,'freshnessBasis':'source_report' if current else 'unknown','maxAgeSeconds':field.get('maxAgeSeconds',self.max_age),'freshness':'current' if current else 'unknown'})
        return {'version':1,'capability':'audio-sensor.level','revision':1,'answerType':'audio-level.v1','asOf':now.isoformat(),'availability':'ready' if usable==len(facts) and facts else 'partial' if usable else 'unavailable',
                'coverage':{'eligible':len(facts),'evaluated':usable,'missing':len(facts)-usable,'unit':'subject'},'summary':{'status':'estimated' if usable else 'unknown','affected':usable},'facts':[f for f in facts if f['unit'] in ('dBFS','dB_SPL') and isinstance(f['integrationWindowSeconds'],(int,float)) and f['integrationWindowSeconds']>0 and f['weighting']=='unweighted'],'evidence':evidence,'limitations':sorted(limitations)}

    def _presence(self, endpoint):
        config=endpoint.get('assistant',{}); rows=[r for r in config.get('subjects',[]) if r.get('kind')=='presence_source' and 'state' in r.get('fields',{}) and r.get('limits',{}).get('observation_kind') in ('ble_device','radar_presence')]
        mapped={v['entity'] for r in rows for v in r['fields'].values()}
        return authorized_subjects(rows,mapped,self.permitted,self.state_get,self.installation_id,endpoint['id'],sensitive=config.get('presence_sensitive', config.get('sensitive')) is True,registry_id=self.registry_id,required_roles=('state',))

    def _presence_answer(self, subjects):
        import uuid
        now=self.now(); facts=[]; evidence=[]; usable=0; limitations={'reported_state_only'}
        for s in subjects:
            eid=s['fields']['state']['entity']; state=self.state_get(eid) if self.permitted(eid) else None
            attrs=getattr(state,'attributes',{}) if state else {}; reported=getattr(state,'last_reported',None); raw=getattr(state,'state','unknown'); kind=s['limits']['observation_kind']
            current=bool(state and not attrs.get('restored') and self.verified_source(eid) and reported and reported.tzinfo and 0<=(now-reported).total_seconds()<=self.max_age)
            if kind=='ble_device': detected=True if raw=='home' else False if raw=='not_home' else None
            else:detected=True if raw=='on' else False if raw=='off' else None
            if not current:detected=None; limitations.add('unknown_freshness')
            usable+=int(detected is not None)
            fid=str(uuid.uuid5(uuid.UUID(s['id']),'presence')); evid=str(uuid.uuid5(uuid.UUID(fid),'evidence'))
            facts.append({'id':fid,'subjectId':s['id'],'label':s['label'],'observationKind':kind,'detected':detected,'observedAt':None,'evidenceRefs':[evid]})
            evidence.append({'id':evid,'sourceRef':str(uuid.uuid5(uuid.UUID(s['id']),'presence-source')),'observedAt':None,'reportedAt':reported.isoformat() if reported and reported.tzinfo else None,'freshnessBasis':'source_report' if current else 'unknown','maxAgeSeconds':self.max_age,'freshness':'current' if current else 'unknown'})
        detected=sum(f['detected'] is True for f in facts)
        status='detected' if detected else 'not_detected' if usable==len(facts) and facts else 'unknown'
        return {'version':1,'capability':'bermuda-presence.observations','revision':1,'answerType':'presence.v1','asOf':now.isoformat(),
                'availability':'ready' if usable==len(facts) and facts else 'partial' if usable else 'unavailable',
                'coverage':{'eligible':len(facts),'evaluated':usable,'missing':len(facts)-usable,'unit':'subject'},'summary':{'status':status,'affected':detected},'facts':facts,'evidence':evidence,'limitations':sorted(limitations)}

    def _weather(self, endpoint):
        rows=[r for r in endpoint.get('assistant',{}).get('subjects',[]) if r.get('kind')=='weather' and 'state' in r.get('fields',{})]
        used={r['fields']['state']['entity'] for r in rows}; legacy=endpoint.get('display',{}).get('weather')
        if legacy and legacy not in used:rows.append({'key':legacy,'kind':'weather','label':'Weather','fields':{'state':{'entity':legacy}}})
        mapped={v['entity'] for r in rows for v in r['fields'].values()}
        return authorized_subjects(rows,mapped,self.permitted,self.state_get,self.installation_id,endpoint['id'],registry_id=self.registry_id,required_roles=('state',))

    def _weather_answer(self, subjects):
        import math
        import uuid
        now=self.now(); facts=[]; evidence=[]; good=0; limitations=set()
        allowed_conditions={'clear-night','cloudy','fog','hail','lightning','lightning-rainy','partlycloudy','pouring','rainy','snowy','snowy-rainy','sunny','windy','windy-variant','exceptional'}
        for s in subjects:
            eid=s['fields']['state']['entity']; state=self.state_get(eid) if self.permitted(eid) else None; attrs=getattr(state,'attributes',{}) if state else {}
            condition=getattr(state,'state',None); condition=condition if condition in allowed_conditions else None
            reported=getattr(state,'last_reported',None); current=bool(state and not attrs.get('restored') and condition)
            if current:good+=1
            limitations.update(('reported_state_only','provider_sync_unknown'))
            if not current:limitations.add('unknown_freshness')
            measurements=[]
            for key,unit_key,allowed in [('temperature','temperature_unit',{'°C','°F'}),('humidity',None,{'%'}),('pressure','pressure_unit',{'hPa','mbar','Pa','kPa'}),('wind_speed','wind_speed_unit',{'km/h','m/s','mph','kn'})]:
                value=attrs.get(key); unit=attrs.get(unit_key) if unit_key else '%'
                if type(value) in (int,float) and math.isfinite(value) and unit in allowed and (key!='humidity' or 0<=value<=100):
                    measurements.append({'metric':key,'value':value if current else None,'unit':unit})
            fid=str(uuid.uuid5(uuid.UUID(s['id']),'weather')); evid=str(uuid.uuid5(uuid.UUID(fid),'evidence'))
            facts.append({'id':fid,'subjectId':s['id'],'condition':condition if current else None,'measurements':measurements,'evidenceRefs':[evid]})
            evidence.append({'id':evid,'sourceRef':str(uuid.uuid5(uuid.UUID(s['id']),'weather-source')),'observedAt':None,'reportedAt':reported.isoformat() if reported and reported.tzinfo else None,'freshnessBasis':'reported_state','maxAgeSeconds':None,'freshness':'unknown'})
        return {'version':1,'capability':'weather.current','revision':1,'answerType':'weather.v1','asOf':now.isoformat(),
                'availability':'ready' if good==len(facts) and facts else 'partial' if good else 'unavailable',
                'coverage':{'eligible':len(facts),'evaluated':good,'missing':len(facts)-good,'unit':'subject'},
                'summary':{'status':'observed' if good else 'unknown','affected':good},'facts':[f for f in facts if f.get('condition') is not None],'evidence':evidence,'limitations':sorted(limitations)}

    def _awnings(self, endpoint):
        rows=[r for r in endpoint.get('assistant',{}).get('subjects',[]) if r.get('kind')=='awning' and 'state' in r.get('fields',{})]
        used={r['fields']['state']['entity'] for r in rows}
        rows += [{'key':r['entity'],'kind':'awning','label':r['label'],'fields':{'state':{'entity':r['entity']}}} for r in endpoint.get('studio',{}).get('covers',[]) if r['entity'] not in used]
        mapped={v['entity'] for r in rows for v in r['fields'].values()}
        return authorized_subjects(rows,mapped,self.permitted,self.state_get,self.installation_id,endpoint['id'],registry_id=self.registry_id,required_roles=('state',))

    def _awning_answer(self, subjects):
        import uuid
        now=self.now(); facts=[]; evidence=[]
        for s in subjects:
            eid=s['fields']['state']['entity']; state=self.state_get(eid) if self.permitted(eid) else None
            attrs=getattr(state,'attributes',{}) if state else {}; raw=getattr(state,'state','unknown')
            value=raw if raw in ('open','closed','opening','closing') and not attrs.get('restored') else 'unknown'
            fid=str(uuid.uuid5(uuid.UUID(s['id']),'awning')); evid=str(uuid.uuid5(uuid.UUID(fid),'evidence'))
            evidence.append({'id':evid,'sourceRef':str(uuid.uuid5(uuid.UUID(s['id']),'state-source')),'observedAt':None,'reportedAt':None,'freshnessBasis':'reported_state','maxAgeSeconds':None,'freshness':'unknown'})
            pos=attrs.get('current_position'); pos=pos if type(pos) in (int,float) and 0<=pos<=100 else None
            facts.append({'id':fid,'subjectId':s['id'],'label':s['label'],'state':{'open':'extended','closed':'retracted'}.get(value,value),'positionPercent':pos,'stateBasis':'reported','positionBasis':'reported','alarm':'unknown','alarmBasis':'unknown','lastHeardAt':None,'evidenceRefs':[evid]})
        good=[f for f in facts if f['state']!='unknown']; states={f['state'] for f in good}
        summary='extended' if states=={'open'} else 'retracted' if states=={'closed'} else 'mixed' if len(states)>1 else 'unknown'
        return {'version':1,'capability':'awnings.status','revision':1,'answerType':'awnings.v1','asOf':now.isoformat(),'availability':'ready' if len(good)==len(facts) and facts else 'partial' if good else 'unavailable',
                'coverage':{'eligible':len(facts),'evaluated':len(good),'missing':len(facts)-len(good),'unit':'subject'},'summary':{'status':summary,'affected':len(good),'basis':'reported'},
                'facts':facts,'evidence':evidence,'limitations':['reported_state_only']}

    def _air(self, endpoint):
        rows=[{'key':r['entity']+':'+r.get('metric',''), 'kind':'room','label':r['label'],'metric':r.get('metric'),
               'policy':r.get('policy'),'fields':{'measurement':{'entity':r['entity']}}} for r in endpoint.get('air',[]) if r.get('metric') in ('score','co2','pm2_5','voc','nox')]
        used={(r['metric'],r['fields']['measurement']['entity']) for r in rows}
        for subject in endpoint.get('assistant',{}).get('subjects',[]):
            if subject.get('kind')!='room':continue
            for metric in ('score','co2','pm2_5','voc','nox'):
                field=subject.get('fields',{}).get(metric)
                if field and (metric,field['entity']) not in used:
                    rows.append({**subject,'metric':metric,'policy':None,'fields':{'measurement':field}})
                    used.add((metric,field['entity']))
        mapped={r['fields']['measurement']['entity'] for r in rows}
        return authorized_subjects(rows,mapped,self.permitted,self.state_get,self.installation_id,endpoint['id'],registry_id=self.registry_id,required_roles=('measurement',))

    def _air_answer(self, subjects, capability):
        import math
        import uuid
        from .semantics import assess, UNITS
        now=self.now(); facts=[]; evidence=[]; limitations=set(); evaluated=0
        for subject in subjects:
            eid=subject['fields']['measurement']['entity']; state=self.state_get(eid) if self.permitted(eid) else None
            attrs=getattr(state,'attributes',{}) if state else {}; reported=getattr(state,'last_reported',None); unit=attrs.get('unit_of_measurement',''); value=None
            try:
                n=float(state.state)
                if math.isfinite(n) and n>=0 and (subject['metric']!='score' or n<=100):value=n
            except (TypeError,ValueError,AttributeError):pass
            status='usable' if value is not None else 'missing'; freshness='unknown'
            if status=='usable' and (not unit or unit not in UNITS[subject['metric']]):status='invalid'; limitations.add('invalid_unit')
            if status=='usable' and (attrs.get('restored') or not self.verified_source(eid) or not reported or not reported.tzinfo):status='unverified'; limitations.add('unknown_freshness')
            elif status=='usable' and ((now-reported).total_seconds()>self.max_age or reported>now):status='stale'; freshness='stale'; limitations.add('stale')
            elif status=='usable':freshness='current'
            if status=='missing':limitations.add('missing_source')
            assessment='unknown'; policy=subject.get('policy'); policy_source='configured'; policy_levels=None; direction=None
            if capability.endswith('air_assessment') and status=='usable':
                if policy:
                    try:
                        result=assess([{'metric':subject['metric'],'value':value,'unit':unit,'available':True,'stale':False,'policy':policy}])
                        assessment=result['status']; policy_levels=[x[0] for x in policy['levels'] if x[0] is not None];direction='higher_is_better' if policy['higher_is_better'] else 'lower_is_better'
                    except ValueError:limitations.add('missing_policy')
                else:
                    candidates=[p for b in self.artifacts.values() for p in b['bindings']['policies']]
                    selected,reason=resolve_air_policy(subject['metric'],eid,unit,None,candidates,self.state_get,self.permitted)
                    if reason:limitations.add(reason)
                    else:
                        policy_levels=selected['levels']; policy_source=selected['source'];direction='higher_is_better' if selected['higher_is_better'] else 'lower_is_better'
                        a,b=policy_levels
                        assessment=('good' if value>=a else 'raised' if value>=b else 'high') if selected['higher_is_better'] else ('good' if value<a else 'raised' if value<b else 'high')
            usable=status=='usable' and (not capability.endswith('air_assessment') or assessment!='unknown')
            if status=='usable' and not usable:status='unverified'
            evaluated+=int(usable)
            fid=str(uuid.uuid5(uuid.UUID(subject['id']),subject['metric'])); evid=str(uuid.uuid5(uuid.UUID(fid),'evidence'))
            e={'id':evid,'sourceRef':str(uuid.uuid5(uuid.UUID(subject['id']),'measurement-source')),'observedAt':None,'reportedAt':reported.isoformat() if reported and reported.tzinfo else None,'freshnessBasis':'source_report' if freshness!='unknown' else 'unknown','maxAgeSeconds':self.max_age,'freshness':freshness}
            if assessment!='unknown':e['policy']={'id':'air-band','revision':1,'bounds':{f'level{i+1}':v for i,v in enumerate(policy_levels)},'direction':direction,'source':policy_source}
            evidence.append(e)
            wire_unit = 'µg/m³' if subject['metric']=='pm2_5' and unit in ('µg/m3','μg/m3') else '%' if subject['metric']=='score' and unit=='/100' else unit
            facts.append({'id':fid,'subjectId':subject['id'],'roomId':subject.get('roomId',subject['id']),'label':subject['label'],'metric':subject['metric'],'value':value if status=='usable' else None,'unit':wire_unit or None,'status':status,'assessment':assessment,'evidenceRefs':[evid]})
        order=['good','raised','high']; selected=max((f['assessment'] for f in facts if f['assessment'] in order),key=order.index,default='unknown')
        return {'version':1,'capability':capability,'revision':1,'answerType':'air.v1','asOf':now.isoformat(),
                'availability':'ready' if evaluated==len(facts) and facts else 'partial' if evaluated else 'unavailable',
                'coverage':{'eligible':len(facts),'evaluated':evaluated,'missing':len(facts)-evaluated},
                'summary':{'status':selected,'affected':sum(f['assessment']==selected for f in facts) if selected!='unknown' else 0},
                'facts':facts,'evidence':evidence,'limitations':sorted(limitations)}

    def _calendars(self, endpoint):
        rows = [{'key':eid,'kind':'calendar','label':'Calendar','fields':{'state':{'entity':eid}}} for eid in endpoint.get('calendars', [])]
        used=set(endpoint.get('calendars', []))
        if endpoint.get('assistant',{}).get('calendar_sensitive') is True:
            for row in endpoint['assistant'].get('subjects',[]):
                if row.get('kind')=='calendar' and row.get('fields',{}).get('state',{}).get('entity') not in used:
                    rows.append(row)
        mapped={v['entity'] for row in rows for v in row['fields'].values()}
        return authorized_subjects(rows, mapped, self.permitted, self.state_get, self.installation_id, endpoint['id'], registry_id=self.registry_id,required_roles=('state',))

    def _players(self, endpoint):
        rows = [r for r in endpoint.get('assistant', {}).get('subjects', []) if r.get('kind') == 'player' and 'state' in r.get('fields', {})]
        used = {r['fields']['state']['entity'] for r in rows}
        legacy = endpoint.get('studio', {}).get('media', {})
        if legacy.get('entity') and legacy['entity'] not in used:
            rows.append({'key':legacy['entity'],'kind':'player','label':'Media','fields':{'state':{'entity':legacy['entity']}}})
        mapped = {v['entity'] for row in rows for v in row['fields'].values()}
        return authorized_subjects(rows, mapped, self.permitted, self.state_get, self.installation_id, endpoint['id'],registry_id=self.registry_id,required_roles=('state',))

    def _media_answer(self, subjects, capability):
        import math
        import uuid
        now = self.now(); facts=[]; evidence=[]
        for subject in subjects:
            entity=subject['fields']['state']['entity']; state=self.state_get(entity) if self.permitted(entity) else None
            attrs=getattr(state,'attributes',{}) if state else {}
            raw=getattr(state,'state','unknown'); playback=raw if raw in ('playing','paused','buffering','idle','off') and not attrs.get('restored') else 'unknown'
            fid=str(uuid.uuid5(uuid.UUID(subject['id']),'media')); evid=str(uuid.uuid5(uuid.UUID(fid),'evidence'))
            evidence.append({'id':evid,'sourceRef':str(uuid.uuid5(uuid.UUID(subject['id']),'state-source')),'observedAt':None,'reportedAt':None,'freshnessBasis':'reported_state','maxAgeSeconds':None,'freshness':'unknown'})
            row={'id':fid,'subjectId':subject['id'],'label':subject['label'],'state':playback,'metadataStatus':'current' if playback in ('playing','paused','buffering') else 'last_known' if playback in ('idle','off') and any(attrs.get(k) for k in ('media_title','media_artist','media_album_name')) else 'unknown','evidenceRefs':[evid]}
            for source,target in (('media_title','title'),('media_artist','artist'),('media_album_name','album'),('source','source')):
                value=attrs.get(source)
                if isinstance(value,str) and value:row[target]=value[:300]
            volume=attrs.get('volume_level')
            if type(volume) in (int,float) and math.isfinite(volume) and 0<=volume<=1:row['volumePercent']=round(volume*100)
            if type(attrs.get('is_volume_muted')) is bool:row['muted']=attrs['is_volume_muted']
            facts.append(row)
        good=[x for x in facts if x['state']!='unknown']; values={x['state'] for x in good}
        status=next(iter(values)) if len(values)==1 else 'mixed' if len(values)>1 else 'unknown'
        return {'version':1,'capability':capability,'revision':1,'answerType':'media.v1','asOf':now.isoformat(),
                'availability':'ready' if len(good)==len(facts) and facts else 'partial' if good else 'unavailable',
                'coverage':{'eligible':len(facts),'evaluated':len(good),'missing':len(facts)-len(good),'unit':'subject'},
                'summary':{'status':status,'affected':len(good)},'facts':facts,'evidence':evidence,'limitations':['reported_state_only']}

    def _lights(self, endpoint):
        manual = endpoint.get('assistant', {}).get('subjects', [])
        rows = [r for r in manual if r.get('kind') == 'light' and 'state' in r.get('fields', {})]
        used = {r['fields']['state']['entity'] for r in rows}
        for row in endpoint.get('studio', {}).get('lights', []):
            if row['entity'] not in used:
                rows.append({'key': row['entity'], 'kind': 'light', 'label': row['label'], 'fields': {'state': {'entity': row['entity']}}})
        mapped = {source['entity'] for row in rows for source in row['fields'].values()}
        return authorized_subjects(rows, mapped, self.permitted, self.state_get, self.installation_id, endpoint['id'],registry_id=self.registry_id,required_roles=('state',))

    def _light_answer(self, subjects, requested='all'):
        import uuid
        now = self.now()
        facts, evidence = [], []
        limitations = {'reported_state_only'}
        for subject in subjects:
            source = subject['fields']['state']['entity']
            state = self.state_get(source) if self.permitted(source) else None
            attrs = getattr(state, 'attributes', {}) if state else {}
            raw = getattr(state, 'state', 'unknown')
            status = raw if raw in ('on','off') and not attrs.get('restored') else 'unknown'
            fid = str(uuid.uuid5(uuid.UUID(subject['id']), 'light'))
            evid = str(uuid.uuid5(uuid.UUID(fid), 'evidence'))
            evidence.append({'id':evid, 'sourceRef':str(uuid.uuid5(uuid.UUID(subject['id']), 'state-source')),
                             'observedAt':None, 'reportedAt':None, 'freshnessBasis':'reported_state', 'maxAgeSeconds':None, 'freshness':'unknown'})
            group = isinstance(attrs.get('entity_id'), (list, tuple)) or subject.get('limits', {}).get('count_unit') != 'bulb'
            row = {'id':fid, 'subjectId':subject['id'], 'label':subject['label'], 'state':status,
                   'countUnit':'group' if group else 'bulb', 'reachability':'unknown', 'evidenceRefs':[evid]}
            brightness = attrs.get('brightness')
            if status == 'on' and type(brightness) in (int,float) and 0 <= brightness <= 255:
                row['brightnessPercent'] = round(brightness * 100 / 255)
            facts.append(row)
        good = [f for f in facts if f['state'] != 'unknown']
        units = {f['countUnit'] for f in facts}
        values = {f['state'] for f in good}
        summary = next(iter(values)) if len(values) == 1 and len(units) == 1 else 'mixed' if len(values) > 1 and len(units) == 1 else 'unknown'
        return {'version':1, 'capability':'lights.status', 'revision':1, 'answerType':'lights.v1', 'asOf':now.isoformat(),
                'availability':'ready' if len(good)==len(facts) and facts else 'partial' if good else 'unavailable',
                'coverage':{'eligible':len(facts),'evaluated':len(good),'missing':len(facts)-len(good),'unit':'subject'},
                'summary':{'status':summary,'affected':len(good) if summary != 'unknown' else 0},
                'facts':[f for f in facts if requested == 'all' or f['state'] == requested], 'evidence':evidence,
                'limitations':sorted(limitations)}

    def _temperature(self, endpoint, subjects, arguments):
        import math
        import uuid
        now = self.now()
        facts, evidence, limitations = [], [], set()
        for subject in subjects:
            binding = subject['fields']['temperature']
            source = binding['entity']
            max_age = binding.get('maxAgeSeconds', self.max_age)
            state = self.state_get(source) if self.permitted(source) else None
            attrs = getattr(state, 'attributes', {}) if state else {}
            reported = getattr(state, 'last_reported', None)
            value, status, unit, freshness = None, 'missing', attrs.get('unit_of_measurement', ''), 'unknown'
            try:
                number = float(attrs.get(binding['attribute']) if 'attribute' in binding else state.state)
                if math.isfinite(number):
                    value = number
                    status = 'usable'
            except (AttributeError, TypeError, ValueError):
                pass
            if status == 'usable' and unit not in ('°C', '°F'):
                status = 'invalid'; limitations.add('invalid_unit')
            if status == 'usable' and unit == '°F':
                value = (value - 32) * 5 / 9; unit = '°C'
            if status == 'usable' and (attrs.get('restored') or not self.verified_source(source) or reported is None or reported.tzinfo is None):
                status = 'unverified'; limitations.add('unknown_freshness')
            elif status == 'usable':
                if (now - reported).total_seconds() > max_age or reported > now:
                    status = 'stale'; freshness = 'stale'; limitations.add('stale')
                else:
                    freshness = 'current'
            if status == 'missing':
                limitations.add('missing_source')
            fid = str(uuid.uuid5(uuid.UUID(subject['id']), 'temperature'))
            evid = str(uuid.uuid5(uuid.UUID(fid), 'evidence'))
            evidence.append({'id': evid, 'sourceRef': str(uuid.uuid5(uuid.UUID(subject['id']), 'temperature-source')),
                             'observedAt': None, 'reportedAt': reported.isoformat() if reported and reported.tzinfo else None,
                             'freshnessBasis': 'source_report' if freshness != 'unknown' else 'unknown',
                             'maxAgeSeconds': max_age, 'freshness': freshness})
            facts.append({'id': fid, 'subjectId': subject['id'], 'roomId': subject.get('roomId',subject['id']), 'label': subject['label'],
                          'indoor': subject.get('indoor', True), 'metric': 'temperature', 'value': value if status == 'usable' else None,
                          'unit': unit or None, 'status': status, 'evidenceRefs': [evid]})
        evaluated = sum(f['status'] == 'usable' for f in facts)
        total = len(facts)
        return {'version': 1, 'capability': 'climate-air.temperature', 'revision': 1, 'answerType': 'temperature.v1',
                'asOf': now.isoformat(), 'availability': 'ready' if evaluated == total and total else 'partial' if evaluated else 'unavailable',
                'coverage': {'eligible': total, 'evaluated': evaluated, 'missing': total - evaluated},
                'summary': {'status': 'measured' if evaluated else 'unknown', 'affected': evaluated},
                'facts': facts, 'evidence': evidence, 'limitations': sorted(limitations)}
