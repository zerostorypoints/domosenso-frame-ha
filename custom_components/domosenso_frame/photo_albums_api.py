"""Version 1 photo album commands for paired, opted-in frames, over the domosenso_photos facade.

The album password is handed only to the facade: never echoed, logged or returned. Errors travel
as fixed WebSocket codes with fixed messages, so no input or exception text reaches the app.
"""
import logging

DOMAIN='domosenso_frame'
FACADE='domosenso_photos_albums'
MESSAGES={'frame_denied':'Photo albums unavailable or access denied','photos_unavailable':'Photo albums are not available right now',
    'invalid_link':'Not a share link','already_added':'Album already added','wrong_password':'Wrong password',
    'unreachable':'Album cannot be reached','unknown_album':'Unknown album','invalid_choice':'Invalid album choice',
    'ramka_unavailable':'Ramka album choice unavailable'}
_LOGGER=logging.getLogger(__name__)


class Refused(Exception):
    def __init__(self,code):super().__init__(code);self.code=code


def facade(hass):
    api=getattr(hass.data.get(FACADE),'frame_api',None)
    if api is None or getattr(api,'version',None)!=1:raise Refused('photos_unavailable')
    return api


def profile_fields(hass,endpoint):
    try:return {'photoAlbumsVersion':1} if 'photo_albums' in endpoint and facade(hass) else {}
    except Refused:return {}


def text(value,limit,code):
    if not isinstance(value,str) or len(value)>limit:raise Refused(code)
    return value


def ramka(hass,config):
    state=hass.states.get(config['entity'])
    if state is None:return None
    names=lambda key:[n for n in state.attributes.get(key) or [] if isinstance(n,str)]
    chosen=names('chosen');everything=state.attributes.get('all')
    # An older Ramka sensor has no 'all': like the Ramka card, nothing chosen means all albums.
    return {'available':names('available'),'chosen':chosen,'all':not chosen if everything is None else bool(everything)}


async def albums(hass,connection,msg,api,config):
    return {'albums':api.albums(),'ramka':ramka(hass,config)}


async def add(hass,connection,msg,api,config):
    url=text(msg.get('url'),2048,'invalid_link');password=text(msg.get('password',''),256,'wrong_password') or None
    name=await api.check(url,password)
    return {'id':(await api.add(url,password))['id'],'name':name}


async def remove(hass,connection,msg,api,config):
    return {'status':await api.remove(text(msg.get('albumId'),64,'unknown_album'))}


async def sync(hass,connection,msg,api,config):
    await api.sync(text(msg.get('albumId'),64,'unknown_album'));return {'status':'started'}


async def choose(hass,connection,msg,api,config):
    chosen,everything=msg.get('chosen'),msg.get('all')
    if not isinstance(chosen,list) or len(chosen)>500 or type(everything) is not bool:raise Refused('invalid_choice')
    for name in chosen:text(name,200,'invalid_choice')
    current=ramka(hass,config);script=config['script'].split('.',1)[1]
    if current is None or not hass.services.has_service('script',script):raise Refused('ramka_unavailable')
    if not set(chosen)<=set(current['available'])|set(current['chosen']):raise Refused('invalid_choice')
    await hass.services.async_call('script',script,{'chosen':list(chosen),'all_albums':everything},blocking=True,context=connection.context(msg))
    return {'status':'saved'}


def command(work):
    async def handle(hass,connection,msg,grant):
        try:
            proof,endpoint=grant(hass,connection)
            if endpoint['id']!=msg['endpoint'] or proof['deviceId']!=msg['deviceId'] or 'photo_albums' not in endpoint:raise ValueError('Proof binding mismatch')
        except Exception:return connection.send_error(msg['id'],'frame_denied',MESSAGES['frame_denied'])
        try:result=await work(hass,connection,msg,facade(hass),endpoint['photo_albums'])
        except Exception as err:
            code=getattr(err,'code',None)
            if code not in MESSAGES or code=='frame_denied':
                _LOGGER.warning('Photo album command failed (%s)',type(err).__name__);code='photos_unavailable'
            return connection.send_error(msg['id'],code,MESSAGES[code])
        connection.send_result(msg['id'],result)
    return handle


COMMANDS={'photo_albums':command(albums),'photo_album_add':command(add),'photo_album_remove':command(remove),
    'photo_album_sync':command(sync),'photo_albums_choose':command(choose)}


def schema_fields(vol):
    def version(value):
        if type(value) is not int or value!=1:raise vol.Invalid('Unsupported photo albums version')
        return value
    # Types and lengths of url and password, and lengths of names, are checked in the handlers:
    # a schema error would echo the value.
    base={vol.Required('version'):version,vol.Required('endpoint'):vol.All(str,vol.Length(min=1,max=40)),vol.Required('deviceId'):vol.All(str,vol.Length(min=1,max=256))}
    return {name:{**base,**extra} for name,extra in {'photo_albums':{},'photo_album_add':{vol.Required('url'):object,vol.Optional('password'):object},
        'photo_album_remove':{vol.Required('albumId'):str},'photo_album_sync':{vol.Required('albumId'):str},
        'photo_albums_choose':{vol.Required('chosen'):list,vol.Required('all'):bool}}.items()}


async def setup(hass,grant,register_command):
    import voluptuous as vol
    from homeassistant.components import websocket_api
    fields=schema_fields(vol)
    def handler(name):
        @websocket_api.websocket_command({vol.Required('type'):DOMAIN+'/'+name,**fields[name]})
        @websocket_api.async_response
        async def run(h,c,msg):await COMMANDS[name](h,c,msg,grant)
        return run
    for name in COMMANDS:register_command(hass,handler(name))
