"""Export closed, fictional protocol examples for native consumers."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
CAPS = ['lights.turn_on','lights.turn_off','covers.open','covers.close','covers.stop','awnings.extend','awnings.retract','awnings.stop']
REASONS = ['command_sent','state_observed','motion_observed','stale_revision','access_denied','unavailable','unsupported','wind_block','partial_scope','partial_failure','service_error','outcome_unknown','request_conflict','ledger_full','storage_failed','invalid_request']


def build():
    """Run production negotiation/action handlers; never hand-edit result payloads."""
    import asyncio
    from test_control_api import api_rig
    from test_controls import module
    api=module('control_api');cases={}
    def rig():
        r,h,c,grant=api_rig()
        r.engine.data['epoch']='11111111-1111-4111-8111-111111111111'
        return r,h,c,grant
    def negotiate(env,key):
        r,h,c,grant=env
        req={'id':10,'type':'domosenso_frame/control_targets','version':1,'endpoint':'frame','deviceId':'tablet','supported':[{'actionType':'control-action.v1','resultType':'control-result.v1'}]}
        api.targets(h,c,req,grant,'read','control');assert not c.errors
        cases[key]={'request':req,'response':copy.deepcopy(c.results[-1])}
    def execute(env,key,cap='lights.turn_on',scope='subject',change=None,choice=False):
        r,h,c,grant=env;req=r.request(cap,scope=scope)|{'id':11,'type':'domosenso_frame/control_action'}
        if choice:req['scopeChoice']='configured'
        if change:change(r)
        asyncio.run(api.action(h,c,req,grant,'read','control'));assert not c.errors
        cases[key]={'request':req,'response':copy.deepcopy(c.results[-1])}
    env=rig();env[0].enable();negotiate(env,'targets');execute(env,'accepted')
    env=rig();r=env[0];r.add('cover.shade','closed',area='office',device_class='shade',supported_features=11)
    r.endpoint['studio']['covers']=[{'entity':'cover.shade','label':'Office shade'}];r.enable()
    negotiate(env,'coverTargets');execute(env,'coverAccepted','covers.open')
    env=rig();r=env[0];r.compiled();r.enable();negotiate(env,'awningTargets');execute(env,'awningAccepted','awnings.extend')
    env=rig();env[0].compiled();env[0].enable()
    execute(env,'windBlock','awnings.extend',change=lambda r:setattr(r.states['binary_sensor.awnings_wind'],'state','on'))
    env=rig();r=env[0];r.areas['other']={'name':'Office','aliases':['Study']};r.add('light.other','off',area='other')
    r.endpoint['studio']['lights'].append({'entity':'light.other','label':'Desk'});r.enable();negotiate(env,'ambiguity')
    env=rig();r=env[0];r.add('light.hidden','off',area='office');r.enable();negotiate(env,'partialScope')
    execute(env,'partialScopeDenied',scope='room');execute(env,'partialScopeChosen',scope='room',choice=True)
    env=rig();env[0].enable();execute(env,'staleRevision',change=lambda r:r.registry['light.desk'].update(id='replaced'))
    env=rig();r=env[0];r.add('light.second','off',area='office');r.endpoint['studio']['lights'].append({'entity':'light.second','label':'Second'});r.enable()
    env[2].user.permissions.check_entity=lambda e,p:e not in r.denied
    r.on_call=lambda:r.denied.add('light.second');execute(env,'partialDispatch',scope='room')
    env=rig();r=env[0];r.enable()
    async def uncertain(*args,**kwargs):raise TimeoutError('Provider did not finish')
    env[1].services.async_call=uncertain;execute(env,'unknown')
    from test_controls import cover_group
    env=rig();cover_group(env[0]);negotiate(env,'coverGroupTargets');execute(env,'coverGroupAccepted','covers.open',scope='room')
    env=rig();cover_group(env[0])
    execute(env,'coverGroupExcluded','covers.open',change=lambda r:r.states['cover.two'].attributes.update(device_class='garage'))
    env=rig();env[0].compiled();env[0].enable()
    execute(env,'missingWindRetract','awnings.retract',change=lambda r:r.registry.pop('binary_sensor.awnings_wind'))
    env=rig();env[0].compiled();env[0].enable()
    execute(env,'missingWindExtend','awnings.extend',change=lambda r:r.registry.pop('binary_sensor.awnings_wind'))
    return {'version':1,'proof':{'transport':'HA authenticated WebSocket','pairing':['begin(publicKey, endpoint, name)','finish(signature of challenge)','administrator approve(deviceId, code)'],
        'binding':'deviceId must equal the approved proof on this same authenticated connection; reconnect requires a new begin/finish proof',
        'fixtureDeviceId':'tablet','fixtureEndpoint':'frame','verification':'tests/controls_ha_smoke.py exercises P-256 pairing and these registered routes on fresh HA'},'cases':cases}


def schema():
    string={'type':'string','minLength':1,'maxLength':256}
    def obj(props): return {'type':'object','additionalProperties':False,'required':list(props),'properties':props}
    def const(v): return {'const':v}
    def arr(v): return {'type':'array','maxItems':256,'items':v}
    cap={'enum':CAPS}; rev={'type':'integer','const':1}; label={'oneOf':[string,{'type':'object','required':['en'],'additionalProperties':string}]}
    capability=obj({'id':cap,'revision':rev}); room=obj({'id':string,'label':label,'aliases':arr(string)})
    target=obj({'id':string,'revision':string,'kind':{'enum':['light','cover','awning']},'scope':{'enum':['subject','room']},'label':label,'aliases':arr(string),'room':{'oneOf':[room,{'type':'null'}]},'capabilities':arr(capability),'partialScope':{'type':'boolean'},'memberIds':arr(string),'limitations':arr(string)})
    base={'id':{'type':'integer'},'type':const('domosenso_frame/control_targets'),'version':rev,'endpoint':string,'deviceId':string}
    negotiate=obj({**base,'supported':arr(obj({'actionType':string,'resultType':string}))})
    request=obj({**base,'type':const('domosenso_frame/control_action'),'capability':cap,'revision':rev,'targetId':string,'targetRevision':string,'requestId':string})
    request['properties']['scopeChoice']={'const':'configured'}
    statuses={'enum':['accepted','confirmed','moving','failed','unknown']}; reason={'enum':REASONS}
    result=obj({'version':rev,'resultType':const('control-result.v1'),'requestId':string,'capability':cap,'revision':rev,'targetId':string,'targetRevision':string,'status':statuses,'reason':reason,'outcomes':arr(obj({'targetId':string,'status':statuses,'reason':reason,'assumedState':{'type':'boolean'}}))})
    targets=obj({'version':rev,'actionType':const('control-action.v1'),'resultType':const('control-result.v1'),'requestIdPrefix':string,'expiresAt':string,'targets':arr(target)})
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','oneOf':[negotiate,request,targets,result]}

if __name__=='__main__':
    (ROOT/'tests/fixtures/shared-controls.json').write_text(json.dumps(build(),indent=2)+'\n')
    (ROOT/'custom_components/domosenso_frame/control-wire.schema.json').write_text(json.dumps(schema(),indent=2)+'\n')
