"""The routines over time: evening, wake up, coming and going, Away, catching
up after a restart, and dimmers."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.core import HomeAssistant

from custom_components.hue_lite.const import ROUTINE
from custom_components.hue_lite.engine import HueLite
from custom_components.hue_lite.timeline import PRESETS

from .conftest import (
    FRIDAY,
    Home,
    action,
    add_device,
    add_light,
    after_dark,
    natural,
    on,
    ours,
    routine,
    set_up,
    settle,
    switch,
    tick,
)

COOL_BRIGHT_XY = list(PRESETS["cool_bright"].xy_color)
EVENING = "switch.hue_lite_evening_routine_sun_thu_nights"  # the tests' Thursday
WEEKENDS = "switch.hue_lite_evening_routine_fri_sat_nights"


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
    assert [c.data for c in home.turn_on] == [
        {"entity_id": ["light.bedroom", "light.living_room"], "brightness_pct": 7, "xy_color": COOL_BRIGHT_XY, "transition": 60}
    ]

    # Nightlight before bed: the Nightlight-labelled bedroom gets Nightlight.
    home.turn_on.clear()
    await tick(hass, freezer, "22:30")
    sent = {c.data["entity_id"][0]: c.data["brightness_pct"] for c in home.turn_on}
    assert sent == {"light.bedroom": PRESETS["nightlight"].brightness_pct, "light.living_room": PRESETS["rest"].brightness_pct}

    # Natural light takes the living room out of the routine, so bedtime fades
    # out only the bedroom, and the routine's done for the night.
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    await tick(hass, freezer, "23:00")
    assert [c.data for c in home.turn_off] == [{"entity_id": ["light.bedroom"], "transition": 15 * 60}]
    assert routine(hass) == []
    assert natural(hass) == ["light.living_room"]


async def test_the_evening_routine_fades_lights_that_are_on_into_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    await tick(hass, freezer, "16:29")
    hue_lite = await set_up(hass, entry)
    # The living room was set by hand; the bedroom's off.
    hass.states.async_set("light.living_room", "on", {"brightness": 200})

    # At 16:30 the routine takes both. The living room fades from how it is to
    # where the routine will be once it's brightened (Cool bright at 17:30),
    # over that hour, rather than dropping to its dim start; the bedroom
    # comes on dim.
    await tick(hass, freezer, "16:30")
    assert routine(hass) == ["light.bedroom", "light.living_room"]
    assert sorted((c.data["entity_id"], c.data["brightness_pct"], c.data["transition"]) for c in home.turn_on) == [
        (["light.bedroom"], 5, 5),
        (["light.living_room"], PRESETS["cool_bright"].brightness_pct, 3600),
    ]

    # Following the routine moves the bedroom, leaving the living room fading...
    on(hass, hue_lite, "light.bedroom")
    home.turn_on.clear()
    await tick(hass, freezer, "16:31")
    assert [c.data["entity_id"] for c in home.turn_on] == [["light.bedroom"]]

    # ...until it's got there, when it follows the routine too.
    home.turn_on.clear()
    await tick(hass, freezer, "21:30")
    assert [c.data["entity_id"] for c in home.turn_on] == [["light.bedroom", "light.living_room"]]


async def test_evening_routine_needs_someone_home(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    await tick(hass, freezer, "16:29")
    await set_up(hass, entry)
    hass.states.async_set("zone.home", "0")
    await tick(hass, freezer, "16:30")
    assert routine(hass) == []
    assert home.turn_on == []


async def test_switching_the_evening_routine_off_and_on(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    await tick(hass, freezer, "16:30")
    await set_up(hass, entry)
    await switch(hass, EVENING, on=False)
    # Switched on during the evening: it starts straight away.
    await switch(hass, EVENING)
    await settle(hass)
    assert routine(hass) == ["light.bedroom", "light.living_room"]

    # Switched off: it lets its lights go, as they are, and bedtime leaves them on.
    home.turn_on.clear()
    await switch(hass, EVENING, on=False)
    assert routine(hass) == []
    await tick(hass, freezer, "23:00")
    assert home.turn_on == home.turn_off == []


async def test_switching_the_evening_routine_on_again_changes_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """An automation switching it on when it's on already doesn't take back
    the lights moved out of it."""
    await tick(hass, freezer, "16:29")
    await set_up(hass, entry)
    await tick(hass, freezer, "16:30")
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    home.turn_on.clear()
    await switch(hass, EVENING)
    await settle(hass)
    assert routine(hass) == ["light.bedroom"]
    assert home.turn_on == []


async def test_a_later_start_ends_the_evening_until_then(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Set during the evening, a start that's still to come ends it, as
    bedtime does (a bedtime after 05:00 ends it in the day the same way), and
    it starts again then."""
    await tick(hass, freezer, "16:29")
    hue_lite = await set_up(hass, entry)
    await tick(hass, freezer, "16:30")
    on(hass, hue_lite, "light.bedroom", "light.living_room")
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.hue_lite_evening_start_before_sunset", "value": 30}, blocking=True
    )
    await settle(hass)
    assert routine(hass) == []
    assert [c.data for c in home.turn_off] == [{"entity_id": ["light.bedroom", "light.living_room"], "transition": 15 * 60}]
    await tick(hass, freezer, "17:30")
    assert routine(hass) == ["light.bedroom", "light.living_room"]


