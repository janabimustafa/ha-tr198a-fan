from __future__ import annotations
from homeassistant.components.light import LightEntity, ColorMode
from .const import DOMAIN

class Tr198aLight(LightEntity):
    """The fan's light.  All state lives in the fan entity, which owns the RF link."""
    _attr_should_poll = False
    _attr_translation_key = "tr198a_light"
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    def __init__(self, fan_entity):
        self.fan = fan_entity
        self.hass = fan_entity.hass
        self._attr_name = f"{fan_entity._attr_name} Light"
        self._attr_unique_id = f"{fan_entity._attr_unique_id}_light"
        self._attr_device_info = fan_entity._attr_device_info

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # follow every change the fan tracks (commands, power switch, …)
        self.async_on_remove(self.fan.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self):
        return self.fan.light_is_on

    async def async_turn_on(self, **kwargs):
        await self.fan.async_set_light(True)

    async def async_turn_off(self, **kwargs):
        await self.fan.async_set_light(False)

    @property
    def extra_state_attributes(self):
        return {}

    @property
    def color_mode(self):
        return ColorMode.ONOFF

async def async_setup_entry(hass, entry, async_add_entities):
    fan_entity = hass.data[DOMAIN][entry.entry_id]["fan_entity"]
    light = Tr198aLight(fan_entity)
    async_add_entities([light])
    hass.data[DOMAIN][entry.entry_id]["light_entity"] = light

