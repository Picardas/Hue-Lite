"""On/off settings: the routines, and which days wake up and the evening routine run."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SWITCHES
from .engine import HueLiteConfigEntry
from .entity import SettingEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HueLiteConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    # The evening routine's one switch is now one for Sun–Thu nights and one
    # for Fri & Sat nights.
    registry = er.async_get(hass)
    if old := registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_evening_routine_enabled"):
        registry.async_remove(old)
    async_add_entities(SettingSwitch(entry.runtime_data, key) for key in SWITCHES)


class SettingSwitch(SettingEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return self.hue_lite.settings[self.key]

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)
