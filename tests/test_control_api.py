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
