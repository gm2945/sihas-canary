"""BCM climate controls with separate occupancy and schedule entities."""

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from .bcm import (
    AWAY,
    BURNING,
    ONDOL_TARGET,
    ONDOL_TEMPERATURE,
    OPERATION_MODE,
    POWER,
    ROOM_TARGET,
    ROOM_TEMPERATURE,
    SCHEDULE,
    BcmEntity,
    operation_mode,
    temperature_command,
)


class Bcm300(BcmEntity, ClimateEntity):
    _attr_icon = "mdi:thermostat"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    # Keep the upstream modes so existing automations keep working.
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.FAN_ONLY, HVACMode.AUTO]

    def __init__(self, coordinator):
        super().__init__(coordinator, None, None)

    @property
    def supported_features(self):
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self.preset_mode in ("실내", "온돌"):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        # PRESET_MODE is deliberately absent until mode writes are verified.
        return features

    @property
    def preset_mode(self):
        """Read-only observed mode; no undocumented R4 writes."""
        return operation_mode(self.registers)

    @property
    def current_temperature(self):
        if self.preset_mode == "온돌":
            value = self.registers[ONDOL_TEMPERATURE]
            return value if 0 <= value <= 100 else None
        value = self.registers[ROOM_TEMPERATURE]
        return value / 10 if 0 <= value <= 1000 else None

    @property
    def target_temperature(self):
        if self.preset_mode == "실내":
            return self.registers[ROOM_TARGET]
        if self.preset_mode == "온돌":
            return self.registers[ONDOL_TARGET]
        return None

    @property
    def min_temp(self):
        if not self.coordinator.nr10e:
            return 0
        return 40 if self.preset_mode == "온돌" else 10

    @property
    def max_temp(self):
        if not self.coordinator.nr10e:
            return 80
        return 80 if self.preset_mode == "온돌" else 40

    @property
    def hvac_mode(self):
        if self.registers[POWER] == 0:
            return HVACMode.OFF
        if self.registers[SCHEDULE] == 1:
            return HVACMode.HEAT
        if self.registers[AWAY] == 1:
            return HVACMode.FAN_ONLY
        return HVACMode.AUTO

    @property
    def hvac_action(self):
        if self.registers[POWER] == 0:
            return HVACAction.OFF
        if self.registers[BURNING] == 0:
            return HVACAction.IDLE
        # A shared burner flag cannot distinguish space heating from hot water.
        return None

    @property
    def extra_state_attributes(self):
        return {
            **super().extra_state_attributes,
            "preset_mode": self.preset_mode,
            "operation_mode_raw": self.registers[OPERATION_MODE],
            "operation_mode_read_only": True,
            "away": self.registers[AWAY] == 1,
            "schedule": self.registers[SCHEDULE] == 1,
            "burning": {0: False, 1: True}.get(self.registers[BURNING]),
        }

    async def async_turn_on(self):
        # Preserve the selected mode, occupancy and schedule.
        await self.coordinator.async_write(POWER, 1)

    async def async_turn_off(self):
        await self.coordinator.async_write(POWER, 0)

    async def async_set_temperature(self, **kwargs):
        try:
            value = float(kwargs[ATTR_TEMPERATURE])
        except (KeyError, TypeError, ValueError) as err:
            raise HomeAssistantError("유효한 설정온도를 입력하세요.") from err
        await self.coordinator.async_control(
            lambda registers: temperature_command(
                registers, value, self.coordinator.nr10e
            )
        )

    async def async_set_hvac_mode(self, hvac_mode):
        def build(registers):
            if hvac_mode == HVACMode.OFF:
                return [(POWER, 0)]
            if hvac_mode not in self.hvac_modes:
                raise HomeAssistantError("지원하지 않는 운전 모드입니다.")
            commands = [] if registers[POWER] == 1 else [(POWER, 1)]
            if hvac_mode == HVACMode.FAN_ONLY:
                return commands + [(AWAY, 1)]
            if registers[AWAY] != 0:
                commands.append((AWAY, 0))
            commands.append((SCHEDULE, 1 if hvac_mode == HVACMode.HEAT else 0))
            return commands

        await self.coordinator.async_control(build)
