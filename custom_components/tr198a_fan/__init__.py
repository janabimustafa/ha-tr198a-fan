from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, DIM_STEP_SIZE, CONF_POWER_ON_DELAY

_LOGGER = logging.getLogger(__name__)

# What the options form of earlier versions saved into entry.options *and*
# that was read back from there.  (It also saved the remote and the name,
# but those were only ever read from entry.data.)
_LEGACY_OPTION_KEYS = ("power_switch_entity_id", DIM_STEP_SIZE, CONF_POWER_ON_DELAY)

@callback
def _fold_legacy_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Settings live in entry.data; entry.options only keeps the "paired" flag.
    Move over what an earlier options form left behind, keeping the values
    that were in effect.
    """
    if all(key == "paired" for key in entry.options):
        return
    data = dict(entry.data)
    for key in _LEGACY_OPTION_KEYS:
        if entry.options.get(key) not in (None, ""):
            data[key] = entry.options[key]
    options = {key: value for key, value in entry.options.items() if key == "paired"}
    hass.config_entries.async_update_entry(entry, data=data, options=options)

async def async_setup(hass: HomeAssistant, _: dict) -> bool:
    return True                                    # YAML disabled

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _fold_legacy_options(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}
    # ← we forward to fan, button, AND light platforms
    await hass.config_entries.async_forward_entry_setups(entry, {"fan"})
    await hass.config_entries.async_forward_entry_setups(entry, {"button", "light"})
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, {"fan", "button", "light"})
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded