import asyncio
import random
from types import SimpleNamespace
import pytest

from homeassistant.exceptions import HomeAssistantError      # stub from conftest.py
from custom_components.tr198a_fan import fan as fan_module
from custom_components.tr198a_fan.const import (
    DOMAIN, ATTR_SPEED, ATTR_BREEZE, ATTR_DIRECTION, ATTR_LIGHT,
    CONF_POWER_ON_DELAY, DEFAULT_POWER_ON_DELAY,
)
from custom_components.tr198a_fan.codec import (
    build_operational_command, LEAD_IN_US, INITIAL_GAP_US,
)
from custom_components.tr198a_fan.fan import Tr198aFan, cycle_power_and_pair
from custom_components.tr198a_fan.light import Tr198aLight
from custom_components.tr198a_fan.button import _execute
from custom_components.tr198a_fan.const import SERVICE_SYNC_LIGHT

HANDSET = 1
SWITCH = 'switch.main'
# time from handing a packet to the RM4 until the receiver has decoded a frame
RF_FLIGHT_S = (LEAD_IN_US + INITIAL_GAP_US) / 1_000_000 + 0.03

# every packet the integration can send, so tests can read back what went out
PACKETS = {}
for _dir in ('forward', 'reverse'):
    for _toggle in (False, True):
        for _speed in range(10):
            PACKETS[build_operational_command(
                HANDSET, speed=_speed, direction=_dir, light_toggle=_toggle
            )] = dict(speed=_speed, breeze=None, direction=_dir, toggle=_toggle)
        for _breeze in (1, 2, 3):
            PACKETS[build_operational_command(
                HANDSET, breeze=_breeze, direction=_dir, light_toggle=_toggle
            )] = dict(speed=None, breeze=_breeze, direction=_dir, toggle=_toggle)

# ---- helpers ----
class Clock:
    def __init__(self):
        self.now = 1000.0
        self.watchers = []
    def advance(self, seconds):
        self.now += seconds
        for watcher in self.watchers:
            watcher(self.now)

class Receiver:
    """
    The physical fan receiver, as the integration assumes it behaves: it keeps
    its state through a power cut, needs BOOT_S after power-up before it hears
    RF, takes speed/breeze as absolute values and the light bit as a toggle.
    """
    def __init__(self, clock, boot_s=1.5):
        self.clock = clock
        self.boot_s = boot_s
        self.power = True
        self.ready_at = 0.0
        self.speed, self.breeze, self.light = 0, None, False
        self.pending = []           # (arrival time, decoded packet)
        self.heard = 0
        clock.watchers.append(self.catch_up)

    def catch_up(self, now):
        while self.pending and self.pending[0][0] <= now:
            arrival, packet = self.pending.pop(0)
            if self.power and arrival >= self.ready_at:
                self.heard += 1
                self.speed = packet['speed'] or 0
                self.breeze = packet['breeze']
                if packet['toggle']:
                    self.light = not self.light

    def transmit(self, cmd):
        self.pending.append((self.clock.now + RF_FLIGHT_S, PACKETS[cmd]))

    def set_power(self, on):
        self.catch_up(self.clock.now)
        if on and not self.power:
            self.ready_at = self.clock.now + self.boot_s
        self.power = on

    @property
    def doing(self):
        """(fan running?, speed, breeze, light) as seen by someone in the room."""
        if not self.power:
            return (False, 0, None, False)
        return (bool(self.speed or self.breeze), self.speed, self.breeze, self.light)

    @property
    def memory(self):
        return (self.speed, self.breeze, self.light)

class FakeState:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}

class FakeStates:
    def __init__(self):
        self._data = {}
        self.listeners = []
    def get(self, entity_id):
        return self._data.get(entity_id)
    def set(self, entity_id, state, attributes=None):
        old = self._data.get(entity_id)
        new = self._data[entity_id] = FakeState(state, attributes)
        for listener in list(self.listeners):
            listener(entity_id, old, new)

class FakeServices:
    def __init__(self, hass):
        self.hass = hass
        self.calls = []
        self.switch_works = True
    async def async_call(self, domain, service, data, *, blocking=False):
        self.calls.append((domain, service, data, blocking))
        if domain == 'switch' and self.switch_works:
            self.hass.set_switch(data['entity_id'], service == 'turn_on')
        if domain == 'remote':
            for cmd in data['command']:
                if self.hass.receiver and cmd in PACKETS:
                    self.hass.receiver.transmit(cmd)
            await asyncio.sleep(0.05)       # network round trip to the RM4
    @property
    def packets(self):
        return [PACKETS[cmd] for d, _, data, _ in self.calls if d == 'remote'
                for cmd in data['command']]
    @property
    def switch_calls(self):
        return [service for d, service, _, _ in self.calls if d == 'switch']

