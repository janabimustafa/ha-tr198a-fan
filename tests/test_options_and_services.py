"""Options form, where settings are stored, and the tr198a_fan.* services."""
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import voluptuous as vol
import yaml

from custom_components.tr198a_fan import _fold_legacy_options, fan as fan_module
from custom_components.tr198a_fan.codec import build_operational_command
from custom_components.tr198a_fan.config_flow import Tr198aOptionsFlow
from custom_components.tr198a_fan.const import (
    DOMAIN, DIM_STEP_SIZE, CONF_POWER_ON_DELAY,
    SERVICE_PAIR, SERVICE_DIM_UP, SERVICE_DIM_DOWN, SERVICE_SYNC_LIGHT,
)

HANDSET = 0x123
REMOTE = 'remote.rm4'
SWITCH = 'switch.fan_power'
SERVICES = {SERVICE_PAIR, SERVICE_DIM_UP, SERVICE_DIM_DOWN, SERVICE_SYNC_LIGHT}

# ---- helpers ----
class FakeConfigEntries:
    def __init__(self):
        self.entries = {}
        self.reloads = []
    def async_get_entry(self, entry_id):
        return self.entries.get(entry_id)
    def async_entries(self, domain=None):
        return list(self.entries.values())
    def async_update_entry(self, entry, **changes):
        for field, value in changes.items():
            assert field in ('data', 'options', 'title')
            setattr(entry, field, value)
    def async_schedule_reload(self, entry_id):
        self.reloads.append(entry_id)

class FakeHass:
    def __init__(self):
        self.config_entries = FakeConfigEntries()
        self.states = SimpleNamespace(get=lambda entity_id: None)
        self.data = {DOMAIN: {}}
        self.sent = []          # (remote entity, base64 command)
        self.services = SimpleNamespace(async_call=self._call)
    async def _call(self, domain, service, data, *, blocking=False):
        assert (domain, service) == ('remote', 'send_command')
        self.sent += [(data['entity_id'], cmd) for cmd in data['command']]

def make_entry(hass, *, switch=None, options=None, **data):
    entry = SimpleNamespace(
        entry_id='entry1', title='Fan',
        data={'remote_entity_id': REMOTE, 'handset_id': HANDSET, 'name': 'Fan',
              'auto_pair': False, DIM_STEP_SIZE: 2, **data},
        options=dict(options or {}),
    )
    if switch:
        entry.data['power_switch_entity_id'] = switch
    hass.config_entries.entries[entry.entry_id] = entry
    hass.data[DOMAIN][entry.entry_id] = {}
    return entry

async def submit(hass, entry, **user_input):
    """Show the options form and submit it the way the frontend would."""
    flow = Tr198aOptionsFlow(entry)
    flow.hass = hass
    form = await flow.async_step_init()
    assert form['type'] == 'form'
    return await flow.async_step_init(form['data_schema'](user_input))

def schema_key(schema, name):
    return next(key for key in schema.schema if key == name)

# ---- options form ----
@pytest.mark.asyncio
async def test_options_save_for_a_fan_without_power_switch():
    """The empty default used to fail validation, so the form could not be saved."""
    hass = FakeHass()
    entry = make_entry(hass)
    result = await submit(hass, entry, remote_entity_id=REMOTE, name='Fan',
                          **{DIM_STEP_SIZE: 4, CONF_POWER_ON_DELAY: 3})
    assert result['type'] == 'create_entry'
    assert entry.data[DIM_STEP_SIZE] == 4 and entry.data[CONF_POWER_ON_DELAY] == 3
    assert 'power_switch_entity_id' not in entry.data

@pytest.mark.asyncio
async def test_options_form_prefills_the_current_settings():
    hass = FakeHass()
    entry = make_entry(hass, switch=SWITCH, **{DIM_STEP_SIZE: 3, CONF_POWER_ON_DELAY: 4.5})
    flow = Tr198aOptionsFlow(entry)
    flow.hass = hass
    schema = (await flow.async_step_init())['data_schema']
    assert schema_key(schema, 'power_switch_entity_id').description == {'suggested_value': SWITCH}
    assert schema({'remote_entity_id': REMOTE}) == {
        'remote_entity_id': REMOTE, 'name': 'Fan', DIM_STEP_SIZE: 3, CONF_POWER_ON_DELAY: 4.5,
    }

@pytest.mark.asyncio
async def test_clearing_the_power_switch_removes_it():
    hass = FakeHass()
    entry = make_entry(hass, switch=SWITCH, options={'paired': True})
    await submit(hass, entry, remote_entity_id=REMOTE, name='Fan', **{DIM_STEP_SIZE: 2})
    assert 'power_switch_entity_id' not in entry.data
    assert hass.config_entries.reloads == ['entry1']        # entities are rebuilt without it
    assert entry.options == {'paired': True}                # no re-pairing afterwards

