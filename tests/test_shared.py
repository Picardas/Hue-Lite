"""The settings every dashboard shares, kept by Hue-Lite: each room's and
light's scenes, and each Hue dimmer's lights and scenes."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized

from custom_components.hue_lite import dashboard
from custom_components.hue_lite.const import DOMAIN, STORAGE_KEY
from custom_components.hue_lite.engine import HueLite

from .conftest import Home, add_device, natural, ours, set_up, settle


def ws(hass: HomeAssistant, handler: Any, msg: dict[str, Any], connection: Mock | None = None, admin: bool = True) -> Mock:
    """Call a websocket command as a dashboard would; the connection records
    what's sent back."""
    connection = connection or Mock(user=Mock(is_admin=admin), subscriptions={})
    handler(hass, connection, {"id": 1, **msg})
    return connection


def events(connection: Mock) -> list[dict[str, Any]]:
    return [call.args[0]["event"] for call in connection.send_message.call_args_list]


async def test_scenes_are_shared_and_kept(hass: HomeAssistant, home: Home, entry: MockConfigEntry, hue_lite: HueLite) -> None:
    open_dashboard = ws(hass, dashboard.websocket_subscribe, {"type": f"{DOMAIN}/subscribe"})
    assert events(open_dashboard)[0]["layouts"] == {}
    assert events(open_dashboard)[0]["dimmer_default"]["hidden"] == ["golden_hour", "nebula", "aurora"]

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


def test_only_real_scenes_are_taken() -> None:
    schema = dashboard.websocket_set_scenes._ws_schema
    schema({"type": f"{DOMAIN}/set_scenes", "id": 1, "key": "x", "scenes": {"order": ["relax"], "hidden": []}})
    with pytest.raises(Exception):
        schema({"type": f"{DOMAIN}/set_scenes", "id": 1, "key": "x", "scenes": {"order": ["sleep"], "hidden": []}})


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
