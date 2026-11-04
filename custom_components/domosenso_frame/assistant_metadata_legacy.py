"""Translate authorized v1 evaluators to generic v2 rows during migration."""

from datetime import date, datetime
import math
import re

from .assistant_contract import ROUTES

SUPPORTED_PAIRS = tuple({'queryType': query, 'answerType': answer}
                        for query, answer in sorted(set(ROUTES.values()) | {
                            ('component-query.v1', 'component-description.v1')}))

# Existing v1 argument contracts are owned by the bridge. No Android dispatch table is needed.
AREA = frozenset({
    'climate-air.temperature', 'climate-air.humidity', 'climate-air.status', 'climate-air.air_readings',
    'climate-air.air_assessment', 'plants.watering_need', 'plants.status', 'media.now_playing', 'media.status',
    'lights.status', 'bermuda-presence.observations', 'awnings.status', 'audio-sensor.level',
})
PLANT = frozenset({'plants.watering_need', 'plants.status', 'plants.last_watered', 'plants.water_in'})
SUBJECT = PLANT | frozenset({
    'media.now_playing', 'media.status', 'media.mains', 'lights.status', 'calendar.agenda', 'weather.current',
    'weather.forecast', 'bermuda-presence.observations', 'awnings.status', 'awnings.wind', 'audio-sensor.level',
    'backup.status', 'ramka.status', 'timers.status',
})
REQUIRE_SUBJECT = frozenset({'plants.last_watered', 'plants.water_in'})