@pytest.mark.asyncio
async def test_remote_switch_and_name_changes_are_applied():
    hass = FakeHass()
    entry = make_entry(hass)
    result = await submit(hass, entry, remote_entity_id='remote.other', name='Bedroom Fan',
                          power_switch_entity_id=SWITCH, **{DIM_STEP_SIZE: 2})
    assert entry.data['remote_entity_id'] == 'remote.other'
    assert entry.data['power_switch_entity_id'] == SWITCH
    assert entry.data['name'] == entry.title == 'Bedroom Fan'
    assert hass.config_entries.reloads == ['entry1']
    assert result['data'] == {}                             # nothing is kept in options

@pytest.mark.asyncio
async def test_a_renamed_entry_keeps_its_title():
    hass = FakeHass()
    entry = make_entry(hass)
    entry.title = 'Named by hand'
    await submit(hass, entry, remote_entity_id=REMOTE, name='Bedroom Fan', **{DIM_STEP_SIZE: 2})
    assert entry.data['name'] == 'Bedroom Fan' and entry.title == 'Named by hand'

@pytest.mark.asyncio
async def test_tuning_values_do_not_reload():
    hass = FakeHass()
    entry = make_entry(hass, switch=SWITCH)
    await submit(hass, entry, remote_entity_id=REMOTE, name='Fan', power_switch_entity_id=SWITCH,
                 **{DIM_STEP_SIZE: 5, CONF_POWER_ON_DELAY: 7})
    assert hass.config_entries.reloads == []
    assert entry.data[DIM_STEP_SIZE] == 5 and entry.data[CONF_POWER_ON_DELAY] == 7

# ---- settings saved by the old options form ----
def test_legacy_options_are_folded_into_data():
    hass = FakeHass()
    entry = make_entry(hass, switch='switch.old', options={
        # the old form stored a full copy here, but only read these three back
        'power_switch_entity_id': SWITCH, DIM_STEP_SIZE: 5, CONF_POWER_ON_DELAY: 4,
        'remote_entity_id': 'remote.never_used', 'name': 'Never Used', 'paired': True,
    })
    _fold_legacy_options(hass, entry)
    assert entry.options == {'paired': True}
    assert entry.data['power_switch_entity_id'] == SWITCH
    assert entry.data[DIM_STEP_SIZE] == 5 and entry.data[CONF_POWER_ON_DELAY] == 4
    assert entry.data['remote_entity_id'] == REMOTE and entry.data['name'] == 'Fan'

def test_fold_leaves_current_entries_alone():
    hass = FakeHass()
    entry = make_entry(hass, switch=SWITCH, options={'paired': True})
    data, options = entry.data, entry.options
    _fold_legacy_options(hass, entry)
    assert entry.data is data and entry.options is options

def test_fold_keeps_a_switch_the_old_form_could_not_remove():
    """Clearing the switch used to have no effect; it stays until removed for real."""
    hass = FakeHass()
    entry = make_entry(hass, switch=SWITCH, options={'remote_entity_id': REMOTE, DIM_STEP_SIZE: 2})
    _fold_legacy_options(hass, entry)
    assert entry.data['power_switch_entity_id'] == SWITCH and entry.options == {}

# ---- services ----
async def setup_fan(hass, entry):
    platform = sys.modules['homeassistant.helpers.entity_platform'].current_platform
    platform.services.clear()
    added = []
    await fan_module.async_setup_entry(hass, entry, added.extend)
    return added[0], platform.services

@pytest.mark.asyncio
async def test_services_are_registered_for_fan_entities():
    hass = FakeHass()
    fan, services = await setup_fan(hass, make_entry(hass))
    assert set(services) == SERVICES

@pytest.mark.asyncio
async def test_sync_light_service_flips_the_lamp_only():
    hass = FakeHass()
    fan, services = await setup_fan(hass, make_entry(hass))
    await services[SERVICE_SYNC_LIGHT](fan, SimpleNamespace(service=SERVICE_SYNC_LIGHT))
    assert hass.sent == [(REMOTE, build_operational_command(HANDSET, speed=0, light_toggle=True))]
    assert not fan.light_is_on

@pytest.mark.asyncio
@pytest.mark.parametrize('service, direction', [(SERVICE_DIM_UP, 'up'), (SERVICE_DIM_DOWN, 'down')])
async def test_dim_services_use_the_configured_step(service, direction):
    hass = FakeHass()
    fan, services = await setup_fan(hass, make_entry(hass, **{DIM_STEP_SIZE: 3}))
    await services[service](fan, SimpleNamespace(service=service))
    assert hass.sent == [(REMOTE, build_operational_command(
        HANDSET, speed=0, dim=direction, radio_repeats=0xC9 + 2 * 4, trailer_us=394))]

@pytest.mark.asyncio
async def test_pair_service_marks_the_entry_paired():
    hass = FakeHass()
    entry = make_entry(hass)
    fan, services = await setup_fan(hass, entry)
    await services[SERVICE_PAIR](fan, SimpleNamespace(service=SERVICE_PAIR))
    assert len(hass.sent) == 1 and entry.options == {'paired': True}

def test_services_yaml_describes_exactly_the_registered_services():
    path = Path(fan_module.__file__).with_name('services.yaml')
    described = yaml.safe_load(path.read_text())
    assert set(described) == SERVICES
    for service in described.values():
        assert service['target']['entity'] == {'integration': DOMAIN, 'domain': 'fan'}
