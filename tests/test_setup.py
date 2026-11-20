"""Entities, what's saved, the dashboard, sun times and Repairs."""

from datetime import date, timedelta
import json
from pathlib import Path
import re
from typing import Any
from unittest.mock import Mock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir, label_registry as lr
from homeassistant.util import dt as dt_util

from custom_components.hue_lite.const import DOMAIN, NUMBERS, STORAGE_KEY
from custom_components.hue_lite.dashboard import SCENE_COLOURS, websocket_config
from custom_components.hue_lite.engine import HueLite

from .conftest import FRIDAY, THURSDAY, Home, action, add_device, add_light, on, set_up, settle

# The real one, before the fixed_sun fixture patches it.
REAL_SUN_TIMES = HueLite.__dict__["_sun_times"]


def stored(hass_storage: dict[str, Any], settings: dict | None = None, scenes: dict | None = None, **data: Any) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {"settings": settings or {}, "scenes": scenes or {}, **data},
    }


async def test_entities(hass: HomeAssistant, hue_lite: HueLite) -> None:
    ids = hue_lite.entity_ids
    assert ids["natural_light"] == "sensor.hue_lite_natural_light"
    assert ids["light_scenes"] == "sensor.hue_lite_light_scenes"
    assert ids["evening_weeknights"] == "switch.hue_lite_evening_routine_sun_thu_nights"
    assert ids["evening_weekends"] == "switch.hue_lite_evening_routine_fri_sat_nights"
    assert ids["wake_up_weekday"] == "time.hue_lite_wake_up_mon_fri"
    assert ids["bedtime_fade_minutes"] == "number.hue_lite_bedtime_fade_out"
    assert len(ids) == 3 + 9 + 4 + 7

    assert hass.states.get("switch.hue_lite_evening_routine_sun_thu_nights").state == "on"
    assert hass.states.get("switch.hue_lite_evening_routine_fri_sat_nights").state == "on"
    assert hass.states.get("switch.hue_lite_lights_off_when_everyone_leaves").state == "off"
    assert hass.states.get("time.hue_lite_bedtime_sun_thu_nights").state == "23:00:00"
    assert hass.states.get("number.hue_lite_bedtime_fade_out").state == "15"
    # Settings are configuration, so Home Assistant's own dashboards show the
    # routines' sensors rather than every setting.
    registry = er.async_get(hass)
    for setting in ("number.hue_lite_bedtime_fade_out", "switch.hue_lite_coming_home", "time.hue_lite_wake_up_mon_fri"):
        assert registry.async_get(setting).entity_category == "config"
    assert registry.async_get("sensor.hue_lite_natural_light").entity_category is None
    assert hass.states.get("sensor.hue_lite_natural_light").state == "day"


async def test_settings_are_kept(hass: HomeAssistant, entry: MockConfigEntry, hue_lite: HueLite) -> None:
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.hue_lite_evening_routine_sun_thu_nights"}, blocking=True
    )
    await hass.services.async_call(
        "time", "set_value", {"entity_id": "time.hue_lite_bedtime_sun_thu_nights", "time": "22:15"}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.hue_lite_bedtime_fade_out", "value": 20}, blocking=True
    )
    assert hass.states.get("sensor.hue_lite_evening_routine").attributes["bedtime"] == "22:15"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.hue_lite_evening_routine_sun_thu_nights").state == "off"
    assert hass.states.get("time.hue_lite_bedtime_sun_thu_nights").state == "22:15:00"
    assert hass.states.get("number.hue_lite_bedtime_fade_out").state == "20"