class FakeHass:
    def __init__(self, receiver=None):
        self.states = FakeStates()
        self.services = FakeServices(self)
        self.receiver = receiver
        self.entries = {}
        self.config_entries = SimpleNamespace(async_get_entry=self.entries.get)
        self.data = {DOMAIN: {'entry1': {}}}
    def set_switch(self, entity_id, on):
        """The switch changed, by whatever means: relay first, then the state."""
        if self.receiver:
            self.receiver.set_power(on)
        self.states.set(entity_id, 'on' if on else 'off')

# fixture: virtual time, so power-on delays cost nothing
@pytest.fixture(autouse=True)
def clock(monkeypatch):
    clock = Clock()
    real_sleep = asyncio.sleep
    async def fake_sleep(delay):
        clock.advance(delay)
        await real_sleep(0)             # still let other tasks run
    monkeypatch.setattr(asyncio, 'sleep', fake_sleep)
    monkeypatch.setattr(fan_module, 'monotonic', lambda: clock.now)
    return clock

async def make(clock, *, switch='on', receiver=True, restore=None, boot_s=1.5):
    """A fan + light pair on a fake hass, optionally wired to a simulated receiver."""
    rx = None
    if receiver is True:
        rx = Receiver(clock, boot_s)
    elif receiver:
        rx = receiver
    hass = FakeHass(rx)
    if switch is not None:
        if rx:
            rx.power = switch == 'on'
        hass.states.set(SWITCH, switch)
    fan = Tr198aFan(hass, 'Fan', 'remote.rm4', HANDSET, power_switch=SWITCH)
    if restore:
        fan.last_extra = restore
    await fan.async_added_to_hass()
    light = Tr198aLight(fan)
    await light.async_added_to_hass()
    return hass, fan, light, rx

def shown(fan, light):
    """What Home Assistant shows, in the same shape as Receiver.doing."""
    breeze = fan.extra_state_attributes[ATTR_BREEZE]
    # a breeze preset replaces the speed, so the last speed kept for display doesn't count
    speed = 0 if breeze else fan.extra_state_attributes[ATTR_SPEED]
    assert (fan.preset_mode is not None) == bool(breeze)
    return (bool(fan.percentage or fan.preset_mode) if not breeze else True, speed,
            breeze, light.is_on)

def remembered(fan):
    breeze = fan._state[ATTR_BREEZE]
    return (0 if breeze else fan._state[ATTR_SPEED], breeze, fan._state[ATTR_LIGHT])

# ---- power switch handling ----
@pytest.mark.asyncio
async def test_ensure_power_on_no_action(clock):
    hass, fan, _, _ = await make(clock)
    await fan._ensure_power_on()
    assert hass.services.calls == []

@pytest.mark.asyncio
async def test_ensure_power_on_turns_on_and_waits_for_boot(clock):
    hass, fan, _, _ = await make(clock, switch='off')
    start = clock.now
    await fan._ensure_power_on()
    assert ('switch', 'turn_on', {'entity_id': SWITCH}, True) in hass.services.calls
    assert hass.states.get(SWITCH).state == 'on'
    assert clock.now - start >= DEFAULT_POWER_ON_DELAY

@pytest.mark.asyncio
async def test_power_on_delay_is_configurable(clock):
    hass, fan, _, _ = await make(clock, switch='off')
    fan._entry_id = 'entry1'
    hass.entries['entry1'] = SimpleNamespace(data={CONF_POWER_ON_DELAY: 6}, options={})
    start = clock.now
    await fan._ensure_power_on()
    assert 6 <= clock.now - start < 6.5

@pytest.mark.asyncio
async def test_switch_that_never_turns_on_aborts_the_command(clock):
    hass, fan, light, rx = await make(clock, switch='off')
    hass.services.switch_works = False
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    assert hass.services.packets == []
    assert remembered(fan) == (0, None, False)
    assert shown(fan, light) == rx.doing == (False, 0, None, False)

