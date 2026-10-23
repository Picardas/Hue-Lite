"""Bases for Hue-Lite's entities, all on one device."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import Entity

from .engine import HueLite


class HueLiteEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hue_lite: HueLite, key: str) -> None:
        self.hue_lite = hue_lite
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{hue_lite.entry.entry_id}_{key}"
        self._attr_device_info = hue_lite.device_info

    async def async_added_to_hass(self) -> None:
        self.hue_lite.entity_ids[self.key] = self.entity_id
        self.async_on_remove(lambda: self.hue_lite.entity_ids.pop(self.key, None))


class SettingEntity(HueLiteEntity):
    """A setting: its value lives in the engine, and only this entity changes it."""

    def _set(self, value: Any) -> None:
        self.hue_lite.async_set_setting(self.key, value)
        self.async_write_ha_state()
