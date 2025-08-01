from __future__ import annotations
from homeassistant.components.light import LightEntity, ColorMode
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.const import CONF_NAME
from .const import DOMAIN, ATTR_LIGHT

class Tr198aLight(LightEntity):
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

    @property
    def is_on(self):
        return self.fan._state[ATTR_LIGHT]

    async def async_turn_on(self, **kwargs):
        # Ensure power switch is on before toggling the light
        was_off = False
        if self.fan._power_switch_id:
            state = self.hass.states.get(self.fan._power_switch_id)
            if not state or state.state != "on":
                was_off = True
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self.fan._power_switch_id}, blocking=True
                )

        # If the power switch was off and the light was previously on, the
        # physical light will turn on automatically when power is restored.
        if was_off and self.fan._prev_light:
            self.fan._state[ATTR_LIGHT] = True
            self.fan._prev_light = True
            self.fan.async_write_ha_state()
            self.async_write_ha_state()
            return

        if not self.fan._state[ATTR_LIGHT]:
            await self.fan._send_state(light_toggle=True)
            self.fan._state[ATTR_LIGHT] = True
            self.fan._prev_light = True
            self.fan.async_write_ha_state()
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        if self.fan._state[ATTR_LIGHT]:
            await self.fan._send_state(light_toggle=True)
            self.fan._state[ATTR_LIGHT] = False
            self.fan._prev_light = False
            self.fan.async_write_ha_state()
            self.async_write_ha_state()

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

