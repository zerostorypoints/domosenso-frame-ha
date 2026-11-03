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
        config={'http':{'server_host':'127.0.0.1','server_port':8123},'domosenso_frame':{'endpoints':[{'id':'frame','name':'Demo frame','dashboard':'/dashboard-ramka/ramka','display':{},'temperature':[{'entity':'sensor.demo_temperature','label':'Office'}]}]}}
        assert await bootstrap.async_from_config_dict(config,hass), 'component setup failed'
        state=hass.data['domosenso_frame']
        assert state.get('assistant_ready'), 'assistant setup failed'
        assert state.get('studio_ready'), 'studio regressed'
        if compiler_fixtures:
            assert set(state['assistant_artifacts']) == {'climate-air','floorplan','plants'}, 'compiler artifacts missing'
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
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(hass)
        temperature = registry.async_get_or_create('sensor', 'tplink', 'demo-temperature', suggested_object_id='demo_temperature')
        state['endpoints']['frame']['temperature'] = [{'entity':temperature.entity_id,'label':'Office'}]
        hass.states.async_set(temperature.entity_id, 22.5, {'unit_of_measurement':'°C'})
        direct = await request(8,'assistant_query',endpoint='frame',version=1,capability='climate-air.temperature',revision=1,answerType='temperature.v1',arguments={})
        assert direct and any(f['value'] == 22.5 for f in direct['facts']), 'direct temperature report missing'
        air = registry.async_get_or_create('sensor', 'ruuvitag_ble', 'AA:BB:CC:DD:EE:FF-voc_index', suggested_object_id='demo_voc')
        state['endpoints']['frame']['air'] = [{'entity':air.entity_id,'label':'Demo air','metric':'voc'}]
        hass.states.async_set(air.entity_id, 100, {})
        index = await request(9,'assistant_query',endpoint='frame',version=1,capability='climate-air.air_readings',revision=1,answerType='air.v1',arguments={})
        assert index and index['facts'][0]['unit'] == 'index' and index['facts'][0]['value'] == 100, 'Ruuvi index normalization missing'
        if compiler_fixtures:
            explanation=await request(7,'assistant_query',endpoint='frame',version=1,capability='floorplan.explain',revision=1,answerType='component-description.v1',arguments={})
            assert explanation and explanation['facts'][0]['componentId']=='floorplan', 'floorplan explanation missing'
            assert explanation['facts'][0]['basis']=='installed_metadata', 'floorplan metadata basis missing'
            assert explanation['facts'][0]['liveStatus']=='not_assessed', 'floorplan live status was assessed'
        if compiler_fixtures:
            plant_bundle=state['assistant_artifacts']['plants']
            plant=plant_bundle['bindings']['subjects'][0]
            values={'moisture':('20','%'),'conductivity':('500','µS/cm'),'brightness':('800','lx'),'temperature':('22','°C'),
                    'last_watered':('2026-10-28T10:00:00+00:00',''),'water_in_days':('2','d'),'drying_rate':('-0.2','%/h'),'brightness_peak':('1000','lx')}
            for role,field in plant['fields'].items():
                eid=field['entity']
                if role=='plant':
                    hass.states.async_set(eid,'problem',{'problem':'moisture low'})
                else:
                    registered=registry.async_get_or_create('sensor','xiaomi_ble','demo-'+role,suggested_object_id=eid.split('.',1)[1])
                    assert registered.entity_id==eid, 'fixture source identity changed'
                    value,unit=values[role];hass.states.async_set(eid,value,{'unit_of_measurement':unit})
            offered=await request(20,'studio_mappings',version=1,endpoint='frame')
            assert offered and offered['assistantCatalog'], 'admin metadata missing'
            proposals=next(c for c in offered['assistantCatalog']['components'] if c['id']=='plants')['subjects']
            selection=proposals[0]['selection']
            assert proposals[0]['missing']==0, 'compiled source roles do not exist'
            assistant={**offered['mappings']['assistant'],'subjects':[],'sensitive':False,'component_sources':[selection]}
            saved=await request(21,'studio_mappings_update',version=1,endpoint='frame',revision=offered['revision'],patch={'assistant':assistant})
            assert saved and saved['mappings']['assistant']['component_sources']==[selection], 'explicit component source save failed'
            pairs=[{'queryType':'plant-query.v1','answerType':t} for t in ('plant-watering.v1','plant-status.v1')]
            discovered=await request(22,'assistant_capabilities',endpoint='frame',version=1,supported=pairs)
            assert len(discovered['capabilities'])==2, 'selected plants not advertised'
            answers={}
            for index,cap in enumerate(discovered['capabilities']):
                answer=await request(23+index,'assistant_query',endpoint='frame',version=1,capability=cap['id'],revision=1,answerType=cap['answerType'],arguments={})
                assert answer and answer['availability']=='ready', 'compiled plant query unavailable'
                answers[cap['id']]=answer
            assert answers['plants.watering_need']['summary']['status']=='needs_water', 'non-default component band ignored'
            assert answers['plants.status']['summary']['status']=='problem', 'plant provider flags lost'
            Path(hass.config.path('assistant-acceptance.json')).write_text(json.dumps({'capabilities':discovered,'answers':answers},ensure_ascii=False))
            # Simulate missing/mismatched deployment without changing saved selections.
            before=state['assistant_artifacts'];state['assistant_artifacts']={}
            disabled=await request(26,'assistant_capabilities',endpoint='frame',version=1,supported=pairs)
            assert not disabled['capabilities'], 'removed metadata silently became a manual adapter'
            state['assistant_artifacts']=before
            # Same component version/commit, new input subjects: no APK or re-mapping.
            import copy
            import yaml
            bundle=copy.deepcopy(plant_bundle)
            def add_plant(suffix):
                row=copy.deepcopy(plant);row['key']+=suffix;row['label']={'en':'Fern '+suffix,'pl':'Paproć '+suffix,'de':'Farn '+suffix}
                for role,field in row['fields'].items():
                    old=hass.states.get(field['entity']);field['entity']+=suffix
                    if role!='plant':registry.async_get_or_create('sensor','xiaomi_ble','future-'+role+suffix,suggested_object_id=field['entity'].split('.',1)[1])
                    hass.states.async_set(field['entity'],old.state,dict(old.attributes))
                bundle['bindings']['subjects'].append(row)
                Path(hass.config.path('domosenso/assistant/plants.yaml')).write_text(yaml.safe_dump(bundle,allow_unicode=True))
            add_plant('_second')
            await request(30,'assistant_reload')
            offered=await request(31,'studio_mappings',version=1,endpoint='frame')
            proposal=next(c for c in offered['assistantCatalog']['components'] if c['id']=='plants')['scopes'][0]
            assistant={**offered['mappings']['assistant'],'component_sources':[], 'source_policy':{'version':1,'scopes':[proposal]}}
            saved=await request(32,'studio_mappings_update',version=1,endpoint='frame',revision=offered['revision'],patch={'assistant':assistant})
            assert saved, 'dynamic source policy save failed'
            two=await request(33,'assistant_query',endpoint='frame',version=1,capability='plants.watering_need',revision=1,answerType='plant-watering.v1',arguments={})
            assert two and len(two['facts'])==2, 'initial dynamic plants missing'
            add_plant('_third')
            await asyncio.sleep(31)
            three=await request(34,'assistant_query',endpoint='frame',version=1,capability='plants.watering_need',revision=1,answerType='plant-watering.v1',arguments={})
            assert three and len(three['facts'])==3, 'same-version automatic refresh failed'
            shown=await request(35,'studio_display',capabilitiesVersion=1)
            assert shown and len(shown['data']['plants'])==3, 'display/assistant source parity failed'
            Path(hass.config.path('assistant-future-acceptance.json')).write_text(json.dumps({'answer':three,'displayPlants':shown['data']['plants']},ensure_ascii=False))
        state['grants'].revoke(pending['deviceId'])
        await request(6,'assistant_query',endpoint='frame',version=1,capability='climate-air.temperature',revision=1,answerType='temperature.v1',arguments={})
        assert 6 in connection.errors and 6 not in connection.results
        summary={'setup':'ok','studio':'ok','profile':'v1','pairing':'signed-and-approved','capabilities':'ok','query':'qualified unknown source','directTemperature':'ok','ruuviIndex':'ok','revocation':'denied'}
        if compiler_fixtures:summary.update({'compilerArtifacts':'loaded','floorplan':'explanation-only','plants':'compiled-selected-and-queried','metadataRemoval':'disabled','futurePlants':'2-to-3-auto-refreshed-and-displayed'})
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
