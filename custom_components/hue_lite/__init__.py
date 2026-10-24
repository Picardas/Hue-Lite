"""Hue-Lite: Hue-style scenes, a Natural light scene that follows the sun, and
lighting routines for any lights in Home Assistant, chosen by area and label."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import dashboard, services
from .config_flow import role_labels
from .const import DOMAIN, ROLES, STORAGE_KEY, STORAGE_VERSION, label_option
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


async def async_migrate_entry(hass: HomeAssistant, entry: HueLiteConfigEntry) -> bool:
    """Set up before a role existed (First one home, added in 1.2), find or
    create its label."""
    if entry.version == 1 and entry.minor_version < 2:
        missing = [role for role in ROLES if label_option(role) not in entry.options]
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, **role_labels(hass, missing)}, minor_version=2
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HueLiteConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: HueLiteConfigEntry) -> None:
    """Forget the settings, scenes and any Repairs issues (the labels stay)."""
    await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_remove()
    for role in ROLES:
        ir.async_delete_issue(hass, DOMAIN, f"missing_label_{role}")


async def _async_options_updated(hass: HomeAssistant, entry: HueLiteConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
