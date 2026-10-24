"""Auto: a light that does what the routines ask, whether or not it has their
labels (on with them, off when none wants it), until a scene is picked for it
or it's changed."""

from __future__ import annotations

from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er

from custom_components.hue_lite.engine import HueLite
from custom_components.hue_lite.timeline import PRESETS

from .conftest import FRIDAY, Home, action, add_light, routine, set_up, settle, switch, tick


def auto(hass: HomeAssistant) -> list[str]:
    return hass.states.get("sensor.hue_lite_light_scenes").attributes["auto"]


def scenes(hass: HomeAssistant) -> dict[str, str]:
    return hass.states.get("sensor.hue_lite_light_scenes").attributes["scenes"]


async def test_auto_lights_follow_wake_up_and_the_evening(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    office = add_light(hass, "office", "living_room")  # no routine labels
    await tick(hass, freezer, "06:00")
    await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_wake_up_on_weekdays")
    await action(hass, "set_auto", {"auto": True, "entity_id": office})
    assert auto(hass) == [office]

    # Wake up (07:00, fading in from 06:30) takes it with the Wake up-labelled bedroom.
    await tick(hass, freezer, "06:30")
    assert home.turn_on[0].data["entity_id"] == ["light.bedroom", office]

    # So does the evening routine, with the Evening-labelled lights.
    hass.states.async_set("light.bedroom", "off")
    await tick(hass, freezer, "16:29")
    await tick(hass, freezer, "16:30")
    assert routine(hass) == ["light.bedroom", "light.living_room", office]


async def test_auto_during_the_evening(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    office = add_light(hass, "office", "living_room")
    desk = add_light(hass, "desk")
    await tick(hass, freezer, "16:29")
    hue_lite = await set_up(hass, entry)
    # The routine takes the Evening-labelled lights, which puts them on Auto.
    await tick(hass, freezer, "16:30")
    assert auto(hass) == ["light.bedroom", "light.living_room"]
    await tick(hass, freezer, "17:00")
    hass.states.async_set(office, "on")
    home.turn_on.clear()

    # Put on Auto during the evening, lights match the routine straight away:
    # on at its light, whether they were on or off.
    await action(hass, "set_auto", {"auto": True, "entity_id": [office, desk]})
    await settle(hass)
    assert routine(hass) == ["light.bedroom", desk, "light.living_room", office]
    assert [c.data["entity_id"] for c in home.turn_on] == [[desk, office]]
    assert home.turn_on[0].data["brightness_pct"] == hue_lite.evening.light.brightness_pct
    assert home.turn_off == []

    # Off Auto, lights leave the routine, labelled or not...
    await action(hass, "set_auto", {"auto": False, "entity_id": [office, "light.bedroom"]})
    assert routine(hass) == [desk, "light.living_room"]
    assert auto(hass) == [desk, "light.living_room"]

    # ...and the routine lets go of the rest at bedtime, but Auto stays on...
    await tick(hass, freezer, "23:00")
    assert auto(hass) == [desk]

    # ...and the labels still count the next evening.
    await tick(hass, freezer, f"{FRIDAY} 12:00:00+01:00")
    await tick(hass, freezer, f"{FRIDAY} 16:30:00+01:00")
    assert routine(hass) == ["light.bedroom", desk, "light.living_room"]


async def test_auto_turns_lights_off_when_no_routine_wants_them(
    hass: HomeAssistant, home: Home, hue_lite: HueLite
) -> None:
    # The tests' Thursday lunchtime: no routine has its lights on.
    hass.states.async_set("light.living_room", "on")
    await action(hass, "apply_preset", {"preset": "relax", "entity_id": "light.living_room"})
    await action(hass, "set_auto", {"auto": True, "entity_id": ["light.bedroom", "light.living_room"]})
    await settle(hass)
    # The light that's on goes off (leaving its scene); the one that's off stays off.
    assert [c.data for c in home.turn_off] == [{"entity_id": ["light.living_room"], "transition": 2}]
    assert (auto(hass), scenes(hass)) == (["light.bedroom", "light.living_room"], {})


async def test_auto_turns_lights_off_with_nobody_home_or_after_bedtime(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    office, desk = add_light(hass, "office", "living_room"), add_light(hass, "desk")
    await tick(hass, freezer, "17:00")
    await set_up(hass, entry)
    hass.states.async_set("zone.home", "0")

    # The evening, but with nobody home the routine isn't on: off.
    hass.states.async_set(office, "on")
    await action(hass, "set_auto", {"auto": True, "entity_id": office})
    await settle(hass)
    assert [c.data["entity_id"] for c in home.turn_off] == [[office]]

    # After bedtime, the routine's done for the night: off.
    hass.states.async_set("zone.home", "1")
    await tick(hass, freezer, "23:30")
    home.turn_off.clear()
    hass.states.async_set(desk, "on")
    await action(hass, "set_auto", {"auto": True, "entity_id": desk})
    await settle(hass)
    assert [c.data["entity_id"] for c in home.turn_off] == [[desk]]


async def test_auto_during_wake_up(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    office = add_light(hass, "office", "living_room")
    await tick(hass, freezer, "06:00")
    await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_wake_up_on_weekdays")
    await tick(hass, freezer, "06:30")  # wake up (07:00) fades in the Wake up-labelled bedroom
    await tick(hass, freezer, "06:40")
    home.turn_on.clear()

    # Put on Auto during the fade-in, a light joins it, reaching Energize by
    # the wake-up time, twenty minutes on.
    await action(hass, "set_auto", {"auto": True, "entity_id": office})
    await settle(hass)
    assert scenes(hass)[office] == "wake_up"
    assert [c.data for c in home.turn_on] == [{"entity_id": [office], **PRESETS["energize"].colour_data(), "transition": 1200}]


async def test_picking_a_scene_or_a_change_takes_a_light_off_auto(
    hass: HomeAssistant, home: Home, hue_lite: HueLite, hass_admin_user: MockUser
) -> None:
    by_hand = Context(user_id=hass_admin_user.id)
    await action(hass, "apply_preset", {"preset": "relax", "entity_id": "light.living_room"})
    # Put on Auto, a light leaves its scene: it's on Auto, not in Relax.
    await action(hass, "set_auto", {"auto": True, "entity_id": "light.living_room"})
    assert (auto(hass), scenes(hass)) == (["light.living_room"], {})

    # Turning it on and off isn't a change: it stays on Auto...
    for state in ("on", "off", "on"):
        hass.states.async_set("light.living_room", state, context=by_hand)
    await settle(hass)
    assert auto(hass) == ["light.living_room"]

    # ...but changing it takes it off.
    hass.states.async_set("light.living_room", "on", {"brightness": 50}, context=by_hand)
    await settle(hass)
    assert auto(hass) == []

    # So does picking a scene for it, which then shows instead.
    await action(hass, "set_auto", {"auto": True, "entity_id": "light.living_room"})
    await action(hass, "apply_preset", {"preset": "rest", "area_id": "living_room"})
    assert (auto(hass), scenes(hass)) == ([], {"light.living_room": "rest"})


async def test_coming_home_keeps_auto(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await action(hass, "set_auto", {"auto": True, "entity_id": "light.living_room"})
    with patch("custom_components.hue_lite.routines.is_up", return_value=False):
        hass.states.async_set("zone.home", "0")
        hass.states.async_set("zone.home", "1")
        await settle(hass)
    # Natural light is what the routine asks for, not a scene someone picked.
    assert [c.data["entity_id"] for c in home.turn_on] == [["light.living_room"]]
    assert (auto(hass), scenes(hass)) == (["light.living_room"], {"light.living_room": "natural"})


async def test_auto_is_kept_and_checked(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite, hass_read_only_user: MockUser
) -> None:
    await action(hass, "set_auto", {"auto": True, "entity_id": ["light.living_room", "light.garden"]})
    # Excluded lights can't be on Auto.
    assert auto(hass) == ["light.living_room"]
    # Like the other actions, it checks the caller's permissions.
    with pytest.raises(Unauthorized):
        await action(hass, "set_auto", {"auto": False, "entity_id": "light.living_room"}, Context(user_id=hass_read_only_user.id))
    # It's kept across a reload...
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert auto(hass) == ["light.living_room"]
    # ...and a new entity id.
    er.async_get(hass).async_update_entity("light.living_room", new_entity_id="light.lounge")
    await hass.async_block_till_done()
    assert auto(hass) == ["light.lounge"]
