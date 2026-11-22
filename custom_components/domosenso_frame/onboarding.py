"""Build the first endpoint from confirmed UI selections, independent of HA IO."""
from .studio_mappings import CLASSES, eligible, merge_patch

AIR_METRICS=('co2','pm2_5','voc','nox')

def build_endpoint(settings,states):
    name=settings.get('name','').strip()
    if not name or len(name)>80 or any(ord(c)<32 for c in name):raise ValueError('Invalid name')
    locale=settings.get('locale','en')
    if locale not in ('en','de','pl'):raise ValueError('Invalid locale')
    endpoint={'id':'panel','name':name,'dashboard':'/lovelace','temperature':[],'air':[], 'calendars':[],'display':{'locale':locale,'status':[]}}
    def selected(key,maximum):
        values=settings.get(key,[])
        if not isinstance(values,list) or len(values)>maximum or any(not isinstance(v,str) for v in values) or len(set(values))!=len(values):raise ValueError('Invalid selection')
        return values
    def label(eid):
        state=states.get(eid)
        if not state:raise ValueError('Source no longer exists')
        return ''.join(c for c in str(state.attributes.get('friendly_name') or eid) if ord(c)>=32)[:80] or eid
    rooms=[{'label':label(eid),'temperature':eid} for eid in selected('temperature',32)]
    air=[]
    for eid in selected('air',32):
        metric=next((metric for metric in AIR_METRICS if eligible(states.get(eid),metric)),None)
        if not metric:raise ValueError('Unsupported air source')
        air.append({'label':label(eid),'entity':eid,'metric':metric})
    patch={'rooms':rooms,'air':air,'calendars':selected('calendars',16)}
    if settings.get('weather'):patch['weather']=settings['weather']
    if settings.get('media'):patch['media']={'entity':settings['media']}
    if settings.get('radar_presence') or settings.get('radar_distance'):
        patch['radar']={key:settings['radar_'+key] for key in ('presence','distance','near_m','off_delay_s') if settings.get('radar_'+key) is not None}
    return merge_patch(endpoint,patch,states)

def endpoint_with_options(endpoint,options):
    result={key:value for key,value in endpoint.items() if key!='photo_albums'}
    if options.get('photo_albums_enabled'):
        entity,script=options.get('photo_albums_entity'),options.get('photo_albums_script')
        if not entity or not script:raise ValueError('Album sensor and script are required')
        result['photo_albums']={'entity':entity,'script':script}
    return result

def options_error(endpoint,options,validate):
    """None when the options give a valid endpoint under validate (the YAML endpoint schema), else the form error."""
    try:candidate=endpoint_with_options(endpoint,options)
    except ValueError:return 'albums_required'
    try:validate(candidate)
    except Exception:return 'invalid_albums'  # vol.Invalid and validators' ValueError/TypeError
    return None

def suggest_photo_albums(states):
    ids=set(states.async_entity_ids())
    pairs=[(eid,'script.'+eid[7:-13]+'_photo_albums_set') for eid in sorted(ids) if eid.startswith('sensor.') and eid.endswith('_photo_albums')]
    pairs=[pair for pair in pairs if pair[1] in ids]
    scripts=[eid for eid in ids if eid.startswith('script.') and eid.endswith('_photo_albums_set')]
    return pairs[0] if len(pairs)==1 and len(scripts)==1 else (None,None)
