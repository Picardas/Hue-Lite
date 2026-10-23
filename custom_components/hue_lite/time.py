"""Wake-up times and bedtimes."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TIMES
from .engine import HueLiteConfigEntry
from .entity import HueLiteEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HueLiteConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(SettingTime(entry.runtime_data, key) for key in TIMES)


class SettingTime(HueLiteEntity, TimeEntity):
    @property
    def native_value(self) -> time:
        return time.fromisoformat(self.hue_lite.settings[self.key])

    async def async_set_value(self, value: time) -> None:
        self.hue_lite.async_set_setting(self.key, value.strftime("%H:%M"))
