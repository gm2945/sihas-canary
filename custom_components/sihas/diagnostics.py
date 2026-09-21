"""Read-only BCM snapshots for checking undocumented app controls."""

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return {"supported": False}
    return {
        "controller_profile": "NR-10E" if coordinator.nr10e else "generic",
        "last_update_success": coordinator.last_update_success,
        # No IP address, MAC address, device name or credentials.
        "registers": dict(enumerate(coordinator.data or [])),
    }
