"""Fresh HA 2026.9.2 smoke; creates only fictional helpers in a temporary directory.

Run with an HA runtime plus requirements-test.txt installed:
  python tests/timers_ha_smoke.py
No existing HA configuration, network endpoint or credentials are used.
"""
import asyncio
import base64
import json
import logging
logging.basicConfig(level=logging.INFO)
import shutil
import sys
import tempfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant, Context
from homeassistant.components import websocket_api


class Connection:
    def __init__(self, user):
        self.user=user; self.results={}; self.errors={}; self.subscriptions={}; self.events=[]
    def send_result(self, ident, value=None): self.results[ident]=value
    def send_error(self, ident, code, message): self.errors[ident]=(code,message)
    def send_event(self, ident, value): self.events.append(value)
    def context(self, msg): return Context(user_id=self.user.id)


async def main():
    if '--websocket' in sys.argv and '--no-listener' in sys.argv:
        raise ValueError('WebSocket transport requires the HTTP listener')
    from aiohttp import ClientSession
    client = ClientSession() if '--websocket' in sys.argv else None
    sockets = {}
    if '--no-listener' in sys.argv:
        # Restricted runners can still exercise actual HA services and WS handlers.
        # Only the TCP listener is omitted; no bridge/provider behavior is mocked.
        from homeassistant.components.http.server import HomeAssistantHTTP
        from unittest.mock import AsyncMock, Mock
        HomeAssistantHTTP._async_create_server = AsyncMock(return_value=Mock(start_serving=AsyncMock(),close=Mock(),wait_closed=AsyncMock()))
    with tempfile.TemporaryDirectory(prefix='frame-timers-ha-') as directory:
        root=Path(directory)
        shutil.copytree(Path(__file__).parents[1]/'custom_components',root/'custom_components')
        hass=HomeAssistant(str(root)); loader.async_setup(hass); hass.config.skip_pip=True
        try:
            config={'homeassistant':{'name':'Fictional timer lab','time_zone':'Europe/Warsaw'},'http':{'server_host':'127.0.0.1','server_port':18187},
                    'timer':{'catalog_demo':{'name':'Catalog demo','duration':60,'restore':True}},
                    'domosenso_frame':{'endpoints':[{'id':'frame','name':'Demo frame','dashboard':'/dashboard-demo/demo','display':{}}]}}
            assert await bootstrap.async_from_config_dict(config,hass), 'bootstrap'
            await hass.async_start(); await hass.async_block_till_done()
            state=hass.data['domosenso_frame']; assert 'timers' in state, 'timer setup'
            admin=Connection(await hass.auth.async_create_user('Fictional admin',group_ids=['system-admin']))
            ordinary=Connection(await hass.auth.async_create_user('Fictional tablet',group_ids=['system-users']))
            from homeassistant.auth.const import GROUP_ID_READ_ONLY
            browser=Connection(await hass.auth.async_create_user('Fictional unpaired browser',group_ids=[GROUP_ID_READ_ONLY]))
            seq=0
            async def request(conn,kind,**fields):
                nonlocal seq
                seq+=1; ident=seq; name=kind if '/' in kind else 'domosenso_frame/'+kind
                handler,schema=hass.data[websocket_api.DOMAIN][name]
                msg={'id':ident,'type':name,**fields}
                if client is None and schema: msg=schema(msg)
                if client is not None:
                    if conn not in sockets:
                        refresh = await hass.auth.async_create_refresh_token(conn.user, client_id='http://localhost/')
                        token = hass.auth.async_create_access_token(refresh)
                        ws = sockets[conn] = await client.ws_connect('http://127.0.0.1:18187/api/websocket')
                        assert (await ws.receive_json(timeout=15))['type'] == 'auth_required'
                        await ws.send_json({'type':'auth','access_token':token})
                        assert (await ws.receive_json(timeout=15))['type'] == 'auth_ok'
                    ws = sockets[conn]
                    await ws.send_json(msg)
                    while True:
                        response = await ws.receive_json(timeout=15)
                        if response.get('type') == 'event':
                            conn.events.append(response['event'])
                            continue
                        assert response.get('type') == 'result' and response['id'] == ident
                        if response['success']: conn.results[ident] = response.get('result')
                        else: conn.errors[ident] = (response['error']['code'], response['error']['message'])
                        break
                else:
                    ret=handler(hass,conn,msg)
                    if asyncio.iscoroutine(ret): await ret
                    for _ in range(500):
                        if ident in conn.results or ident in conn.errors: break
                        await asyncio.sleep(.01)
                assert ident not in conn.errors, (kind, conn.errors.get(ident))
                assert ident in conn.results, (kind,'no response')
                return conn.results[ident]
            # Normal HA UI-created storage helper, not manually injected state.
            await request(admin,'timer/create',name='Vanilla demo',duration=60,restore=True)
            await hass.async_block_till_done()
            key=ec.generate_private_key(ec.SECP256R1())
            public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
            challenge=await request(ordinary,'begin',publicKey=public,endpoint='frame',name='Fictional tablet')
            proof=base64.b64encode(key.sign(challenge['challenge'].encode(),ec.ECDSA(hashes.SHA256()))).decode()
            pending=await request(ordinary,'finish',signature=proof)
            await request(admin,'approve',deviceId=pending['deviceId'],code=pending['code'])
            mapping=await request(admin,'studio_mappings',endpoint='frame',version=1)
            assert {'timer.catalog_demo','timer.vanilla_demo'} <= {r['entity'] for r in mapping['candidates']['timers']}
            items=[{'entity':eid,'label':label,'alarmDeviceId':pending['deviceId']} for eid,label in [('timer.catalog_demo','Catalog demo'),('timer.vanilla_demo','Vanilla demo')]]
            await request(admin,'studio_mappings_update',endpoint='frame',version=1,revision=mapping['revision'],patch={'timers':{'writeEnabled':True,'items':items}})
            await request(ordinary,'subscribe')
            caps=await request(ordinary,'assistant_actions',endpoint='frame',version=1,supported=[{'actionType':'timer-action.v1','resultType':'timer-result.v1'}])
            assert len(caps['actions'])==4 and len(caps['actions'][0]['subjects'])==2
            async def status():
                return await request(ordinary,'assistant_query',endpoint='frame',version=1,capability='timers.status',revision=1,answerType='timer-status.v1',arguments={})
            async def action(name,fact,request_id,args=None):
                return await request(ordinary,'assistant_action',endpoint='frame',version=1,capability='timers.'+name,revision=1,subjectId=fact['subjectId'],requestId=request_id,expectedStateRevision=fact['stateRevision'],expectedRunId=fact['runId'],arguments=args or {})
            start_inputs=[]
            for index,eid in enumerate(('timer.catalog_demo','timer.vanilla_demo')):
                first=(await status())['facts'][index]
                start_inputs.append((first,eid+'-start'))
                started=await action('start',first,eid+'-start',{'durationSeconds':60})
                assert started['status']=='applied', started
                assert hass.states.get(eid).state=='active'
                duplicate=await action('start',first,eid+'-start',{'durationSeconds':60});assert duplicate==started
                paused=await action('pause',started['timer'],eid+'-pause');assert paused['status']=='applied',paused
                resumed=await action('resume',paused['timer'],eid+'-resume');assert resumed['status']=='applied',resumed
                assert resumed['timer']['runId']==started['timer']['runId']
                cancelled=await action('cancel',resumed['timer'],eid+'-cancel');assert cancelled['status']=='applied',cancelled
                assert cancelled['timer']['outcome']=='cancelled'
                await hass.services.async_call('timer','start',{'entity_id':eid,'duration':2},blocking=True,context=Context(user_id=admin.user.id))
                await hass.async_block_till_done()
                external=(await status())['facts'][index]; assert external['state']=='active'
                await hass.services.async_call('timer','finish',{'entity_id':eid},blocking=True)
                await hass.async_block_till_done()
                finished=(await status())['facts'][index]; assert finished['outcome']=='completed',finished
                receipt=await request(ordinary,'timer_acknowledge',endpoint='frame',version=1,subjectId=finished['subjectId'],runId=finished['runId'])
                assert receipt['timer']['acknowledged']
            clock=await request(ordinary,'assistant_query',endpoint='frame',version=1,capability='ramka.current_time',revision=1,answerType='time.v1',arguments={})
            assert clock['facts'][0]['timezone']=='Europe/Warsaw'
            assert any(event.get('kind')=='timers' for event in ordinary.events)
            # The classic client is an ordinary unpaired READ-only HA user.
            from datetime import datetime, timezone
            before=datetime.now(timezone.utc)
            browser_state=await request(browser,'timer_browser_snapshot',version=1,entities=['timer.catalog_demo','timer.missing'])
            after=datetime.now(timezone.utc)
            assert before <= datetime.fromisoformat(browser_state['asOf']) <= after
            assert browser_state['installationId']==state['saved']['installationId']
            assert [row['entity'] for row in browser_state['timers']]==['timer.catalog_demo']
            assert not browser.user.permissions.check_entity('timer.catalog_demo','control')
            fact=browser_state['timers'][0]['timer']
            no_sound=await request(browser,'timer_browser_claim',version=1,subjectId=fact['subjectId'],runId=fact['runId'])
            assert not no_sound['claimed']
            mapping=await request(admin,'studio_mappings',endpoint='frame',version=1)
            browser_items=[dict(row,alarmDeviceId=None) for row in items]
            await request(admin,'studio_mappings_update',endpoint='frame',version=1,revision=mapping['revision'],patch={'timers':{'writeEnabled':False,'items':browser_items}})
            await hass.services.async_call('timer','start',{'entity_id':'timer.catalog_demo','duration':1},blocking=True)
            await asyncio.sleep(1.2)
            await hass.async_block_till_done()
            browser_state=await request(browser,'timer_browser_snapshot',version=1,entities=['timer.catalog_demo'])
            fact=browser_state['timers'][0]['timer']; assert fact['outcome']=='completed',fact
            browser_run={'version':1,'subjectId':fact['subjectId'],'runId':fact['runId']}
            second_tab = Connection(browser.user)
            claims=await asyncio.gather(request(browser,'timer_browser_claim',**browser_run),request(second_tab,'timer_browser_claim',**browser_run))
            assert sorted(r['claimed'] for r in claims)==[False,True]
            assert not any(r['timer']['acknowledged'] for r in claims)
            browser_receipt=await request(browser,'timer_browser_acknowledge',**browser_run)
            assert browser_receipt['timer']['acknowledged']
            saved=json.loads((root/'.storage/domosenso_frame_timers').read_text())['data']
            assert saved['requests'] and saved['receipts'] and saved['browserDeliveries']
            # Simulate executor restart using the real persisted envelope and real HA state.
            from custom_components.domosenso_frame.timers import Timers
            original=state['timers']
            state['timers']=Timers(state['saved']['installationId'],hass.states.get,original.persist,saved)
            assert not (await request(browser,'timer_browser_claim',**browser_run))['claimed']
            # Restore native write mapping before the original action replay.
            mapping=await request(admin,'studio_mappings',endpoint='frame',version=1)
            await request(admin,'studio_mappings_update',endpoint='frame',version=1,revision=mapping['revision'],patch={'timers':{'writeEnabled':True,'items':items}})
            # Retry a completed start returns the original receipt without changing idle HA.
            first_request=next(iter(saved['requests'].values()))['result']
            replay=await action('start',start_inputs[0][0],start_inputs[0][1],{'durationSeconds':60})
            assert replay==first_request and replay['status']=='applied'
            assert all(hass.states.get(eid).state=='idle' for eid in ('timer.catalog_demo','timer.vanilla_demo'))
            await state['timers'].flush()
            await request(admin,'revoke',deviceId=pending['deviceId'])
            assert state['grants'].records[pending['deviceId']]['state']=='revoked'
            before=seq
            try:
                await request(ordinary,'assistant_actions',endpoint='frame',version=1,supported=[{'actionType':'timer-action.v1','resultType':'timer-result.v1'}])
            except AssertionError:
                assert ordinary.errors[before+1][0]=='frame_denied'
            else: raise AssertionError('revoked device still negotiated actions')
            browser.user.is_active=False
            before=seq
            try:
                await request(browser,'timer_browser_snapshot',version=1,entities=['timer.catalog_demo'])
            except AssertionError:
                assert browser.errors[before+1][0]=='frame_denied'
            else: raise AssertionError('inactive browser still authorized')
            print(('WEBSOCKET ' if client is not None else 'HANDLER ') + 'PASS HA 2026.9.2: fresh YAML/core + UI helper, ordinary paired user, all action/read/receipt routes, lifecycle invalidations, durable executor reload/replay, device revocation, unpaired READ-only browser snapshot/claim/ack, concurrent claims and browser replay after reload')
        finally:
            for socket in sockets.values(): await socket.close()
            if client is not None: await client.close()
            if 'domosenso_frame' in hass.data and 'timers_close' in hass.data['domosenso_frame']:
                await hass.data['domosenso_frame']['timers_close']()
            await hass.async_stop(force=True)


if __name__=='__main__': asyncio.run(main())
