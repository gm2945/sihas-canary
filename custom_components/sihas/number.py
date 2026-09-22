"""Independent BCM room and ondol temperature setpoints."""

import math

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from .bcm import ONDOL_TARGET, ROOM_TARGET, BcmEntity
from .const import CONF_TYPE, DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data[CONF_TYPE] != "BCM":
        return
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BcmTargetTemperature(
                coordinator, "room_target_temperature", "실내 설정온도", ROOM_TARGET
            ),
            BcmTargetTemperature(
                coordinator, "ondol_target_temperature", "온돌 설정온도", ONDOL_TARGET
            ),
        ]
    )


class BcmTargetTemperature(BcmEntity, NumberEntity):
    """Write a specific setpoint without changing power or operation mode."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, suffix, label, register):
        super().__init__(coordinator, suffix, label)
        self.register = register
        limits = (10, 40) if register == ROOM_TARGET else (40, 80)
        if not coordinator.nr10e:
            limits = (0, 80)
        self._attr_native_min_value, self._attr_native_max_value = limits

    @property
    def native_value(self):
        value = self.registers[self.register]
        return (
            value if self.native_min_value <= value <= self.native_max_value else None
        )

    async def async_set_native_value(self, value):
        try:
            temperature = float(value)
        except (TypeError, ValueError, OverflowError) as err:
            raise HomeAssistantError("유효한 설정온도를 입력하세요.") from err
        if not math.isfinite(temperature) or not temperature.is_integer():
            raise HomeAssistantError("설정온도는 유한한 정수여야 합니다.")
        if not self.native_min_value <= temperature <= self.native_max_value:
            raise HomeAssistantError(
                f"설정온도 범위는 {self.native_min_value}–{self.native_max_value} °C입니다."
            )
        await self.coordinator.async_write(self.register, int(temperature))
