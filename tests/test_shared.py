"""The settings every dashboard shares, kept by Hue-Lite: each room's and
light's scenes, and each Hue dimmer's lights and scenes."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant.components.lovelace.const import STORAGE_DASHBOARD_CREATE_FIELDS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.hue_lite import dashboard
from custom_components.hue_lite.const import DOMAIN, STORAGE_KEY
from custom_components.hue_lite.engine import HueLite

from .conftest import Home, add_device, natural, ours, set_up, settle, ws


def events(connection: Mock) -> list[dict[str, Any]]:
    return [call.args[0]["event"] for call in connection.send_message.call_args_list]


async def test_scenes_are_shared_and_kept(hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite) -> None:
    open_dashboard = ws(hass, dashboard.websocket_subscribe, {"type": f"{DOMAIN}/subscribe"})
    assert events(open_dashboard)[0]["layouts"] == {}
    assert events(open_dashboard)[0]["dimmer_default"]["hidden"] == ["golden_hour", "nebula", "aurora", "ember", "forest", "moonlight"]

    # A room's scenes, set from one dashboard, go to every one open...
    living = {"order": ["relax", "natural"], "hidden": ["nebula"]}
    other = ws(hass, dashboard.websocket_set_scenes, {"type": f"{DOMAIN}/set_scenes", "key": "living_room", "scenes": living})
    other.send_result.assert_called_once()
    assert events(open_dashboard)[-1]["layouts"] == {"living_room": living}

    # ...and are kept across a restart.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.layouts == {"living_room": living}

    # None puts a room back to the default; a dashboard closing stops what it's sent.
    ws(hass, dashboard.websocket_set_scenes, {"type": f"{DOMAIN}/set_scenes", "key": "living_room", "scenes": None})
    assert entry.runtime_data.layouts == {}
    sent = len(events(open_dashboard))
    open_dashboard.subscriptions[1]()
    ws(hass, dashboard.websocket_set_scenes, {"type": f"{DOMAIN}/set_scenes", "key": "bedroom", "scenes": living})
    assert len(events(open_dashboard)) == sent


async def test_only_real_scenes_are_taken(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    def set_scenes(order: list[str]) -> Mock:
        return ws(hass, dashboard.websocket_set_scenes, {"type": f"{DOMAIN}/set_scenes", "key": "x", "scenes": {"order": order, "hidden": []}})

    set_scenes(["relax", "relax"]).send_result.assert_called_once()
    assert hue_lite.layouts["x"] == {"order": ["relax"], "hidden": []}
    assert set_scenes(["sleep"]).send_error.call_args.args[1] == "unknown_scene"


async def test_damaged_shared_settings_are_left_out(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {
            "layouts": {"living_room": {"order": ["relax", "sleep", 3], "hidden": "nebula"}, "bad": 1},
            "dimmers": {"dimmer": {"lights": ["light.bedroom", 2], "scenes": "no"}, "empty": {}, "bad": []},
        },
    }
    hue_lite = await set_up(hass, entry)
    assert hue_lite.layouts == {"living_room": {"order": ["relax"], "hidden": []}}
    assert hue_lite.dimmer_settings == {"dimmer": {"lights": ["light.bedroom"]}}


async def test_the_lights_dashboard_is_added_once(hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite) -> None:
    def config() -> dict[str, Any]:
        return ws(hass, dashboard.websocket_config, {"type": f"{DOMAIN}/config"}).send_result.call_args.args[1]

    def claim(release: bool = False, admin: bool = True) -> bool:
        connection = ws(hass, dashboard.websocket_claim_dashboard, {"type": f"{DOMAIN}/claim_dashboard", "release": release}, admin=admin)
        return connection.send_result.call_args.args[1]["claimed"]

    # The first admin's browser to ask adds it; any other open at the time doesn't.
    assert config()["add_dashboard"]
    with pytest.raises(Unauthorized):
        claim(admin=False)
    assert claim()
    assert not claim()
    assert not config()["add_dashboard"]

    # If adding it failed, the claim's given back for the next try.
    assert not claim(release=True)
    assert claim()

    # Added, it isn't added again after a restart (so one you delete stays deleted)...
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not config()["add_dashboard"]

    # ...until the option's turned off (when it isn't added at all) and on again.
    hass.config_entries.async_update_entry(entry, options={**entry.options, "dashboard": False})
    await hass.async_block_till_done()
    assert not config()["add_dashboard"]
    assert not claim()
    hass.config_entries.async_update_entry(entry, options={**entry.options, "dashboard": True})
    await hass.async_block_till_done()
    assert config()["add_dashboard"]


def test_the_dashboard_is_one_home_assistant_takes() -> None:
    """What the script asks Home Assistant for (as in addDashboard in
    hue-lite.js) passes its own check for a new dashboard."""
    schema = vol.Schema(STORAGE_DASHBOARD_CREATE_FIELDS)
    schema(
        {
            "url_path": "lights",
            "allow_single_word": True,
            "mode": "storage",
            "title": "Lights",
            "icon": "mdi:lightbulb-group",
            "show_in_sidebar": True,
            "require_admin": False,
        }
    )


async def test_a_failure_to_add_the_dashboard_is_logged(
    hass: HomeAssistant, home: Home, hue_lite: HueLite, caplog: pytest.LogCaptureFixture
) -> None:
    claim = {"type": f"{DOMAIN}/claim_dashboard", "release": False}
    ws(hass, dashboard.websocket_claim_dashboard, claim)
    ws(hass, dashboard.websocket_claim_dashboard, {**claim, "release": True, "error": "Url path needs to contain a hyphen (-)"})
    assert "Couldn't add the Lights dashboard (it'll be tried again): Url path needs to contain a hyphen (-)" in caplog.text
    assert not hue_lite.dashboard_added


async def test_a_dimmers_lights_and_scenes(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    dimmer = add_device(hass, "dimmer", model="RWL021", area="living_room")
    config = ws(hass, dashboard.websocket_config, {"type": f"{DOMAIN}/config"}).send_result.call_args.args[1]
    assert [(d["id"], d["area_id"]) for d in config["dimmers"]] == [(dimmer.id, "living_room")]

    async def press() -> None:
        hass.bus.async_fire("zha_event", {"device_id": dimmer.id, "command": "on_short_release"})
        await settle(hass)

    def set_dimmer(admin: bool = True, device_id: str = dimmer.id, **changes: Any) -> Mock:
        return ws(hass, dashboard.websocket_set_dimmer, {"type": f"{DOMAIN}/set_dimmer", "device_id": device_id, **changes}, admin=admin)

    # By default it controls its area, starting with Natural light.
    await press()
    assert natural(hass) == ["light.living_room"]

    # Set to control the bedroom instead, stepping through Relax and Nebula.
    set_dimmer(lights=["light.bedroom"], scenes={"order": ["relax", "nebula", "natural"], "hidden": ["natural"]})
    home.turn_on.clear()
    seen = []
    for _ in range(3):
        await press()
        seen.append(hue_lite.scenes["light.bedroom"])
        hass.states.async_set("light.bedroom", "on", context=ours(hue_lite))
    assert seen == ["relax", "nebula", "relax"]
    assert home.touched() == {"light.bedroom"}

    # Back to the defaults: its area again, from Natural light.
    set_dimmer(lights=None, scenes=None)
    assert hue_lite.dimmer_settings == {}

    # Only admins can change a dimmer, and only a Hue dimmer.
    with pytest.raises(Unauthorized):
        set_dimmer(admin=False, lights=["light.bedroom"])
    other = add_device(hass, "remote", model="TRADFRI")
    assert set_dimmer(device_id=other.id, lights=["light.bedroom"]).send_error.call_args.args[1] == "not_found"
    assert hue_lite.dimmer_settings == {}


async def test_a_dimmer_keeps_up_with_its_lights(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    """Its lights keep up with new entity ids and are dropped when deleted,
    and so are the scenes shown for a light on its own; a deleted dimmer is
    forgotten."""
    dimmer = add_device(hass, "dimmer", model="RWL021", area="living_room")
    hue_lite.set_dimmer(dimmer.id, lights=["light.bedroom", "light.living_room"])
    mine = {"order": ["relax"], "hidden": []}
    hue_lite.set_layout("bedroom|light.bedroom", mine)
    hue_lite.set_layout("living_room|light.living_room", mine)
    entities = er.async_get(hass)
    entities.async_update_entity("light.bedroom", new_entity_id="light.master")
    entities.async_remove("light.living_room")
    await hass.async_block_till_done()
    assert hue_lite.dimmer_settings == {dimmer.id: {"lights": ["light.master"]}}
    assert hue_lite.layouts == {"bedroom|light.master": mine}

    dr.async_get(hass).async_remove_device(dimmer.id)
    await hass.async_block_till_done()
    assert hue_lite.dimmer_settings == {}


async def test_commands_need_hue_lite_and_a_limit(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only so many rooms and lights keep scenes of their own (one, here)...
    monkeypatch.setattr(dashboard, "MAX_LAYOUTS", 1)

    def set_scenes(key: str) -> Mock:
        return ws(hass, dashboard.websocket_set_scenes, {"type": f"{DOMAIN}/set_scenes", "key": key, "scenes": {"order": [], "hidden": []}})

    set_scenes("living_room").send_result.assert_called_once()
    assert set_scenes("bedroom").send_error.call_args.args[1] == "too_many"
    set_scenes("living_room").send_result.assert_called_once()

    # ...and none answer while Hue-Lite isn't set up.
    assert await hass.config_entries.async_unload(entry.entry_id)
    for command in (
        dashboard.websocket_config,
        dashboard.websocket_claim_dashboard,
        dashboard.websocket_subscribe,
        dashboard.websocket_set_scenes,
        dashboard.websocket_set_dimmer,
        dashboard.websocket_save_scene,
        dashboard.websocket_preview_scene,
        dashboard.websocket_delete_scene,
    ):
        assert ws(hass, command, {}).send_error.call_args.args[1] == "not_loaded"
