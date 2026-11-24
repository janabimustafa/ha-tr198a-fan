from __future__ import annotations
from typing import Any, Callable, Mapping, Optional
import logging, asyncio
from time import monotonic
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity, RestoredExtraData
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.const import CONF_NAME, STATE_ON, STATE_OFF
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import ranged_value_to_percentage, percentage_to_ranged_value
from homeassistant.util.scaling import int_states_in_range
import math
from .const import (
    DOMAIN,
    ATTR_SPEED,
    ATTR_DIRECTION,
    ATTR_TIMER,
    ATTR_BREEZE,
    ATTR_LIGHT,
    ATTR_HANDSET_ID,
    CONF_POWER_ON_DELAY,
    DEFAULT_POWER_ON_DELAY,
    SERVICE_PAIR,
    SERVICE_DIM_UP,
    SERVICE_DIM_DOWN,
    SERVICE_SYNC_LIGHT,
    DEF_STATE
)
from .codec import build_operational_command, LEAD_IN_US, INITIAL_GAP_US
from homeassistant.helpers.event import async_track_state_change_event

_LOGGER = logging.getLogger(__name__)
SPEED_RANGE: tuple[int, int] = (1, 9)       # 0 is *not* in the range
# List of user-facing preset names
PRESET_BREEZE = ["breeze_1", "breeze_2", "breeze_3"]
ATTR_PREV_SPEED = "prev_speed"
# Every packet starts with this much carrier before the first data frame.  A
# power cut inside that window means the receiver never heard the command.
RF_LEAD_IN_S = (LEAD_IN_US + INITIAL_GAP_US) / 1_000_000
# How long the power switch gets to report "on" before a command is abandoned
POWER_ON_TIMEOUT_S = 10

