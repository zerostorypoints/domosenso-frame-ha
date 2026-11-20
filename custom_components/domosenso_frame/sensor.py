"""Albums sensor: number of synced albums, public album list as attribute."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .albums import DATA, SIGNAL


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data.get('kind') != 'albums':
        return
    async_add_entities([AlbumsSensor(hass.data[DATA])])


class AlbumsSensor(SensorEntity):
    _attr_name = 'Albums'
    _attr_unique_id = 'domosenso_frame_albums'
    _attr_icon = 'mdi:image-multiple'
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({'albums'})

    def __init__(self, manager):
        self._manager = manager
        self.entity_id = 'sensor.domosenso_frame_albums'

    @property
    def native_value(self) -> int:
        return len(self._manager.albums)

    @property
    def extra_state_attributes(self) -> dict:
        return {'albums': self._manager.public_list()}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self.async_write_ha_state))
