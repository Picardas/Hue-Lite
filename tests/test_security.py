"""Permissions, audit trail, what the dashboard is told, and bad stored data."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er, label_registry as lr

from custom_components.hue_lite.const import DOMAIN, STORAGE_KEY
from custom_components.hue_lite.dashboard import websocket_config
from custom_components.hue_lite.engine import HueLite

from .conftest import Home
from .test_routines import set_up, tick


async def test_actions_respect_the_callers_permissions(
    hass: HomeAssistant, home: Home, hue_lite: HueLite, hass_read_only_user: MockUser
) -> None:
    """Hue-Lite changes lights as itself, which Home Assistant doesn't check,
    so its actions check the caller like light.turn_on would."""
    as_reader = Context(user_id=hass_read_only_user.id)

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, "apply_preset", {"preset": "relax", "area_id": "living_room"}, blocking=True, context=as_reader
        )
    # With no target, only the lights they may control: none.
    await hass.services.async_call(DOMAIN, "natural_light", {}, blocking=True, context=as_reader)
    assert home.turn_on == []
    assert hue_lite.scenes == {}


async def test_changes_are_linked_to_their_cause(
    hass: HomeAssistant, home: Home, hue_lite: HueLite, hass_admin_user: MockUser
) -> None:
    call = Context(user_id=hass_admin_user.id)
    await hass.services.async_call(
        DOMAIN, "apply_preset", {"preset": "rest", "entity_id": "light.bedroom"}, blocking=True, context=call
    )
    sent = home.turn_on[0].context
    assert sent.parent_id == call.id
    # Still not a change by hand.
    assert sent.user_id is None


async def test_dashboard_is_told_only_the_labels_it_shows(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    labels = lr.async_get(hass)
    lamps = labels.async_create("Lamps")
    labels.async_create("Payroll")  # not on any light
    er.async_get(hass).async_update_entity("light.living_room", labels={home.labels["evening"], lamps.label_id})

    connection = Mock()
    websocket_config(hass, connection, {"id": 1, "type": f"{DOMAIN}/config"})
    names = {label["name"] for label in connection.send_result.call_args.args[1]["labels"]}
    assert "Lamps" in names
    assert "Payroll" not in names
    # The role labels always, for the How it works text.
    assert {"Evening", "Wake up", "Nightlight", "Coming home", "Exclude from lighting"} <= names


async def test_bad_stored_settings_keep_their_defaults(
    hass: HomeAssistant, home: Home, entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "key": STORAGE_KEY,
        "data": {
            "settings": {"bedtime_weeknight": "25:99", "bedtime_fade_minutes": "lots", "wake_up_weekdays": True},
            "scenes": {"light.bedroom": "relax", "light.broken": 3},
        },
    }
    hue_lite = await set_up(hass, entry)
    assert hue_lite.settings["bedtime_weeknight"] == "23:00"
    assert hue_lite.settings["bedtime_fade_minutes"] == 15
    assert hue_lite.settings["wake_up_weekdays"] is True
    assert hue_lite.scenes == {"light.bedroom": "relax"}


async def test_sensors_change_as_visibly_as_the_lights(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, home: Home, entry: MockConfigEntry
) -> None:
    """Not a new state (and recorder row) every minute of a slow blend."""
    await tick(hass, freezer, "08:00")
    hue_lite = await set_up(hass, entry)
    first = hass.states.get("sensor.hue_lite_natural_light")

    await tick(hass, freezer, "08:01")
    assert hass.states.get("sensor.hue_lite_natural_light").last_updated == first.last_updated
    await tick(hass, freezer, "08:10")
    now = hass.states.get("sensor.hue_lite_natural_light")
    assert now.last_updated != first.last_updated
    assert now.attributes["color_temp_kelvin"] == hue_lite.natural.light.kelvin
