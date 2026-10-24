"""Your own scenes, kept by Hue-Lite with the built-in ones: static (one
colour, like a preset) or dynamic (several, drifting at their own speed)."""

from __future__ import annotations

from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.hue_lite.const import STORAGE_KEY

from .conftest import Home, action, add_light, on, set_up, tick

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