ANCHORS = {
    'climate-air.temperature': (
        'temperature|how warm|how cold|how hot|degrees|warm|cold|heat',
        'temperatura|temperaturę|temperatury|temperaturze|ile stopni|stopni|ciepło|zimno'),
    'climate-air.humidity': (
        'humidity|humid|moisture in the air',
        'wilgotność powietrza|wilgotność|wilgotności|wilgotno|wilgotne'),
    'climate-air.status': ('humidity alert|humidity warning|temperature difference',
                           'alarm wilgotności|ostrzeżenie o wilgotności|różnica temperatur'),
    'climate-air.air_readings': (
        'air readings|air measurements|air sensor readings|co2|carbon dioxide|particulates|particulate matter|voc|nox|pm2|pm2 5|pm 2 5',
        'pomiary powietrza|odczyty powietrza|co2|dwutlenek węgla|dwutlenku węgla|pył|pyłu|cząstki|cząstek|voc|nox|pm2|pm2 5|pm 2 5'),
    'climate-air.air_assessment': (
        'air quality|is the air good|air|stuffy|ventilation|breathing',
        'jakość powietrza|jakie mamy powietrze|powietrze|powietrza|powietrzem|duszno|wietrzyć|oddycha|oddychać'),
    'plants.watering_need': (
        'need water|need watering|water the plants|needs watering|needs water|watering|thirsty|should water|water|need a drink|due for water|time to give',
        'podlać|wymaga podlewania|wymagają podlewania|potrzebuje wody|potrzebują wody|podlewania|podlania|wody|brakuje wody|podlanie|potrzebują'),
    'plants.status': (
        'plant condition|plants reporting problems|plants|plant|houseplants|houseplant|plant health|condition of|doing|wrong with|what condition',
        'stan roślin|rośliny|roślinom|roślina|roślin|roślinie|roślinami|kwiaty|kwiatów|dolega|stan|w jakim stanie|w jakiej kondycji|jak się ma|w porządku'),
    'plants.last_watered': (
        'last watered|watering detected|last had water|last record watering|was watered|watered|last watering|previous watering|last water|watered last|register watering|detected watering|last detect watering',
        'ostatnio podlano|wykryto podlewanie|była podlewana|podlano|podlewano|podlana|podlanie|podlewanie|podlewana|ostatnio podlana|ostatnie podlewanie|po raz ostatni|ostatniego podlewania|ostatni raz|podlał'),
    'plants.water_in': (
        'when might|how soon|when will this plant|when will|how long before|another watering|next watering|for its next watering|need watering again|until watering|wait for water|how long until|when do you expect|next water',
        'za ile|kiedy może wymagać|kiedy będzie potrzebować|kiedy podlać|kiedy podlewać|następne podlewanie|kiedy znów|poczekać na podlewanie|ile dni|termin podlewania|bez podlewania|znów trzeba|kolejnego podlewania|szacunkowy termin|znowu potrzebować'),
    'media.now_playing': ('now playing|what is playing|what music|song', 'co gra|co leci|jaki utwór|odtwarzana muzyka'),
    'media.status': ('player status|playback status', 'stan odtwarzacza|status odtwarzacza'),
    'media.mains': ('player power|player voltage|power does the player', 'moc odtwarzacza|napięcie odtwarzacza|moc zgłasza odtwarzacz'),
    'lights.status': ('lights|lamps', 'światła|światło|lampy'),
    'calendar.agenda': ('calendar|agenda|appointments|appointment|events|event|meeting|meetings|schedule|scheduled|plans|booked',
                        'kalendarz|kalendarzu|kalendarza|wydarzenia|wydarzenie|wydarzeń|spotkania|spotkanie|spotkań|planie|plany|terminy|zaplanowano|zaplanowanego|zaplanowane'),
    'weather.current': ('weather now|current weather|weather|conditions outside|conditions outdoors|outside|raining|outdoor weather',
                        'pogoda teraz|obecna pogoda|pogoda|pogodę|pogody|na dworze|na zewnątrz|pada|raport pogodowy|pogodowy|warunki pogodowe|za oknem'),
    'weather.forecast': ('forecast|will it rain|when will it rain|weather tomorrow|tomorrow weather|weather this week|rain|outlook|forecasted|supposed to be|outdoor weather forecast',
                         'prognoza|prognozę|prognozy|będzie padać|pogoda jutro|jutro pogoda|pogody na jutro|deszcz|padać|zapowiada|zapowiadają'),
    'bermuda-presence.observations': ('presence|presence sensor', 'obecność|czujnik obecności'),
    'awnings.status': ('awning status|awning|awnings', 'markizy|markiza|stan markizy'),
    'awnings.wind': ('awning wind|wind alarm', 'alarm wiatrowy|alarm wiatru|markizie grozi wiatr'),
    'audio-sensor.level': ('sound level|noise level', 'poziom dźwięku|poziom hałasu|jaki poziom'),
    'backup.status': ('backup|backups', 'kopia zapasowa|kopie zapasowe|backup'),
    'ramka.status': ('frame mode|frame status|mode is the frame', 'tryb ramki|stan ramki|status ramki'),
    'ramka.current_time': ('what time|current time|time is it|clock|local time|hour|the time|time check',
                            'która godzina|jaka jest godzina|która jest|godzina|godzinę|godzinę mamy|zegar|zegarze|zegara|aktualny czas|bieżący czas'),
    'timers.status': ('timer status|time left|time remaining|how much time|timer|countdown|remaining time|how long until|counting down|timers',
                      'stan minutnika|ile zostało|ile czasu|pozostało|minutnik|minutnika|minutniku|odliczanie|odliczania|ile jeszcze czasu|będzie odliczał'),
}
TERMS = {
    'measured': ('HA reports measurements', 'HA podaje pomiary'),
    'good': ('Good under the configured policy', 'Dobra według ustawionej reguły'),
    'raised': ('Elevated under the configured policy', 'Podwyższona według ustawionej reguły'),
    'high': ('High under the configured policy', 'Wysoka według ustawionej reguły'),
    'needs_water': ('Watering needed', 'Potrzebne podlewanie'),
    'within_band': ('Within the configured range', 'W ustawionym zakresie'),
    'too_wet': ('Excess moisture', 'Zbyt duża wilgotność'),
    'no_reported_alarm': ('No alarm reported by HA', 'HA nie zgłasza alarmu'),
    'wind_alarm': ('Wind alarm reported', 'Zgłoszono alarm wiatru'),
    'none': ('No events in the selected period', 'Brak wydarzeń w wybranym okresie'),
    'on': ('On', 'Włączone'), 'off': ('Off', 'Wyłączone'),
    'playing': ('Playing', 'Odtwarzanie'), 'paused': ('Paused', 'Wstrzymane'),
    'active': ('Active', 'Aktywny'), 'idle': ('Idle', 'Nieaktywny'),
    'extended': ('Extended', 'Wysunięta'), 'retracted': ('Retracted', 'Wsunięta'),
    'sunny': ('Sunny', 'Słonecznie'), 'rainy': ('Rain', 'Deszcz'),
    'cloudy': ('Cloudy', 'Pochmurno'), 'snowy': ('Snow', 'Śnieg'),
    'low': ('Low', 'Niska wartość'), 'normal': ('Normal', 'W normie'),
    'unknown': ('Unknown', 'Nieznane'), 'reported': ('Reported by HA', 'Zgłoszone przez HA'),
    'forecast': ('Forecast', 'Prognoza'), 'estimated': ('Estimate', 'Szacunek'),
    'last_known': ('Last known', 'Ostatnio znane'), 'unavailable': ('Unavailable', 'Niedostępne'),
}
LIMITATIONS = {
    'stale': ('Some readings are stale.', 'Część odczytów jest nieaktualna.'),
    'missing_source': ('Some data is missing.', 'Brakuje części danych.'),
    'unknown_freshness': ('Freshness has not been verified.', 'Aktualność danych jest niepotwierdzona.'),
    'invalid_unit': ('A measurement unit is unsupported.', 'Jednostka pomiaru jest nieobsługiwana.'),
    'missing_policy': ('Assessment rules are missing.', 'Brakuje reguł oceny.'),
    'conflicting_policy': ('Assessment rules conflict.', 'Reguły oceny są sprzeczne.'),
    'provider_sync_unknown': ('Provider synchronization time is unknown.', 'Czas synchronizacji dostawcy jest nieznany.'),
    'reported_state_only': ('This is the state reported by HA, without physical confirmation.', 'To stan zgłoszony przez HA bez potwierdzenia fizycznego.'),
    'history_coverage_unknown': ('History coverage is unverified.', 'Kompletność historii jest niepotwierdzona.'),
    'provider_history_unknown': ('Provider history coverage is unknown.', 'Kompletność historii dostawcy jest nieznana.'),
    'watering_history_unknown': ('Watering history is unknown.', 'Historia podlewania jest nieznana.'),
    'truncated': ('The results are truncated.', 'Wyniki zostały ucięte.'),
    'no_live_status_provider': ('Live status was not assessed.', 'Nie sprawdzono bieżącego stanu.'),
}
TEXT_FIELDS = {
    'purpose': ('Description', 'Opis'), 'title': ('Title', 'Tytuł'), 'artist': ('Artist', 'Wykonawca'),
    'album': ('Album', 'Album'), 'source': ('Source', 'Źródło'), 'condition': ('Conditions', 'Warunki'),
    'state': ('State', 'Stan'), 'alarm': ('Alarm', 'Alarm'), 'mode': ('Mode', 'Tryb'),
    'page': ('View', 'Widok'), 'theme': ('Theme', 'Motyw'), 'reachability': ('Reachability', 'Dostępność'),
    'alertCode': ('Alert', 'Alarm'), 'assessment': ('Assessment', 'Ocena'),
    'wateringAssessment': ('Watering', 'Podlewanie'), 'metadataStatus': ('Metadata', 'Metadane'),
    'positionBasis': ('Position evidence', 'Podstawa pozycji'), 'liveStatus': ('Live status', 'Bieżący stan'),
    'physicalDisplayState': ('Physical display state', 'Stan fizyczny ekranu'),
    'basis': ('Basis', 'Podstawa'), 'weighting': ('Weighting', 'Ważenie'),
    'observationKind': ('Observation type', 'Rodzaj obserwacji'), 'verification': ('Verification', 'Weryfikacja'),
}
NUMERIC_FIELDS = {
    'brightnessPercent': ('Brightness', 'Jasność', '%'), 'positionPercent': ('Position', 'Pozycja', '%'),
    'volumePercent': ('Volume', 'Głośność', '%'), 'archiveCount': ('Archives', 'Archiwa', ''),
    'newestAgeHours': ('Age of newest archive', 'Wiek ostatniego archiwum', 'h'),
    'sizeMB': ('Size', 'Rozmiar', 'MB'), 'sizeMiB': ('Size', 'Rozmiar', 'MiB'),
    'checkDays': ('Assessment window', 'Okres oceny', 'd'),
}
TIME_FIELDS = {
    'instant': ('Time', 'Godzina'), 'start': ('Start', 'Początek'), 'end': ('End', 'Koniec'),
    'eventAt': ('Detected at', 'Wykryto o'), 'lastHeardAt': ('Last report', 'Ostatnie zgłoszenie'),
    'deadline': ('Deadline', 'Termin'), 'validFrom': ('Forecast for', 'Prognoza na'),
    'observedAt': ('Observed at', 'Czas obserwacji'),
}
UNITS = frozenset({'°C','°F','%','ppm','µg/m³','μg/m³','ug/m3','µg/m3','μg/m3','dB','dB(A)','dBFS','W','V','lx',
                   'index','d','h','s','MB','MiB','hPa','mbar','mm','in','km/h','m/s','mph','°','µS/cm','mS/cm'})


