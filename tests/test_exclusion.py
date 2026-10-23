"""Excluded lights are never controlled, and Home Assistant's own scenes win
over Hue-Lite's."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.hue_lite.const import DOMAIN, STORAGE_KEY
from custom_components.hue_lite.engine import HueLite

from .conftest import Home
from .test_routines import natural, on, set_up, switch_on, tick

EXCLUDED = "light.garden"


def touched(home: Home) -> set[str]:
    """Every light sent a command."""
    found: set[str] = set()
    for call in [*home.turn_on, *home.turn_off]:
        ids = call.data["entity_id"]
        found.update([ids] if isinstance(ids, str) else ids)
    return found


async def exclude_in_living_room(hass: HomeAssistant, home: Home) -> None:
    """The garden light: excluded, but in the living room and labelled for
    every routine, so only the Exclude label keeps it out."""
    er.async_get(hass).async_update_entity(
        EXCLUDED, area_id="living_room", labels={home.labels[role] for role in home.labels}
    )
    hass.states.async_set(EXCLUDED, "off")


async def test_excluded_light_is_never_touched(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    await exclude_in_living_room(hass, home)
    await tick(hass, freezer, "06:00")
    hue_lite = await set_up(hass, entry)
    for switch in ("wake_up_on_weekdays", "lights_off_when_everyone_leaves"):
        await switch_on(hass, f"switch.hue_lite_{switch}")

    # Actions by area, label and on everything.
    await hass.services.async_call(DOMAIN, "apply_preset", {"preset": "relax", "area_id": "living_room"}, blocking=True)
    await hass.services.async_call(DOMAIN, "natural_light", {"label_id": home.labels["evening"]}, blocking=True)
    await hass.services.async_call(DOMAIN, "next_scene", {}, blocking=True)
    # Wake up (06:30), the evening routine (16:30), bedtime (23:00).
    hass.states.async_set("light.bedroom", "off")
    await tick(hass, freezer, "06:30")
    await tick(hass, freezer, "16:29")
    await tick(hass, freezer, "16:30")
    await tick(hass, freezer, "23:00")
    # A Hue dimmer in the living room.
    zha = MockConfigEntry(domain="zha")
    zha.add_to_hass(hass)
    devices = dr.async_get(hass)
    dimmer = devices.async_get_or_create(config_entry_id=zha.entry_id, identifiers={("zha", "d")}, model="RWL021")
    devices.async_update_device(dimmer.id, area_id="living_room")
    for command in ("on_short_release", "up_press", "off_short_release"):
        hass.bus.async_fire("zha_event", {"device_id": dimmer.id, "command": command})
    await hass.async_block_till_done(wait_background_tasks=True)
    # Coming home after dark, then everyone leaving.
    hass.states.async_set(EXCLUDED, "on")
    hass.states.async_set("zone.home", "0")
    freezer.tick(130)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.states.async_set(EXCLUDED, "off")
    hass.states.async_set("zone.home", "1")
    await hass.async_block_till_done(wait_background_tasks=True)

    assert home.turn_on and home.turn_off  # the other lights were driven
    assert EXCLUDED not in touched(home)
    assert EXCLUDED not in hue_lite.scenes


async def test_light_excluded_while_in_a_scene_is_let_go(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await hass.services.async_call(DOMAIN, "natural_light", {"entity_id": "light.living_room"}, blocking=True)
    er.async_get(hass).async_update_entity(
        "light.living_room", labels={home.labels["exclude"], home.labels["evening"]}
    )
    await hass.async_block_till_done()
    assert hue_lite.scenes == {}

    # Switched off and on again, it's no longer put back into Natural light.
    home.turn_on.clear()
    hass.states.async_set("light.living_room", "off")
    hass.states.async_set("light.living_room", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert home.turn_on == []


async def test_hiding_a_light_lets_it_go_too(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await hass.services.async_call(DOMAIN, "apply_preset", {"preset": "rest", "entity_id": "light.bedroom"}, blocking=True)
    er.async_get(hass).async_update_entity("light.bedroom", hidden_by=er.RegistryEntryHider.USER)
    await hass.async_block_till_done()
    assert hue_lite.scenes == {}


async def test_excluded_light_named_directly_is_set_but_not_kept(
    hass: HomeAssistant, home: Home, hue_lite: HueLite
) -> None:
    """Lights named in an action are used as given, once; Hue-Lite doesn't
    then keep moving an excluded one."""
    await hass.services.async_call(DOMAIN, "natural_light", {"entity_id": EXCLUDED}, blocking=True)
    assert touched(home) == {EXCLUDED}
    assert hue_lite.scenes == {}


async def test_saved_scenes_of_lights_excluded_since_are_dropped(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {"settings": {}, "scenes": {EXCLUDED: "natural", "light.bedroom": "natural"}},
    }
    hue_lite = await set_up(hass, entry)
    assert hue_lite.scenes == {"light.bedroom": "natural"}


async def test_home_assistant_scene_takes_lights_out_of_hue_lites(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Activating one of your own scenes (scene editor, scenes.yaml) is like
    picking a scene in Hue: Natural light lets go of those lights."""
    await tick(hass, freezer, "08:00")
    hue_lite = await set_up(hass, entry)
    assert await async_setup_component(
        hass, "scene", {"scene": [{"name": "Movie", "entities": {"light.living_room": {"state": "on", "brightness": 50}}}]}
    )
    await hass.services.async_call(DOMAIN, "natural_light", {}, blocking=True)
    on(hass, hue_lite, "light.living_room", "light.bedroom")
    assert natural(hass) == ["light.bedroom", "light.living_room"]

    home.turn_on.clear()
    await hass.services.async_call(
        "scene", "turn_on", {"entity_id": "scene.movie"}, blocking=True, context=Context(user_id=None)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert natural(hass) == ["light.bedroom"]
    # The scene set the living room; Hue-Lite didn't follow up on it.
    assert [call.data["entity_id"] for call in home.turn_on] == ["light.living_room"]

    # Natural light keeps moving only the bedroom.
    home.turn_on.clear()
    await tick(hass, freezer, "09:00")
    assert touched(home) == {"light.bedroom"}


async def test_scene_reload_is_not_an_activation(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    assert await async_setup_component(
        hass, "scene", {"scene": [{"name": "Movie", "entities": {"light.living_room": {"state": "on"}}}]}
    )
    await hass.services.async_call(DOMAIN, "natural_light", {"entity_id": "light.living_room"}, blocking=True)
    hass.states.async_set("scene.movie", "unavailable")
    hass.states.async_set("scene.movie", "unknown", {"entity_id": ["light.living_room"]})
    await hass.async_block_till_done()
    assert natural(hass) == ["light.living_room"]
