"""Minimal Home Assistant stubs, installed before any test imports the integration."""
import sys
import os
import types
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ---- minimal Home Assistant stubs -----
ha = types.ModuleType('homeassistant')
sys.modules.setdefault('homeassistant', ha)

core = types.ModuleType('homeassistant.core')
ha.core = core
sys.modules['homeassistant.core'] = core
class HomeAssistant: pass
core.HomeAssistant = HomeAssistant
core.callback = lambda func: func

exceptions = types.ModuleType('homeassistant.exceptions')
ha.exceptions = exceptions
sys.modules['homeassistant.exceptions'] = exceptions
class HomeAssistantError(Exception): pass
exceptions.HomeAssistantError = HomeAssistantError

components = types.ModuleType('homeassistant.components')
ha.components = components
sys.modules['homeassistant.components'] = components

class Entity:
    def async_write_ha_state(self):
        self.writes = getattr(self, 'writes', 0) + 1
        self.written = getattr(self, 'written', []) + [getattr(self, 'is_on', None)]
    def async_on_remove(self, func):
        pass
    async def async_added_to_hass(self):
        pass
    @property
    def unique_id(self):
        return getattr(self, '_attr_unique_id', None)

fan_mod = types.ModuleType('homeassistant.components.fan')
components.fan = fan_mod
sys.modules['homeassistant.components.fan'] = fan_mod
class FanEntity(Entity): pass
class FanEntityFeature:
    SET_SPEED = 1
    DIRECTION = 2
    TURN_ON = 4
    TURN_OFF = 8
    PRESET_MODE = 16
fan_mod.FanEntity = FanEntity
fan_mod.FanEntityFeature = FanEntityFeature

light_mod = types.ModuleType('homeassistant.components.light')
components.light = light_mod
sys.modules['homeassistant.components.light'] = light_mod
class LightEntity(Entity): pass
class ColorMode:
    ONOFF = 'onoff'
light_mod.LightEntity = LightEntity
light_mod.ColorMode = ColorMode

button_mod = types.ModuleType('homeassistant.components.button')
components.button = button_mod
sys.modules['homeassistant.components.button'] = button_mod
class ButtonEntity(Entity): pass
button_mod.ButtonEntity = ButtonEntity

helpers = types.ModuleType('homeassistant.helpers')
ha.helpers = helpers
sys.modules['homeassistant.helpers'] = helpers

restore_state = types.ModuleType('homeassistant.helpers.restore_state')
helpers.restore_state = restore_state
sys.modules['homeassistant.helpers.restore_state'] = restore_state
class RestoredExtraData:
    def __init__(self, json_dict):
        self.json_dict = json_dict
    def as_dict(self):
        return self.json_dict
class RestoreEntity:
    """Hands back whatever the test stored in last_state / last_extra."""
    last_state = None
    last_extra = None
    async def async_get_last_state(self):
        return self.last_state
    async def async_get_last_extra_data(self):
        return None if self.last_extra is None else RestoredExtraData(self.last_extra)
restore_state.RestoreEntity = RestoreEntity
restore_state.RestoredExtraData = RestoredExtraData

helpers.device_registry = types.ModuleType('homeassistant.helpers.device_registry')
sys.modules['homeassistant.helpers.device_registry'] = helpers.device_registry
class DeviceInfo(dict):
    pass
helpers.device_registry.DeviceInfo = DeviceInfo

helpers.entity_platform = types.ModuleType('homeassistant.helpers.entity_platform')
sys.modules['homeassistant.helpers.entity_platform'] = helpers.entity_platform
class AddEntitiesCallback: pass
helpers.entity_platform.AddEntitiesCallback = AddEntitiesCallback
class FakePlatform:
    """Records entity services the way EntityPlatform would register them."""
    def __init__(self):
        self.services = {}
    def async_register_entity_service(self, name, schema, func):
        self.services.setdefault(name, func)
helpers.entity_platform.current_platform = FakePlatform()
helpers.entity_platform.async_get_current_platform = lambda: helpers.entity_platform.current_platform

helpers.selector = types.ModuleType('homeassistant.helpers.selector')
sys.modules['homeassistant.helpers.selector'] = helpers.selector
def selector(config):
    """Validate like the real selectors do for the two kinds the flows use."""
    import voluptuous as vol
    kind, = config
    def validate(value):
        if kind == 'entity':
            if not isinstance(value, str) or '.' not in value:
                raise vol.Invalid(f'Entity {value} is neither a valid entity ID nor a valid UUID')
            return value
        return float(value)
    return validate
helpers.selector.selector = selector

helpers.event = types.ModuleType('homeassistant.helpers.event')
sys.modules['homeassistant.helpers.event'] = helpers.event
def async_track_state_change_event(hass, entity_ids, action):
    """Deliver state changes of *entity_ids* to *action*, like the real helper."""
    def listener(entity_id, old_state, new_state):
        if entity_id in entity_ids:
            action(SimpleNamespace(data={
                'entity_id': entity_id, 'old_state': old_state, 'new_state': new_state,
            }))
    hass.states.listeners.append(listener)
    return lambda: hass.states.listeners.remove(listener)
helpers.event.async_track_state_change_event = async_track_state_change_event

ha.util = types.ModuleType('homeassistant.util')
sys.modules['homeassistant.util'] = ha.util
ha.util.percentage = types.ModuleType('homeassistant.util.percentage')
sys.modules['homeassistant.util.percentage'] = ha.util.percentage
def ranged_value_to_percentage(range_, val):
    start, end = range_
    if val == 0:
        return 0
    return (val - start + 1) * 100 // (end - start + 1)

def percentage_to_ranged_value(range_, pct):
    start, end = range_
    return (end - start + 1) * pct / 100 + start - 1
ha.util.percentage.ranged_value_to_percentage = ranged_value_to_percentage
ha.util.percentage.percentage_to_ranged_value = percentage_to_ranged_value

ha.util.scaling = types.ModuleType('homeassistant.util.scaling')
sys.modules['homeassistant.util.scaling'] = ha.util.scaling
def int_states_in_range(range_):
    start, end = range_
    return end - start + 1
ha.util.scaling.int_states_in_range = int_states_in_range

ha.const = types.ModuleType('homeassistant.const')
sys.modules['homeassistant.const'] = ha.const
ha.const.CONF_NAME = 'name'
ha.const.STATE_ON = 'on'
ha.const.STATE_OFF = 'off'
ha.const.EntityCategory = SimpleNamespace(CONFIG='config')

ha.config_entries = types.ModuleType('homeassistant.config_entries')
sys.modules['homeassistant.config_entries'] = ha.config_entries
class ConfigEntry: pass
ha.config_entries.ConfigEntry = ConfigEntry
class _Flow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)
    def async_show_form(self, **kwargs):
        return {'type': 'form', **kwargs}
    def async_create_entry(self, **kwargs):
        return {'type': 'create_entry', **kwargs}
class ConfigFlow(_Flow): pass
class OptionsFlow(_Flow): pass
ha.config_entries.ConfigFlow = ConfigFlow
ha.config_entries.OptionsFlow = OptionsFlow