class Tr198aFan(FanEntity, RestoreEntity):
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.DIRECTION
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = PRESET_BREEZE
    _attr_translation_key = "tr198a_fan"
    # TR-198A has 9 discrete speeds (1-9) + 0 = off
    def __init__(self,
                 hass: HomeAssistant,
                 name: str,
                 remote_entity: str,
                 handset_id: int,
                 power_switch: str | None = None):
        self.hass = hass
        self._attr_name          = name
        self._attr_unique_id     = f"tr198a_{handset_id:04x}"
        self._remote_entity_id   = remote_entity
        self._handset_id         = handset_id
        # What the *receiver* holds.  It keeps this through a power cut and
        # comes back in the same state, so it is tracked separately from
        # whether the receiver currently has power (self._powered).
        self._state: dict[str, Any] = DEF_STATE.copy()
        self._prev_speed: int = 5      # last running speed, for a bare turn_on
        self._dev_id = (DOMAIN, f"{handset_id:04x}")
        self._power_switch_id    = power_switch
        self._entry_id: str | None = None
        self._powered: bool = True                 # False only while the switch is off
        self._powered_at: float | None = None      # monotonic() of the last power-up
        self._waking: bool = False                 # a command of ours is restoring power
        # (deadline, state before) of each command still in its RF lead-in,
        # i.e. that the receiver cannot have heard yet; oldest first
        self._unconfirmed: list[tuple[float, dict[str, Any]]] = []
        self._lock = asyncio.Lock()                # one RF transaction at a time
        self._listeners: list[Callable[[], None]] = []
        self._attr_device_info = DeviceInfo(
            identifiers={self._dev_id},
            manufacturer="TR-198A",
            model="Ceiling-Fan Remote",
        )


    # ─────── internal helpers ───────
    async def _send_base64(self, cmd: str):
        """Low-level helper used by both fan actions & buttons."""
        await self.hass.services.async_call(
            "remote",
            "send_command",
            {
                "entity_id": self._remote_entity_id,   # ← back to schema-approved key
                "command": [cmd],
            },
            blocking=True,
        )

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call *listener* whenever the tracked state changes (used by the light)."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _write_states(self) -> None:
        self.async_write_ha_state()
        for listener in list(self._listeners):
            listener()

    # ───────────────── RAW SENDER (buttons) ─────────────────
    async def _send_state(self, *,
                          radio_repeats=None,
                          trailer_us=None,
                          **overrides):
        """
        Transmit the **entire** current state plus one-off *overrides* (dim
        step, raw light toggle …) without changing what is tracked.
        """
        async with self._lock:
            try:
                await self._ensure_power_on()
                base = dict(
                    speed=self._state[ATTR_SPEED],
                    direction=self._state[ATTR_DIRECTION],
                    timer=self._state[ATTR_TIMER],
                    breeze=self._state[ATTR_BREEZE],
                    light_toggle=False,
                )
                base.update(overrides)                   # apply caller’s changes
                if radio_repeats is not None:
                    base["radio_repeats"] = radio_repeats
                if trailer_us is not None:
                    base["trailer_us"] = trailer_us

                await self._send_base64(build_operational_command(self._handset_id, **base))
            finally:
                self._write_states()

    # ───────────────── STATE-CHANGING SENDER ─────────────────
    async def _async_apply(self, **changes) -> None:
        """
        Bring the receiver to its current state plus *changes* (ATTR_SPEED,
        ATTR_BREEZE, ATTR_DIRECTION, ATTR_LIGHT) and track the result.
        """
        async with self._lock:
            if self._powered:
                if set(changes) == {ATTR_LIGHT} and changes[ATTR_LIGHT] == self._state[ATTR_LIGHT]:
                    return                  # light already as requested – don't toggle
                all_off: dict[str, Any] = {}
            else:
                # Power is cut: fan and light are off, whatever the receiver
                # will restore.  Start from "all off" so that only what was
                # asked for comes on once power is back.
                all_off = {ATTR_SPEED: 0, ATTR_BREEZE: None, ATTR_LIGHT: False}
                target = {**all_off, **changes}
                if not (target[ATTR_SPEED] or target[ATTR_BREEZE] or target[ATTR_LIGHT]):
                    # Nothing to switch on – leave the receiver unpowered and
                    # just remember the direction for the next packet.
                    if ATTR_DIRECTION in changes:
                        self._state[ATTR_DIRECTION] = changes[ATTR_DIRECTION]
                        self._write_states()
                    return
            try:
                await self._ensure_power_on()

                # The receiver is up and in the state it remembered (self._state).
                # The light bit is a toggle, so it is sent only when the receiver's
                # light differs from what we want.  Everything else is absolute.
                before = self._state
                target = {**before, **all_off, **changes}
                cmd = build_operational_command(
                    self._handset_id,
                    speed=target[ATTR_SPEED],
                    direction=target[ATTR_DIRECTION],
                    timer=target[ATTR_TIMER],
                    breeze=target[ATTR_BREEZE],
                    light_toggle=target[ATTR_LIGHT] != before[ATTR_LIGHT],
                )
                now = monotonic()
                unheard = (now + RF_LEAD_IN_S, before)
                self._unconfirmed = [u for u in self._unconfirmed if u[0] > now] + [unheard]
                self._state = target
                try:
                    await self._send_base64(cmd)
                except BaseException:
                    if self._state is target:       # not already undone by a power cut
                        self._state = before
                        self._unconfirmed.remove(unheard)
                    raise
                if not target[ATTR_BREEZE] and target[ATTR_SPEED]:
                    self._prev_speed = target[ATTR_SPEED]   # remember last running speed
            finally:
                self._write_states()

    async def async_set_light(self, on: bool) -> None:
        """Turn the light on/off (called by the light entity)."""
        await self._async_apply(**{ATTR_LIGHT: on})

    @property
    def light_is_on(self) -> bool:
        return self._powered and bool(self._state[ATTR_LIGHT])

    # ───────────────── POWER SWITCH ─────────────────
    def _switch_is(self, wanted: str) -> bool:
        state = self.hass.states.get(self._power_switch_id)
        return state is not None and state.state == wanted

    def _power_on_delay(self) -> float:
        """Seconds the receiver needs after power-up before it accepts RF."""
        entry = None
        if self._entry_id:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return DEFAULT_POWER_ON_DELAY
        return float(entry.data.get(CONF_POWER_ON_DELAY, DEFAULT_POWER_ON_DELAY))

    @callback
    def _note_power(self, powered: bool) -> None:
        """Record that the receiver gained or lost mains power."""
        if powered == self._powered:
            return
        self._powered = powered
        if powered:
            self._powered_at = monotonic()
        else:
            now = monotonic()
            unheard = [u for u in self._unconfirmed if u[0] > now]
            if unheard:
                # Cut before these commands made it on air (e.g. "turn
                # everything off" also switched the power off): the receiver
                # still holds, and will restore, the state from before them.
                _LOGGER.debug("%s: power cut before %d command(s) were transmitted; "
                              "assuming the receiver missed them",
                              self._power_switch_id, len(unheard))
                self._state = unheard[0][1]
            self._unconfirmed = []
        if not (powered and self._waking):
            # (when a command of ours restores power, what the receiver brings
            # back is about to be replaced; the command writes the end result)
            self._write_states()

    async def _ensure_power_on(self) -> None:
        """
        If a power-switch is configured and OFF, turn it on, then wait until
        the receiver has had time to boot – it ignores RF sent before that.
        """
        if not self._power_switch_id:
            return

        if not self._powered:
            asked = monotonic()
            self._waking = True
            try:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._power_switch_id}, blocking=True
                )
                # Poll the state machine so we don’t race the next RF send
                while not self._switch_is(STATE_ON):
                    if monotonic() - asked >= POWER_ON_TIMEOUT_S:
                        raise HomeAssistantError(
                            f"Power switch {self._power_switch_id} did not turn on; command not sent"
                        )
                    await asyncio.sleep(0.1)
                self._note_power(True)      # no-op if the state listener got there first
            finally:
                self._waking = False

        while self._powered_at is not None and (
            remaining := self._powered_at + self._power_on_delay() - monotonic()
        ) > 0:
            await asyncio.sleep(remaining)

        if not self._powered:
            raise HomeAssistantError(
                f"Power switch {self._power_switch_id} turned off again; command not sent"
            )

    # ─────── FanEntity API ───────
    @property
    def percentage(self):
        """Return current speed as 0-100 % (or None if off)."""
        speed = self._state[ATTR_SPEED] if self._powered else 0
        return ranged_value_to_percentage(SPEED_RANGE, speed)
    @property
    def speed_count(self) -> int:
        """Return number of discrete speeds the fan supports (excluding off)."""
        return int_states_in_range(SPEED_RANGE)

    async def async_set_percentage(self, percentage: int):
        # Convert 0-100 % → 1-9.  round UP to ensure >0 % becomes speed 1
        speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        speed = min(speed, SPEED_RANGE[1])  # clamp to max speed
        await self._async_apply(**{ATTR_SPEED: speed, ATTR_BREEZE: None})

    async def async_turn_on(self, percentage: int | None = None,
                            preset_mode: str | None = None, **kwargs):
        """Turn on at *percentage*, in *preset_mode*, or at the last remembered speed."""
        if preset_mode is not None:
            await self.async_set_preset_mode(preset_mode)
            return

        if percentage is not None:           # no value given → use remembered speed
            speed = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
            speed = max(1, min(speed, SPEED_RANGE[1]))  # clamp to 1-9
        else:
            speed = self._prev_speed or 1

        await self._async_apply(**{ATTR_SPEED: speed, ATTR_BREEZE: None})

    async def async_turn_off(self, **kwargs):
        await self._async_apply(**{ATTR_SPEED: 0, ATTR_BREEZE: None})

    @property
    def direction(self):
        return self._state[ATTR_DIRECTION]

    async def async_set_direction(self, direction: str):
        if direction not in ("forward", "reverse"):
            raise ValueError(f"Invalid direction: {direction}")

        await self._async_apply(**{ATTR_DIRECTION: direction})
    @property
    def preset_mode(self) -> Optional[str]:
        lvl = self._state[ATTR_BREEZE] if self._powered else None
        return None if lvl is None else PRESET_BREEZE[lvl - 1]

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set one of the three breeze modes."""
        if preset_mode not in PRESET_BREEZE:
            raise ValueError(f"Unsupported preset {preset_mode}")

        level = PRESET_BREEZE.index(preset_mode) + 1  # 1,2,3
        await self._async_apply(**{ATTR_BREEZE: level})  # speed bits replaced
    # ─────── RestoreEntity ───────
    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Persist what the receiver holds, even while its power is cut."""
        return RestoredExtraData({
            ATTR_SPEED: self._state[ATTR_SPEED],
            ATTR_DIRECTION: self._state[ATTR_DIRECTION],
            ATTR_BREEZE: self._state[ATTR_BREEZE],
            ATTR_LIGHT: self._state[ATTR_LIGHT],
            ATTR_PREV_SPEED: self._prev_speed,
        })

    def _restore(self, data: Mapping[str, Any]) -> None:
        try:
            speed = int(data.get(ATTR_SPEED) or 0)
        except (TypeError, ValueError):
            speed = 0
        self._state[ATTR_SPEED] = speed
        if data.get(ATTR_DIRECTION) in ("forward", "reverse"):
            self._state[ATTR_DIRECTION] = data[ATTR_DIRECTION]
        breeze = data.get(ATTR_BREEZE)
        self._state[ATTR_BREEZE] = breeze if breeze in (1, 2, 3) else None
        self._state[ATTR_LIGHT] = bool(data.get(ATTR_LIGHT))
        try:
            self._prev_speed = int(data.get(ATTR_PREV_SPEED) or speed) or self._prev_speed
        except (TypeError, ValueError):
            pass

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) is not None:
            self._restore(extra.as_dict())
        elif (state := await self.async_get_last_state()) is not None:
            # saved by a version that only had the state attributes
            self._restore(state.attributes)

        self._subscribe_power_switch()

    # ─────── state attributes ───────
    @property
    def extra_state_attributes(self):
        return {
            ATTR_SPEED: self._state[ATTR_SPEED] if self._powered else 0,
            ATTR_DIRECTION: self._state[ATTR_DIRECTION],
            ATTR_TIMER: self._state[ATTR_TIMER],
            ATTR_BREEZE: self._state[ATTR_BREEZE] if self._powered else None,
            ATTR_LIGHT: self.light_is_on,
            ATTR_HANDSET_ID: hex(self._handset_id),
        }

    def _subscribe_power_switch(self):
        if not self._power_switch_id:
            return
        # Only an explicit "off" means the receiver is unpowered
        self._powered = not self._switch_is(STATE_OFF)

        @callback
        def power_switch_listener(event):
            new_state = event.data.get("new_state")
            # unavailable/unknown and attribute-only updates say nothing about
            # mains power, and must not disturb the tracked state
            if new_state is None or new_state.state not in (STATE_ON, STATE_OFF):
                return
            self._note_power(new_state.state == STATE_ON)

        self.async_on_remove(async_track_state_change_event(
            self.hass, [self._power_switch_id], power_switch_listener
        ))