BOOL_FIELDS = {
    'detected': ('Detected', 'Wykryto'), 'calibrated': ('Calibrated', 'Skalibrowany'),
    'online': ('Online', 'Dostępny'), 'allDay': ('All day', 'Cały dzień'),
    'ongoing': ('Ongoing', 'W toku'), 'radarWakeEnabled': ('Radar wake', 'Wybudzanie radarem'),
}
DURATIONS = {
    'remainingSeconds': ('Remaining', 'Pozostało'), 'durationSeconds': ('Duration', 'Czas trwania'),
    'integrationWindowSeconds': ('Integration window', 'Okno pomiaru'),
}
METRICS = {
    'temperature': ('Temperature', 'Temperatura'), 'precipitation': ('Precipitation', 'Opady'),
    'precipitation_probability': ('Rain probability', 'Prawdopodobieństwo opadów'),
    'pressure': ('Pressure', 'Ciśnienie'), 'humidity': ('Humidity', 'Wilgotność'),
    'wind_speed': ('Wind speed', 'Prędkość wiatru'), 'wind_gust': ('Wind gust', 'Porywy wiatru'),
    'moisture': ('Soil moisture', 'Wilgotność podłoża'), 'brightness': ('Light level', 'Natężenie światła'),
}

def local(en, pl):
    return {'en': en, 'pl': pl}