async def test_the_old_evening_switch_becomes_two(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """The evening routine's one switch, switched off, becomes two switched
    off, and its entity goes."""
    stored(hass_storage, settings={"evening_routine_enabled": False})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", DOMAIN, f"{entry.entry_id}_evening_routine_enabled", config_entry=entry)
    await set_up(hass, entry)
    assert hass.states.get("switch.hue_lite_evening_routine_sun_thu_nights").state == "off"
    assert hass.states.get("switch.hue_lite_evening_routine_fri_sat_nights").state == "off"
    assert registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_evening_routine_enabled") is None


async def test_a_newer_setup_loses_the_old_evening_switch_too(hass: HomeAssistant, home: Home, entry: MockConfigEntry) -> None:
    """Set up after First one home was added (minor version 2) but before
    the evening routine's switch became two: its entity goes, and the entry
    is brought up to date."""
    newer = MockConfigEntry(domain=DOMAIN, title="Hue-Lite", minor_version=2, options=dict(entry.options))
    newer.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", DOMAIN, f"{newer.entry_id}_evening_routine_enabled", config_entry=newer)
    await set_up(hass, newer)
    assert newer.minor_version == 3
    assert registry.async_get_entity_id("switch", DOMAIN, f"{newer.entry_id}_evening_routine_enabled") is None
    assert hass.states.get("switch.hue_lite_evening_routine_sun_thu_nights").state == "on"


async def test_damaged_saved_data_is_left_out(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Parts of the wrong kind are left out rather than stopping Hue-Lite
    starting (or a string of a light being read as many one-letter lights)."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {
            "settings": ["off"],
            "scenes": "light.bedroom",
            "auto": "light.bedroom",
            "dimmed": ["light.bedroom"],
            "last_wake_up": "2026-10-22T07:00:00",
            "mimicked": "light.bedroom",
        },
    }
    hue_lite = await set_up(hass, entry)
    assert (hue_lite.scenes, hue_lite.auto, hue_lite.dimmed, hue_lite.last_wake_up) == ({}, set(), {}, None)
    assert hue_lite.routines.mimicked == set()
    assert hue_lite.settings["evening_weeknights"] is True


async def test_bad_or_stale_saved_data_is_dropped(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Values of the wrong kind keep their defaults (a malformed time would
    break every tick), and lights excluded since, or in a scene there isn't
    any more, are let go."""
    stored(
        hass_storage,
        settings={"bedtime_weeknight": "25:99", "bedtime_fade_minutes": "lots", "wake_up_weekdays": True},
        scenes={
            "light.bedroom": "relax",
            "light.living_room": "movie",
            "light.garden": "natural",
            "light.broken": 3,
            "light.desk": "sleep",
            "light.lamp": "moonlit",
        },
        layouts={"kitchen": {"order": ["moonlit", "relax"], "hidden": ["movie"]}},
        dimmers={"dimmer": {"scenes": {"order": ["moonlit"], "hidden": []}}},
    )
    hue_lite = await set_up(hass, entry)
    assert hue_lite.settings["bedtime_weeknight"] == "23:00"
    assert hue_lite.settings["bedtime_fade_minutes"] == 15
    assert hue_lite.settings["wake_up_weekdays"] is True
    # Movie is called Nebula now, and Moonlit Moonlight, in rooms and dimmers too.
    assert hue_lite.scenes == {"light.bedroom": "relax", "light.living_room": "nebula", "light.lamp": "moonlight"}
    assert hue_lite.layouts == {"kitchen": {"order": ["moonlight", "relax"], "hidden": ["nebula"]}}
    assert hue_lite.dimmer_settings == {"dimmer": {"scenes": {"order": ["moonlight"], "hidden": []}}}


async def test_routine_lights_let_go_when_bedtime_passed_while_down(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Home Assistant was down long past bedtime (more than the 15 minutes it
    catches up on): the routine's lights are let go, left as they are, rather
    than kept following it until the next evening."""
    freezer.move_to(f"{FRIDAY} 01:00:00+01:00")
    stored(hass_storage, scenes={"light.bedroom": "routine", "light.living_room": "natural"})
    hue_lite = await set_up(hass, entry)
    assert hue_lite.scenes == {"light.living_room": "natural"}


@pytest.mark.parametrize("written", ["23", "2300", "T23:00", "23:00+05:00"])
async def test_a_saved_time_written_another_way_keeps_its_default(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any], written: str
) -> None:
    """A bedtime or wake-up time saved in some other form than HH:MM (in an
    edited or damaged file) keeps its default, rather than stopping Hue-Lite
    starting."""
    stored(hass_storage, settings={"bedtime_weeknight": written, "wake_up_weekday": written})
    hue_lite = await set_up(hass, entry)
    assert (hue_lite.settings["bedtime_weeknight"], hue_lite.settings["wake_up_weekday"]) == ("23:00", "07:00")


