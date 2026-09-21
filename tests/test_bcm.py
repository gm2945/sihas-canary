"""BCM behavior against HA entities with the device transport replaced."""

import asyncio
from types import MappingProxyType

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.exceptions import HomeAssistantError

from custom_components.sihas.bcm import (
    AWAY,
    BURNING,
    CONF_BCM_NR10E,
    CONF_BCM_PRESET_CONTROL,
    CONNECTIVITY,
    HOT_WATER_TARGET,
    ONDOL_TARGET,
    OPERATION_MODE,
    POWER,
    PROBLEM,
    ROOM_TARGET,
    SCHEDULE,
    WATER_STATUS,
    BcmCoordinator,
    operation_mode,
    preset_command,
)
from custom_components.sihas.bcm_climate import Bcm300
from custom_components.sihas.binary_sensor import BcmStatus
from custom_components.sihas.sensor import BcmWaterStatus
from custom_components.sihas.select import BcmSelect
from custom_components.sihas.switch import BcmSchedule
from custom_components.sihas import binary_sensor, climate, select, sensor, switch
from custom_components.sihas.diagnostics import async_get_config_entry_diagnostics
from custom_components.sihas.config_flow import ConfigFlow


class Device:
    def __init__(self):
        self.regs = [0] * 64
        self.regs[:5] = [1, 22, 50, 0, 3]
        self.regs[8] = 237
        self.regs[9] = 46
        self.writes = []
        self.fail_read = False
        self.fail_write = False
        self.apply_writes = True

    def poll(self):
        return None if self.fail_read else self.regs.copy()

    def command(self, register, value):
        self.writes.append((register, value))
        if self.fail_write:
            return False
        if self.apply_writes:
            self.regs[register] = value
        return True


@pytest_asyncio.fixture
async def setup(tmp_path, request):
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        entry_id="bcm-test",
        domain="sihas",
        title="BCM",
        version=1,
        minor_version=1,
        source="user",
        unique_id="bcm-test",
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
        options=getattr(
            request, "param", {CONF_BCM_NR10E: True, CONF_BCM_PRESET_CONTROL: False}
        ),
        data={
            "ip": "192.0.2.1",
            "mac": "00:11:22:33:44:55",
            "type": "BCM",
            "cfg": 0,
            "name": "보일러",
        },
    )
    coordinator = BcmCoordinator(hass, entry)
    coordinator.api = Device()
    await coordinator.async_refresh()
    hass.data["sihas"] = {entry.entry_id: coordinator}
    entity = Bcm300(coordinator)
    entity.hass = hass
    yield hass, entry, coordinator, coordinator.api, entity
    await coordinator.async_shutdown()
    await hass.async_stop()


@pytest.mark.parametrize(
    "raw,mode",
    [
        (0, None),
        (1, "온수"),
        (2, "실내"),
        (3, "실내"),
        (5, "온수"),
        (6, "온돌"),
        (7, "온돌"),
        (0x107, "온돌"),
    ],
)
def test_mode_flags(raw, mode):
    registers = [0] * 64
    registers[OPERATION_MODE] = raw
    assert operation_mode(registers) == mode


@pytest.mark.asyncio
async def test_identity_ranges_and_read_only_preset(setup):
    _, _, coordinator, device, climate = setup
    assert climate.unique_id == "BCM-00:11:22:33:44:55"
    assert climate.current_temperature == 23.7
    assert (climate.min_temp, climate.max_temp) == (10, 40)
    assert climate.extra_state_attributes["preset_mode"] == "실내"
    assert not climate.supported_features & ClimateEntityFeature.PRESET_MODE
    await climate.async_set_temperature(temperature=25)
    assert device.writes == [(ROOM_TARGET, 25)]
    assert climate.target_temperature == 25
    device.regs[OPERATION_MODE] = 7
    await coordinator.async_refresh()
    assert climate.current_temperature == 46
    assert (climate.min_temp, climate.max_temp) == (40, 80)


