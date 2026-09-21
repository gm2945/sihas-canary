from __future__ import annotations

from datetime import timedelta
from typing import Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .climate import Acm300
from .bcm import BcmEntity, BURNING, PROBLEM, CONNECTIVITY
from .const import DOMAIN

from .const import (
    CONF_CFG,
    CONF_IP,
    CONF_MAC,
    CONF_NAME,
    CONF_TYPE,
    DEFAULT_PARALLEL_UPDATES,
    SIHAS_PLATFORM_SCHEMA,
)
from .sihas_base import SihasEntity

SCAN_INTERVAL = timedelta(seconds=10)

PARALLEL_UPDATES = DEFAULT_PARALLEL_UPDATES
PLATFORM_SCHEMA = SIHAS_PLATFORM_SCHEMA
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if entry.data[CONF_TYPE] == "BCM":
        coordinator = hass.data[DOMAIN][entry.entry_id]
        async_add_entities([
            BcmStatus(coordinator, "burning", "연소", BURNING, None),
            BcmStatus(coordinator, "problem", "이상", PROBLEM, BinarySensorDeviceClass.PROBLEM),
            BcmStatus(coordinator, "connectivity", "보일러 연결", CONNECTIVITY, BinarySensorDeviceClass.CONNECTIVITY),
        ])
    elif entry.data[CONF_TYPE] == "ACM":
        async_add_entities(
            [
                AcmVibrationSensor(
                    entry.data[CONF_IP],
                    entry.data[CONF_MAC],
                    entry.data[CONF_TYPE],
                    entry.data[CONF_CFG],
                    entry.data[CONF_NAME],
                ),
            ],
        )

class AcmVibrationSensor(SihasEntity, BinarySensorEntity):
    def __init__(
        self,
        ip: str,
        mac: str,
        device_type: str,
        config: int,
        name: Optional[str] = None,
    ):
        super().__init__(
            ip=ip,
            mac=mac,
            device_type=device_type,
            config=config,
            name=f"{name} 진동 상태",
        )
        self._attr_device_class = BinarySensorDeviceClass.VIBRATION
        
    
    def update(self):
        if regs := self.poll():            
            self._attr_is_on = regs[7] != 0


class BcmStatus(BcmEntity, BinarySensorEntity):
    def __init__(self, coordinator, suffix, label, register, device_class):
        super().__init__(coordinator, suffix, label)
        self.register = register
        self._attr_device_class = device_class
        if register == BURNING:
            self._attr_icon = "mdi:fire"

    @property
    def available(self):
        # Connectivity stays readable while the BCM reports a disconnected boiler.
        if self.register == CONNECTIVITY:
            return self.coordinator.last_update_success and self.registers is not None
        return super().available

    @property
    def is_on(self):
        value = self.registers[self.register]
        if self.register == PROBLEM:
            return value != 0
        if self.register == CONNECTIVITY:
            return {0: True, 1: False}.get(value)
        return {0: False, 1: True}.get(value)

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes, "raw_value": self.registers[self.register]}