def _clean(value, length=120):
    return re.sub(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]', ' ', value).strip()[:length]


def _localized(value, fallback, length=120):
    if isinstance(value, str):
        value = {'en': value}
    safe = {lang: cleaned for lang in ('en', 'de', 'pl')
            if isinstance(value, dict) and isinstance(item := value.get(lang), str) and (cleaned := _clean(item, length))}
    return {'en': fallback, **safe}


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,160}', value)


OPTION_VALUES = {
    'period': {'today': ('today', 'dzisiaj|dziś|dzisiejsze|dzisiejszego'), 'tomorrow': ('tomorrow', 'jutro'),
               'next_7_days': ('this week|next seven days|next 7 days|coming seven days|seven days ahead',
                               'w tym tygodniu|najbliższe siedem dni|najbliższych siedem dni|najbliższych siedmiu dni|najbliższych siedmiu dniach|kolejne siedem dni|następne siedem dni|najbliższy tydzień')},
    'filter': {'on': ('on', 'włączone'), 'off': ('off', 'wyłączone'), 'unknown': ('unknown', 'nieznane')},
    'metric': {'score': ('score', 'wskaźnik'), 'co2': ('co2|carbon dioxide', 'co2|dwutlenek węgla|dwutlenku węgla'),
               'pm2_5': ('pm2 5|pm 2 5|particulates|particulate matter', 'pm2 5|pm 2 5|pył|pyłu'), 'voc': ('voc', 'voc'), 'nox': ('nox', 'nox'),
               'power': ('power|watts', 'moc|waty'), 'voltage': ('voltage|volts', 'napięcie|wolty')},
    'nextOnly': {'yes': ('next event|next appointment|next meeting|appointment comes next|meeting comes next', 'następne wydarzenie|następne spotkanie|najbliższe wydarzenie|najbliższe spotkanie|spotkanie jest następne')},
}


def _options(identifier):
    keys = {}
    if identifier in ('calendar.agenda', 'weather.forecast'):
        keys['period'] = tuple(OPTION_VALUES['period'])
    if identifier == 'calendar.agenda':
        keys['nextOnly'] = ('yes',)
    if identifier in ('climate-air.air_readings', 'climate-air.air_assessment'):
        keys['metric'] = ('score', 'co2', 'pm2_5', 'voc', 'nox')
    if identifier == 'media.mains':
        keys['metric'] = ('power', 'voltage')
    if identifier == 'lights.status':
        keys['filter'] = tuple(OPTION_VALUES['filter'])
    options = [{'key': key, 'kind': 'choice', 'values': [
        {'value': value, 'aliases': {lang: phrases.split('|') for lang, phrases in
                                   zip(('en', 'pl'), OPTION_VALUES[key][value])}} for value in values
    ]} for key, values in keys.items()]
    if 'period' in keys:
        options.append({'key': 'date', 'kind': 'date'})
    return options


