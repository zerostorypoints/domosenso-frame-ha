"""Shared display configuration. Screen rotation is selected by HA, not the tablet."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError
_SCHEMA=json.loads((Path(__file__).parent/'ui/0.1.0/schema.json').read_text())

def validate_config(config):
    try:Draft202012Validator(_SCHEMA).validate(config)
    except ValidationError as error:raise ValueError('Invalid Frame display configuration') from error
    screens=[s['id'] for s in config['screens']];modes=[m['id'] for m in config['modes']]
    if len(set(screens))!=len(screens) or len(set(modes))!=len(modes):raise ValueError('Duplicate display identifiers')
    for s in config['screens']:
        ids=[w['id'] for w in s['widgets']]
        if len(set(ids))!=len(ids):raise ValueError('Duplicate widget identifiers')
    for mode in config['modes']:
        if any(s not in screens for s in mode['screens']):raise ValueError('Unknown display screen')
    return config

def selection(config,seconds,mode=None,page=None,capabilities=None):
    selected=next((m for m in config['modes'] if m['id']==mode),config['modes'][0])
    capabilities=set(['weather','temperature','air','status'] if capabilities is None else capabilities)
    eligible={s['id'] for s in config['screens'] if set(s.get('requires',[]))<=capabilities}
    pages=[p for p in selected['screens'] if p in eligible];duration=selected['rotationSeconds']
    if not pages:raise ValueError('No visible display screens')
    return selected['id'],page if page in pages else pages[int(seconds//duration)%len(pages)] if duration else pages[0]

def default_config(endpoint,locale='en'):
    labels={'en':['Home','Temperatures','Air quality'],'pl':['Dom','Temperatury','Powietrze'],'de':['Zuhause','Temperaturen','Luftqualität']}[locale]
    widgets=[{'id':'clock','type':'clock','size':'small'}];display=endpoint.get('display',{})
    if display.get('weather'):widgets.append({'id':'weather','type':'weather','size':'wide'})
    if display.get('status'):widgets.append({'id':'status','type':'status','size':'full'})
    screens=[{'id':'home','title':labels[0],'widgets':widgets}]
    for topic,label in [('temperature',labels[1]),('air',labels[2])]:
        if endpoint.get(topic):screens.append({'id':topic,'title':label,'widgets':[{'id':topic,'type':'measurements','topic':topic,'size':'full'}]})
    return {'version':1,'locale':locale,'screens':screens,'modes':[{'id':'ambient','screens':[s['id'] for s in screens],'rotationSeconds':20}]}
