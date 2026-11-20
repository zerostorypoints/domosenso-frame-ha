"""Administrator onboarding for a vanilla or DomoSenso Home Assistant."""
import os
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from . import DOMAIN, ENDPOINT
from .albums import DATA as ALBUMS, DEFAULT_PHOTO_DIR, DEFAULTS, FLOW_ERRORS, AlbumError, format_host_map, parse_host_map
from .onboarding import AIR_METRICS, CLASSES, build_endpoint
from .studio_mappings import discover

class FrameConfigFlow(config_entries.ConfigFlow,domain=DOMAIN):
    VERSION=1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):return AlbumsOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls,config_entry):return config_entry.data.get('kind')=='albums'

    def _bridge_blocked(self):
        if self.hass.data.get(DOMAIN,{}).get('source')=='yaml':return 'yaml_configured'
        if any(e.data.get('kind','bridge')=='bridge' for e in self._async_current_entries()):return 'already_configured'
        return None

    def _existing(self):
        if reason:=self._bridge_blocked():return self.async_abort(reason=reason)
        return None

    async def async_step_user(self,user_input=None):
        # A configured bridge (YAML or UI) leaves only the albums entry to add.
        if self._bridge_blocked():return await self.async_step_albums()
        return self.async_show_menu(step_id='user',menu_options=['bridge','albums'])

    async def async_step_albums(self,user_input=None):
        await self.async_set_unique_id('albums')
        self._abort_if_unique_id_configured()
        errors={}
        if user_input is not None:
            path=user_input['photo_dir'].strip()
            if os.path.isabs(path) and await self.hass.async_add_executor_job(os.path.isdir,path):
                return self.async_create_entry(title='DomoSenso Frame albums',data={'kind':'albums','photo_dir':path})
            errors['photo_dir']='invalid_photo_dir'
        return self.async_show_form(step_id='albums',data_schema=vol.Schema({
            vol.Required('photo_dir',default=DEFAULT_PHOTO_DIR):selector.TextSelector()}),errors=errors)

    async def async_step_bridge(self,user_input=None):
        if existing:=self._existing():return existing
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors={}
        if user_input is not None:
            try:build_endpoint(user_input,self.hass.states)
            except (ValueError,TypeError):errors['base']='invalid_settings'
            else:
                self.settings=dict(user_input)
                return await self.async_step_sources()
        return self.async_show_form(step_id='bridge',data_schema=vol.Schema({
            vol.Required('name',default='DomoSenso Frame'):selector.TextSelector(),
            vol.Required('locale',default='en'):selector.SelectSelector(selector.SelectSelectorConfig(options=[{'value':'en','label':'English'},{'value':'de','label':'Deutsch'},{'value':'pl','label':'Polski'}],mode=selector.SelectSelectorMode.DROPDOWN)),
        }),errors=errors)

    async def async_step_sources(self,user_input=None):
        if existing:=self._existing():return existing
        errors={}
        if user_input is not None:
            try:endpoint=ENDPOINT(build_endpoint({**self.settings,**user_input},self.hass.states))
            except (ValueError,TypeError,vol.Invalid):errors['base']='invalid_sources'
            else:
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=endpoint['name'],data={'endpoint':endpoint})
        def entities(domain,multiple=False,classes=None):
            filters={'domain':domain}
            if classes:filters['device_class']=classes
            return selector.EntitySelector(selector.EntitySelectorConfig(filter=filters,multiple=multiple))
        candidates=discover(self.hass.states)
        count=len({row['entity'] for key in ('temperature',*AIR_METRICS,'weather','calendars','media') for row in candidates[key] if row['suggested']})
        schema=vol.Schema({
            vol.Optional('temperature'):entities('sensor',True,['temperature']),
            vol.Optional('air'):entities('sensor',True,sorted({kind for metric in AIR_METRICS for kind in CLASSES[metric]})),
            vol.Optional('weather'):entities('weather'),
            vol.Optional('calendars'):entities('calendar',True),
            vol.Optional('media'):entities('media_player'),
            vol.Optional('radar_presence'):entities('binary_sensor'),
            vol.Optional('radar_distance'):entities('sensor'),
            vol.Optional('radar_near_m',default=1.5):selector.NumberSelector(selector.NumberSelectorConfig(min=0.1,max=20,step=0.1,mode=selector.NumberSelectorMode.BOX,unit_of_measurement='m')),
            vol.Optional('radar_off_delay_s',default=300):selector.NumberSelector(selector.NumberSelectorConfig(min=1,max=3600,step=1,mode=selector.NumberSelectorMode.BOX,unit_of_measurement='s')),
        })
        return self.async_show_form(step_id='sources',data_schema=self.add_suggested_values_to_schema(schema,user_input),errors=errors,description_placeholders={'suggested_count':str(count)})


