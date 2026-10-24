"""Entities, what's saved, the dashboard, sun times and Repairs."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import Mock

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir, label_registry as lr

from custom_components.hue_lite.const import DOMAIN, STORAGE_KEY
from custom_components.hue_lite.dashboard import websocket_config
from custom_components.hue_lite.engine import HueLite

from .conftest import FRIDAY, Home, add_device, add_light, set_up

# The real one, before the fixed_sun fixture patches it.
REAL_SUN_TIMES = HueLite.__dict__["_sun_times"]


def stored(hass_storage: dict[str, Any], settings: dict | None = None, scenes: dict | None = None) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {"settings": settings or {}, "scenes": scenes or {}},
    }


async def test_entities(hass: HomeAssistant, hue_lite: HueLite) -> None:
    ids = hue_lite.entity_ids
    assert ids["natural_light"] == "sensor.hue_lite_natural_light"
    assert ids["light_scenes"] == "sensor.hue_lite_light_scenes"
    assert ids["evening_weeknights"] == "switch.hue_lite_evening_routine_sun_thu_nights"
    assert ids["evening_weekends"] == "switch.hue_lite_evening_routine_fri_sat_nights"
    assert ids["wake_up_weekday"] == "time.hue_lite_wake_up_mon_fri"
    assert ids["bedtime_fade_minutes"] == "number.hue_lite_bedtime_fade_out"
    assert len(ids) == 3 + 9 + 4 + 7

    assert hass.states.get("switch.hue_lite_evening_routine_sun_thu_nights").state == "on"
    assert hass.states.get("switch.hue_lite_evening_routine_fri_sat_nights").state == "on"
    assert hass.states.get("switch.hue_lite_lights_off_when_everyone_leaves").state == "off"
    assert hass.states.get("time.hue_lite_bedtime_sun_thu_nights").state == "23:00:00"
    assert hass.states.get("number.hue_lite_bedtime_fade_out").state == "15"
    # Settings are configuration, so Home Assistant's own dashboards show the
    # routines' sensors rather than every setting.
    registry = er.async_get(hass)
    for setting in ("number.hue_lite_bedtime_fade_out", "switch.hue_lite_coming_home", "time.hue_lite_wake_up_mon_fri"):
        assert registry.async_get(setting).entity_category == "config"
    assert registry.async_get("sensor.hue_lite_natural_light").entity_category is None
    assert hass.states.get("sensor.hue_lite_natural_light").state == "day"


async def test_settings_are_kept(hass: HomeAssistant, entry: MockConfigEntry, hue_lite: HueLite) -> None:
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.hue_lite_evening_routine_sun_thu_nights"}, blocking=True
    )
    await hass.services.async_call(
        "time", "set_value", {"entity_id": "time.hue_lite_bedtime_sun_thu_nights", "time": "22:15"}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.hue_lite_bedtime_fade_out", "value": 20}, blocking=True
    )
    assert hass.states.get("sensor.hue_lite_evening_routine").attributes["bedtime"] == "22:15"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.hue_lite_evening_routine_sun_thu_nights").state == "off"
    assert hass.states.get("time.hue_lite_bedtime_sun_thu_nights").state == "22:15:00"
    assert hass.states.get("number.hue_lite_bedtime_fade_out").state == "20"


async def test_the_old_evening_switch_becomes_two(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """The evening routine's one switch, switched off, becomes two switched
    off, and its entity goes."""
    stored(hass_storage, settings={"evening_routine_enabled": False})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}_evening_routine_enabled", config_entry=entry)
    await set_up(hass, entry)
    assert hass.states.get("switch.hue_lite_evening_routine_sun_thu_nights").state == "off"
    assert hass.states.get("switch.hue_lite_evening_routine_fri_sat_nights").state == "off"
    assert registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_evening_routine_enabled") is None


async def test_damaged_saved_data_is_left_out(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Parts of the wrong kind are left out rather than stopping Hue-Lite
    starting (or a string of a light being read as many one-letter lights)."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {
            "settings": ["off"],
            "scenes": "light.bedroom",
            "auto": "light.bedroom",
            "dimmed": ["light.bedroom"],
            "last_wake_up": "2026-10-22T07:00:00",
        },
    }
    hue_lite = await set_up(hass, entry)
    assert (hue_lite.scenes, hue_lite.auto, hue_lite.dimmed, hue_lite.last_wake_up) == ({}, set(), {}, None)
    assert hue_lite.settings["evening_weeknights"] is True


async def test_bad_or_stale_saved_data_is_dropped(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Values of the wrong kind keep their defaults (a malformed time would
    break every tick), and lights excluded since, or in a scene there isn't
    any more, are let go."""
    stored(
        hass_storage,
        settings={"bedtime_weeknight": "25:99", "bedtime_fade_minutes": "lots", "wake_up_weekdays": True},
        scenes={
            "light.bedroom": "relax",
            "light.living_room": "movie",
            "light.garden": "natural",
            "light.broken": 3,
            "light.desk": "sleep",
        },
    )
    hue_lite = await set_up(hass, entry)
    assert hue_lite.settings["bedtime_weeknight"] == "23:00"
    assert hue_lite.settings["bedtime_fade_minutes"] == 15
    assert hue_lite.settings["wake_up_weekdays"] is True
    # Movie is called Nebula now.
    assert hue_lite.scenes == {"light.bedroom": "relax", "light.living_room": "nebula"}