@pytest.mark.asyncio
async def test_power_switch_shows_everything_off_then_restores(clock):
    hass, fan, light, rx = await make(clock)
    await fan.async_set_percentage(33)
    await light.async_turn_on()
    clock.advance(5)
    assert shown(fan, light) == rx.doing == (True, 3, None, True)

    hass.set_switch(SWITCH, False)
    assert shown(fan, light) == rx.doing == (False, 0, None, False)
    assert fan.writes and light.writes

    hass.set_switch(SWITCH, True)
    assert shown(fan, light) == rx.doing == (True, 3, None, True)

@pytest.mark.asyncio
async def test_power_restore_does_not_revive_a_fan_that_was_off(clock):
    hass, fan, light, rx = await make(clock)
    await fan.async_set_percentage(33)
    await light.async_turn_on()
    await fan.async_turn_off()
    clock.advance(5)

    hass.set_switch(SWITCH, False)
    hass.set_switch(SWITCH, True)
    assert shown(fan, light) == rx.doing == (False, 0, None, True)

@pytest.mark.asyncio
async def test_switch_noise_does_not_change_state(clock):
    """Attribute updates and availability blips are not power cycles."""
    hass, fan, light, rx = await make(clock)
    await fan.async_set_percentage(33)
    await fan.async_turn_off()
    await light.async_turn_on()
    await light.async_turn_off()
    clock.advance(5)

    hass.states.set(SWITCH, 'on', {'power_w': 12})
    hass.states.set(SWITCH, 'unavailable')
    hass.states.set(SWITCH, 'on')
    assert shown(fan, light) == rx.doing == (False, 0, None, False)

    # ... and a switch that was off before it dropped out is still off
    await light.async_turn_on()
    clock.advance(5)
    hass.set_switch(SWITCH, False)
    hass.states.set(SWITCH, 'unavailable')
    assert shown(fan, light) == rx.doing == (False, 0, None, False)

# ---- light ----
@pytest.mark.asyncio
async def test_light_toggles_only_when_needed(clock):
    hass, fan, light, rx = await make(clock)
    await light.async_turn_on()
    await light.async_turn_on()         # already on: a second toggle would turn it off
    assert [p['toggle'] for p in hass.services.packets] == [True]
    clock.advance(5)
    assert light.is_on and rx.light

    await light.async_turn_off()
    await light.async_turn_off()
    assert [p['toggle'] for p in hass.services.packets] == [True, True]
    clock.advance(5)
    assert not light.is_on and not rx.light

@pytest.mark.asyncio
async def test_light_on_after_power_cut_waits_for_receiver_to_boot(clock):
    """The toggle used to go out while the receiver was still starting up."""
    hass, fan, light, rx = await make(clock, switch='off', boot_s=3.0)
    await light.async_turn_on()
    clock.advance(5)
    assert rx.heard == 1
    assert shown(fan, light) == rx.doing == (False, 0, None, True)

@pytest.mark.asyncio
async def test_light_on_after_power_cut_when_receiver_restores_it(clock):
    hass, fan, light, rx = await make(clock)
    await fan.async_set_percentage(55)
    await light.async_turn_on()
    clock.advance(5)
    hass.set_switch(SWITCH, False)
    clock.advance(60)

    # the receiver brings back fan + light by itself; only the light was asked for
    await light.async_turn_on()
    clock.advance(5)
    assert hass.services.packets[-1] == dict(speed=0, breeze=None, direction='reverse', toggle=False)
    assert shown(fan, light) == rx.doing == (False, 0, None, True)

@pytest.mark.asyncio
async def test_light_follows_fan_state_changes(clock):
    hass, fan, light, rx = await make(clock)
    before = getattr(light, 'writes', 0)
    await light.async_turn_on()
    assert light.writes > before

# ---- fan and light stay independent (GitHub #4) ----
@pytest.mark.asyncio
async def test_fan_on_after_power_cut_leaves_light_off(clock):
    hass, fan, light, rx = await make(clock)
    await light.async_turn_on()
    clock.advance(5)
    hass.set_switch(SWITCH, False)
    clock.advance(60)

    light.written = []
    await fan.async_turn_on(percentage=55)
    clock.advance(5)
    # the receiver restored the light on power-up, so it is toggled back off
    assert hass.services.packets[-1] == dict(speed=5, breeze=None, direction='reverse', toggle=True)
    assert shown(fan, light) == rx.doing == (True, 5, None, False)
    # ... and that brief restore is never reported as the light turning on
    assert light.written and True not in light.written

