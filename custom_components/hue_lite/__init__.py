"""Hue-Lite: Hue-style scenes, a Natural light scene that follows the sun, and
lighting routines for any lights in Home Assistant, chosen by area and label."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er, issue_registry as ir
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
    # The dashboard's script first, so pages opened while Home Assistant's
    # starting get it as soon as possible (they wait 5 seconds for it).
    entry.async_on_unload(dashboard.async_add_script(hass))
    hue_lite = HueLite(hass, entry)
    await hue_lite.async_load()
    entry.runtime_data = hue_lite
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Before starting, so a start that fails part-way still stops what it started.
    entry.async_on_unload(hue_lite.async_stop)
    hue_lite.async_start()
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: HueLiteConfigEntry) -> bool:
    """Bring an older entry up to date. Before minor version 2 it had no First
    one home label, so one is found or created; before 3 it had the evening
    routine's single switch, which is removed (there's one for Sun–Thu and one
    for Fri & Sat nights now). An entry from a later major version isn't loaded."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        missing = [role for role in ROLES if label_option(role) not in entry.options]
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, **role_labels(hass, missing)}, minor_version=2
        )
    if entry.minor_version < 3:
        registry = er.async_get(hass)
        if old := registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_evening_routine_enabled"):
            registry.async_remove(old)
        hass.config_entries.async_update_entry(entry, minor_version=3)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HueLiteConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Stopped first, so nothing changes (and is saved again) after this save.
        entry.runtime_data.async_stop()
        await entry.runtime_data.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: HueLiteConfigEntry) -> None:
    """Forget the settings, scenes and any Repairs issues (the labels stay)."""
    await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_remove()
    for role in ROLES:
        ir.async_delete_issue(hass, DOMAIN, f"missing_label_{role}")

