"""Fresh HA 2026.9.2, fictional registries/states, real authenticated WebSockets.

Run in the HA image with this repository mounted read-only at /work:
  docker run --rm --network none -v "$PWD:/work:ro" -w /work \
    --entrypoint python homeassistant/home-assistant:2026.9.2 tests/controls_ha_smoke.py
All actuator services are intercepted before negotiation; no hardware or existing
HA config is used. Temporary configuration is deleted at exit.
"""
import asyncio
import base64
import copy
import json
import logging
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from aiohttp import ClientSession
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from homeassistant import bootstrap, loader
from homeassistant.const import __version__
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, area_registry as ar

logging.basicConfig(level=logging.WARNING)
ROOT=Path(__file__).parents[1]


async def main():
    assert __version__=='2026.9.2',__version__
    with tempfile.TemporaryDirectory(prefix='frame-controls-ha-') as directory:
        root=Path(directory);shutil.copytree(ROOT/'custom_components',root/'custom_components')
        artifacts=root/'domosenso/assistant-actions';artifacts.mkdir(parents=True)
        bundles={}
        for cid in ('lights','awnings','timers'):
            raw=(ROOT/'tests/fixtures/assistant-actions'/f'{cid}.json').read_text()
            (artifacts/(cid+'.yaml')).write_text(raw);bundles[cid]=json.loads(raw)
        hass=HomeAssistant(str(root));loader.async_setup(hass);hass.config.skip_pip=True
        sockets=[];calls=[]
        try:
            config={'homeassistant':{'name':'Fictional controls lab','time_zone':'Europe/Warsaw'},
                    'http':{'server_host':'127.0.0.1','server_port':18189},
                    'input_datetime':{'wind_heard':{'has_date':True,'has_time':True}},
                    'domosenso_frame':{'endpoints':[{'id':'frame','name':'Fictional frame','dashboard':'/dashboard-fictional/main','display':{}}]}}
            assert await bootstrap.async_from_config_dict(config,hass),'bootstrap'
            await hass.async_start();await hass.async_block_till_done()
            state=hass.data['domosenso_frame'];assert 'controls' in state,'control setup'
            registry=er.async_get(hass);areas=ar.async_get(hass)
            office=areas.async_create('Office').id;terrace=areas.async_create('Terrace').id
            def entity(eid,value,area=None,**attrs):
                domain,object_id=eid.split('.')
                entry=registry.async_get_or_create(domain,'fixture',object_id,suggested_object_id=object_id)
                assert entry.entity_id==eid,(entry.entity_id,eid)
                if area:registry.async_update_entity(eid,area_id=area)
                hass.states.async_set(eid,value,attrs)
            entity('light.room_office','off',office,component='lights')
            entity('light.office_group','off',office,entity_id=['light.desk'])
            entity('light.desk','off',office)
            entity('light.vanilla','off',office)
            entity('cover.vanilla','closed',office,device_class='shade',supported_features=11)
            entity('cover.terrace','closed',terrace,device_class='awning',supported_features=11,assumed_state=True)
            entity('binary_sensor.awnings_wind','off')
            hass.states.async_set('sensor.domosenso_components','3',{'components':[{**b['component'],'package':''} for b in bundles.values()]})
            async def intercepted(call):
                calls.append({'domain':call.domain,'service':call.service,'data':dict(call.data),'user':call.context.user_id})
                if on_call[0]:on_call[0]()
            on_call=[None]
            for domain,names in [('light',('turn_on','turn_off')),('cover',('open_cover','close_cover','stop_cover'))]:
                for name in names:hass.services.async_register(domain,name,intercepted)
            admin=await hass.auth.async_create_user('Fictional administrator',group_ids=['system-admin'])
            user=await hass.auth.async_create_user('Fictional tablet',group_ids=['system-users'])
            read_only=await hass.auth.async_create_user('Fictional read only',group_ids=['system-read-only'])
            async with ClientSession() as client:
                async def connect(user):
                    refresh=await hass.auth.async_create_refresh_token(user,client_id='http://localhost/')
                    ws=await client.ws_connect('http://127.0.0.1:18189/api/websocket');sockets.append(ws)
                    assert (await ws.receive_json())['type']=='auth_required'
                    await ws.send_json({'type':'auth','access_token':hass.auth.async_create_access_token(refresh)})
                    assert (await ws.receive_json())['type']=='auth_ok';return ws
                adm=await connect(admin);tablet=await connect(user);reader=await connect(read_only);seq=0
                async def request(ws,kind,expect_error=False,**fields):
                    nonlocal seq
                    seq+=1;ident=seq
                    await ws.send_json({'id':ident,'type':'domosenso_frame/'+kind,**fields})
                    response=await ws.receive_json(timeout=30)
                    assert response['id']==ident,response
                    if expect_error:
                        assert not response['success'],response;return response['error']
                    assert response['success'],response
                    return response['result']
                async def pair(ws):
                    key=ec.generate_private_key(ec.SECP256R1())
                    public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
                    challenge=await request(ws,'begin',publicKey=public,endpoint='frame',name='Fictional tablet')
                    signature=base64.b64encode(key.sign(challenge['challenge'].encode(),ec.ECDSA(hashes.SHA256()))).decode()
                    pending=await request(ws,'finish',signature=signature)
                    await request(adm,'approve',deviceId=pending['deviceId'],code=pending['code'])
                    return pending['deviceId']
                device=await pair(tablet);reader_device=await pair(reader)
                common={'version':1,'endpoint':'frame','deviceId':device}
                supported=[{'actionType':'control-action.v1','resultType':'control-result.v1'}]
                async def negotiate(ws=tablet,**extra):return await request(ws,'control_targets',**(common|{'supported':supported}|extra))
                assert (await negotiate())['targets']==[],'disabled default'
                await request(adm,'studio_mappings',version=1,endpoint='frame')
                m=await request(adm,'studio_mappings',version=1,endpoint='frame')
                m=await request(adm,'studio_mappings_update',version=1,endpoint='frame',revision=m['revision'],patch={'lights':[{'entity':'light.vanilla','label':'Vanilla lamp'}],'covers':[{'entity':'cover.vanilla','label':'Vanilla shade'}]})
                grants=[row['grant'] for row in m['controlCatalog']['sources'] if row.get('grant')]
                assert len(grants)==5,m['controlCatalog']
                controls={'enabled':True,'grants':grants,'mappings':[]}
                m=await request(adm,'studio_mappings_update',version=1,endpoint='frame',revision=m['revision'],patch={'assistant':{'controls':controls}})
                assert (await negotiate(reader,deviceId=reader_device))['targets']==[],'READ is not CONTROL'
                assert (await request(tablet,'control_targets',expect_error=True,**(common|{'deviceId':'wrong','supported':supported})))['code']=='frame_denied'
                counter=0
                async def command(cap,scope='subject',label=None,choice=None,prepare=None):
                    nonlocal counter
                    n=await negotiate();target=next(t for t in n['targets'] if t['scope']==scope and any(c['id']==cap for c in t['capabilities']) and (label is None or t['label']==label))
                    counter+=1
                    args={**common,'capability':cap,'revision':1,'targetId':target['id'],'targetRevision':target['revision'],'requestId':n['requestIdPrefix']+str(counter)}
                    if choice:args['scopeChoice']=choice
                    if prepare:prepare()
                    return await request(tablet,'control_action',**args),args
                # Real input_datetime helper, local wall clock plus HA timestamp.
                async def heard(delta):
                    instant=datetime.now(timezone.utc)+timedelta(seconds=delta)
                    await hass.services.async_call('input_datetime','set_datetime',{'entity_id':'input_datetime.wind_heard','timestamp':instant.timestamp()},blocking=True)
                    sample=hass.states.get('input_datetime.wind_heard')
                    assert '+' not in sample.state and sample.attributes['has_date'] and sample.attributes['has_time']
                    return sample
                sample=await heard(-2);print('LOCAL_HELPER',sample.state,sample.attributes['timestamp'],hass.config.time_zone)
                result,duplicate=await command('awnings.extend');assert result['status']=='accepted' and result['outcomes'][0]['assumedState'],result
                count=len(calls);assert await request(tablet,'control_action',**duplicate)==result;assert len(calls)==count
                for delta in (300,-15000):
                    await heard(delta);before=len(calls);blocked,_=await command('awnings.extend');assert blocked['reason']=='wind_block' and len(calls)==before,blocked
                await heard(-2)
                hass.states.async_set('binary_sensor.awnings_wind','on')
                before=len(calls);blocked,_=await command('awnings.extend');assert blocked['reason']=='wind_block' and len(calls)==before
                assert (await command('awnings.retract'))[0]['status']=='accepted'
                assert (await command('awnings.stop'))[0]['status']=='accepted'
                hass.states.async_set('binary_sensor.awnings_wind','off')
                assert (await command('covers.open'))[0]['status']=='accepted'
                assert (await command('lights.turn_off',label='Vanilla lamp'))[0]['status']=='accepted'
                # ISO timestamp sensor through explicit vanilla awning mapping.
                entity('cover.iso_awning','closed',terrace,device_class='awning',supported_features=11)
                entity('sensor.wind_heard_iso',(datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat())
                m=await request(adm,'studio_mappings',version=1,endpoint='frame')
                covers=m['mappings']['covers']+[{'entity':'cover.iso_awning','label':'ISO awning'}]
                m=await request(adm,'studio_mappings_update',version=1,endpoint='frame',revision=m['revision'],patch={'covers':covers})
                controls=m['mappings']['assistant']['controls'];controls['mappings']=[{'source':'studio:cover.iso_awning','entity':'cover.iso_awning','guard':{'wind':{'entity':'binary_sensor.awnings_wind'},'last_heard':{'entity':'sensor.wind_heard_iso'},'max_age_seconds':60}}]
                m=await request(adm,'studio_mappings_update',version=1,endpoint='frame',revision=m['revision'],patch={'assistant':{'controls':controls}})
                controls['grants'].append(next(s['grant'] for s in m['controlCatalog']['sources'] if s['entity']=='cover.iso_awning'))
                m=await request(adm,'studio_mappings_update',version=1,endpoint='frame',revision=m['revision'],patch={'assistant':{'controls':controls}})
                assert (await command('awnings.extend',label='ISO awning'))[0]['status']=='accepted'
                for value in ('unknown','bad',(datetime.now(timezone.utc)+timedelta(seconds=300)).isoformat(),(datetime.now(timezone.utc)-timedelta(seconds=300)).isoformat()):
                    hass.states.async_set('sensor.wind_heard_iso',value);before=len(calls)
                    assert (await command('awnings.extend',label='ISO awning'))[0]['reason']=='wind_block';assert len(calls)==before
                # Unknown source in the same area forces a current explicit choice.
                entity('light.hidden','off',office)
                before=len(calls);blocked,_=await command('lights.turn_on',scope='room');assert blocked['reason']=='partial_scope' and len(calls)==before
                result,_=await command('lights.turn_on',scope='room',choice='configured');assert result['status']=='accepted',result
                assert [c['data']['entity_id'] for c in calls[before:]]==['light.room_office','light.vanilla']
                # A changed graph invalidates the old reviewed proxy grant.
                before=len(calls)
                result,_=await command('lights.turn_on',scope='room',choice='configured',prepare=lambda:hass.states.async_set('light.office_group','off',{'entity_id':['light.desk','light.hidden']}))
                assert result['reason']=='stale_revision' and len(calls)==before,result
                hass.states.async_set('light.office_group','off',{'entity_id':['light.desk']})
                # An uncertain service result survives a real Store reload; no replay.
                def provider_timeout():raise TimeoutError('Fictional provider timeout')
                on_call[0]=provider_timeout
                before=len(calls);unknown,unknown_args=await command('covers.close')
                assert unknown['status']=='unknown' and len(calls)==before+1,unknown
                on_call[0]=None
                from homeassistant.helpers.storage import Store
                from custom_components.domosenso_frame.control_actions import Controls
                old=state['controls']
                state['controls']=Controls(state['saved']['installationId'],old.profiles,old.persist,await Store(hass,1,'domosenso_frame_controls').async_load())
                assert await request(tablet,'control_action',**unknown_args)==unknown
                assert len(calls)==before+1
                # Revoke device while first member service is awaited: second never runs.
                on_call[0]=lambda:state['grants'].revoke(device)
                before=len(calls);result,_=await command('lights.turn_on',scope='room',choice='configured')
                assert result['reason']=='partial_failure' and len(calls)==before+1,result
                assert [o['status'] for o in result['outcomes']]==['accepted','failed'],result
                on_call[0]=None
                await request(tablet,'control_targets',expect_error=True,**common,supported=supported)
                assert all(call['user']==user.id for call in calls)
                print('ACTUATOR_CALLS',json.dumps([{k:v for k,v in c.items() if k!='user'} for c in calls],sort_keys=True))
                print('PASS HA',__version__,'real WebSocket/P-256/admin grants, default disabled, READ-only denial, exact proof, real local input_datetime fresh/future/stale, ISO fresh/unknown/invalid/future/stale, alarm, retract/stop, vanilla cover/light, deduped proxy, explicit partial scope, graph expansion, provider uncertainty and disk reload, mid-dispatch revocation; intercepted calls=',len(calls))
                for ws in sockets:await ws.close()
        finally:
            await hass.async_stop(force=True)


if __name__=='__main__':asyncio.run(main())
