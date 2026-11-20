"""Lights on child devices: one part of a product with several (a light
strip's second segment, say), which has no area of its own unless given one
and is then in its parent's."""

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)

from custom_components.hue_lite import dashboard
from custom_components.hue_lite.const import DOMAIN
from custom_components.hue_lite.engine import HueLite

from .conftest import Home, action, add_light, settle, ws


def strip(hass: HomeAssistant, area: str) -> tuple[dr.DeviceEntry, str]:
    """A light strip in `area`, and the light on its second segment (a child
    device with no area of its own)."""
    other = MockConfigEntry(domain="zwave_js")
    other.add_to_hass(hass)
    devices = dr.async_get(hass)
    parent = devices.async_get_or_create(config_entry_id=other.entry_id, identifiers={("zwave_js", "strip")})
    parent = devices.async_update_device(parent.id, area_id=area)
    child = devices.async_get_or_create_child(
        config_entry_id=other.entry_id, identifiers={("zwave_js", "strip-2")}, parent_device_id=parent.id
    )
    return parent, add_light(hass, "strip_light", device_id=child.id)


def config(hass: HomeAssistant) -> dict[str, Any]:
    return ws(hass, dashboard.websocket_config, {"type": f"{DOMAIN}/config"}).send_result.call_args.args[1]


async def test_a_child_light_in_an_excluded_area_is_left_out(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    # The strip is in the garage, which is excluded from lighting...
    garage = ar.async_get(hass).async_create("Garage", labels={home.labels["exclude"]})
    _, light = strip(hass, garage.id)
    # ...so its second segment is too, as it's in the garage as well.
    assert hue_lite.excluded(light)
    assert light not in config(hass)["lights"]


async def test_the_dashboard_shows_a_child_light_in_its_parents_area(
    hass: HomeAssistant, home: Home, hue_lite: HueLite
) -> None:
    # The strip is in the attic, which has a label of its own.
    lamps = lr.async_get(hass).async_create("Lamps")
    attic = ar.async_get(hass).async_create("Attic", labels={lamps.label_id})
    _, light = strip(hass, attic.id)
    # The dashboard is told the segment's light is in the attic, and about
    # the attic's label.
    setup = config(hass)
    assert setup["areas"][light] == attic.id
    assert setup["areas"]["light.living_room"] == "living_room"
    assert lamps.label_id in {label["label_id"] for label in setup["labels"]}


async def test_a_child_device_is_not_a_dimmer(
    hass: HomeAssistant, home: Home, hue_lite: HueLite, caplog: pytest.LogCaptureFixture
) -> None:
    _, light = strip(hass, ar.async_get(hass).async_create("Hall").id)
    segment = er.async_get(hass).async_get(light).device_id
    # Choosing lights for the segment is refused, without reading the model
    # child devices don't have (Home Assistant warns when that's tried).
    connection = ws(hass, dashboard.websocket_set_dimmer, {"type": f"{DOMAIN}/set_dimmer", "device_id": segment, "lights": [light]})
    assert connection.send_error.call_args.args[1] == "not_found"
    assert "ChildDeviceEntry" not in caplog.text


async def test_a_child_light_is_let_go_when_its_parent_moves_to_an_excluded_area(
    hass: HomeAssistant, home: Home, hue_lite: HueLite
) -> None:
    # The strip's in the hall, and its segment's light is in Natural light.
    hall = ar.async_get(hass).async_create("Hall")
    shed = ar.async_get(hass).async_create("Shed", labels={home.labels["exclude"]})
    parent, light = strip(hass, hall.id)
    await action(hass, "natural_light", {"entity_id": light})
    assert light in hue_lite.scenes

    # The strip moves to the shed, which is excluded: the segment's light is let go.
    dr.async_get(hass).async_update_device(parent.id, area_id=shed.id)
    await settle(hass)
    assert light not in hue_lite.scenes
