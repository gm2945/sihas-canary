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
from custom_components.sihas.select import BcmOperationMode, BcmSelect
from custom_components.sihas.switch import BcmPower, BcmSchedule
from custom_components.sihas import (
    PLATFORMS,
    binary_sensor,
    climate,
    number,
    select,
    sensor,
    switch,
)
from custom_components.sihas.number import BcmTargetTemperature
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
    for platform in (climate, select, switch, number, sensor, binary_sensor):
        await platform.async_setup_entry(hass, entry, entities.extend)
    assert len(entities) == 12
    assert len({entity.unique_id for entity in entities}) == 12
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
        any(isinstance(entity, BcmOperationMode) for entity in entities)
        is expected_presets
    )
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


@pytest.mark.asyncio
async def test_power_switch_and_climate_share_state_and_preserve_settings(setup):
    _, _, coordinator, device, boiler = setup
    power = BcmPower(coordinator, "power", "전원")
    before = device.regs[1:].copy()
    assert power.unique_id == "BCM-00:11:22:33:44:55-power"
    await power.async_turn_off()
    assert power.is_on is False and boiler.hvac_mode == HVACMode.OFF
    await boiler.async_turn_on()
    assert power.is_on is True and boiler.hvac_mode == HVACMode.HEAT
    await power.async_turn_on()
    assert device.writes == [(POWER, 0), (POWER, 1), (POWER, 1)]
    assert device.regs[1:] == before
    device.regs[POWER] = 0  # App change appears on the shared poll.
    await coordinator.async_refresh()
    assert power.is_on is False and boiler.hvac_mode == HVACMode.OFF
    device.regs[POWER] = 9
    await coordinator.async_refresh()
    assert power.is_on is None


@pytest.mark.asyncio
async def test_operation_select_and_climate_share_preset_and_preserve_settings(setup):
    _, _, coordinator, device, boiler = setup
    coordinator.preset_control = True
    mode = BcmOperationMode(coordinator, "operation_mode", "운전모드")
    assert mode.unique_id == "BCM-00:11:22:33:44:55-operation_mode"
    assert mode.options == ["실내", "온돌", "온수"]
    device.regs[POWER] = 0
    device.regs[AWAY] = device.regs[SCHEDULE] = 1
    before = device.regs.copy()
    for option in mode.options:
        await mode.async_select_option(option)
        assert mode.current_option == boiler.preset_mode == option
    assert all(register == OPERATION_MODE for register, _ in device.writes)
    assert device.regs[:4] == before[:4]
    assert device.regs[5:] == before[5:]
    await boiler.async_set_preset_mode("온돌")
    assert mode.current_option == "온돌"
    device.regs[OPERATION_MODE] = 3
    await coordinator.async_refresh()
    assert mode.current_option == boiler.preset_mode == "실내"
    device.regs[OPERATION_MODE] = 0
    await coordinator.async_refresh()
    assert mode.current_option is None


@pytest.mark.asyncio
async def test_new_controls_readback_and_offline_behavior(setup):
    _, _, coordinator, device, boiler = setup
    coordinator.preset_control = True
    power = BcmPower(coordinator, "power", "전원")
    mode = BcmOperationMode(coordinator, "operation_mode", "운전모드")
    device.apply_writes = False
    await power.async_turn_off()
    assert power.is_on is True and boiler.hvac_mode == HVACMode.HEAT
    with pytest.raises(HomeAssistantError, match="반영하지 않았습니다"):
        await mode.async_select_option("온돌")
    assert mode.current_option == boiler.preset_mode == "실내"
    device.writes.clear()
    with pytest.raises(HomeAssistantError):
        await mode.async_select_option("invalid")
    device.regs[CONNECTIVITY] = 1
    await coordinator.async_refresh()
    assert not power.available and not mode.available
    with pytest.raises(HomeAssistantError):
        await power.async_turn_on()
    with pytest.raises(HomeAssistantError):
        await mode.async_select_option("온수")
    assert device.writes == []
    device.fail_read = True
    await coordinator.async_refresh()
    assert not power.available and not mode.available


@pytest.mark.asyncio
@pytest.mark.parametrize("setup", [{}], indirect=True)
async def test_default_platform_setup_includes_both_new_controls_without_commands(
    setup,
):
    hass, entry, coordinator, device, _ = setup
    entities = []
    for platform in (climate, select, switch, number, sensor, binary_sensor):
        await platform.async_setup_entry(hass, entry, entities.extend)
    assert len(entities) == len({entity.unique_id for entity in entities}) == 13
    assert sum(isinstance(entity, BcmPower) for entity in entities) == 1
    assert sum(isinstance(entity, BcmOperationMode) for entity in entities) == 1
    assert all(entity.coordinator is coordinator for entity in entities)
    assert device.writes == []


