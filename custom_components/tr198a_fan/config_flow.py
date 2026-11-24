from __future__ import annotations
import random
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import selector
from .const import DOMAIN, DIM_STEP_SIZE, CONF_POWER_ON_DELAY, DEFAULT_POWER_ON_DELAY

HANDSET_ID_BITS = 0x1FFF  # 13-bit max
POWER_ON_DELAY_SELECTOR = selector(
    {"number": {"min": 0, "max": 30, "step": 0.5, "mode": "box", "unit_of_measurement": "s"}}
)

class Tr198aConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _LOGGER = __import__("logging").getLogger(__name__)

    # ───────────────── STEP: USER ─────────────────
    async def async_step_user(self, user_input=None):
        errors = {}
        # Build schema with auto_pair option
        schema = vol.Schema(
            {
                vol.Required("remote_entity_id"): selector({"entity": {"domain": "remote"}}),
                vol.Optional("power_switch_entity_id"): selector({"entity": {"domain": "switch"}}),
                vol.Optional(CONF_NAME): str,
                vol.Optional("auto_pair", default=True): bool,
                vol.Optional(DIM_STEP_SIZE, default=2): int,
                vol.Optional(CONF_POWER_ON_DELAY, default=DEFAULT_POWER_ON_DELAY): POWER_ON_DELAY_SELECTOR,
            }
        )

        if user_input is not None:
            auto_pair = user_input.get("auto_pair", True)
            power_switch = user_input.get("power_switch_entity_id")
            if auto_pair and not power_switch:
                errors["auto_pair"] = "auto_pair_requires_power_switch"
                return self.async_show_form(
                    step_id="user",
                    data_schema=schema,
                    errors=errors,
                    description_placeholders={
                        "auto_pair_disabled": True
                    },
                )
            # 1. generate a unique handset-id
            handset_id = random.randint(0, HANDSET_ID_BITS)

            # 2. ensure *ConfigEntry* unique-id uniqueness
            unique_id = f"tr198a_{handset_id:04x}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            data = {
                "remote_entity_id": user_input["remote_entity_id"],
                "handset_id": handset_id,
                CONF_NAME: user_input.get(CONF_NAME) or f"TR198A Fan {handset_id:04X}",
                "auto_pair": auto_pair,
            }
            if power_switch:
                data["power_switch_entity_id"] = power_switch
            title = data[CONF_NAME]
            data[DIM_STEP_SIZE] = user_input[DIM_STEP_SIZE]
            data[CONF_POWER_ON_DELAY] = user_input.get(CONF_POWER_ON_DELAY, DEFAULT_POWER_ON_DELAY)

            return self.async_create_entry(title=title, data=data)
        else:
            return self.async_show_form(
                step_id="user",
                data_schema=schema,
            )

    # ───────────────── (optional) OPTIONS FLOW ─────────────────
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return Tr198aOptionsFlow(config_entry)


class Tr198aOptionsFlow(config_entries.OptionsFlow):
    """Let the user change the friendly-name or swap the RM4 without re-adding."""
    def __init__(self, entry):
        self.entry = entry

    async def async_step_init(self, user_input=None):
        current = self.entry.data
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(
                        "remote_entity_id",
                        default=current["remote_entity_id"],
                    ): selector({"entity": {"domain": "remote"}}),
                    # Suggested rather than default: a fan without a power
                    # switch has no valid default, and clearing the field is
                    # how the switch is removed.
                    vol.Optional(
                        "power_switch_entity_id",
                        description={"suggested_value": current.get("power_switch_entity_id")},
                    ): selector({"entity": {"domain": "switch"}}),
                    vol.Optional(
                        CONF_NAME, default=current.get(CONF_NAME, "")
                    ): str,
                    vol.Optional(
                        DIM_STEP_SIZE,
                        default=current.get(DIM_STEP_SIZE, 2),
                    ): int,
                    vol.Optional(
                        CONF_POWER_ON_DELAY,
                        default=current.get(CONF_POWER_ON_DELAY, DEFAULT_POWER_ON_DELAY),
                    ): POWER_ON_DELAY_SELECTOR,
                }
            )
            return self.async_show_form(step_id="init", data_schema=schema)

        # Settings are kept in entry.data, which is what everything reads.
        data = dict(current)
        data["remote_entity_id"] = user_input["remote_entity_id"]
        if user_input.get(CONF_NAME):
            data[CONF_NAME] = user_input[CONF_NAME]
        if user_input.get("power_switch_entity_id"):
            data["power_switch_entity_id"] = user_input["power_switch_entity_id"]
        else:
            data.pop("power_switch_entity_id", None)
        data[DIM_STEP_SIZE] = user_input[DIM_STEP_SIZE]
        data[CONF_POWER_ON_DELAY] = user_input.get(CONF_POWER_ON_DELAY, DEFAULT_POWER_ON_DELAY)

        # The entities are built around these three, so they need a reload;
        # the dim step and power-on delay are read on every use.
        rebuild = any(
            data.get(key) != current.get(key)
            for key in ("remote_entity_id", "power_switch_entity_id", CONF_NAME)
        )
        changes = {"data": data}
        if self.entry.title == current.get(CONF_NAME):
            changes["title"] = data[CONF_NAME]      # title still follows the fan name
        self.hass.config_entries.async_update_entry(self.entry, **changes)
        if rebuild:
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

        # entry.options only carries the "paired" flag; leave it as it is
        return self.async_create_entry(title="", data=dict(self.entry.options))
