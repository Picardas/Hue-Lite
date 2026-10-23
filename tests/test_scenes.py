"""Entities, actions, choosing lights and who's in which scene."""

from __future__ import annotations

from unittest.mock import Mock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.hue_lite.const import DOMAIN
from custom_components.hue_lite.dashboard import websocket_config
from custom_components.hue_lite.engine import HueLite
from custom_components.hue_lite.timeline import PRESETS

from .conftest import Home

RELAX = {"brightness_pct": 56, "xy_color": [0.5019, 0.4152]}


def lights_in(hass: HomeAssistant, sensor: str) -> list[str]:
    return hass.states.get(sensor).attributes["lights"]


async def test_entities(hass: HomeAssistant, hue_lite: HueLite) -> None:
    ids = hue_lite.entity_ids
    assert ids["natural_light"] == "sensor.hue_lite_natural_light"
    assert ids["evening_routine_enabled"] == "switch.hue_lite_evening_routine"
    assert ids["wake_up_weekday"] == "time.hue_lite_wake_up_mon_fri"
    assert ids["bedtime_fade_minutes"] == "number.hue_lite_bedtime_fade_out"
    assert ids["light_scenes"] == "sensor.hue_lite_light_scenes"
    assert len(ids) == 3 + 7 + 4 + 7

    assert hass.states.get("switch.hue_lite_evening_routine").state == "on"
    assert hass.states.get("switch.hue_lite_lights_off_when_everyone_leaves").state == "off"
    assert hass.states.get("time.hue_lite_bedtime_sun_thu_nights").state == "23:00:00"
    assert hass.states.get("number.hue_lite_bedtime_fade_out").state == "15"
    assert er.async_get(hass).async_get("number.hue_lite_bedtime_fade_out").entity_category == "config"
    assert hass.states.get("sensor.hue_lite_natural_light").state in (
        "night", "sunrise", "morning", "day", "sunset", "evening"
    )


async def test_settings_are_kept(hass: HomeAssistant, entry: MockConfigEntry, hue_lite: HueLite) -> None:
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.hue_lite_evening_routine"}, blocking=True)
    await hass.services.async_call(
        "time", "set_value", {"entity_id": "time.hue_lite_bedtime_sun_thu_nights", "time": "22:15"}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.hue_lite_bedtime_fade_out", "value": 20}, blocking=True
    )
    assert hass.states.get("sensor.hue_lite_evening_routine").attributes["bedtime"] == "22:15"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.hue_lite_evening_routine").state == "off"
    assert hass.states.get("time.hue_lite_bedtime_sun_thu_nights").state == "22:15:00"
    assert hass.states.get("number.hue_lite_bedtime_fade_out").state == "20"