@pytest.mark.asyncio
async def test_fan_commands_never_touch_the_light(clock):
    hass, fan, light, rx = await make(clock)
    await light.async_turn_on()
    await fan.async_turn_on()
    await fan.async_set_percentage(100)
    await fan.async_set_direction('forward')
    await fan.async_set_preset_mode('breeze_2')
    await fan.async_turn_off()
    clock.advance(5)
    assert [p['toggle'] for p in hass.services.packets] == [True, False, False, False, False, False]
    assert shown(fan, light) == rx.doing == (False, 0, None, True)

@pytest.mark.asyncio
async def test_turning_off_while_unpowered_does_not_power_up(clock):
    hass, fan, light, rx = await make(clock)
    await fan.async_set_percentage(55)
    await light.async_turn_on()
    clock.advance(5)
    hass.set_switch(SWITCH, False)
    hass.services.calls.clear()

    await fan.async_turn_off()
    await fan.async_set_percentage(0)
    await light.async_turn_off()
    await fan.async_set_direction('forward')
    assert hass.services.calls == []
    assert hass.states.get(SWITCH).state == 'off'
    assert fan.direction == 'forward'
    # the receiver was never told, so it still comes back as it was
    hass.set_switch(SWITCH, True)
    assert shown(fan, light) == rx.doing == (True, 5, None, True)

@pytest.mark.asyncio
async def test_fan_and_light_turned_on_together_after_power_cut(clock):
    hass, fan, light, rx = await make(clock, switch='off')
    await asyncio.gather(fan.async_turn_on(percentage=55), light.async_turn_on())
    clock.advance(5)
    assert hass.services.switch_calls == ['turn_on']
    assert shown(fan, light) == rx.doing == (True, 5, None, True)

@pytest.mark.asyncio
async def test_command_right_after_someone_else_powered_up_waits_for_boot(clock):
    """e.g. "turn on the bedroom" hits the power switch and the light together."""
    hass, fan, light, rx = await make(clock, switch='off', boot_s=3.0)
    hass.set_switch(SWITCH, True)
    await light.async_turn_on()
    clock.advance(5)
    assert hass.services.switch_calls == []
    assert shown(fan, light) == rx.doing == (False, 0, None, True)

# ---- commands the receiver cannot have heard ----
@pytest.mark.asyncio
async def test_power_cut_right_after_a_command_undoes_it(clock):
    """ "Turn everything off" sends light-off and cuts the power at once: the
    receiver loses power before the packet is on air and keeps the light on. """
    hass, fan, light, rx = await make(clock)
    await fan.async_set_percentage(55)
    await light.async_turn_on()
    clock.advance(5)

    await light.async_turn_off()
    await fan.async_turn_off()
    clock.advance(0.3)
    hass.set_switch(SWITCH, False)
    clock.advance(60)
    assert remembered(fan) == rx.memory == (5, None, True)

    hass.set_switch(SWITCH, True)
    assert shown(fan, light) == rx.doing == (True, 5, None, True)
    clock.advance(5)
    await light.async_turn_off()        # and Home Assistant can still turn it off
    clock.advance(5)
    assert shown(fan, light) == rx.doing == (True, 5, None, False)

@pytest.mark.asyncio
async def test_power_cut_after_the_command_was_heard_keeps_it(clock):
    hass, fan, light, rx = await make(clock)
    await light.async_turn_on()
    clock.advance(5)
    await light.async_turn_off()
    clock.advance(2)
    hass.set_switch(SWITCH, False)
    assert remembered(fan) == rx.memory == (0, None, False)

@pytest.mark.asyncio
async def test_failed_send_changes_nothing(clock):
    hass, fan, light, rx = await make(clock)
    async def broken(cmd):
        raise HomeAssistantError('RM4 offline')
    fan._send_base64 = broken
    with pytest.raises(HomeAssistantError):
        await light.async_turn_on()
    assert not light.is_on and fan._unconfirmed == []

# ---- restart ----
@pytest.mark.asyncio
async def test_state_survives_restart_even_while_unpowered(clock):
    hass, fan, light, rx = await make(clock)
    await fan.async_set_percentage(55)
    await light.async_turn_on()
    clock.advance(5)
    hass.set_switch(SWITCH, False)
    saved = fan.extra_restore_state_data.as_dict()

    hass2, fan2, light2, _ = await make(clock, switch='off', receiver=rx, restore=saved)
    assert shown(fan2, light2) == rx.doing == (False, 0, None, False)
    hass2.set_switch(SWITCH, True)
    assert shown(fan2, light2) == rx.doing == (True, 5, None, True)
    assert fan2._prev_speed == 5

