import asyncio, json, logging, os
from pathlib import Path
from types import SimpleNamespace
from homeassistant.core import HomeAssistant
from homeassistant import loader, bootstrap
from homeassistant.components import websocket_api
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
import base64

logging.disable(logging.CRITICAL)
class Connection:
    def __init__(self, user):
        self.user=user
        self.subscriptions={}
        self.results={}
        self.errors={}
    def send_result(self, ident, value):self.results[ident]=value
    def send_error(self, ident, code, message):self.errors[ident]=(code,message)
    def context(self,msg):
        from homeassistant.core import Context
        return Context(user_id=self.user.id)

async def main():
    hass=HomeAssistant(os.environ.get('HA_CONFIG_DIR', '/config'))
    compiler_fixtures=os.environ.get('ASSISTANT_COMPILER_FIXTURES') == '1'
    loader.async_setup(hass)
    hass.config.time_zone='Europe/Zurich'
    try:
        config={'http':{'server_host':'127.0.0.1','server_port':8123},'domosenso_frame':{'endpoints':[{'id':'frame','name':'Demo frame','dashboard':'/dashboard-ramka/ramka','temperature':[{'entity':'sensor.demo_temperature','label':'Office'}]}]}}
        assert await bootstrap.async_from_config_dict(config,hass), 'component setup failed'
        state=hass.data['domosenso_frame']
        assert state.get('assistant_ready'), 'assistant setup failed'
        assert state.get('studio_ready'), 'studio regressed'
        if compiler_fixtures:
            assert set(state['assistant_artifacts']) == {'climate-air','floorplan'}, 'compiler artifacts missing'
            records=[dict(bundle['component'],installed=True,package='') for bundle in state['assistant_artifacts'].values()]
            hass.states.async_set('sensor.domosenso_components',len(records),{'components':records})
            state['endpoints']['frame']['assistant']={'explanations':['floorplan']}
        user=await hass.auth.async_create_user('Demo tester',group_ids=['system-admin'])
        connection=Connection(user)
        async def request(n,kind,**args):
            name='domosenso_frame/'+kind
            handler,schema=hass.data[websocket_api.DOMAIN][name]
            msg={'id':n,'type':name,**args}
            if schema:msg=schema(msg)
            result=handler(hass,connection,msg)
            if asyncio.iscoroutine(result):await result
            for _ in range(100):
                if n in connection.results or n in connection.errors:break
                await asyncio.sleep(.01)
            return connection.results.get(n)
        profile=await request(1,'profile')
        assert profile['assistantVersion']==1
        key=ec.generate_private_key(ec.SECP256R1())
        public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        begin=await request(2,'begin',publicKey=public,endpoint='frame',name='Demo tablet')
        signed=base64.b64encode(key.sign(begin['challenge'].encode(),ec.ECDSA(hashes.SHA256()))).decode()
        pending=await request(3,'finish',signature=signed)
        state['grants'].approve(pending['deviceId'],pending['code'])
        hass.states.async_set('sensor.demo_temperature',22.5,{'unit_of_measurement':'°C'})
        supported=[{'queryType':'temperature-query.v1','answerType':'temperature.v1'}]
        caps=await request(4,'assistant_capabilities',endpoint='frame',version=1,supported=supported)
        assert caps and any(c['id']=='climate-air.temperature' for c in caps['capabilities']), 'temperature capability missing'
        result=await request(5,'assistant_query',endpoint='frame',version=1,capability='climate-air.temperature',revision=1,answerType='temperature.v1',arguments={})
        assert result and result['answerType']=='temperature.v1', 'temperature query failed'
        # Unknown platform reports must remain qualified, never fresh physical measurements.
        assert result['availability'] in ('unavailable','partial'), 'unknown source was not qualified'
        if compiler_fixtures:
            explanation=await request(7,'assistant_query',endpoint='frame',version=1,capability='floorplan.explain',revision=1,answerType='component-description.v1',arguments={})
            assert explanation and explanation['facts'][0]['componentId']=='floorplan', 'floorplan explanation missing'
            assert explanation['facts'][0]['basis']=='installed_metadata', 'floorplan metadata basis missing'
            assert explanation['facts'][0]['liveStatus']=='not_assessed', 'floorplan live status was assessed'
        state['grants'].revoke(pending['deviceId'])
        await request(6,'assistant_query',endpoint='frame',version=1,capability='climate-air.temperature',revision=1,answerType='temperature.v1',arguments={})
        assert 6 in connection.errors and 6 not in connection.results
        summary={'setup':'ok','studio':'ok','profile':'v1','pairing':'signed-and-approved','capabilities':'ok','query':'qualified unknown source','revocation':'denied'}
        if compiler_fixtures:summary.update({'compilerArtifacts':'loaded','floorplan':'explanation-only'})
        print(json.dumps(summary))
    finally:
        await hass.async_stop(force=True)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception:
        # Exceptions from HA may contain pairing material or configuration data.
        print('Home Assistant smoke failed; no diagnostic data emitted.')
        raise SystemExit(1) from None