async def test_choosing_lights(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    # Every light: not the excluded, hidden or group ones.
    assert hue_lite.lights() == ["light.bedroom", "light.living_room"]
    assert hue_lite.lights({"area_id": ["bedroom"]}) == ["light.bedroom"]
    assert hue_lite.lights({"label_id": [home.labels["evening"]]}) == ["light.bedroom", "light.living_room"]
    # Named lights are used as given, unless they're unavailable or missing.
    assert hue_lite.lights({"entity_id": ["light.garden", "light.nope"]}) == ["light.garden"]
    hass.states.async_set("light.bedroom", "unavailable")
    assert hue_lite.lights() == ["light.living_room"]


async def test_apply_preset(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await hass.services.async_call(DOMAIN, "apply_preset", {"preset": "relax", "area_id": "living_room"}, blocking=True)
    assert len(home.turn_on) == 1
    assert home.turn_on[0].data == {"entity_id": ["light.living_room"], **RELAX, "transition": 0.4}
    assert home.turn_on[0].context.user_id is None


async def test_natural_light_and_leaving_it(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await hass.services.async_call(DOMAIN, "natural_light", {}, blocking=True)
    assert lights_in(hass, "sensor.hue_lite_natural_light") == ["light.bedroom", "light.living_room"]
    assert hass.states.get("sensor.hue_lite_natural_light").state == "day"
    assert [call.data for call in home.turn_on] == [
        {"entity_id": ["light.bedroom", "light.living_room"], **PRESETS["cool_bright"].colour_data(), "transition": 2}
    ]

    # A preset takes the light out; follow off takes the rest out.
    await hass.services.async_call(
        DOMAIN, "apply_preset", {"preset": "rest", "entity_id": "light.bedroom"}, blocking=True
    )
    assert lights_in(hass, "sensor.hue_lite_natural_light") == ["light.living_room"]
    await hass.services.async_call(DOMAIN, "natural_light", {"follow": False}, blocking=True)
    assert lights_in(hass, "sensor.hue_lite_natural_light") == []


async def test_turning_on_and_changing_by_hand(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await hass.services.async_call(DOMAIN, "natural_light", {"entity_id": "light.living_room"}, blocking=True)
    home.turn_on.clear()

    # Hue-Lite's own change (its context) is left alone.
    hass.states.async_set("light.living_room", "on", {"brightness": 100}, context=Context(id=hue_lite._ours[-1]))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert home.turn_on == []

    # Switched off and on again (e.g. at the wall): it picks the scene up again.
    hass.states.async_set("light.living_room", "off")
    hass.states.async_set("light.living_room", "on", {"brightness": 255})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [call.data["entity_id"] for call in home.turn_on] == [["light.living_room"]]
    assert lights_in(hass, "sensor.hue_lite_natural_light") == ["light.living_room"]

    # A change without a user (a bulb reporting in) doesn't count as by hand...
    hass.states.async_set("light.living_room", "on", {"brightness": 200})
    await hass.async_block_till_done()
    assert lights_in(hass, "sensor.hue_lite_natural_light") == ["light.living_room"]
    # ...but dimming it from the dashboard does.
    hass.states.async_set("light.living_room", "on", {"brightness": 50}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    assert lights_in(hass, "sensor.hue_lite_natural_light") == []


async def test_next_scene_cycles(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    async def press() -> dict:
        home.turn_on.clear()
        await hass.services.async_call(DOMAIN, "next_scene", {"area_id": "living_room"}, blocking=True)
        return home.turn_on[-1].data

    # Off: Natural light first.
    await press()
    assert lights_in(hass, "sensor.hue_lite_natural_light") == ["light.living_room"]
    hass.states.async_set("light.living_room", "on", {}, context=Context(id=hue_lite._ours[-1]))
    # Then on through the presets, whatever the bulb reports.
    assert await press() == {"entity_id": ["light.living_room"], **PRESETS["energize"].colour_data(), "transition": 0.4}
    assert (await press())["xy_color"] == list(PRESETS["cool_bright"].xy_color)
    assert hass.states.get("sensor.hue_lite_light_scenes").attributes["scenes"] == {"light.living_room": "cool_bright"}
    # Changed by hand: back to Natural light.
    hass.states.async_set("light.living_room", "on", {"brightness": 50}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    await press()
    assert lights_in(hass, "sensor.hue_lite_natural_light") == ["light.living_room"]


async def test_white_and_dimmable_bulbs(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    """White-ambiance bulbs get a colour temperature, not a converted colour;
    their scene is remembered, so they cycle and highlight like colour bulbs."""
    hass.states.async_set("light.bedroom", "on", {"supported_color_modes": ["color_temp"]})
    hass.states.async_set("light.living_room", "on", {"supported_color_modes": ["xy", "color_temp"]})
    entry = er.async_get(hass).async_get_or_create("light", "test", "lamp", suggested_object_id="lamp")
    er.async_get(hass).async_update_entity(entry.entity_id, area_id="bedroom")
    hass.states.async_set("light.lamp", "on", {"supported_color_modes": ["brightness"]})

    await hass.services.async_call(DOMAIN, "apply_preset", {"preset": "nightlight"}, blocking=True)
    assert [call.data for call in home.turn_on] == [
        {"entity_id": ["light.living_room"], "brightness_pct": 1, "xy_color": [0.67, 0.32], "transition": 0.4},
        {"entity_id": ["light.bedroom", "light.lamp"], "brightness_pct": 1, "color_temp_kelvin": 2000, "transition": 0.4},
    ]
    assert hue_lite.current_scene(["light.bedroom", "light.lamp", "light.living_room"]) == "nightlight"

    # The dimmer cycle carries on from there: Natural light next.
    home.turn_on.clear()
    await hass.services.async_call(DOMAIN, "next_scene", {"area_id": "bedroom"}, blocking=True)
    assert lights_in(hass, "sensor.hue_lite_natural_light") == ["light.bedroom", "light.lamp"]
    assert home.turn_on[-1].data == {
        "entity_id": ["light.bedroom", "light.lamp"], **PRESETS["cool_bright"].white_data(), "transition": 2
    }


async def test_dashboard_script(hass: HomeAssistant, entry: MockConfigEntry, hue_lite: HueLite) -> None:
    """Loaded on every page (versioned by content) while Hue-Lite is set up."""
    scripts = hass.data[DATA_EXTRA_MODULE_URL]
    assert [url.split("?v=")[0] for url in scripts.urls] == ["/hue_lite/hue-lite.js"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not scripts.urls


async def test_dashboard_config(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    connection = Mock()
    websocket_config(hass, connection, {"id": 1, "type": f"{DOMAIN}/config"})
    config = connection.send_result.call_args.args[1]
    assert config["roles"] == home.labels
    assert {label["name"] for label in config["labels"]} >= {"Evening", "Exclude from lighting"}
    assert config["entities"]["light_scenes"] == "sensor.hue_lite_light_scenes"
