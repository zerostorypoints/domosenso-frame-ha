"""Admin-owned display overrides, independent of device grants and source mapping."""
import asyncio
import copy
import hashlib
import json
from .display import validate_config,default_config

class Conflict(ValueError):
    """The caller edited a configuration that has since changed."""

class Layouts:
    def __init__(self,endpoints,saved,persist):
        self.endpoints=endpoints;self.saved=copy.deepcopy(saved);self.persist=persist;self.lock=asyncio.Lock()
        for eid,value in self.saved.items():
            if eid in endpoints and 'display' in endpoints[eid]:validate_config(value['config'])

    def get(self,eid):
        endpoint=self.endpoints.get(eid)
        if not endpoint or 'display' not in endpoint:raise ValueError('Display unavailable')
        settings=endpoint['display'];default=settings.get('config') or default_config(endpoint,settings['locale'])
        entry=self.saved.get(eid);config=entry['config'] if entry else default
        revision=hashlib.sha256(json.dumps([config,entry.get('sequence',0) if entry else 0,default],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        capabilities=[k for k in ('temperature','air') if endpoint.get(k)]+[k for k in ('weather','status') if settings.get(k)]
        return copy.deepcopy({'config':config,'defaults':default,'revision':revision,'source':'saved' if entry else 'default','capabilities':capabilities,'helperOverrides':bool(settings.get('mode_entity') or settings.get('page_entity'))})

    async def save(self,eid,revision,config):
        config=copy.deepcopy(validate_config(config))
        # Every playlist must retain a safe screen when integrations disappear.
        fallback={s['id'] for s in config['screens'] if not s.get('requires')}
        if any(not fallback.intersection(mode['screens']) for mode in config['modes']):raise ValueError('Each mode needs an unconditional screen')
        async with self.lock:
            current=self.get(eid)
            if current['revision']!=revision:raise Conflict('Layout changed; reload before saving')
            updated=copy.deepcopy(self.saved)
            updated[eid]={'config':config,'sequence':self.saved.get(eid,{}).get('sequence',0)+1}
            await self.persist(copy.deepcopy(updated))
            self.saved=updated
            return self.get(eid)

async def save_verified(store,data,path,key,executor):
    """HA Store logs write failures; verify the on-disk envelope before publishing."""
    await store.async_save(data)
    def verify():
        try:
            envelope=json.loads(path.read_text())
            if envelope.get('version')!=1 or envelope.get('key')!=key or envelope.get('data')!=data:
                raise OSError('Layout storage verification failed')
        except (ValueError,AttributeError) as error:
            raise OSError('Layout storage verification failed') from error
    await executor(verify)