async def test_weeknights_and_weekends(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    def summary() -> str:
        return hass.states.get("sensor.hue_lite_evening_routine").attributes["summary"]

    # Fri & Sat nights off: Thursday's evening runs...
    await tick(hass, freezer, "16:29")
    await set_up(hass, entry)
    await switch(hass, WEEKENDS, on=False)
    await tick(hass, freezer, "16:30")
    assert routine(hass) == ["light.bedroom", "light.living_room"]
    assert summary() == "On 16:30 · off 23:00"

    # ...and Friday's doesn't.
    await tick(hass, freezer, "23:00")
    await tick(hass, freezer, f"{FRIDAY} 12:00:00+01:00")
    await tick(hass, freezer, f"{FRIDAY} 16:30:00+01:00")
    assert routine(hass) == []
    assert summary() == "Off tonight"

    # Sun–Thu's switch doesn't change Friday night; its own does.
    await switch(hass, EVENING, on=False)
    await switch(hass, EVENING)
    await settle(hass)
    assert routine(hass) == []
    await switch(hass, WEEKENDS)
    await settle(hass)
    assert routine(hass) == ["light.bedroom", "light.living_room"]
    assert summary() == "On 16:30 · off 00:00"


async def test_wake_up(hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry) -> None:
    await tick(hass, freezer, "06:00")
    hue_lite = await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_wake_up_on_weekdays")
    await switch(hass, "switch.hue_lite_wake_up_lights_turn_off")
    # The bedroom was left in Natural light (and switched off) last night.
    await action(hass, "natural_light", {"entity_id": "light.bedroom"})
    home.turn_on.clear()

    # Wake up at 07:00 with a 30-minute fade: a sunrise from 06:30, from
    # Nightlight's red through Rest and Relax to Energize.
    await tick(hass, freezer, "06:29")
    assert home.turn_on == []
    await tick(hass, freezer, "06:30")
    assert [c.data for c in home.turn_on] == [
        {"entity_id": ["light.bedroom"], **PRESETS["nightlight"].colour_data(), "transition": 0},
        {"entity_id": ["light.bedroom"], **PRESETS["rest"].colour_data(), "transition": 12 * 60},
        {"entity_id": ["light.bedroom"], **PRESETS["relax"].colour_data(), "transition": 9 * 60},
        {"entity_id": ["light.bedroom"], **PRESETS["energize"].colour_data(), "transition": 9 * 60},
    ]
    # Out of Natural light, so it doesn't move the bedroom during the fade.
    assert natural(hass) == []

    # And off an hour after the wake-up time.
    on(hass, hue_lite, "light.bedroom")
    await tick(hass, freezer, "08:00")
    assert [c.data for c in home.turn_off] == [{"entity_id": ["light.bedroom"], "transition": 60}]


async def restart(hass: HomeAssistant, freezer: FrozenDateTimeFactory, entry: MockConfigEntry, when: str) -> HueLite:
    """Home Assistant's down (Hue-Lite unloaded, so no ticks) until `when`
    (HH:MM on Thursday), then Hue-Lite catches up."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(f"2026-10-22 {when}:00+01:00")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return entry.runtime_data


async def test_a_wake_up_missed_during_a_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    await tick(hass, freezer, "06:00")
    await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_wake_up_on_weekdays")

    # Down over the 06:30 start of a 07:00 wake-up: back at 06:40, the
    # sunrise starts then, faster, still reaching Energize at 07:00.
    await restart(hass, freezer, entry, "06:40")
    assert [c.data["transition"] for c in home.turn_on] == [0, 8 * 60, 6 * 60, 6 * 60]

    # Started, it isn't started again by another restart.
    home.turn_on.clear()
    await restart(hass, freezer, entry, "06:42")
    assert home.turn_on == []
    assert hass.states.get("sensor.hue_lite_light_scenes").attributes["scenes"] == {"light.bedroom": "wake_up"}

    # Down over its end at 07:00, it lets go of the bedroom once back.
    await restart(hass, freezer, entry, "09:00")
    assert (hass.states.get("sensor.hue_lite_light_scenes").attributes["scenes"], home.turn_off) == ({}, [])


async def test_an_evening_missed_during_a_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    # Down from 16:20 over the evening's 16:30 start: back at 16:40, it starts then.
    await tick(hass, freezer, "16:20")
    await set_up(hass, entry)
    hue_lite = await restart(hass, freezer, entry, "16:40")
    assert routine(hass) == ["light.bedroom", "light.living_room"]

    # Down over the 23:00 bedtime until 23:05: back, its lights fade out.
    on(hass, hue_lite, "light.bedroom", "light.living_room")
    await restart(hass, freezer, entry, "23:05")
    assert [c.data for c in home.turn_off] == [{"entity_id": ["light.bedroom", "light.living_room"], "transition": 15 * 60}]
    assert routine(hass) == []


async def test_an_evening_long_missed_during_a_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    # Back more than 15 minutes after the evening's 16:30 start, it stays
    # missed (the lights may well be on by hand by then)...
    await tick(hass, freezer, "16:20")
    await set_up(hass, entry)
    hue_lite = await restart(hass, freezer, entry, "16:50")
    assert routine(hass) == []

    # ...and back as long after bedtime, the routine's lights are left as they are.
    hue_lite.set_scene(["light.bedroom"], ROUTINE)
    await restart(hass, freezer, entry, "23:30")
    assert (routine(hass), home.turn_off) == ([], [])


async def test_away_across_a_restart(hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry) -> None:
    # Away switched the bedroom on while nobody's home...
    await tick(hass, freezer, "16:00")
    hue_lite = await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_mimic_presence")
    hass.states.async_set("zone.home", "0")
    with patch("custom_components.hue_lite.routines.random") as chance:
        chance.randint.return_value = 10
        chance.choice.side_effect = lambda lights: lights[0]
        await tick(hass, freezer, "16:30")
    on(hass, hue_lite, "light.bedroom")

    # ...then Home Assistant was down over bedtime: back, it turns it off.
    await restart(hass, freezer, entry, "23:40")
    assert [c.data for c in home.turn_off] == [{"entity_id": ["light.bedroom"], "transition": 60}]


async def test_dimming_during_a_wake_up_stops_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    await tick(hass, freezer, "06:00")
    hue_lite = await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_wake_up_on_weekdays")
    home.turn_on.clear()

    # Dimmed just after its sunrise starts (at its first pause, once its
    # Nightlight's been sent: patching asyncio.sleep reaches every pause), the
    # bedroom stays at that level.
    async def dim(_seconds: float) -> None:
        if len(home.turn_on) == 1:
            await action(hass, "set_brightness", {"entity_id": "light.bedroom", "brightness_pct": 10})

    with patch("custom_components.hue_lite.routines.asyncio.sleep", side_effect=dim):
        await tick(hass, freezer, "06:30")
    assert [c.data["transition"] for c in home.turn_on] == [0, 0.4]
    assert hue_lite.scenes == {}


async def test_wake_up_leaves_lights_youve_changed_on(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Turning the wake-up lights off afterwards skips ones put into a scene since."""
    await tick(hass, freezer, "06:00")
    hue_lite = await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_wake_up_on_weekdays")
    await switch(hass, "switch.hue_lite_wake_up_lights_turn_off")
    await tick(hass, freezer, "06:30")
    on(hass, hue_lite, "light.bedroom")
    await tick(hass, freezer, "07:10")
    await action(hass, "natural_light", {"entity_id": "light.bedroom"})
    await tick(hass, freezer, "08:00")
    assert home.turn_off == []


async def test_wake_up_skips_when_off_or_already_on(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    await tick(hass, freezer, "06:00")
    await set_up(hass, entry)
    await tick(hass, freezer, "06:30")  # weekdays switched off (the default)
    await switch(hass, "switch.hue_lite_wake_up_on_weekdays")
    hass.states.async_set("light.bedroom", "on")
    await tick(hass, freezer, f"{FRIDAY} 06:30:00+01:00")  # Friday, but the bedroom's on
    assert home.turn_on == []


async def test_wake_up_leaves_lights_that_are_on_alone(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """A Wake up light that's on already is in use; the rest still fade in."""
    hall = add_light(hass, "hall", None, {home.labels["wake_up"]})
    await tick(hass, freezer, "06:00")
    await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_wake_up_on_weekdays")
    hass.states.async_set(hall, "on")
    await tick(hass, freezer, "06:30")
    assert {tuple(c.data["entity_id"]) for c in home.turn_on} == {("light.bedroom",)}


async def test_wake_up_just_after_midnight(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Saturday's 00:15 wake-up fades in from 23:45 on Friday, using Saturday's
    time and toggle (Friday's weekday toggle is off)."""
    await tick(hass, freezer, f"{FRIDAY} 22:00:00+01:00")
    hue_lite = await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_wake_up_on_weekends")
    await switch(hass, "switch.hue_lite_wake_up_lights_turn_off")
    await hass.services.async_call("time", "set_value", {"entity_id": "time.hue_lite_wake_up_sat_sun", "time": "00:15"}, blocking=True)

    await tick(hass, freezer, f"{FRIDAY} 23:45:00+01:00")
    assert len(home.turn_on) == 4
    on(hass, hue_lite, "light.bedroom")
    await tick(hass, freezer, "2026-10-24 01:15:00+01:00")
    assert [c.data for c in home.turn_off] == [{"entity_id": ["light.bedroom"], "transition": 60}]


async def test_wake_up_when_the_clocks_go_forward(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """On Sunday 28 March 2027 the clocks jump from 01:00 to 02:00, so a 01:40
    wake-up (fading in from 01:10) never shows on the clock; it still happens."""
    freezer.move_to("2027-03-28 00:59:00+00:00")
    await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_wake_up_on_weekends")
    await hass.services.async_call("time", "set_value", {"entity_id": "time.hue_lite_wake_up_sat_sun", "time": "01:40"}, blocking=True)
    await tick(hass, freezer, "2027-03-28 01:09:00+00:00")  # 02:09 BST
    assert home.turn_on == []
    await tick(hass, freezer, "2027-03-28 01:10:00+00:00")  # 02:10 BST
    assert len(home.turn_on) == 4


async def test_coming_and_going(hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, hue_lite: HueLite) -> None:
    await switch(hass, "switch.hue_lite_lights_off_when_everyone_leaves")
    hass.states.async_set("light.living_room", "on")
    hass.states.async_set("light.garden", "on")  # excluded

    # Out for less than two minutes: nothing happens.
    hass.states.async_set("zone.home", "0")
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    hass.states.async_set("zone.home", "1")
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await settle(hass)
    assert home.turn_off == []

    # Gone for two minutes: every light that's on goes off.
    hass.states.async_set("zone.home", "0")
    freezer.tick(timedelta(seconds=121))
    async_fire_time_changed(hass)
    await settle(hass)
    assert [c.data for c in home.turn_off] == [{"entity_id": ["light.living_room"], "transition": 2}]

    # Coming home, only after dark (the default): not in daylight...
    hass.states.async_set("light.living_room", "off")
    with after_dark(False):
        hass.states.async_set("zone.home", "1")
        await settle(hass)
    assert home.turn_on == []

    with after_dark():
        # ...nor when the zone comes back from unknown (zones reloading)...
        hass.states.async_set("zone.home", "unknown")
        hass.states.async_set("zone.home", "1")
        await settle(hass)
        assert home.turn_on == []
        # ...but after dark the Coming home lights come on in Natural light.
        hass.states.async_set("zone.home", "0")
        hass.states.async_set("zone.home", "1")
        await settle(hass)
    assert natural(hass) == ["light.living_room"]
    assert [c.data["entity_id"] for c in home.turn_on] == [["light.living_room"]]


async def test_coming_home_during_the_evening(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Coming home lights the evening routine takes join it (so they fade out
    at bedtime), even if nobody was home when it started, and the rest get
    Natural light; in its Nightlight stretch before bed, they all get Natural
    light, so they don't go off again soon after."""
    office = add_light(hass, "office", "living_room", {home.labels["coming_home"]})  # not an Evening light
    await tick(hass, freezer, "16:29")
    hue_lite = await set_up(hass, entry)
    hass.states.async_set("zone.home", "0")
    await tick(hass, freezer, "16:30")  # nobody's home, so the routine takes nothing
    assert routine(hass) == []

    with after_dark():
        hass.states.async_set("zone.home", "1")
        await settle(hass)
        assert (routine(hass), natural(hass)) == (["light.living_room"], [office])
        assert [c.data for c in home.turn_on] == [
            {"entity_id": ["light.living_room"], **hue_lite.evening.light.colour_data(), "transition": 2},
            {"entity_id": [office], **hue_lite.natural.light.colour_data(), "transition": 2},
        ]

        # Both off again by half ten (bedtime's at eleven); someone else gets home.
        await tick(hass, freezer, "22:30")
        hass.states.async_set("zone.home", "2")
        await settle(hass)
    assert (routine(hass), natural(hass)) == ([], ["light.living_room", office])


async def test_first_one_home(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    """A First one home light comes on only when the house was empty, not for
    everyone who gets home after (a Coming home light does)."""
    bedside = add_light(hass, "bedside", "bedroom", {home.labels["first_home"]})
    with after_dark():
        hass.states.async_set("zone.home", "0")
        hass.states.async_set("zone.home", "1")
        await settle(hass)
        assert natural(hass) == [bedside, "light.living_room"]

        # Both off again; someone else gets home.
        home.turn_on.clear()
        hass.states.async_set("zone.home", "2")
        await settle(hass)
    assert [c.data["entity_id"] for c in home.turn_on] == [["light.living_room"]]


async def test_away(hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry) -> None:
    """While nobody's home in the evening, an Evening light switches on or
    off every so often (here: the bedroom, every ten minutes). At bedtime
    its lights go off; when someone gets home it stops, leaving them."""
    await tick(hass, freezer, "16:00")
    hue_lite = await set_up(hass, entry)
    await switch(hass, "switch.hue_lite_mimic_presence")
    hass.states.async_set("zone.home", "0")
    with patch("custom_components.hue_lite.routines.random") as chance:
        chance.randint.return_value = 10
        chance.choice.side_effect = lambda lights: lights[0]

        # Not in the day; from the evening's start, the bedroom comes on at
        # the evening routine's light...
        await tick(hass, freezer, "16:01")
        assert home.turn_on == []
        await tick(hass, freezer, "16:30")
        assert [c.data for c in home.turn_on] == [
            {"entity_id": ["light.bedroom"], **hue_lite.evening.light.colour_data(), "transition": 2}
        ]
        on(hass, hue_lite, "light.bedroom")

        # ...and goes off ten minutes later, and on again ten after that.
        await tick(hass, freezer, "16:35")
        assert home.turn_off == []
        await tick(hass, freezer, "16:40")
        assert [c.data for c in home.turn_off] == [{"entity_id": ["light.bedroom"], "transition": 2}]
        hass.states.async_set("light.bedroom", "off", context=ours(hue_lite))
        await tick(hass, freezer, "16:50")
        on(hass, hue_lite, "light.bedroom")

        # At bedtime, it's turned off.
        home.turn_off.clear()
        await tick(hass, freezer, "23:00")
        assert [c.data for c in home.turn_off] == [{"entity_id": ["light.bedroom"], "transition": 60}]
        hass.states.async_set("light.bedroom", "off", context=ours(hue_lite))

        # Friday: on at the evening's start, then someone gets home, so it
        # stops and the bedroom stays on, at bedtime too.
        await tick(hass, freezer, f"{FRIDAY} 12:00:00+01:00")
        await tick(hass, freezer, f"{FRIDAY} 16:30:00+01:00")
        on(hass, hue_lite, "light.bedroom")
        with after_dark(False):
            hass.states.async_set("zone.home", "1")
            await settle(hass)
        home.turn_on.clear()
        home.turn_off.clear()
        await tick(hass, freezer, f"{FRIDAY} 16:40:00+01:00")
        await tick(hass, freezer, "2026-10-24 00:00:00+01:00")
    assert home.turn_on == home.turn_off == []


async def test_quick_dimmer_presses(hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, hue_lite: HueLite) -> None:
    """Pressed again before the bulbs say they're on, On steps on rather than
    starting again; after Off, or a while later, it starts again."""
    dimmer = add_device(hass, "dimmer", model="RWL021", area="living_room")

    async def press(command: str) -> str:
        hass.bus.async_fire("zha_event", {"device_id": dimmer.id, "command": command})
        await settle(hass)
        return hue_lite.scenes.get("light.living_room", "")

    assert [await press("on_short_release"), await press("on_short_release")] == ["natural", "energize"]
    await press("off_short_release")
    assert await press("on_short_release") == "natural"
    freezer.tick(timedelta(seconds=5))
    assert await press("on_short_release") == "natural"


async def test_hue_dimmer(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    dimmer = add_device(hass, "dimmer", model="RWL021", area="living_room")
    remote = add_device(hass, "remote", model="TRADFRI", area="living_room")

    async def press(command: str, device_id: str = dimmer.id) -> None:
        hass.bus.async_fire("zha_event", {"device_id": device_id, "command": command})
        await settle(hass)

    # Up does nothing while the room is off, as on Hue.
    await press("up_press")
    assert home.turn_on == []

    # On: the room's lights go into Natural light.
    await press("on_short_release")
    assert natural(hass) == ["light.living_room"]
    assert home.turn_on[-1].context.parent_id is not None  # linked to the press

    # Down dims by 10% (from 78%), keeping the room in Natural light.
    hass.states.async_set("light.living_room", "on", {"brightness": 200}, context=ours(hue_lite))
    await press("down_press")
    assert home.turn_on[-1].data == {"entity_id": ["light.living_room"], "brightness_pct": 68, "transition": 0.4}
    assert natural(hass) == ["light.living_room"]

    # Down stops at about 10%.
    calls = len(home.turn_on)
    hass.states.async_set("light.living_room", "on", {"brightness": 20})
    await press("down_hold")
    assert len(home.turn_on) == calls

    # Off turns the room off, and takes it off Auto.
    await action(hass, "set_auto", {"auto": True, "entity_id": "light.living_room"})
    await press("off_short_release")
    assert home.turn_off[-1].data == {"entity_id": ["light.living_room"], "transition": 1}
    assert hue_lite.on_auto() == []

    # Other remotes are left to their own automations.
    await press("on_short_release", remote.id)
    assert len(home.turn_on) == calls
