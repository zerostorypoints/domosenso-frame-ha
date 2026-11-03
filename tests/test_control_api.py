import asyncio
from types import SimpleNamespace
import pytest
from test_controls import Rig,module,run


class Connection:
    def __init__(self):
        self.user=SimpleNamespace(id='user',is_active=True,permissions=SimpleNamespace(check_entity=lambda e,p:True))
        self.results=[];self.errors=[]
    def send_result(self,i,v):self.results.append(v)
    def send_error(self,i,c,m):self.errors.append((c,m))
    def context(self,msg):return SimpleNamespace(id='context')


def api_rig():
    r=Rig();c=Connection();h=SimpleNamespace(data={'domosenso_frame':{'controls':r.engine}},services=SimpleNamespace(async_call=None,has_service=lambda d,n:True))
    async def service(domain,name,data,blocking,context):await r.service(domain,name,data,context.id)
    h.services.async_call=service
    def grant(h,c):
        if not r.allowed:raise ValueError('revoked')
        return {'deviceId':'tablet','endpoint':'frame'},r.endpoint
    return r,h,c,grant


def test_authenticated_negotiation_and_action_recheck_exact_proof():
    async def case():
        api=module('control_api');r,h,c,grant=api_rig();r.enable()
        req={'id':1,'type':'domosenso_frame/control_targets','version':1,'endpoint':'frame','deviceId':'tablet','supported':[{'actionType':'control-action.v1','resultType':'control-result.v1'}]}
        api.targets(h,c,req,grant,'read','control');assert c.results[-1]['targets']
        api.targets(h,c,req|{'deviceId':'other'},grant,'read','control');assert c.errors
        c.errors.clear();req=r.request()|{'id':2,'type':'domosenso_frame/control_action'}
        r.on_save=lambda:setattr(r,'allowed',False)
        await api.action(h,c,req,grant,'read','control');assert not r.calls
        assert c.results[-1]['status']=='failed'
    run(case)


@pytest.mark.parametrize('policy',['read','control'])
def test_read_and_control_are_independent_requirements(policy):
    api=module('control_api');r,h,c,grant=api_rig();r.enable()
    c.user.permissions.check_entity=lambda e,p:p!=policy
    api.targets(h,c,{'id':1,'version':1,'endpoint':'frame','deviceId':'tablet','supported':[{'actionType':'control-action.v1','resultType':'control-result.v1'}]},grant,'read','control')
    assert c.results[-1]['targets']==[]


def test_cover_group_route_authorizes_members_and_deduplicates_room():
    from test_controls import cover_group
    async def case():
        api=module('control_api');r,h,c,grant=api_rig();cover_group(r)
        c.user.permissions.check_entity=lambda e,p:e not in r.denied
        msg=r.request('covers.open',scope='room')|{'id':1}
        await api.action(h,c,msg,grant,'read','control')
        assert c.results[-1]['status']=='accepted' and r.calls==[('cover','open_cover',{'entity_id':'cover.group'})]
        req=r.request('covers.close')|{'id':2,'requestId':r.negotiate()['requestIdPrefix']+'close'}
        r.on_save=lambda:r.denied.add('cover.two')
        await api.action(h,c,req,grant,'read','control')
        assert c.results[-1]['status']=='failed' and len(r.calls)==1
    run(case)


def test_cover_route_rejects_new_excluded_child_and_incomparable_room_overlap():
    from test_controls import cover_group
    async def case():
        api=module('control_api');r,h,c,grant=api_rig();cover_group(r);req=r.request('covers.open')|{'id':1}
        r.add('cover.new','closed',area='office',device_class='garage',supported_features=11)
        r.states['cover.group'].attributes['entity_id'].append('cover.new')
        await api.action(h,c,req,grant,'read','control');assert c.results[-1]['status']=='failed' and not r.calls
        r.states['cover.group'].attributes['entity_id'].remove('cover.new')
        r.add('cover.three','closed',area='office',device_class='shade',supported_features=11)
        r.add('cover.overlap','closed',area='office',device_class='shade',supported_features=11,entity_id=['cover.two','cover.three'])
        r.endpoint['studio']['covers'].append({'entity':'cover.overlap','label':'Overlap'});r.enable()
        api.targets(h,c,{'id':2,'version':1,'endpoint':'frame','deviceId':'tablet','supported':[{'actionType':'control-action.v1','resultType':'control-result.v1'}]},grant,'read','control')
        assert not any(t['kind']=='cover' and t['scope']=='room' for t in c.results[-1]['targets'])
    run(case)


def test_missing_wind_registry_keeps_existing_safe_actions_on_authenticated_route():
    async def case():
        api=module('control_api');r,h,c,grant=api_rig();r.compiled();r.enable()
        retract=r.request('awnings.retract')|{'id':1};extend=r.request('awnings.extend')|{'id':2,'requestId':r.negotiate()['requestIdPrefix']+'extend'}
        del r.registry['binary_sensor.awnings_wind']
        await api.action(h,c,retract,grant,'read','control');assert c.results[-1]['status']=='accepted'
        await api.action(h,c,extend,grant,'read','control');assert c.results[-1]['reason']=='wind_block'
        assert r.calls==[('cover','close_cover',{'entity_id':'cover.terrace'})]
    run(case)
