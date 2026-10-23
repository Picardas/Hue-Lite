"""Which lights an action, routine or dimmer uses, and which it leaves alone."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser, async_fire_time_changed

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import area_registry as ar, entity_registry as er, label_registry as lr

from custom_components.hue_lite.engine import HueLite

from .conftest import Home, action, add_device, add_light, set_up, settle, switch, tick

EXCLUDED = "light.garden"


async def test_choosing_lights(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    # Every light: not the excluded, hidden or group ones.
    assert hue_lite.lights() == ["light.bedroom", "light.living_room"]
    assert hue_lite.lights({"area_id": ["bedroom"]}) == ["light.bedroom"]
    assert hue_lite.lights({"label_id": [home.labels["evening"]]}) == ["light.bedroom", "light.living_room"]
    # Named lights are used as given, unless they're unavailable or missing.
    assert hue_lite.lights({"entity_id": ["light.garden", "light.nope"]}) == ["light.garden"]
    hass.states.async_set("light.bedroom", "unavailable")
    assert hue_lite.lights() == ["light.living_room"]


async def test_labels_on_devices_and_areas_count(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    """As in Home Assistant's own targets: a label on a light's device or
    area counts, for the routines' labels and for Exclude from lighting."""
    lamps = lr.async_get(hass).async_create("Lamps").label_id
    add_light(hass, "lamp", device_id=add_device(hass, "lamp", labels={lamps}).id)
    assert hue_lite.lights({"label_id": [lamps]}) == ["light.lamp"]

    ar.async_get(hass).async_update("bedroom", labels={home.labels["exclude"]})
    assert hue_lite.lights() == ["light.lamp", "light.living_room"]


async def test_excluded_light_is_never_touched(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """An excluded light in the living room with every role label: nothing
    Hue-Lite does reaches it."""
    er.async_get(hass).async_update_entity(EXCLUDED, area_id="living_room", labels=set(home.labels.values()))
    await tick(hass, freezer, "06:00")
    hue_lite = await set_up(hass, entry)
    for name in ("wake_up_on_weekdays", "lights_off_when_everyone_leaves"):
        await switch(hass, f"switch.hue_lite_{name}")

    await action(hass, "apply_preset", {"preset": "relax", "area_id": "living_room"})
    await action(hass, "natural_light", {"label_id": home.labels["evening"]})
    await action(hass, "next_scene")
    await tick(hass, freezer, "06:30")  # wake up
    await tick(hass, freezer, "16:29")
    await tick(hass, freezer, "16:30")  # the evening routine
    await tick(hass, freezer, "23:00")  # bedtime
    dimmer = add_device(hass, "dimmer", model="RWL021", area="living_room")
    hass.states.async_set("light.living_room", "on")
    for command in ("on_short_release", "up_press", "off_short_release"):
        hass.bus.async_fire("zha_event", {"device_id": dimmer.id, "command": command})
    await settle(hass)
    # Everyone leaving, then coming home.
    hass.states.async_set(EXCLUDED, "on")
    hass.states.async_set("zone.home", "0")
    freezer.tick(130)
    async_fire_time_changed(hass)
    await settle(hass)
    hass.states.async_set(EXCLUDED, "off")
    hass.states.async_set("zone.home", "1")
    await settle(hass)

    assert home.turn_on and home.turn_off  # the other lights were driven
    assert EXCLUDED not in home.touched()
    assert EXCLUDED not in hue_lite.scenes


async def test_light_excluded_while_in_a_scene_is_let_go(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    registry = er.async_get(hass)
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    registry.async_update_entity("light.living_room", labels={home.labels["exclude"]})
    await hass.async_block_till_done()
    assert hue_lite.scenes == {}

    # Switched off and on again, it isn't put back into Natural light.
    home.turn_on.clear()
    hass.states.async_set("light.living_room", "off")
    hass.states.async_set("light.living_room", "on")
    await settle(hass)
    assert home.turn_on == []

    # Excluding its area lets a light go too...
    await action(hass, "apply_preset", {"preset": "rest", "entity_id": "light.bedroom"})
    ar.async_get(hass).async_update("bedroom", labels={home.labels["exclude"]})
    await hass.async_block_till_done()
    assert hue_lite.scenes == {}

    # ...and so does hiding it.
    desk = add_light(hass, "desk")
    await action(hass, "natural_light", {"entity_id": desk})
    registry.async_update_entity(desk, hidden_by=er.RegistryEntryHider.USER)
    await hass.async_block_till_done()
    assert hue_lite.scenes == {}


async def test_excluded_light_named_directly_is_set_but_not_kept(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await action(hass, "natural_light", {"entity_id": EXCLUDED})
    assert home.touched() == {EXCLUDED}
    assert hue_lite.scenes == {}


async def test_renamed_light_keeps_its_scene(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    registry = er.async_get(hass)
    registry.async_update_entity("light.living_room", new_entity_id="light.lounge")
    await hass.async_block_till_done()
    assert hue_lite.scenes == {"light.lounge": "natural"}

    # Watched under its new id: changing it by hand takes it out.
    hass.states.async_set("light.lounge", "on")
    hass.states.async_set("light.lounge", "on", {"brightness": 10}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    assert hue_lite.scenes == {}

    # Deleted lights are forgotten.
    await action(hass, "apply_preset", {"preset": "rest", "entity_id": "light.lounge"})
    registry.async_remove("light.lounge")
    await hass.async_block_till_done()
    assert hue_lite.scenes == {}


async def test_actions_respect_the_callers_permissions(
    hass: HomeAssistant, home: Home, hue_lite: HueLite, hass_read_only_user: MockUser
) -> None:
    """Hue-Lite changes lights as itself, which Home Assistant doesn't check,
    so its actions check the caller as light.turn_on would."""
    as_reader = Context(user_id=hass_read_only_user.id)
    with pytest.raises(Unauthorized):
        await action(hass, "apply_preset", {"preset": "relax", "area_id": "living_room"}, as_reader)
    # With no target, only the lights they may control: none.
    await action(hass, "natural_light", context=as_reader)
    assert home.turn_on == []
    assert hue_lite.scenes == {}
