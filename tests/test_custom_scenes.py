"""Your own scenes, kept by Hue-Lite with the built-in ones: static (one
colour, like a preset) or dynamic (several, drifting at their own speed)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.hue_lite import dashboard
from custom_components.hue_lite.const import DOMAIN, STORAGE_KEY
from custom_components.hue_lite.engine import DEFAULT_ORDER, DIMMER_DEFAULT, HueLite
from custom_components.hue_lite.timeline import kelvin_for

from .conftest import Home, action, add_device, add_light, on, set_up, settle, tick, ws

DISCO = {"name": "Disco", "icon": "mdi:party-popper", "colours": [[0.68, 0.31, 60], [0.17, 0.05, 40]], "white": [50, 2700], "drift_minutes": 1}
TEAL = {"name": "Teal", "icon": "mdi:water", "colours": [[0.17, 0.35, 30]], "white": [30, 5000]}


def stored(hass_storage: dict[str, Any], **data: Any) -> None:
    hass_storage[STORAGE_KEY] = {"version": 1, "key": STORAGE_KEY, "data": data}


async def test_your_own_scenes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    stored(hass_storage, custom_scenes={"custom_disco": DISCO, "custom_teal": TEAL}, scenes={"light.bedroom": "custom_teal"})
    hue_lite = await set_up(hass, entry)
    assert hue_lite.scenes == {"light.bedroom": "custom_teal"}
    lamp = add_light(hass, "lamp", "living_room")

    def sent() -> dict[str, dict]:
        return {c.data["entity_id"][0]: c.data for c in home.turn_on}

    # A static one: its one colour for every colour bulb, and it doesn't drift.
    await action(hass, "apply_preset", {"preset": "custom_teal", "area_id": "living_room"})
    assert [c.data for c in home.turn_on] == [
        {"entity_id": [lamp, "light.living_room"], "brightness_pct": 30, "xy_color": [0.17, 0.35], "transition": 0.4}
    ]
    on(hass, hue_lite, lamp, "light.living_room")
    home.turn_on.clear()
    await tick(hass, freezer, "13:10")
    assert home.turn_on == []

    # A dynamic one spreads its colours, each at its own brightness, and
    # drifts at its own speed: every minute.
    await action(hass, "apply_preset", {"preset": "custom_disco", "area_id": "living_room"})
    first = {light: data["brightness_pct"] for light, data in sent().items()}
    assert sorted(first.values()) == [40, 60]
    on(hass, hue_lite, lamp, "light.living_room")
    home.turn_on.clear()
    await tick(hass, freezer, "13:11")
    assert {light: data["brightness_pct"] for light, data in sent().items()} == {lamp: first["light.living_room"], "light.living_room": first[lamp]}
    assert {data["transition"] for data in sent().values()} == {60}

    # They're kept across a reload; a scene that isn't there is refused.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert set(entry.runtime_data.custom) == {"custom_disco", "custom_teal"}
    assert entry.runtime_data.scenes[lamp] == "custom_disco"
    with pytest.raises(ServiceValidationError):
        await action(hass, "apply_preset", {"preset": "custom_gone"})

    # For automations, the scenes sensor lists them, and they can be named.
    scenes = hass.states.get("sensor.hue_lite_light_scenes").attributes["custom_scenes"]
    assert scenes == {"custom_disco": "Disco", "custom_teal": "Teal"}
    await action(hass, "apply_preset", {"preset": " teal ", "entity_id": lamp})
    assert entry.runtime_data.scenes[lamp] == "custom_teal"


async def test_making_changing_and_deleting_scenes(
    hass: HomeAssistant, home: Home, hue_lite: HueLite, monkeypatch: pytest.MonkeyPatch
) -> None:
    dimmer = add_device(hass, "dimmer", model="RWL021", area="living_room")
    hue_lite.set_layout("living_room", {"order": ["relax", "natural"], "hidden": []})

    def save(admin: bool = True, **scene: Any) -> Mock:
        return ws(hass, dashboard.websocket_save_scene, {"type": f"{DOMAIN}/save_scene", "icon": "mdi:waves", "drift_minutes": 5, **scene}, admin=admin)

    def delete(scene_id: str) -> Mock:
        return ws(hass, dashboard.websocket_delete_scene, {"type": f"{DOMAIN}/delete_scene", "scene": scene_id})

    # Colours come as RGB, each with a brightness; white bulbs get the first
    # colour's nearest white, at their average brightness.
    sea = save(
        name="Sea",
        colours=[{"rgb": [0, 128, 255], "brightness": 50}, {"rgb": [0, 255, 128], "brightness": 30}],
        show={"layouts": ["living_room", "kitchen"], "dimmers": [dimmer.id, "not_a_dimmer"]},
    ).send_result.call_args.args[1]["id"]
    scene = hue_lite.custom[sea]
    assert (scene["name"], scene["drift_minutes"], [c[2] for c in scene["colours"]]) == ("Sea", 5, [50, 30])
    assert scene["white"] == [40, kelvin_for(tuple(scene["colours"][0][:2]))]
    assert len(hue_lite.shared()["custom"][sea]["rgb_colors"]) == 2

    # It's shown where it was made (a room with no scenes of its own starts
    # from the usual order), so hidden everywhere else.
    assert hue_lite.layouts == {
        "living_room": {"order": ["relax", "natural", sea], "hidden": []},
        "kitchen": {"order": [*DEFAULT_ORDER, sea], "hidden": []},
    }
    assert hue_lite.dimmer_settings == {dimmer.id: {"scenes": {"order": [*DIMMER_DEFAULT["order"], sea], "hidden": DIMMER_DEFAULT["hidden"]}}}

    # Changed, the lights in it that are on get it straight away, and the
    # others when they're next switched on.
    await action(hass, "apply_preset", {"preset": sea, "entity_id": ["light.living_room", "light.bedroom"]})
    on(hass, hue_lite, "light.living_room")
    home.turn_on.clear()
    save(scene=sea, name="Deep sea", colours=[{"rgb": [0, 0, 255], "brightness": 20}])
    await settle(hass)
    assert (hue_lite.custom[sea]["name"], [c.data["entity_id"] for c in home.turn_on]) == ("Deep sea", [["light.living_room"]])
    hass.states.async_set("light.bedroom", "on")
    await settle(hass)
    assert [c.data["brightness_pct"] for c in home.turn_on] == [20, 20]

    # The built-in scenes can't be changed or deleted, and there's a limit.
    assert save(scene="relax", name="Mine", colours=[{"rgb": [1, 2, 3], "brightness": 5}]).send_error.call_args.args[1] == "not_found"
    assert delete("relax").send_error.call_args.args[1] == "not_found"
    monkeypatch.setattr(dashboard, "MAX_CUSTOM", 1)
    assert save(name="More", colours=[{"rgb": [1, 2, 3], "brightness": 5}]).send_error.call_args.args[1] == "too_many"

    # Deleted, its lights leave it and the rooms and dimmers forget it.
    delete(sea).send_result.assert_called_once()
    assert (hue_lite.custom, hue_lite.scenes) == ({}, {})
    assert hue_lite.layouts["living_room"] == {"order": ["relax", "natural"], "hidden": []}
    assert sea not in hue_lite.dimmer_settings[dimmer.id]["scenes"]["order"]

    # Anyone can make one, but only an admin's joins a dimmer's scenes.
    monkeypatch.setattr(dashboard, "MAX_CUSTOM", 50)
    hue_lite.set_dimmer(dimmer.id, scenes=None)
    mine = save(admin=False, name="Mine", colours=[{"rgb": [1, 2, 3], "brightness": 5}], show={"layouts": ["bedroom"], "dimmers": [dimmer.id]})
    assert mine.send_result.call_args.args[1]["id"] in hue_lite.layouts["bedroom"]["order"]
    assert hue_lite.dimmer_settings == {}


async def test_trying_colours_on_the_lights(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    # The living room's in Natural light; the editor shows two colours on
    # it and the bedroom (not the excluded garden light): they leave their scene.
    await action(hass, "natural_light", {"entity_id": "light.living_room"})
    home.turn_on.clear()
    msg = {
        "type": f"{DOMAIN}/preview_scene",
        "lights": ["light.living_room", "light.bedroom", "light.garden"],
        "colours": [{"rgb": [255, 0, 0], "brightness": 80}, {"rgb": [0, 0, 255], "brightness": 20}],
    }
    ws(hass, dashboard.websocket_preview_scene, msg)
    await settle(hass)
    assert {c.data["entity_id"][0]: c.data["brightness_pct"] for c in home.turn_on} == {"light.bedroom": 80, "light.living_room": 20}
    assert hue_lite.scenes == {}


async def test_damaged_scenes_are_left_out(hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]) -> None:
    stored(
        hass_storage,
        custom_scenes={
            "custom_teal": TEAL,
            "relax": TEAL,  # not one of yours
            "custom_dark": {**TEAL, "colours": [[0.2, 0.3, 0]]},
            "custom_many": {**DISCO, "colours": [[0.2, 0.3, 50]] * 9},
            "custom_slow": {**DISCO, "drift_minutes": 90},
            "custom_white": {**TEAL, "white": [50, 9000]},
            "custom_nameless": {**TEAL, "name": " "},
        },
        layouts={"kitchen": {"order": ["custom_teal", "custom_dark", "relax"], "hidden": []}},
    )
    hue_lite = await set_up(hass, entry)
    assert hue_lite.custom == {"custom_teal": {**TEAL, "drift_minutes": 3}}
    assert hue_lite.layouts == {"kitchen": {"order": ["custom_teal", "relax"], "hidden": []}}
