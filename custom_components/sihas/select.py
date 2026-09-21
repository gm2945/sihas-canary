"""Independent, documented BCM controls."""

from homeassistant.components.select import SelectEntity

from .bcm import AWAY, HOT_WATER_TARGET, BcmEntity
from .const import CONF_TYPE, DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data[CONF_TYPE] != "BCM":
        return
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        BcmSelect(coordinator, "occupancy_mode", "외출 모드", AWAY, ["재실", "외출"])
    ]
    if coordinator.nr10e:
        entities.append(
            BcmSelect(
                coordinator,
                "hot_water_level",
                "온수 단계",
                HOT_WATER_TARGET,
                ["저온", "중온", "고온"],
            )
        )
    async_add_entities(entities)


class BcmSelect(BcmEntity, SelectEntity):
    def __init__(self, coordinator, suffix, label, register, options):
        super().__init__(coordinator, suffix, label)
        self.register = register
        self._attr_options = options

    @property
    def current_option(self):
        value = self.registers[self.register]
        return self.options[value] if 0 <= value < len(self.options) else None

    async def async_select_option(self, option):
        await self.coordinator.async_write(self.register, self.options.index(option))
