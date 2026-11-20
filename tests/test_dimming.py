"""Dimming lights in their scenes: each keeps its own light (a colour scene's
colour, Nightlight at night), only a level kept in a scene is saved, and a
room's worth of lights is worked out at once rather than light by light."""

from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from custom_components.hue_lite.const import ROUTINE
from custom_components.hue_lite.engine import HueLite
from custom_components.hue_lite.routines import Routines
from custom_components.hue_lite.timeline import PALETTES, PRESETS

from .conftest import FRIDAY, Home, action, add_light, on, ours, set_up, settle, switch, tick


def sent(home: Home) -> dict[str, dict]:
    """The last command each light was sent."""
    return {c.data["entity_id"][0]: c.data for c in home.turn_on}


async def test_lights_in_a_colour_scene_come_on_in_their_own_colours(
    hass: HomeAssistant, home: Home, hue_lite: HueLite
) -> None:
    # Three colour bulbs and a white one in Golden hour, all switched off.
    lamps = [add_light(hass, f"lamp_{n}", "living_room") for n in range(3)]
    white = add_light(hass, "white", "living_room")
    hass.states.async_set(white, "off", {"supported_color_modes": ["color_temp"]})
    await action(hass, "apply_preset", {"preset": "golden_hour", "entity_id": [*lamps, white]})

    # Dimmed to half, each colour bulb comes on in its own colour (in order
    # round them), the white one in the scene's white, all at 50%; they stay
    # in the scene.
    home.turn_on.clear()
    await action(hass, "set_brightness", {"entity_id": [*lamps, white], "brightness_pct": 50})
    palette = PALETTES["golden_hour"]
    step = hue_lite._drift_step(palette)
    for i, lamp in enumerate(sorted(lamps)):
        assert sent(home)[lamp]["xy_color"] == list(palette.colour(i + step).xy_color)
        assert sent(home)[lamp]["brightness_pct"] == 50
    assert sent(home)[white] == {"entity_id": [white], "brightness_pct": 50, "color_temp_kelvin": palette.white.kelvin, "transition": 0.4}
    assert {hue_lite.scenes[light] for light in [*lamps, white]} == {"golden_hour"}


async def test_nightlight_lights_come_on_in_nightlight_at_night(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, hue_lite: HueLite
) -> None:
    # At night, both lights are in Natural light and switched off.
    await tick(hass, freezer, "23:30")
    await action(hass, "natural_light", {"entity_id": ["light.bedroom", "light.living_room"]})
    for light in ("light.bedroom", "light.living_room"):
        hass.states.async_set(light, "off")
    await settle(hass)

    # Dimmed, the bedroom (labelled Nightlight) comes on in Nightlight and
    # the living room in Natural light's night, both at that level.
    home.turn_on.clear()
    await action(hass, "set_brightness", {"entity_id": ["light.bedroom", "light.living_room"], "brightness_pct": 20})
    assert sent(home)["light.bedroom"]["xy_color"] == list(PRESETS["nightlight"].xy_color)
    assert sent(home)["light.living_room"]["xy_color"] == list(hue_lite.natural.light.xy_color)
    assert {data["brightness_pct"] for data in sent(home).values()} == {20}


async def test_only_a_level_kept_in_a_scene_is_saved(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    lamp = add_light(hass, "lamp", "living_room")
    hass.states.async_set(lamp, "on", {"brightness": 100})

    # A light in no scene just gets the brightness: there's nothing to save.
    with patch.object(Store, "async_delay_save", autospec=True) as save:
        await action(hass, "set_brightness", {"entity_id": lamp, "brightness_pct": 40})
    assert save.call_count == 0

    # In Natural light it keeps that level, which is saved.
    await action(hass, "natural_light", {"entity_id": lamp})
    with patch.object(Store, "async_delay_save", autospec=True) as save:
        await action(hass, "set_brightness", {"entity_id": lamp, "brightness_pct": 40})
    assert save.call_count == 1
    assert lamp in hue_lite.dimmed


async def test_dimming_many_lights_works_out_each_scene_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, hue_lite: HueLite
) -> None:
    """A big room's lights are worked out together: the Nightlight label is
    looked up once, and each colour scene's order round its bulbs once,
    however many lights there are."""
    await tick(hass, freezer, "23:30")
    natural = [add_light(hass, f"natural_{n}", "living_room") for n in range(6)]
    ember = [add_light(hass, f"ember_{n}", "living_room") for n in range(6)]
    nebula = [add_light(hass, f"nebula_{n}", "bedroom") for n in range(6)]
    await action(hass, "natural_light", {"entity_id": natural})
    await action(hass, "apply_preset", {"preset": "ember", "entity_id": ember})
    await action(hass, "apply_preset", {"preset": "nebula", "entity_id": nebula})
    for light in [*natural, *ember, *nebula]:
        hass.states.async_set(light, "off")
    await settle(hass)

    with (
        patch.object(HueLite, "labelled", autospec=True, side_effect=HueLite.labelled) as labelled,
        patch.object(HueLite, "_palette_order", autospec=True, side_effect=HueLite._palette_order) as palette_order,
    ):
        await action(hass, "set_brightness", {"entity_id": [*natural, *ember, *nebula], "brightness_pct": 30})
    assert [c.args[1] for c in labelled.call_args_list].count("nightlight") <= 1
    assert sorted(c.args[1] for c in palette_order.call_args_list) == ["ember", "nebula"]