def describe(cap):
    identifier = cap['id']
    pair = (cap['queryType'], cap['answerType'])
    expected = ('component-query.v1', 'component-description.v1') if identifier.endswith('.explain') else ROUTES.get(identifier)
    if pair != expected:
        raise ValueError('Unsupported v1 route')
    title = _localized(cap['title'], 'Home Assistant')
    phrases = ANCHORS.get(identifier, (title['en'].lower(), title.get('pl', title['en']).lower()))
    anchors = {'en': phrases[0].split('|'), 'pl': phrases[1].split('|')}
    selectors = {}
    for subject in cap['subjects']:
        if identifier in AREA and _identifier(subject.get('roomId')):
            selectors.setdefault(subject['roomId'], {'id': subject['roomId'], 'type': 'area',
                                 'label': _localized(subject.get('roomLabel') or subject['label'], 'Area'), 'aliases': {}})
        if identifier in SUBJECT and _identifier(subject.get('id')):
            selectors.setdefault(subject['id'], {'id': subject['id'], 'type': 'subject',
                                 'label': _localized(subject['label'], 'Source'), 'aliases': {}})
    examples = {lang: [_clean(item, 240).replace('{', '').replace('}', '') for item in values[:8]
                       if isinstance(item, str) and _clean(item)] for lang, values in cap['examples'].items()
                if lang in ('en', 'de', 'pl') and isinstance(values, list)}
    examples = {lang: values for lang, values in examples.items() if values}
    if identifier == 'ramka.current_time':
        examples.update(en=['Which hour is it?', 'What time is it?', 'What is the time?'],
                        pl=['Która godzina?', 'Która jest godzina?', 'Jaka jest godzina?'])
    return {'id': identifier, 'revision': cap['revision'], 'operation': 'read', 'availability': cap['availability'],
            'title': title, 'description': _localized(cap['description'], title['en']),
            'anchors': anchors, 'examples': {'en': [title['en']], **examples},
            'arguments': sorted({row['type'] for row in selectors.values()}), 'answer': {'kind': 'records.v1'},
            'selectors': list(selectors.values()), 'requireSubject': identifier in REQUIRE_SUBJECT,
            'options': _options(identifier)}


def make_arguments(cap, descriptor, values):
    options = {row['key']: row for row in descriptor['options']}
    if not isinstance(values, dict) or set(values) - set(descriptor['arguments']) - set(options):
        raise ValueError('Invalid projected read arguments')
    if descriptor['requireSubject'] and 'subject' not in values:
        raise ValueError('Select a source')
    if {'area', 'subject'} <= values.keys() or {'date', 'period'} <= values.keys():
        raise ValueError('Select one scope and period')
    arguments = {}
    for key, value in values.items():
        if key in descriptor['arguments']:
            if not any(item['type'] == key and item['id'] == value for item in descriptor['selectors']):
                raise ValueError('Unknown projected selector')
            argument = 'roomId' if key == 'area' else 'plantId' if cap['id'] in PLANT else 'subjectId'
            arguments[argument] = value
        elif options[key]['kind'] == 'date':
            if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
                raise ValueError('Invalid projected date')
            arguments[key] = value
        elif any(item['value'] == value for item in options[key]['values']):
            arguments[key] = True if key == 'nextOnly' else value
        else:
            raise ValueError('Invalid projected option')
    return arguments


def _time(value):
    try:
        if len(value) == 10:
            return ('date', date.fromisoformat(value).isoformat())
        time = datetime.fromisoformat(value)
        if time.utcoffset() is not None:
            return ('timestamp', time.isoformat())
    except (ValueError, TypeError):
        pass
    return None