@pytest.mark.asyncio
async def test_fresh_mode_selects_correct_target(setup):
    _, _, _, device, climate = setup
    # HA last saw room mode, but the app has switched to ondol.
    device.regs[OPERATION_MODE] = 7
    await climate.async_set_temperature(temperature=55)
    assert device.writes == [(ONDOL_TARGET, 55)]


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [9, 41, 22.5, float("nan"), float("inf")])
async def test_invalid_room_temperature_sends_nothing(setup, temperature):
    _, _, _, device, climate = setup
    with pytest.raises(HomeAssistantError):
        await climate.async_set_temperature(temperature=temperature)
    assert device.writes == []


@pytest.mark.asyncio
async def test_hot_water_no_celsius_or_room_write(setup):
    _, _, coordinator, device, climate = setup
    device.regs[OPERATION_MODE] = 5
    await coordinator.async_refresh()
    assert climate.target_temperature is None
    assert not climate.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
    with pytest.raises(HomeAssistantError):
        await climate.async_set_temperature(temperature=22)
    assert device.writes == []
    hot_water = BcmSelect(
        coordinator,
        "hot_water_level",
        "온수",
        HOT_WATER_TARGET,
        ["저온", "중온", "고온"],
    )
    for value, option in enumerate(hot_water.options):
        await hot_water.async_select_option(option)
        assert hot_water.current_option == option
        assert device.writes[-1] == (HOT_WATER_TARGET, value)


@pytest.mark.asyncio
async def test_turn_on_and_independent_controls_preserve_other_settings(setup):
    _, _, coordinator, device, climate = setup
    device.regs[POWER] = 0
    device.regs[AWAY] = device.regs[SCHEDULE] = 1
    await climate.async_turn_on()
    assert device.writes == [(POWER, 1)]
    assert device.regs[AWAY] == device.regs[SCHEDULE] == 1
    occupancy = BcmSelect(coordinator, "occupancy_mode", "외출", AWAY, ["재실", "외출"])
    await occupancy.async_select_option("재실")
    schedule = BcmSchedule(coordinator, "schedule", "예약")
    await schedule.async_turn_off()
    assert device.writes == [(POWER, 1), (AWAY, 0), (SCHEDULE, 0)]


@pytest.mark.asyncio
async def test_hvac_modes_only_control_power(setup):
    _, _, _, device, climate = setup
    device.regs[AWAY] = device.regs[SCHEDULE] = 1
    assert climate.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    assert device.writes == [(POWER, 1)]
    assert climate.hvac_mode == HVACMode.HEAT
    for legacy in (HVACMode.AUTO, HVACMode.FAN_ONLY):
        with pytest.raises(HomeAssistantError):
            await climate.async_set_hvac_mode(legacy)
    await climate.async_set_hvac_mode(HVACMode.OFF)
    assert climate.hvac_mode == HVACMode.OFF
    assert device.writes == [(POWER, 1), (POWER, 0)]
    assert device.regs[AWAY] == device.regs[SCHEDULE] == 1


@pytest.mark.asyncio
async def test_offline_distinct_from_failed_transport(setup):
    _, _, coordinator, device, climate = setup
    connection = BcmStatus(coordinator, "connectivity", "연결", CONNECTIVITY, None)
    device.regs[CONNECTIVITY] = 1
    await coordinator.async_refresh()
    assert connection.available and connection.is_on is False
    assert not climate.available
    with pytest.raises(HomeAssistantError):
        await climate.async_turn_on()
    assert device.writes == []
    device.fail_read = True
    await coordinator.async_refresh()
    assert not connection.available and not climate.available


@pytest.mark.asyncio
async def test_failed_write_does_not_report_success(setup):
    _, _, coordinator, device, climate = setup
    device.fail_write = True
    with pytest.raises(HomeAssistantError):
        await climate.async_turn_off()
    assert not coordinator.last_update_success
    assert device.regs[POWER] == 1
    device.fail_write = False
    await coordinator.async_refresh()
    assert climate.available