async def test_routine_lights_let_go_when_bedtime_passed_while_down(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Home Assistant was down over bedtime: the routine's lights aren't kept
    (and left following it) until the next evening."""
    freezer.move_to(f"{FRIDAY} 01:00:00+01:00")
    stored(hass_storage, scenes={"light.bedroom": "routine", "light.living_room": "natural"})
    hue_lite = await set_up(hass, entry)
    assert hue_lite.scenes == {"light.living_room": "natural"}


async def test_dashboard_script_and_config(hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite) -> None:
    labels = lr.async_get(hass)
    lamps = labels.async_create("Lamps")
    shelf = labels.async_create("Shelf")
    labels.async_create("Payroll")  # on nothing
    er.async_get(hass).async_update_entity("light.living_room", labels={home.labels["evening"], lamps.label_id})
    add_light(hass, "strip", device_id=add_device(hass, "strip", labels={shelf.label_id}).id)

    connection = Mock()
    websocket_config(hass, connection, {"id": 1, "type": f"{DOMAIN}/config"})
    config = connection.send_result.call_args.args[1]
    names = {label["name"] for label in config["labels"]}
    # Labels on lights and their devices, and the role labels; not the rest.
    assert {"Lamps", "Shelf", "Evening", "Wake up", "Nightlight", "Coming home", "First one home", "Exclude from lighting"} <= names
    assert "Payroll" not in names
    assert config["roles"] == home.labels
    assert config["entities"]["light_scenes"] == "sensor.hue_lite_light_scenes"
    # Each preset's and colour scene's colours as a colour bulb reports them.
    # With their brightness, for a copy made in the scene editor.
    assert config["presets"]["relax"] == {"rgb_colors": [[255, 189, 86]], "brightness": [56]}
    assert len(config["presets"]["nebula"]["rgb_colors"]) == len(config["presets"]["nebula"]["brightness"]) == 5
    assert config["presets"]["aurora"]["rgb_colors"][0] == [20, 255, 126]

    # The script is on every page (versioned by content) while Hue-Lite is set up.
    scripts = hass.data[DATA_EXTRA_MODULE_URL]
    assert [url.split("?v=")[0] for url in scripts.urls] == ["/hue_lite/hue-lite.js"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not scripts.urls


async def test_deleted_role_label_raises_a_repair(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    labels = lr.async_get(hass)
    issues = ir.async_get(hass)
    labels.async_delete(home.labels["evening"])
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, "missing_label_evening")
    assert issue is not None
    assert issue.translation_placeholders == {"role": "Evening"}

    # Making the label again clears it.
    assert labels.async_create("Evening").label_id == home.labels["evening"]
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, "missing_label_evening") is None


async def test_removing_hue_lite_forgets_it(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite, hass_storage: dict[str, Any]
) -> None:
    """Its settings and scenes, and its Repairs issues; the labels stay."""
    lr.async_get(hass).async_delete(home.labels["evening"])
    await hass.async_block_till_done()
    await hue_lite.async_save()
    assert STORAGE_KEY in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    assert STORAGE_KEY not in hass_storage
    assert ir.async_get(hass).async_get_issue(DOMAIN, "missing_label_evening") is None
    assert lr.async_get(hass).async_get_label(home.labels["wake_up"])


async def test_real_sun_times(hass: HomeAssistant, hue_lite: HueLite) -> None:
    """Without the fixed times the other tests use: London on midsummer's day."""
    hass.config.latitude, hass.config.longitude = 51.5, -0.13
    sun = REAL_SUN_TIMES(hue_lite, date(2026, 6, 21))
    assert sun.dawn < sun.sunrise < sun.noon < sun.sunset < sun.dusk
    assert 4 * 60 <= sun.sunrise <= 5 * 60  # about 04:43 BST
    assert 21 * 60 <= sun.sunset <= 21 * 60 + 30  # about 21:21 BST