def _cells(fact):
    cells = []
    for key, (en, pl) in TEXT_FIELDS.items():
        value = fact.get(key)
        if isinstance(value, (str, dict)) and value:
            translated = local(*TERMS[value]) if isinstance(value, str) and value in TERMS else _localized(value, '', 240)
            if translated['en']:
                cells.append({'kind': 'text', 'label': local(en, pl), 'value': translated})
    for key, (en, pl) in TIME_FIELDS.items():
        value = fact.get(key)
        if isinstance(value, str) and (time := _time(value)):
            cells.append({'kind': 'clock' if key == 'instant' and time[0] == 'timestamp' else time[0],
                          'label': local(en, pl), 'value': time[1]})
    if fact.get('status') in (None, 'usable'):
        for key, (en, pl, unit) in {'value': ('Measurement', 'Pomiar', fact.get('unit')),
                                    'level': ('Level', 'Poziom', fact.get('unit')), **NUMERIC_FIELDS}.items():
            value = fact.get(key)
            if type(value) in (int, float) and math.isfinite(value) and (unit == '' or unit in UNITS):
                cells.append({'kind': 'measurement' if unit else 'number', 'label': local(en, pl), 'value': value,
                              **({'unit': unit} if unit else {})})
    else:
        cells.append({'kind': 'text', 'label': local('Reading', 'Odczyt'), 'value': local(*TERMS['unavailable'])})
    for key, (en, pl) in BOOL_FIELDS.items():
        if type(value := fact.get(key)) is bool:
            cells.append({'kind':'boolean','label':local(en, pl),'value':value})
    for key, (en, pl) in DURATIONS.items():
        if type(value := fact.get(key)) is int and 0 <= value <= 86400:
            cells.append({'kind':'duration','label':local(en, pl),'value':value})
    for measurement in fact.get('measurements', [])[:8]:
        unit, value = measurement.get('unit'), measurement.get('value')
        if unit in UNITS and type(value) in (int,float) and math.isfinite(value):
            heading = METRICS.get(measurement.get('metric'), ('Measurement', 'Pomiar'))
            cells.append({'kind':'measurement','label':local(*heading),'value':value,'unit':unit})
    for problem in fact.get('problems', [])[:6]:
        if isinstance(problem, str):
            problem = {'status': {'moisture_low': 'needs_water', 'moisture_high': 'too_wet'}.get(problem)}
        if isinstance(problem, dict) and problem.get('status') in TERMS:
            label = METRICS.get(problem.get('metric'), ('Assessment', 'Ocena'))
            cells.append({'kind':'text','label':local(*label),'value':local(*TERMS[problem['status']])})
    return cells[:16]


def project(result, capability):
    status = {'ready': 'complete', 'partial': 'partial', 'unavailable': 'unknown'}[result['availability']]
    code = result['summary']['status'] if status != 'unknown' else 'unavailable'
    evidence = {item['id']: item for item in result['evidence'] if _identifier(item.get('id'))}
    subjects = {item['id']: item['label'] for item in capability['subjects']}
    rows = []
    total_rows = 0
    for row in result['facts']:
        label = row.get('label') or row.get('name') or subjects.get(row.get('subjectId'))
        refs = [evidence[ref] for ref in row.get('evidenceRefs', []) if ref in evidence][:8]
        proof = [{key: item.get(key) for key in ('sourceRef','observedAt','reportedAt','freshness','freshnessBasis')}
                 for item in refs if _identifier(item.get('sourceRef'))]
        periods = row.get('periods') or ()
        total_rows += max(1, len(periods))
        if len(rows) >= 64:
            continue
        if periods:
            for index, period in enumerate(periods[:64-len(rows)]):
                rows.append({'id': f"{row['id']}.{index}", 'label': _localized(label, 'Home Assistant'),
                             'cells': _cells(period), 'evidence': proof})
        else:
            rows.append({'id': row['id'], 'label': _localized(label, 'Home Assistant'), 'cells': _cells(row),
                         'evidence': proof})
    coverage = result['coverage']
    unit = 'plant' if capability['id'] in PLANT else coverage.get('unit', 'subject')
    reasons = [local(*LIMITATIONS.get(key, ('The data has limitations.', 'Dane mają ograniczenia.')))
               for key in dict.fromkeys(result['limitations'])][:8]
    if total_rows > len(rows) and local(*LIMITATIONS['truncated']) not in reasons:
        reasons.append(local(*LIMITATIONS['truncated']))
    return {'facts': rows, 'coverage': {'eligible': coverage['eligible'], 'evaluated': coverage['evaluated'],
                                         'omitted': total_rows - len(rows), 'unit': unit},
            'assessment': {'status': status, 'headline': local(*TERMS[code]) if code in TERMS else
                           _localized(capability['title'], 'Home Assistant'), 'limitations': reasons}}
