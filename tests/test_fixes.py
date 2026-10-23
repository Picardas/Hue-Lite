"""Edge cases: follow throttling, wake-up across midnight, the home zone coming
back from unknown, leaving Natural light, renamed lights and real sun times."""

from __future__ import annotations

from datetime import date

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.hue_lite.const import DOMAIN
from custom_components.hue_lite.engine import HueLite

from .conftest import Home
from .test_routines import natural, on, set_up, switch_on, tick

# The real one, before the fixed_sun fixture patches it.
REAL_SUN_TIMES = HueLite.__dict__["_sun_times"]


async def test_natural_light_moves_in_visible_steps(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    # The morning blend (Energize -> Cool bright, 07:30 -> 12:00) changes too
    # little in a minute to see.
    await tick(hass, freezer, "08:00")
    hue_lite = await set_up(hass, entry)
    await hass.services.async_call(DOMAIN, "natural_light", {"entity_id": "light.living_room"}, blocking=True)
    on(hass, hue_lite, "light.living_room")
    home.turn_on.clear()

    await tick(hass, freezer, "08:01")
    assert home.turn_on == []
    await tick(hass, freezer, "08:10")
    assert [call.data["transition"] for call in home.turn_on] == [60]
    assert home.turn_on[0].data["xy_color"] == list(hue_lite.natural.light.xy_color)

    # A new phase is always sent, however small the change.
    home.turn_on.clear()
    await tick(hass, freezer, "11:59")
    home.turn_on.clear()
    await tick(hass, freezer, "12:00")
    assert hue_lite.natural.phase == "day"
    assert len(home.turn_on) == 1


async def test_wake_up_just_after_midnight(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Saturday's 00:15 wake-up fades in from 23:45 on Friday, using Saturday's
    time and toggle; Friday's (weekday) toggle is off."""
    await tick(hass, freezer, "2026-10-23 22:00:00+01:00")
    hue_lite = await set_up(hass, entry)
    await switch_on(hass, "switch.hue_lite_wake_up_on_weekends")
    await switch_on(hass, "switch.hue_lite_wake_up_lights_turn_off")
    await hass.services.async_call(
        "time", "set_value", {"entity_id": "time.hue_lite_wake_up_sat_sun", "time": "00:15"}, blocking=True
    )
    home.turn_on.clear()

    await tick(hass, freezer, "2026-10-23 23:45:00+01:00")
    assert [call.data["entity_id"] for call in home.turn_on] == [["light.bedroom"], ["light.bedroom"]]

    on(hass, hue_lite, "light.bedroom")
    await tick(hass, freezer, "2026-10-24 01:15:00+01:00")
    assert [call.data for call in home.turn_off] == [{"entity_id": ["light.bedroom"], "transition": 60}]


async def test_home_zone_back_from_unknown_is_not_an_arrival(
    hass: HomeAssistant, home: Home, hue_lite: HueLite
) -> None:
    hass.states.async_set("zone.home", "unknown")
    hass.states.async_set("zone.home", "1")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert home.turn_on == []


async def test_natural_light_follow_off_leaves_other_scenes(
    hass: HomeAssistant, home: Home, hue_lite: HueLite
) -> None:
    await hass.services.async_call(DOMAIN, "natural_light", {"entity_id": "light.living_room"}, blocking=True)
    await hass.services.async_call(DOMAIN, "apply_preset", {"preset": "relax", "entity_id": "light.bedroom"}, blocking=True)

    await hass.services.async_call(DOMAIN, "natural_light", {"follow": False}, blocking=True)
    assert hue_lite.scenes == {"light.bedroom": "relax"}


async def test_renamed_light_keeps_its_scene(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await hass.services.async_call(DOMAIN, "natural_light", {"entity_id": "light.living_room"}, blocking=True)
    registry = er.async_get(hass)

    registry.async_update_entity("light.living_room", new_entity_id="light.lounge")
    await hass.async_block_till_done()
    assert hue_lite.scenes == {"light.lounge": "natural"}

    # And it's watched under its new id: changing it by hand takes it out.
    hass.states.async_set("light.lounge", "on")
    hass.states.async_set("light.lounge", "on", {"brightness": 10}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    assert natural(hass) == []

    await hass.services.async_call(DOMAIN, "apply_preset", {"preset": "rest", "entity_id": "light.lounge"}, blocking=True)
    registry.async_remove("light.lounge")
    await hass.async_block_till_done()
    assert hue_lite.scenes == {}


async def test_real_sun_times(hass: HomeAssistant, hue_lite: HueLite) -> None:
    """Without the fixed times the tests use: London on midsummer's day."""
    hass.config.latitude, hass.config.longitude = 51.5, -0.13
    sun = REAL_SUN_TIMES(hue_lite, date(2026, 6, 21))
    assert sun.dawn < sun.sunrise < sun.noon < sun.sunset < sun.dusk
    assert 4 * 60 <= sun.sunrise <= 5 * 60  # about 04:43 BST
    assert 21 * 60 <= sun.sunset <= 21 * 60 + 30  # about 21:21 BST