@pytest.mark.asyncio
async def test_restores_light_from_state_saved_by_older_versions(clock):
    hass = FakeHass()
    hass.states.set(SWITCH, 'on')
    fan = Tr198aFan(hass, 'Fan', 'remote.rm4', HANDSET, power_switch=SWITCH)
    fan.last_state = FakeState('on', {ATTR_SPEED: 4, ATTR_DIRECTION: 'forward',
                                      ATTR_BREEZE: None, ATTR_LIGHT: True})
    await fan.async_added_to_hass()
    light = Tr198aLight(fan)
    assert shown(fan, light) == (True, 4, None, True)
    assert fan.direction == 'forward'

# ---- buttons ----
@pytest.mark.asyncio
async def test_sync_button_flips_the_lamp_but_not_the_state(clock):
    hass, fan, light, rx = await make(clock)
    rx.light = True                     # out of sync: lamp on, Home Assistant says off
    await _execute(fan, SERVICE_SYNC_LIGHT)
    clock.advance(5)
    assert hass.services.packets == [dict(speed=0, breeze=None, direction='reverse', toggle=True)]
    assert shown(fan, light) == rx.doing == (False, 0, None, False)

# ---- pairing ----
@pytest.mark.asyncio
async def test_pairing_power_cycle_keeps_state(clock):
    hass, fan, light, rx = await make(clock)
    await fan.async_set_percentage(55)
    await light.async_turn_on()
    clock.advance(5)
    sent = []
    async def send(cmd):
        sent.append(cmd)
    await cycle_power_and_pair(hass, SWITCH, HANDSET, send)
    assert len(sent) == 1
    assert hass.services.switch_calls == ['turn_off', 'turn_on']
    assert shown(fan, light) == rx.doing == (True, 5, None, True)

# ---- anything goes ----
@pytest.mark.asyncio
@pytest.mark.parametrize('seed', range(150))
async def test_home_assistant_always_matches_the_receiver(clock, seed):
    """Random mixes of commands, wall-switch flips, "all off" races and
    restarts never leave Home Assistant showing something the fan isn't doing."""
    rng = random.Random(seed)
    hass, fan, light, rx = await make(clock, boot_s=rng.choice([0.5, 1.5, 3.0]))

    for step in range(40):
        op = rng.choice(['fan_on', 'fan_pct', 'fan_off', 'preset', 'light_on', 'light_off',
                         'switch_off', 'switch_on', 'all_off', 'all_on', 'restart', 'noise'])
        if op == 'fan_on':
            await fan.async_turn_on()
        elif op == 'fan_pct':
            await fan.async_set_percentage(rng.choice([0, 11, 33, 55, 100]))
        elif op == 'fan_off':
            await fan.async_turn_off()
        elif op == 'preset':
            await fan.async_set_preset_mode(rng.choice(['breeze_1', 'breeze_2', 'breeze_3']))
        elif op == 'light_on':
            await light.async_turn_on()
        elif op == 'light_off':
            await light.async_turn_off()
        elif op == 'switch_off':
            hass.set_switch(SWITCH, False)
        elif op == 'switch_on':
            hass.set_switch(SWITCH, True)
        elif op == 'all_off':           # everything in the room, power switch included
            await asyncio.gather(light.async_turn_off(), fan.async_turn_off())
            clock.advance(rng.choice([0.1, 0.5, 1.0]))
            hass.set_switch(SWITCH, False)
        elif op == 'all_on':
            hass.set_switch(SWITCH, True)
            await asyncio.gather(light.async_turn_on(), fan.async_turn_on())
        elif op == 'restart':
            saved = fan.extra_restore_state_data.as_dict()
            switch = hass.states.get(SWITCH).state
            hass, fan, light, _ = await make(clock, switch=switch, receiver=rx, restore=saved)
        elif op == 'noise':
            hass.states.set(SWITCH, hass.states.get(SWITCH).state, {'n': step})

        clock.advance(rng.choice([3, 10, 600]))
        assert remembered(fan) == rx.memory, (seed, step, op)
        assert shown(fan, light) == rx.doing, (seed, step, op)
