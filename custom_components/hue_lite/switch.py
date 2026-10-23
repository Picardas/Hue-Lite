"""On/off settings: the routines and which days wake up runs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONFIG_SWITCHES, SWITCHES
from .engine import HueLiteConfigEntry
from .entity import SettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HueLiteConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(SettingSwitch(entry.runtime_data, key) for key in SWITCHES)


class SettingSwitch(SettingEntity, SwitchEntity):
    def __init__(self, hue_lite, key: str) -> None:
        super().__init__(hue_lite, key)
        if key in CONFIG_SWITCHES:
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool:
        return self.hue_lite.settings[self.key]

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)
