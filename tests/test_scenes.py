"""Presets, Natural light, which scene each light is in, and Home Assistant's
own scenes."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.hue_lite.engine import HueLite
from custom_components.hue_lite.timeline import PALETTES, PRESETS

from .conftest import Home, action, add_light, natural, on, ours, set_up, settle, tick

RELAX = {"brightness_pct": 56, "xy_color": [0.5019, 0.4152]}


async def test_colour_scene(hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, hue_lite: HueLite) -> None:
    lamp = add_light(hass, "lamp", "living_room")
    desk = add_light(hass, "desk", "living_room")
    hass.states.async_set(desk, "off", {"supported_color_modes": ["color_temp"]})
    colours = list(PALETTES["golden_hour"].colours)

    def sent() -> dict[str, dict]:
        return {c.data["entity_id"][0]: c.data for c in home.turn_on}

    def colour(data: dict) -> int:
        return colours.index(tuple(data["xy_color"]))

    # The colour bulbs get the palette's colours, one after another; white
    # bulbs get its white.
    await action(hass, "apply_preset", {"preset": "golden_hour", "area_id": "living_room"})
    first = sent()
    assert colour(first["light.living_room"]) == (colour(first[lamp]) + 1) % len(colours)
    assert first[lamp]["brightness_pct"] == 70
    assert first[desk] == {"entity_id": [desk], "brightness_pct": 70, "color_temp_kelvin": 2500, "transition": 0.4}
    assert set(hass.states.get("sensor.hue_lite_light_scenes").attributes["scenes"].values()) == {"golden_hour"}

    # Every few minutes the colour bulbs that are on drift on to their next
    # colour, taking until the next move; the white one stays put.
    on(hass, hue_lite, lamp, "light.living_room")
    hass.states.async_set(desk, "on", {"supported_color_modes": ["color_temp"]}, context=ours(hue_lite))
    home.turn_on.clear()
    await tick(hass, freezer, "13:03")
    moved = sent()
    assert set(moved) == {lamp, "light.living_room"}
    assert colour(moved[lamp]) == (colour(first[lamp]) + 1) % len(colours)
    assert moved[lamp]["transition"] == 180

    # Switched off and on again, a bulb comes back in its colour and drifts on
    # from there: nothing's sent (a second command would only queue up).
    home.turn_on.clear()
    hass.states.async_set(lamp, "off")
    hass.states.async_set(lamp, "on")
    await settle(hass)
    assert home.turn_on == []
    assert hass.states.get("sensor.hue_lite_light_scenes").attributes["scenes"][lamp] == "golden_hour"


async def test_apply_preset(hass: HomeAssistant, home: Home, hue_lite: HueLite, hass_admin_user: MockUser) -> None:
    call = Context(user_id=hass_admin_user.id)
    await action(hass, "apply_preset", {"preset": "relax", "area_id": "living_room"}, call)
    assert [c.data for c in home.turn_on] == [{"entity_id": ["light.living_room"], **RELAX, "transition": 0.4}]
    # Sent as Hue-Lite (not a change by hand), linked to the call for the logbook.
    sent = home.turn_on[0].context
    assert (sent.user_id, sent.parent_id) == (None, call.id)
    assert hass.states.get("sensor.hue_lite_light_scenes").attributes["scenes"] == {"light.living_room": "relax"}


async def test_natural_light_and_leaving_it(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await action(hass, "natural_light")
    assert natural(hass) == ["light.bedroom", "light.living_room"]
    assert [c.data for c in home.turn_on] == [
        {"entity_id": ["light.bedroom", "light.living_room"], **PRESETS["cool_bright"].colour_data(), "transition": 2}
    ]

    # A preset takes a light out; follow off takes the rest out of Natural
    # light, leaving other scenes alone.
    await action(hass, "apply_preset", {"preset": "rest", "entity_id": "light.bedroom"})
    await action(hass, "natural_light", {"follow": False})
    assert hue_lite.scenes == {"light.bedroom": "rest"}


async def test_lights_turned_on_or_changed(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    home.turn_on.clear()

    # Hue-Lite's own change (its context) is left alone.
    on(hass, hue_lite, "light.living_room")
    await settle(hass)
    assert home.turn_on == []

    # Switched off and on again (e.g. at the wall): it picks the scene up again.
    hass.states.async_set("light.living_room", "off")
    hass.states.async_set("light.living_room", "on", {"brightness": 255})
    await settle(hass)
    assert [c.data["entity_id"] for c in home.turn_on] == [["light.living_room"]]

    # A bulb reporting in (no user, no cause) isn't a change...
    hass.states.async_set("light.living_room", "on", {"brightness": 200})
    await hass.async_block_till_done()
    assert natural(hass) == ["light.living_room"]
    # ...but one from an automation is...
    hass.states.async_set("light.living_room", "on", {"brightness": 80}, context=Context(parent_id="automation"))
    await hass.async_block_till_done()
    assert natural(hass) == []
    # ...and so is one by hand.
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    hass.states.async_set("light.living_room", "on", {"brightness": 50}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    assert natural(hass) == []

    # Switched off and on again by hand, it picks the scene up; switched on
    # at a brightness of your own (the slider), that's a change by hand.
    by_hand = Context(user_id="someone")
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    on(hass, hue_lite, "light.living_room")
    hass.states.async_set("light.living_room", "on", {"brightness": 255}, context=ours(hue_lite))
    hass.states.async_set("light.living_room", "off", context=by_hand)
    home.turn_on.clear()
    hass.states.async_set("light.living_room", "on", {"brightness": 255}, context=by_hand)
    await settle(hass)
    assert (natural(hass), len(home.turn_on)) == (["light.living_room"], 1)
    hass.states.async_set("light.living_room", "off", context=by_hand)
    home.turn_on.clear()
    hass.states.async_set("light.living_room", "on", {"brightness": 60}, context=by_hand)
    await settle(hass)
    assert (natural(hass), home.turn_on) == ([], [])


async def test_natural_light_moves_in_visible_steps(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """The morning blend changes too little in a minute to see: the lights,
    and the sensor (and recorder), move every few minutes instead."""
    await tick(hass, freezer, "08:00")
    hue_lite = await set_up(hass, entry)
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    on(hass, hue_lite, "light.living_room")
    home.turn_on.clear()
    shown = hass.states.get("sensor.hue_lite_natural_light")

    await tick(hass, freezer, "08:01")
    assert home.turn_on == []
    assert hass.states.get("sensor.hue_lite_natural_light").last_updated == shown.last_updated
    await tick(hass, freezer, "08:10")
    assert [c.data["transition"] for c in home.turn_on] == [60]
    assert home.turn_on[0].data["xy_color"] == list(hue_lite.natural.light.xy_color)
    assert hass.states.get("sensor.hue_lite_natural_light").attributes["color_temp_kelvin"] == hue_lite.natural.light.kelvin

    # A new phase is always sent, however small the change.
    await tick(hass, freezer, "11:59")
    home.turn_on.clear()
    await tick(hass, freezer, "12:00")
    assert hue_lite.natural.phase == "day"
    assert len(home.turn_on) == 1


async def test_next_scene_cycles(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    async def press() -> dict:
        home.turn_on.clear()
        await action(hass, "next_scene", {"area_id": "living_room"})
        return home.turn_on[-1].data

    # Off: Natural light first, then on through the presets.
    await press()
    assert natural(hass) == ["light.living_room"]
    on(hass, hue_lite, "light.living_room")
    assert await press() == {"entity_id": ["light.living_room"], **PRESETS["energize"].colour_data(), "transition": 0.4}
    assert (await press())["xy_color"] == list(PRESETS["cool_bright"].xy_color)
    # Changed by hand: back to Natural light.
    hass.states.async_set("light.living_room", "on", {"brightness": 50}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    await press()
    assert natural(hass) == ["light.living_room"]


async def test_white_and_dimmable_bulbs(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    """White-ambiance bulbs get a colour temperature, not a converted colour;
    their scene is remembered, so they cycle and highlight like colour bulbs."""
    hass.states.async_set("light.bedroom", "on", {"supported_color_modes": ["color_temp"]})
    hass.states.async_set("light.living_room", "on", {"supported_color_modes": ["xy", "color_temp"]})
    add_light(hass, "lamp", "bedroom")
    hass.states.async_set("light.lamp", "on", {"supported_color_modes": ["brightness"]})

    await action(hass, "apply_preset", {"preset": "nightlight"})
    assert [c.data for c in home.turn_on] == [
        {"entity_id": ["light.living_room"], "brightness_pct": 1, "xy_color": [0.67, 0.32], "transition": 0.4},
        {"entity_id": ["light.bedroom", "light.lamp"], "brightness_pct": 1, "color_temp_kelvin": 2000, "transition": 0.4},
    ]
    assert hue_lite.current_scene(["light.bedroom", "light.lamp", "light.living_room"]) == "nightlight"

    home.turn_on.clear()
    await action(hass, "next_scene", {"area_id": "bedroom"})
    assert natural(hass) == ["light.bedroom", "light.lamp"]
    assert home.turn_on[-1].data == {
        "entity_id": ["light.bedroom", "light.lamp"], **PRESETS["cool_bright"].white_data(), "transition": 2
    }


async def test_home_assistant_scenes_win(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Activating one of your own scenes is like picking a scene in Hue:
    Natural light lets go of its lights."""
    await tick(hass, freezer, "08:00")
    hue_lite = await set_up(hass, entry)
    assert await async_setup_component(
        hass, "scene", {"scene": [{"name": "Movie", "entities": {"light.living_room": {"state": "on", "brightness": 50}}}]}
    )
    # The scene entity is added after setup returns; wait for it, or the states
    # written below would take scene.movie first (and the real one be _2).
    await hass.async_block_till_done()
    assert hass.states.get("scene.movie").attributes["friendly_name"] == "Movie"
    await action(hass, "natural_light")
    on(hass, hue_lite, "light.living_room", "light.bedroom")

    # Reloading the scene isn't activating it.
    hass.states.async_set("scene.movie", "unavailable")
    hass.states.async_set("scene.movie", "unknown", {"entity_id": ["light.living_room"]})
    await hass.async_block_till_done()
    assert natural(hass) == ["light.bedroom", "light.living_room"]

    home.turn_on.clear()
    await hass.services.async_call("scene", "turn_on", {"entity_id": "scene.movie"}, blocking=True)
    await settle(hass)
    assert natural(hass) == ["light.bedroom"]
    assert [c.data["entity_id"] for c in home.turn_on] == ["light.living_room"]  # only the scene's own call

    # Natural light keeps moving only the bedroom.
    home.turn_on.clear()
    await tick(hass, freezer, "09:00")
    assert home.touched() == {"light.bedroom"}