async def test_dimming_as_the_evening_brightens_keeps_the_wind_down(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Set while the routine's still dim and brightening, a light stays at
    that level until it's brightened, then follows it at that share of its
    full light: winding down later, it goes down too, rather than staying
    at full brightness."""
    await tick(hass, freezer, "16:29")
    hue_lite = await set_up(hass, entry)
    # At 16:30 the routine takes both lights, dim (5%), brightening to Cool
    # bright (100%) by 17:30.
    await tick(hass, freezer, "16:30")
    on(hass, hue_lite, "light.living_room", "light.bedroom")
    await tick(hass, freezer, "16:31")

    # Too dim: the living room's slider takes it to 30%.
    await action(hass, "set_brightness", {"entity_id": "light.living_room", "brightness_pct": 30})
    assert sent(home)["light.living_room"]["brightness_pct"] == 30

    # While the routine brightens, it's left at 30%; the bedroom follows.
    home.turn_on.clear()
    await tick(hass, freezer, "16:45")
    assert set(sent(home)) == {"light.bedroom"}

    # Brightened, it's at 30% of Cool bright, and in the Nightlight stretch
    # at 30% of Rest.
    await tick(hass, freezer, "17:30")
    assert sent(home)["light.living_room"]["brightness_pct"] == 30
    await tick(hass, freezer, "22:30")
    assert sent(home)["light.living_room"]["brightness_pct"] == round(PRESETS["rest"].brightness_pct * 0.3)


async def test_brightening_a_nightlight_at_night_leaves_the_next_evening_alone(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Nightlight is 1%: a dimmer's Up at night is a share of the light
    Natural light gives other lights (Rest), not of that 1%, so the next
    evening the light isn't stuck at full brightness."""
    await tick(hass, freezer, "23:30")
    hue_lite = await set_up(hass, entry)
    for nights in ("switch.hue_lite_evening_routine_sun_thu_nights", "switch.hue_lite_evening_routine_fri_sat_nights"):
        await switch(hass, nights, on=False)
    # The bedroom (labelled Nightlight) is in Natural light at night: Nightlight.
    await action(hass, "natural_light", {"entity_id": "light.bedroom"})
    hass.states.async_set("light.bedroom", "on", {"brightness": 3}, context=ours(hue_lite))

    # Up: 1% to 11%.
    await hue_lite.async_set_brightness(["light.bedroom"], step_pct=10)
    assert sent(home)["light.bedroom"]["brightness_pct"] == 11

    # Friday evening, Natural light is between Relax and Rest: the bedroom
    # gets 11/35 of it, not 11 times it.
    home.turn_on.clear()
    await tick(hass, freezer, f"{FRIDAY} 19:00:00+01:00")
    level = 11 / PRESETS["rest"].brightness_pct
    assert sent(home)["light.bedroom"]["brightness_pct"] == round(hue_lite.natural.light.brightness_pct * level)


async def test_dimming_the_routine_as_it_brightens_works_it_out_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """However many of the routine's lights are dimmed together, where its
    brightening ends is worked out once, and the Nightlight label looked up
    at most once."""
    lamps = [add_light(hass, f"lamp_{n}", "living_room", {home.labels["evening"]}) for n in range(8)]
    await tick(hass, freezer, "16:29")
    hue_lite = await set_up(hass, entry)
    await tick(hass, freezer, "16:31")
    assert set(lamps) <= set(hue_lite.in_scene(ROUTINE))

    with (
        patch.object(Routines, "brightening_until", autospec=True, side_effect=Routines.brightening_until) as ramp,
        patch.object(HueLite, "labelled", autospec=True, side_effect=HueLite.labelled) as labelled,
    ):
        await action(hass, "set_brightness", {"entity_id": lamps, "brightness_pct": 30})
    assert ramp.call_count == 1
    assert [c.args[1] for c in labelled.call_args_list].count("nightlight") <= 1
    assert {hue_lite.dimmed[lamp] for lamp in lamps} == {0.3}