@pytest.mark.asyncio
async def test_readback_is_authoritative(setup):
    _, _, _, device, climate = setup
    device.apply_writes = False
    await climate.async_set_temperature(temperature=25)
    assert device.writes == [(ROOM_TARGET, 25)]
    assert climate.target_temperature == 22


@pytest.mark.asyncio
async def test_unknown_water_and_combustion(setup):
    _, _, coordinator, device, climate = setup
    water = BcmWaterStatus(coordinator, "water_status", "물 상태")
    error = BcmStatus(coordinator, "problem", "오류", PROBLEM, None)
    device.regs[WATER_STATUS] = 2
    device.regs[PROBLEM] = 19
    device.regs[BURNING] = 1
    await coordinator.async_refresh()
    assert water.native_value is None
    assert water.extra_state_attributes["raw_value"] == 2
    assert error.is_on is True
    assert climate.hvac_action is None
    device.regs[BURNING] = 0
    await coordinator.async_refresh()
    assert climate.hvac_action == HVACAction.IDLE


@pytest.mark.asyncio
async def test_undocumented_writes_blocked_before_any_command(setup):
    _, _, coordinator, device, _ = setup
    with pytest.raises(HomeAssistantError):
        await coordinator.async_control(lambda _: [(POWER, 1), (OPERATION_MODE, 7)])
    assert device.writes == []


@pytest.mark.asyncio
async def test_platform_setup_shared_snapshot_and_profile_gate(setup):
    hass, entry, coordinator, device, _ = setup
    entities = []
    for platform in (climate, select, switch, sensor, binary_sensor):
        await platform.async_setup_entry(hass, entry, entities.extend)
    assert len(entities) == 9
    assert len({entity.unique_id for entity in entities}) == 9
    assert all(entity.coordinator is coordinator for entity in entities)
    assert len({tuple(entity.device_info["identifiers"]) for entity in entities}) == 1
    coordinator.nr10e = False
    entities = []
    await select.async_setup_entry(hass, entry, entities.extend)
    assert len(entities) == 1
    with pytest.raises(HomeAssistantError):
        await coordinator.async_write(HOT_WATER_TARGET, 1)
    assert device.writes == []


@pytest.mark.asyncio
async def test_diagnostics_and_serial_commands(setup):
    hass, entry, coordinator, device, _ = setup
    await asyncio.gather(
        coordinator.async_write(AWAY, 1), coordinator.async_write(SCHEDULE, 1)
    )
    assert device.regs[AWAY] == device.regs[SCHEDULE] == 1
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert len(diagnostics["registers"]) == 64
    assert entry.data["mac"] not in str(diagnostics)
    assert entry.data["ip"] not in str(diagnostics)


@pytest.mark.asyncio
async def test_nr10e_options_form_and_save(setup):
    hass, entry, _, _, _ = setup
    flow = ConfigFlow.async_get_options_flow(entry)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert result["data_schema"]({}) == {
        CONF_BCM_NR10E: True,
        CONF_BCM_PRESET_CONTROL: False,
    }
    result = await flow.async_step_init({CONF_BCM_NR10E: False})
    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_BCM_NR10E: False, CONF_BCM_PRESET_CONTROL: False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup,expected_nr10e,expected_presets",
    [
        ({}, True, True),
        ({CONF_BCM_NR10E: False}, False, True),
        ({CONF_BCM_PRESET_CONTROL: False}, True, False),
        ({CONF_BCM_NR10E: False, CONF_BCM_PRESET_CONTROL: False}, False, False),
    ],
    indirect=["setup"],
)
async def test_default_controls_visible_and_explicit_disables_preserved(
    setup, expected_nr10e, expected_presets
):
    hass, entry, coordinator, device, boiler = setup
    entities = []
    await select.async_setup_entry(hass, entry, entities.extend)
    hot_water = [
        entity for entity in entities if entity.unique_id.endswith("-hot_water_level")
    ]
    assert bool(hot_water) is expected_nr10e
    assert (
        bool(boiler.supported_features & ClimateEntityFeature.PRESET_MODE)
        is expected_presets
    )
    if expected_presets:
        assert boiler.capability_attributes["preset_modes"] == ["실내", "온돌", "온수"]
    flow = ConfigFlow.async_get_options_flow(entry)
    flow.hass = hass
    form = await flow.async_step_init()
    assert form["data_schema"]({}) == {
        CONF_BCM_NR10E: expected_nr10e,
        CONF_BCM_PRESET_CONTROL: expected_presets,
    }
    # Exposing controls must not send commands to the boiler at startup.
    assert device.writes == []