async def test_saved_timings_out_of_range_are_brought_within_it(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """A timing saved outside the range its slider allows (in an edited file,
    or a range changed since) is brought within it, rather than stopping
    Hue-Lite starting; one that isn't a whole number keeps its default."""
    stored(
        hass_storage,
        settings={
            # Far too long, too short, just over and just under...
            "wake_up_fade_minutes": 10**13,
            "wake_up_off_after_minutes": 0,
            "evening_brighten_minutes": 181,
            "evening_start_before_sunset": -1,
            # ...and not whole numbers at all.
            "bedtime_fade_minutes": float("nan"),
            "evening_wind_down_minutes": float("inf"),
            "evening_nightlight_minutes": True,
        },
    )
    hue_lite = await set_up(hass, entry)
    assert {key: hue_lite.settings[key] for key in NUMBERS} == {
        "wake_up_fade_minutes": 60,
        "wake_up_off_after_minutes": 15,
        "bedtime_fade_minutes": 15,
        "evening_start_before_sunset": 0,
        "evening_brighten_minutes": 180,
        "evening_wind_down_minutes": 120,
        "evening_nightlight_minutes": 30,
    }


@pytest.mark.parametrize("saved", ["9999-12-31T23:59:59+00:00", "2026-10-24T13:00:00+01:00"])
async def test_a_saved_wake_up_far_ahead_is_forgotten(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any], saved: str
) -> None:
    """The last wake-up's time, saved more than a day ahead (in an edited or
    damaged file), is forgotten, rather than stopping Hue-Lite starting or
    holding off the next wake-up."""
    stored(hass_storage, last_wake_up=saved)
    hue_lite = await set_up(hass, entry)
    assert hue_lite.last_wake_up is None


@pytest.mark.parametrize(
    "saved",
    [
        {"last_evening": "9999-12-31T23:59:59+00:00"},
        {"last_evening": 5},
        {"late": {"lights": ["light.living_room"], "until": "2026-10-22T14:00:00+01:00"}},  # beyond half an hour
        {"late": {"lights": [], "until": "2026-10-22T13:20:00+01:00"}},
        {"late": {"lights": "light.living_room", "until": "2026-10-22T13:20:00+01:00"}},
        {"late": ["light.living_room"]},
        {"left": {"at": "2026-10-22T13:05:00+01:00", "done": False}},  # still to come
        {"left": {"at": "soon", "done": False}},
        {"left": "2026-10-22T12:59:00+01:00"},
    ],
)
async def test_damaged_restart_records_are_left_out(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any], saved: dict[str, Any]
) -> None:
    """What a restart carries on with (when the evening started, lights lit
    for a late arrival, everyone going out) is forgotten if it's damaged or
    further ahead than it could be, rather than holding back the evening or
    switching lights off at odd times."""
    hass.states.async_set("zone.home", "0")
    stored(hass_storage, **saved)
    hue_lite = await set_up(hass, entry)
    routines = hue_lite.routines
    assert (hue_lite.last_evening, routines.late, routines.late_until, routines.left_at) == (None, set(), None, None)


