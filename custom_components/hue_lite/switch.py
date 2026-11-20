"""On/off settings: the routines, and which days wake up and the evening routine run."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SWITCHES
from .engine import HueLiteConfigEntry
from .entity import SettingEntity

# Nothing polls, so Home Assistant needn't limit parallel updates.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: HueLiteConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(SettingSwitch(entry.runtime_data, key) for key in SWITCHES)


class SettingSwitch(SettingEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return self.hue_lite.settings[self.key]

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)