@pytest.mark.parametrize("preset", ["실내", "온돌", "온수"])
@pytest.mark.parametrize("raw", [0, 1, 2, 3, 5, 6, 7, 0xAD03])
def test_experimental_preset_masks_preserve_other_bits(preset, raw):
    registers = [0] * 64
    registers[OPERATION_MODE] = raw
    register, value = preset_command(registers, preset)[0]
    assert register == OPERATION_MODE
    assert value & ~7 == raw & ~7
    if preset == "온수":
        assert value & 4 == raw & 4  # remember heating type
    else:
        assert value & 1 == raw & 1  # preserve DHW enable
    registers[OPERATION_MODE] = value
    assert operation_mode(registers) == preset


@pytest.mark.asyncio
async def test_presets_require_opt_in_and_invalid_presets_write_nothing(setup):
    _, _, coordinator, device, climate = setup
    assert climate.preset_modes is None
    with pytest.raises(HomeAssistantError):
        await climate.async_set_preset_mode("실내")
    coordinator.preset_control = True
    with pytest.raises(HomeAssistantError):
        await climate.async_set_preset_mode("invalid")
    # Even with the experiment enabled, raw R4 writes are not accepted.
    with pytest.raises(HomeAssistantError):
        await coordinator.async_write(OPERATION_MODE, 7)
    assert device.writes == []


@pytest.mark.asyncio
async def test_preset_ui_and_temperature_controls_follow_device_mode(setup):
    _, _, coordinator, device, climate = setup
    coordinator.preset_control = True
    device.regs[POWER] = 0
    device.regs[AWAY] = device.regs[SCHEDULE] = 1
    assert climate.capability_attributes["preset_modes"] == ["실내", "온돌", "온수"]
    assert climate.supported_features & ClimateEntityFeature.PRESET_MODE
    for preset, temperature, target_register in [
        ("온돌", 55, ONDOL_TARGET),
        ("실내", 25, ROOM_TARGET),
    ]:
        await climate.async_set_preset_mode(preset)
        assert climate.state_attributes["preset_mode"] == preset
        assert climate.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
        await climate.async_set_temperature(temperature=temperature)
        assert climate.state_attributes["temperature"] == temperature
        assert device.writes[-1] == (target_register, temperature)
    await climate.async_set_preset_mode("온수")
    assert climate.state_attributes["preset_mode"] == "온수"
    assert "temperature" not in climate.state_attributes
    assert not climate.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
    await climate.async_set_preset_mode("실내")
    assert climate.state_attributes["temperature"] == 25
    assert device.regs[POWER] == 0
    assert device.regs[AWAY] == device.regs[SCHEDULE] == 1


@pytest.mark.asyncio
async def test_ignored_preset_reports_error_without_optimistic_state(setup):
    _, _, coordinator, device, climate = setup
    coordinator.preset_control = True
    device.apply_writes = False
    with pytest.raises(HomeAssistantError, match="반영하지 않았습니다"):
        await climate.async_set_preset_mode("온돌")
    assert climate.preset_mode == "실내"
    assert climate.available
    assert device.writes == [(OPERATION_MODE, 7)]


@pytest.mark.asyncio
async def test_stop_sequence_after_failed_power_on(setup):
    _, _, _, device, climate = setup
    device.regs[POWER] = 0
    device.fail_write = True
    with pytest.raises(HomeAssistantError):
        await climate.async_set_hvac_mode(HVACMode.HEAT)
    assert device.writes == [(POWER, 1)]