def _number(lo,hi,step=1,unit=None):
    # NumberSelectorConfig rejects unit_of_measurement=None; leave it out for unitless numbers.
    return selector.NumberSelector(selector.NumberSelectorConfig(min=lo,max=hi,step=step,mode=selector.NumberSelectorMode.BOX,**({'unit_of_measurement':unit} if unit else {})))

class AlbumsOptionsFlow(config_entries.OptionsFlow):
    """Album actions run on the loaded manager; only settings change the entry options."""

    def _manager(self):return self.hass.data.get(ALBUMS)

    def _done(self):return self.async_create_entry(data=dict(self.config_entry.options))

    def _choices(self):
        return [{'value':a['id'],'label':a['name'] or a['path'] or a['id']} for a in self._manager().public_list()]

    async def async_step_init(self,user_input=None):
        if not self._manager():return self.async_abort(reason='not_loaded')
        return self.async_show_menu(step_id='init',menu_options=['add_album','remove_album','sync_now','settings'])

    async def async_step_add_album(self,user_input=None):
        errors={}
        if user_input is not None:
            manager=self._manager()
            try:rec=await manager.add(user_input['url'],user_input.get('password'),user_input.get('path'))
            except AlbumError as err:errors['base']=err.code
            else:
                if not (error:=FLOW_ERRORS.get(rec['status'])):return self._done()
                await manager.remove(rec['id'])  # the user is still in the form: keep nothing
                errors['base']=error
        schema=vol.Schema({
            vol.Required('url'):selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Optional('password'):selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional('path'):selector.TextSelector(),
        })
        shown={k:v for k,v in (user_input or {}).items() if k!='password'}
        return self.async_show_form(step_id='add_album',data_schema=self.add_suggested_values_to_schema(schema,shown),errors=errors)

    async def async_step_remove_album(self,user_input=None):
        if not (choices:=self._choices()):return self.async_abort(reason='no_albums')
        errors={}
        if user_input is not None:
            try:busy=await self._manager().remove(user_input['album_id'])
            except AlbumError as err:errors['base']=err.code
            else:
                if busy is None:return self._done()
                errors['base']='album_busy'
        return self.async_show_form(step_id='remove_album',data_schema=vol.Schema({
            vol.Required('album_id'):selector.SelectSelector(selector.SelectSelectorConfig(options=choices,mode=selector.SelectSelectorMode.LIST))}),errors=errors)

    async def async_step_sync_now(self,user_input=None):
        if not (choices:=self._choices()):return self.async_abort(reason='no_albums')
        if user_input is not None:
            # Runs in the background; the Albums sensor shows progress and the result.
            self.config_entry.async_create_background_task(self.hass,self._manager().sync(user_input.get('album_id'),user_input.get('confirm_deletions',False)),'domosenso_frame album sync now')
            return self._done()
        return self.async_show_form(step_id='sync_now',data_schema=vol.Schema({
            vol.Optional('album_id'):selector.SelectSelector(selector.SelectSelectorConfig(options=choices,mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional('confirm_deletions',default=False):selector.BooleanSelector()}))

    async def async_step_settings(self,user_input=None):
        opts={**DEFAULTS,**self.config_entry.options}
        errors={}
        if user_input is not None:
            try:host_map=parse_host_map(user_input.get('host_map',''))
            except ValueError:errors['host_map']='invalid_host_map'
            else:
                return self.async_create_entry(data={'interval_min':int(user_input['interval_min']),'max_edge':int(user_input['max_edge']),
                    'quality':int(user_input['quality']),'free_floor':int(round(float(user_input['free_floor_gb'])*1e9)),'host_map':host_map})
        schema=vol.Schema({
            vol.Required('interval_min',default=opts['interval_min']):_number(15,1440,1,'min'),
            vol.Required('max_edge',default=opts['max_edge']):_number(1024,8192,1,'px'),
            vol.Required('quality',default=opts['quality']):_number(60,95),
            vol.Required('free_floor_gb',default=opts['free_floor']/1e9):_number(0.1,1000,0.1,'GB'),
            vol.Optional('host_map',description={'suggested_value':format_host_map(opts['host_map'])}):selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
        })
        return self.async_show_form(step_id='settings',data_schema=self.add_suggested_values_to_schema(schema,user_input) if user_input else schema,errors=errors)
