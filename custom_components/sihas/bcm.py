"""Shared BCM polling and documented register controls."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import math
from collections.abc import Callable

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import CONF_CFG, CONF_IP, CONF_MAC, CONF_NAME, CONF_TYPE, DOMAIN
from .sihas_base import SihasBase

_LOGGER = logging.getLogger(__name__)
CONF_BCM_NR10E = "bcm_nr10e"
CONF_BCM_PRESET_CONTROL = "bcm_preset_control"
PRESET_MODES = ("실내", "온돌", "온수")

POWER = 0
ROOM_TARGET = 1
ONDOL_TARGET = 2
HOT_WATER_TARGET = 3
OPERATION_MODE = 4
AWAY = 5
SCHEDULE = 6
ROOM_TEMPERATURE = 8
ONDOL_TEMPERATURE = 9
BURNING = 11
PROBLEM = 12
WATER_STATUS = 13
CONNECTIVITY = 14


def operation_mode(registers: list[int]) -> str | None:
    """Decode flags; bit 2 can remain set while heating is disabled."""
    value = registers[OPERATION_MODE]
    if value & 2:
        return "온돌" if value & 4 else "실내"
    if value & 1:
        return "온수"
    return None


def preset_command(registers: list[int], preset: str):
    """Experimental inverse of upstream's R4 decoder, not a verified write API.

    Preserve unrelated bits, DHW enable during heating, and the last heating
    type while selecting hot-water-only. Never change power or temperatures.
    """
    value = registers[OPERATION_MODE]
    if preset == "실내":
        value = (value & ~6) | 2
    elif preset == "온돌":
        value |= 6
    elif preset == "온수":
        value = (value & ~2) | 1
    else:
        raise HomeAssistantError("지원하지 않는 프리셋입니다.")
    return [(OPERATION_MODE, value)]


def temperature_command(registers: list[int], temperature: float, nr10e: bool):
    """Choose the target from freshly polled mode, never a stale UI value."""
    mode = operation_mode(registers)
    if mode not in ("실내", "온돌"):
        raise HomeAssistantError(
            "실내/온돌 모드에서 설정온도를 변경하세요. 온수는 온수 단계 엔티티를 사용하세요."
        )
    if not math.isfinite(temperature) or not temperature.is_integer():
        raise HomeAssistantError("설정온도는 유한한 정수여야 합니다.")
    low, high = (10, 40) if mode == "실내" else (40, 80)
    if not nr10e:
        low, high = 0, 80  # Preserve the original integration's generic range.
    if not low <= temperature <= high:
        raise HomeAssistantError(f"설정온도 범위는 {low}–{high} °C입니다.")
    return [(ROOM_TARGET if mode == "실내" else ONDOL_TARGET, int(temperature))]


class BcmCoordinator(DataUpdateCoordinator):
    """One serialized connection and snapshot shared by all BCM entities."""

    def __init__(self, hass, entry):
        super().__init__(
            hass,
            _LOGGER,
            name=f"BCM {entry.entry_id}",
            config_entry=entry,
            update_interval=timedelta(seconds=5),
            always_update=False,
        )
        self.entry = entry
        self.nr10e = entry.options.get(CONF_BCM_NR10E, False)
        self.preset_control = entry.options.get(CONF_BCM_PRESET_CONTROL, False)
        self.api = SihasBase(
            entry.data[CONF_IP],
            entry.data[CONF_MAC],
            entry.data[CONF_TYPE],
            entry.data[CONF_CFG],
        )
        self._lock = asyncio.Lock()

    async def _read(self):
        registers = await self.hass.async_add_executor_job(self.api.poll)
        if registers is None:
            raise UpdateFailed("BCM 상태를 읽지 못했습니다.")
        return registers

    async def _async_update_data(self):
        async with self._lock:
            return await self._read()

    async def async_control(self, build_commands: Callable, *, expected_preset=None):
        """Read, validate, write and read back; do not report optimistic success."""
        async with self._lock:
            try:
                registers = await self._read()
                if registers[CONNECTIVITY] != 0:
                    self.async_set_updated_data(registers)
                    raise HomeAssistantError("보일러 통신 상태가 온라인이 아닙니다.")
                commands = build_commands(registers)
                # Validate the entire sequence before sending the first command.
                for register, value in commands:
                    if register in (POWER, AWAY, SCHEDULE):
                        valid = value in (0, 1)
                    elif register == HOT_WATER_TARGET:
                        valid = self.nr10e and value in (0, 1, 2)
                    elif register in (ROOM_TARGET, ONDOL_TARGET):
                        limits = (10, 40) if register == ROOM_TARGET else (40, 80)
                        if not self.nr10e:
                            limits = (0, 80)
                        valid = limits[0] <= value <= limits[1]
                    elif register == OPERATION_MODE:
                        valid = (
                            self.preset_control
                            and expected_preset in PRESET_MODES
                            and commands == preset_command(registers, expected_preset)
                        )
                    else:
                        valid = False
                    if not isinstance(value, int) or not valid:
                        raise HomeAssistantError("지원하지 않는 BCM 설정값입니다.")
                for index, (register, value) in enumerate(commands):
                    if index:
                        await asyncio.sleep(1)
                    if not await self.hass.async_add_executor_job(
                        self.api.command, register, value
                    ):
                        raise UpdateFailed(f"BCM R{register} 명령 전송에 실패했습니다.")
                actual = await self._read()
                self.async_set_updated_data(actual)
                if expected_preset is not None:
                    # Some controllers apply commands after acknowledging the packet.
                    for _ in range(3):
                        if operation_mode(actual) == expected_preset:
                            break
                        await asyncio.sleep(1)
                        actual = await self._read()
                        self.async_set_updated_data(actual)
                    if operation_mode(actual) != expected_preset:
                        raise HomeAssistantError(
                            "보일러가 프리셋 변경을 반영하지 않았습니다. "
                            "이 펌웨어의 모드 쓰기 지원은 확인되지 않았습니다. "
                            "시하스 앱에서 모드를 변경하고 진단 정보를 확인하세요."
                        )
            except UpdateFailed as err:
                self.async_set_update_error(err)
                raise HomeAssistantError(str(err)) from err

    async def async_write(self, register: int, value: int):
        await self.async_control(lambda registers: [(register, value)])

    async def async_set_preset(self, preset: str):
        if not self.preset_control:
            raise HomeAssistantError("BCM 구성에서 시험 기능인 프리셋 전환을 켜세요.")
        if preset not in PRESET_MODES:
            raise HomeAssistantError("지원하지 않는 프리셋입니다.")
        await self.async_control(
            lambda registers: preset_command(registers, preset),
            expected_preset=preset,
        )


class BcmEntity(CoordinatorEntity):
    """Stable entity IDs and a shared device for the BCM platforms."""

    def __init__(self, coordinator, suffix: str | None, label: str | None):
        super().__init__(coordinator)
        data = coordinator.entry.data
        base = f"BCM-{data[CONF_MAC]}"
        self._attr_unique_id = f"{base}-{suffix}" if suffix else base
        name = data.get(CONF_NAME) or base
        self._attr_name = f"{name} {label}" if label else name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, data[CONF_MAC])},
            "name": name,
            "manufacturer": "SiHAS",
            "model": "BCM-300",
        }

    @property
    def registers(self):
        return self.coordinator.data

    @property
    def available(self):
        return (
            super().available
            and self.registers is not None
            and self.registers[CONNECTIVITY] == 0
        )

    @property
    def extra_state_attributes(self):
        return {"controller_profile": "NR-10E" if self.coordinator.nr10e else "generic"}
