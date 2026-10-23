"""Hue-Lite: Hue-style scenes, a Natural light scene that follows the sun, and
lighting routines for any lights in Home Assistant, chosen by area and label."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import dashboard, services
from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION
from .engine import HueLite, HueLiteConfigEntry

PLATFORMS = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH, Platform.TIME]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    services.async_register(hass)
    await dashboard.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HueLiteConfigEntry) -> bool:
    hue_lite = HueLite(hass, entry)
    await hue_lite.async_load()
    entry.runtime_data = hue_lite
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hue_lite.async_start()
    entry.async_on_unload(hue_lite.async_stop)
    entry.async_on_unload(dashboard.async_add_script(hass))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HueLiteConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: HueLiteConfigEntry) -> None:
    """Forget the settings and scene lists (the labels stay)."""
    await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_remove()


async def _async_options_updated(hass: HomeAssistant, entry: HueLiteConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
