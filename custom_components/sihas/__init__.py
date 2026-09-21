"""The sihas integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_TYPE
from .bcm import BcmCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
    "binary_sensor",
    "button",
    "climate",
    "cover",
    "light",
    "sensor",
    "select",
    "switch",
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    # NOTE: how about checking supported type at here?
    _LOGGER.info(f"entry setuped: {entry.data}")
    if entry.data[CONF_TYPE] == "BCM":
        coordinator = BcmCoordinator(hass, entry)
        await coordinator.async_config_entry_first_refresh()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
        entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    _LOGGER.info(f"entry unloadded: {entry.data}")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def _async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

