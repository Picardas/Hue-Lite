"""Timings and fades, in minutes."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import NUMBERS
from .engine import HueLiteConfigEntry
from .entity import HueLiteEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HueLiteConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(SettingNumber(entry.runtime_data, key) for key in NUMBERS)


class SettingNumber(HueLiteEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, hue_lite, key: str) -> None:
        super().__init__(hue_lite, key)
        _, self._attr_native_min_value, self._attr_native_max_value, self._attr_native_step = NUMBERS[key]

    @property
    def native_value(self) -> int:
        return self.hue_lite.settings[self.key]

    async def async_set_native_value(self, value: float) -> None:
        self.hue_lite.async_set_setting(self.key, int(value))
