"""Administrator onboarding for a vanilla or DomoSenso Home Assistant."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from . import DOMAIN, ENDPOINT
from .onboarding import AIR_METRICS, CLASSES, build_endpoint
from .studio_mappings import discover

class FrameConfigFlow(config_entries.ConfigFlow,domain=DOMAIN):
    VERSION=1

    def _existing(self):
        if self.hass.data.get(DOMAIN,{}).get('source')=='yaml':
            return self.async_abort(reason='yaml_configured')
        if self._async_current_entries():return self.async_abort(reason='already_configured')
        return None

    async def async_step_user(self,user_input=None):
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
        return self.async_show_form(step_id='user',data_schema=vol.Schema({
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