async def test_restart_records_are_kept(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    hass.states.async_set("zone.home", "0")
    stored(
        hass_storage,
        last_evening="2026-10-21T16:30:00+01:00",
        late={"lights": ["light.living_room", "light.gone", 3], "until": "2026-10-22T13:20:00+01:00"},
        left={"at": "2026-10-22T12:00:00+01:00", "done": "yes"},
    )
    hue_lite = await set_up(hass, entry)
    routines = hue_lite.routines
    assert hue_lite.last_evening.isoformat() == "2026-10-21T16:30:00+01:00"
    assert (routines.late, routines.late_until.isoformat()) == ({"light.living_room", "light.gone"}, "2026-10-22T13:20:00+01:00")
    assert (routines.left_at.isoformat(), routines.left_done) == ("2026-10-22T12:00:00+01:00", False)


async def test_saved_lists_of_lights_keep_only_lights(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """The lights on Auto, the ones Away switched on and the ones in a scene
    are saved by their entity ids. Anything else in those lists (another
    kind of entity, or an id too long to be a light's) is left out; a light
    that isn't set up yet is kept, for when it is."""
    # It's evening and everyone's out, so Away still has the lights it switched on.
    freezer.move_to(f"{THURSDAY} 20:00:00+01:00")
    hass.states.async_set("zone.home", "0")
    too_long = "light." + "x" * 250
    stored(
        hass_storage,
        scenes={"light.bedroom": "relax", "lock.front_door": "relax", too_long: "relax", "light.desk": "relax"},
        auto=["light.bedroom", "light.bedroom", "switch.kettle", too_long, 3, "light.desk"],
        mimicked=["light.living_room", "lock.front_door", "light.living_room"],
    )
    hue_lite = await set_up(hass, entry)
    assert hue_lite.scenes == {"light.bedroom": "relax", "light.desk": "relax"}
    assert hue_lite.auto == {"light.bedroom", "light.desk"}
    assert hue_lite.routines.mimicked == {"light.living_room"}


async def test_saved_lists_of_lights_are_capped(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """A damaged file listing a huge number of lights keeps the first 5,000
    of each list, so Hue-Lite still starts quickly."""
    # It's evening and everyone's out, so Away still has the lights it switched on.
    freezer.move_to(f"{THURSDAY} 20:00:00+01:00")
    hass.states.async_set("zone.home", "0")
    many = [f"light.lamp_{i}" for i in range(6000)]
    stored(hass_storage, scenes=dict.fromkeys(many, "relax"), auto=many, mimicked=many)
    hue_lite = await set_up(hass, entry)
    assert len(hue_lite.scenes) == len(hue_lite.auto) == len(hue_lite.routines.mimicked) == 5000
    assert "light.lamp_4999" in hue_lite.auto
    assert "light.lamp_5000" not in hue_lite.auto


@pytest.mark.parametrize("page", ["harness/index.html", "screenshots/index.html"])
def test_the_mock_dashboards_show_the_real_colours(page: str) -> None:
    """The harness and the screenshot page copy each scene's colours, as
    Hue-Lite sends them to the dashboard: they're kept the same."""
    text = (Path(__file__).parent / page).read_text(encoding="utf-8")
    copied = {scene: json.loads(rgbs) for scene, rgbs in re.findall(r"(\w+): \{ rgb_colors: (\[\[.*?\]\])", text)}
    assert copied == {scene: colours["rgb_colors"] for scene, colours in SCENE_COLOURS.items()}


async def test_dashboard_script_and_config(hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite) -> None:
    labels = lr.async_get(hass)
    lamps = labels.async_create("Lamps")
    shelf = labels.async_create("Shelf")
    labels.async_create("Payroll")  # on nothing
    er.async_get(hass).async_update_entity("light.living_room", labels={home.labels["evening"], lamps.label_id})
    add_light(hass, "strip", device_id=add_device(hass, "strip", labels={shelf.label_id}).id)

    connection = Mock()
    websocket_config(hass, connection, {"id": 1, "type": f"{DOMAIN}/config"})
    config = connection.send_result.call_args.args[1]
    names = {label["name"] for label in config["labels"]}
    # Labels on lights and their devices, and the role labels; not the rest.
    assert {"Lamps", "Shelf", "Evening", "Wake up", "Nightlight", "Coming home", "First one home", "Exclude from lighting"} <= names
    assert "Payroll" not in names
    assert config["roles"] == home.labels
    assert config["entities"]["light_scenes"] == "sensor.hue_lite_light_scenes"
    # The lights that take part: not the excluded garden light, the hidden
    # cupboard light or the light group.
    assert config["lights"] == ["light.bedroom", "light.living_room", "light.strip"]
    # Each preset's and colour scene's colours as a colour bulb reports them.
    # With their brightness, for a copy made in the scene editor.
    assert config["presets"]["relax"] == {"rgb_colors": [[255, 148, 43]], "brightness": [56]}
    assert len(config["presets"]["nebula"]["rgb_colors"]) == len(config["presets"]["nebula"]["brightness"]) == 5
    assert config["presets"]["aurora"]["rgb_colors"][0] == [45, 255, 93]

    # The script's loader is on every page (versioned by content) while
    # Hue-Lite is set up.
    scripts = hass.data[DATA_EXTRA_MODULE_URL]
    assert [url.split("?v=")[0] for url in scripts.urls] == ["/hue_lite/hue-lite-loader.js"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not scripts.urls


async def test_deleted_role_label_raises_a_repair(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    labels = lr.async_get(hass)
    issues = ir.async_get(hass)
    labels.async_delete(home.labels["evening"])
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, "missing_label_evening")
    assert issue is not None
    assert issue.translation_placeholders == {"role": "Evening"}

    # Making the label again clears it.
    assert labels.async_create("Evening").label_id == home.labels["evening"]
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, "missing_label_evening") is None


async def test_removing_hue_lite_forgets_it(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite, hass_storage: dict[str, Any]
) -> None:
    """Its settings and scenes, and its Repairs issues; the labels stay."""
    lr.async_get(hass).async_delete(home.labels["evening"])
    await hass.async_block_till_done()
    await hue_lite.async_save()
    assert STORAGE_KEY in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    assert STORAGE_KEY not in hass_storage
    assert ir.async_get(hass).async_get_issue(DOMAIN, "missing_label_evening") is None
    assert lr.async_get(hass).async_get_label(home.labels["wake_up"])


async def test_a_removed_hue_lite_stays_forgotten(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite, hass_storage: dict[str, Any]
) -> None:
    """A light changed by hand while Hue-Lite saves on its way out doesn't
    bring its saved data back once it's removed."""
    # The living room light is in Natural light and on.
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    on(hass, hue_lite, "light.living_room")
    await settle(hass)
    store = hue_lite._store
    write = store._async_write_data

    async def write_while_a_light_changes(data: dict[str, Any]) -> None:
        # Someone dims it while the file's being written (Home Assistant
        # writes in another thread, so events go on meanwhile).
        hass.states.async_set("light.living_room", "on", {"brightness": 40}, context=Context(user_id="someone"))
        await write(data)

    with patch.object(store, "_async_write_data", write_while_a_light_changes):
        await hass.config_entries.async_remove(entry.entry_id)
    assert STORAGE_KEY not in hass_storage

    # Well after a delayed save would have been written.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert STORAGE_KEY not in hass_storage


async def test_a_failed_start_stops_what_it_started(hass: HomeAssistant, home: Home, entry: MockConfigEntry) -> None:
    """If starting fails part-way, Hue-Lite doesn't go on listening."""
    before = hass.bus.async_listeners().get(lr.EVENT_LABEL_REGISTRY_UPDATED, 0)
    entry.add_to_hass(hass)
    with patch.object(HueLite, "_check_labels", side_effect=RuntimeError("boom")):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.bus.async_listeners().get(lr.EVENT_LABEL_REGISTRY_UPDATED, 0) == before


async def test_real_sun_times(hass: HomeAssistant, hue_lite: HueLite) -> None:
    """Without the fixed times the other tests use: London on midsummer's day."""
    hass.config.latitude, hass.config.longitude = 51.5, -0.13
    sun = REAL_SUN_TIMES(hue_lite, date(2026, 6, 21))
    assert sun.dawn < sun.sunrise < sun.noon < sun.sunset < sun.dusk
    assert 4 * 60 <= sun.sunrise <= 5 * 60  # about 04:43 BST
    assert 21 * 60 <= sun.sunset <= 21 * 60 + 30  # about 21:21 BST
