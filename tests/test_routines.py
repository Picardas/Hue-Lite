"""The routines over time: evening, wake up, coming and going, and dimmers."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.hue_lite.const import DOMAIN
from custom_components.hue_lite.engine import HueLite
from custom_components.hue_lite.timeline import PRESETS

from .conftest import THURSDAY, Home

COOL_BRIGHT_XY = list(PRESETS["cool_bright"].xy_color)


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> HueLite:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, when: str) -> None:
    """Move the clock to `when` (HH:MM, or a full date and time) and let Hue-Lite act."""
    freezer.move_to(f"{THURSDAY} {when}:00+01:00" if len(when) == 5 else when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def on(hass: HomeAssistant, hue_lite: HueLite, *lights: str) -> None:
    """Lights reporting that Hue-Lite turned them on."""
    for light in lights:
        hass.states.async_set(light, "on", {}, context=Context(id=hue_lite._ours[-1]))


def routine(hass: HomeAssistant) -> list[str]:
    return hass.states.get("sensor.hue_lite_evening_routine").attributes["lights"]


def natural(hass: HomeAssistant) -> list[str]:
    return hass.states.get("sensor.hue_lite_natural_light").attributes["lights"]


async def switch_on(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)


async def test_evening_routine(hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry) -> None:
    # Sunset 18:00, so the evening starts at 16:30; bedtime 23:00.
    await tick(hass, freezer, "16:29")
    hue_lite = await set_up(hass, entry)
    assert hass.states.get("sensor.hue_lite_evening_routine").state == "day"

    # The routine takes the Evening lights and turns them on dim.
    await tick(hass, freezer, "16:30")
    assert routine(hass) == ["light.bedroom", "light.living_room"]
    assert home.turn_on[-1].data == {
        "entity_id": ["light.bedroom", "light.living_room"], "brightness_pct": 5, "xy_color": COOL_BRIGHT_XY, "transition": 5,
    }

    # Every minute it moves the ones that are on.
    on(hass, hue_lite, "light.living_room", "light.bedroom")
    home.turn_on.clear()
    await tick(hass, freezer, "16:31")
    assert [call.data for call in home.turn_on] == [
        {"entity_id": ["light.bedroom", "light.living_room"], "brightness_pct": 7, "xy_color": COOL_BRIGHT_XY, "transition": 60}
    ]

    # Nightlight before bed: the Nightlight-labelled bedroom gets Nightlight.
    home.turn_on.clear()
    await tick(hass, freezer, "22:30")
    sent = {call.data["entity_id"][0]: call.data["brightness_pct"] for call in home.turn_on}
    assert sent == {"light.bedroom": PRESETS["nightlight"].brightness_pct, "light.living_room": PRESETS["rest"].brightness_pct}

    # Natural light takes the living room out of the routine...
    await hass.services.async_call(DOMAIN, "natural_light", {"entity_id": "light.living_room"}, blocking=True)
    assert routine(hass) == ["light.bedroom"]

    # ...so bedtime fades out only the bedroom, and the routine's done for the night.
    await tick(hass, freezer, "23:00")
    assert [call.data for call in home.turn_off] == [{"entity_id": ["light.bedroom"], "transition": 15 * 60}]
    assert routine(hass) == []
    assert natural(hass) == ["light.living_room"]


async def test_evening_routine_needs_someone_home(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    await tick(hass, freezer, "16:29")
    await set_up(hass, entry)
    hass.states.async_set("zone.home", "0")
    await tick(hass, freezer, "16:30")
    assert routine(hass) == []
    assert home.turn_on == []


async def test_wake_up(hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry) -> None:
    await tick(hass, freezer, "06:00")
    hue_lite = await set_up(hass, entry)
    await switch_on(hass, "switch.hue_lite_wake_up_on_weekdays")
    await switch_on(hass, "switch.hue_lite_wake_up_lights_turn_off")
    # The bedroom was left in Natural light (and switched off) last night.
    await hass.services.async_call(DOMAIN, "natural_light", {"entity_id": "light.bedroom"}, blocking=True)
    home.turn_on.clear()

    # Wake up at 07:00 with a 30-minute fade: it starts at 06:30.
    await tick(hass, freezer, "06:29")
    assert home.turn_on == []
    await tick(hass, freezer, "06:30")
    assert [call.data for call in home.turn_on] == [
        {"entity_id": ["light.bedroom"], "brightness": 1, "color_temp_kelvin": 2200},
        {"entity_id": ["light.bedroom"], **PRESETS["energize"].colour_data(), "transition": 30 * 60},
    ]
    # Out of Natural light, so it doesn't move the bedroom during the fade.
    assert natural(hass) == []

    # And off an hour after the wake-up time.
    on(hass, hue_lite, "light.bedroom")
    await tick(hass, freezer, "08:00")
    assert [call.data for call in home.turn_off] == [{"entity_id": ["light.bedroom"], "transition": 60}]


async def test_wake_up_skips_when_off_or_already_on(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    await tick(hass, freezer, "06:00")
    await set_up(hass, entry)
    await tick(hass, freezer, "06:30")  # weekdays switched off (the default)
    await switch_on(hass, "switch.hue_lite_wake_up_on_weekdays")
    hass.states.async_set("light.bedroom", "on")
    await tick(hass, freezer, "2026-10-23 06:30:00+01:00")  # Friday, but the bedroom's on
    assert home.turn_on == []


async def test_coming_and_going(hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, hue_lite: HueLite) -> None:
    await switch_on(hass, "switch.hue_lite_lights_off_when_everyone_leaves")
    hass.states.async_set("light.living_room", "on")
    hass.states.async_set("light.garden", "on")  # excluded

    # Out for less than two minutes: nothing happens.
    hass.states.async_set("zone.home", "0")
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    hass.states.async_set("zone.home", "1")
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert home.turn_off == []

    # Gone for two minutes: every light that's on goes off.
    hass.states.async_set("zone.home", "0")
    freezer.tick(timedelta(seconds=121))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [call.data for call in home.turn_off] == [{"entity_id": ["light.living_room"], "transition": 2}]

    # Coming home, only when dark (the default): not in daylight...
    hass.states.async_set("light.living_room", "off")
    with patch("custom_components.hue_lite.engine.is_up", return_value=True):
        hass.states.async_set("zone.home", "1")
        await hass.async_block_till_done(wait_background_tasks=True)
    assert home.turn_on == []

    # ...but after dark the Coming home lights come on in Natural light.
    hass.states.async_set("zone.home", "0")
    with patch("custom_components.hue_lite.engine.is_up", return_value=False):
        hass.states.async_set("zone.home", "1")
        await hass.async_block_till_done(wait_background_tasks=True)
    assert natural(hass) == ["light.living_room"]
    assert [call.data["entity_id"] for call in home.turn_on] == [["light.living_room"]]


async def test_hue_dimmer(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    zha = MockConfigEntry(domain="zha")
    zha.add_to_hass(hass)
    devices = dr.async_get(hass)
    dimmer = devices.async_get_or_create(config_entry_id=zha.entry_id, identifiers={("zha", "dimmer")}, model="RWL021")
    devices.async_update_device(dimmer.id, area_id="living_room")
    remote = devices.async_get_or_create(config_entry_id=zha.entry_id, identifiers={("zha", "remote")}, model="TRADFRI")
    devices.async_update_device(remote.id, area_id="living_room")

    async def press(command: str, device: dr.DeviceEntry = dimmer) -> None:
        hass.bus.async_fire("zha_event", {"device_id": device.id, "command": command})
        await hass.async_block_till_done(wait_background_tasks=True)

    # On: the room's lights go into Natural light.
    await press("on_short_release")
    assert natural(hass) == ["light.living_room"]
    assert home.turn_on[-1].data["entity_id"] == ["light.living_room"]

    # Down dims and counts as a change by hand.
    hass.states.async_set("light.living_room", "on", {"brightness": 200}, context=Context(id=hue_lite._ours[-1]))
    await press("down_press")
    assert home.turn_on[-1].data == {"entity_id": ["light.living_room"], "brightness_step_pct": -10, "transition": 0.4}
    assert natural(hass) == []

    # Down stops at about 10%.
    calls = len(home.turn_on)
    hass.states.async_set("light.living_room", "on", {"brightness": 20})
    await press("down_hold")
    assert len(home.turn_on) == calls

    await press("off_short_release")
    assert home.turn_off[-1].data == {"entity_id": ["light.living_room"], "transition": 1}

    # Other remotes are left to their own automations.
    await press("on_short_release", remote)
    assert len(home.turn_on) == calls