@pytest.mark.asyncio
async def test_temperature_number_setup_and_climate_state_sync(setup):
    hass, entry, coordinator, device, boiler = setup
    assert "number" in PLATFORMS
    entities = []
    await number.async_setup_entry(hass, entry, entities.extend)
    room, ondol = entities
    assert room.unique_id == "BCM-00:11:22:33:44:55-room_target_temperature"
    assert ondol.unique_id == "BCM-00:11:22:33:44:55-ondol_target_temperature"
    assert (room.native_min_value, room.native_max_value, room.native_step) == (
        10,
        40,
        1,
    )
    assert (ondol.native_min_value, ondol.native_max_value, ondol.native_step) == (
        40,
        80,
        1,
    )
    assert room.native_unit_of_measurement == ondol.native_unit_of_measurement == "°C"
    assert device.writes == []
    await room.async_set_native_value(25)
    assert room.native_value == boiler.target_temperature == 25
    await boiler.async_set_temperature(temperature=26)
    assert room.native_value == 26
    device.regs[OPERATION_MODE] = 7
    await coordinator.async_refresh()
    await ondol.async_set_native_value(55)
    assert ondol.native_value == boiler.target_temperature == 55
    device.regs[ROOM_TARGET] = 23
    device.regs[ONDOL_TARGET] = 60
    await coordinator.async_refresh()
    assert room.native_value == 23 and ondol.native_value == 60
    assert boiler.target_temperature == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("power,raw_mode", [(0, 1), (1, 1), (1, 3), (1, 7)])
async def test_temperature_numbers_preserve_power_preset_and_other_registers(
    setup, power, raw_mode
):
    _, _, coordinator, device, _ = setup
    device.regs[POWER] = power
    device.regs[OPERATION_MODE] = raw_mode
    device.regs[AWAY] = device.regs[SCHEDULE] = 1
    before = device.regs.copy()
    room = BcmTargetTemperature(
        coordinator, "room_target_temperature", "실내 설정온도", ROOM_TARGET
    )
    ondol = BcmTargetTemperature(
        coordinator, "ondol_target_temperature", "온돌 설정온도", ONDOL_TARGET
    )
    # Both explicitly named setpoints are writable regardless of the active preset.
    await room.async_set_native_value(24)
    await ondol.async_set_native_value(52)
    expected = before.copy()
    expected[ROOM_TARGET], expected[ONDOL_TARGET] = 24, 52
    assert device.regs == expected
    assert device.writes == [(ROOM_TARGET, 24), (ONDOL_TARGET, 52)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "register,value",
    [
        (ROOM_TARGET, 9),
        (ROOM_TARGET, 41),
        (ONDOL_TARGET, 39),
        (ONDOL_TARGET, 81),
        (ROOM_TARGET, 22.5),
        (ROOM_TARGET, float("nan")),
        (ONDOL_TARGET, float("inf")),
        (ROOM_TARGET, "invalid"),
        (ONDOL_TARGET, None),
    ],
)
async def test_temperature_number_rejects_invalid_values_without_writes(
    setup, register, value
):
    _, _, coordinator, device, _ = setup
    entity = BcmTargetTemperature(coordinator, "test_target", "설정온도", register)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(value)
    assert device.writes == []


@pytest.mark.asyncio
async def test_temperature_number_readback_failure_and_offline(setup):
    _, _, coordinator, device, _ = setup
    room = BcmTargetTemperature(
        coordinator, "room_target_temperature", "실내 설정온도", ROOM_TARGET
    )
    device.apply_writes = False
    await room.async_set_native_value(25)
    assert room.native_value == 22
    device.fail_write = True
    with pytest.raises(HomeAssistantError):
        await room.async_set_native_value(26)
    assert not room.available
    device.fail_write = False
    device.regs[CONNECTIVITY] = 1
    await coordinator.async_refresh()
    device.writes.clear()
    with pytest.raises(HomeAssistantError):
        await room.async_set_native_value(25)
    assert not room.available and device.writes == []
    device.regs[CONNECTIVITY] = 0
    device.regs[ROOM_TARGET] = 65535
    await coordinator.async_refresh()
    assert room.available and room.native_value is None


@pytest.mark.asyncio
@pytest.mark.parametrize("setup", [{CONF_BCM_NR10E: False}], indirect=True)
async def test_generic_temperature_number_ranges_and_boundaries(setup):
    hass, entry, _, device, _ = setup
    entities = []
    await number.async_setup_entry(hass, entry, entities.extend)
    for entity in entities:
        assert (entity.native_min_value, entity.native_max_value) == (0, 80)
        for value in (0, 80):
            await entity.async_set_native_value(value)
            assert entity.native_value == value
    assert device.writes == [
        (ROOM_TARGET, 0),
        (ROOM_TARGET, 80),
        (ONDOL_TARGET, 0),
        (ONDOL_TARGET, 80),
    ]


@pytest.mark.asyncio
async def test_number_platform_ignores_other_device_types():
    from types import SimpleNamespace

    entities = []
    await number.async_setup_entry(
        None, SimpleNamespace(data={"type": "CCM"}), entities.extend
    )
    assert entities == []
