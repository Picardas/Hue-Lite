"""Auto: a light that does what the routines ask, whether or not it has their labels."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized

from custom_components.hue_lite.engine import HueLite

from .conftest import Home, action, add_light, routine, set_up, settle, switch, tick


def auto(hass: HomeAssistant) -> list[str]:
    return hass.states.get("sensor.hue_lite_light_scenes").attributes["auto"]


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
    await tick(hass, freezer, "16:30")  # the routine takes the Evening-labelled lights
    await tick(hass, freezer, "17:00")
    hass.states.async_set(office, "on")
    home.turn_on.clear()

    # On Auto during the evening: a light that's on takes the routine's light at once...
    await action(hass, "set_auto", {"auto": True, "entity_id": [office, desk]})
    await settle(hass)
    assert routine(hass) == ["light.bedroom", desk, "light.living_room", office]
    assert [c.data["entity_id"] for c in home.turn_on] == [[office]]
    assert home.turn_on[0].data["brightness_pct"] == hue_lite.evening.light.brightness_pct

    # ...and one that's off picks it up when it's turned on.
    home.turn_on.clear()
    hass.states.async_set(desk, "on")
    await settle(hass)
    assert [c.data["entity_id"] for c in home.turn_on] == [[desk]]

    # Off Auto: the routine lets go of lights it only had through Auto, and
    # keeps labelled ones (Auto doesn't change what the labels do).
    await action(hass, "set_auto", {"auto": False, "entity_id": [office, "light.bedroom"]})
    assert routine(hass) == ["light.bedroom", desk, "light.living_room"]
    assert auto(hass) == [desk]


async def test_auto_is_kept_and_checked(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite, hass_read_only_user: MockUser
) -> None:
    await action(hass, "set_auto", {"auto": True, "entity_id": ["light.living_room", "light.garden"]})
    # Excluded lights can't be on Auto.
    assert auto(hass) == ["light.living_room"]
    # It's kept across a reload.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert auto(hass) == ["light.living_room"]
    # Like the other actions, it checks the caller's permissions.
    with pytest.raises(Unauthorized):
        await action(hass, "set_auto", {"auto": False, "entity_id": "light.living_room"}, Context(user_id=hass_read_only_user.id))