async def cycle_power_and_pair(hass, switch_id, handset_id, send_base64_func):
    from .codec import build_pair_command
    cmd = build_pair_command(handset_id)
    state = hass.states.get(switch_id)
    if state:
        if state.state == "on":
            await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
            await asyncio.sleep(0.5)
            await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
            for _ in range(20):
                await asyncio.sleep(0.1)
                st = hass.states.get(switch_id)
                if st and st.state == "on":
                    break
        else:
            await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
            for _ in range(20):
                await asyncio.sleep(0.1)
                st = hass.states.get(switch_id)
                if st and st.state == "on":
                    break
    await send_base64_func(cmd)

async def _async_handle_service(fan: Tr198aFan, call) -> None:
    """tr198a_fan.pair / dim_up / dim_down / sync_light on a fan entity."""
    from .button import _execute
    await _execute(fan, call.service)

async def async_setup_entry(
    hass, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = entry.data
    name        = data.get(CONF_NAME) or f"TR198A Fan {data['handset_id']:04X}"
    switch_id = data.get("power_switch_entity_id")

    # Entity services: Home Assistant resolves the target (entity, device,
    # area …) to this integration's fan entities, whichever entry they belong to
    platform = entity_platform.async_get_current_platform()
    for svc in (SERVICE_PAIR, SERVICE_DIM_UP, SERVICE_DIM_DOWN, SERVICE_SYNC_LIGHT):
        platform.async_register_entity_service(svc, None, _async_handle_service)

    fan = Tr198aFan(hass, name, data["remote_entity_id"], data["handset_id"], power_switch=switch_id)
    fan._entry_id = entry.entry_id  # lets the fan (and buttons) look up entry options
    async_add_entities([fan])
    hass.data[DOMAIN][entry.entry_id]["fan_unique_id"] = fan.unique_id
    hass.data[DOMAIN][entry.entry_id]["fan_entity"]    = fan  # ← give buttons direct access

    # Automatically start pairing if a power switch is associated and auto_pair is enabled
    paired = entry.options.get("paired", False)
    if switch_id and data.get("auto_pair", True) and not paired:
        await cycle_power_and_pair(hass, switch_id, fan._handset_id, fan._send_base64)
        # Save the paired flag in options
        new_options = dict(entry.options)
        new_options["paired"] = True
        hass.config_entries.async_update_entry(entry, options=new_options)
