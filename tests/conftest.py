"""Fixtures: a home with a few lights, areas and labels, and Hue-Lite set up in it."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar, entity_registry as er, label_registry as lr

from custom_components.hue_lite.const import CONF_DIMMERS, CONF_PRESENCE, DOMAIN, ROLES, label_option
from custom_components.hue_lite.engine import HueLite
from custom_components.hue_lite.timeline import SunTimes

# Thursday, with the sun at the timeline's defaults (dawn 06:00, sunrise 06:30,
# noon 12:00, sunset 18:00, dusk 18:30).
THURSDAY = "2026-10-22"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/."""


@pytest.fixture(autouse=True)
def no_frontend(hass: HomeAssistant) -> None:
    """Stand-ins for the frontend and http server (which need the frontend's
    own package), keeping the frontend's list of extra scripts."""
    hass.config.components.update({"http", "frontend"})
    hass.http = Mock(async_register_static_paths=AsyncMock())
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_: None, [])


@pytest.fixture(autouse=True)
def fixed_sun() -> Generator[None]:
    with patch.object(HueLite, "_sun_times", return_value=SunTimes()):
        yield


@pytest.fixture(autouse=True)
def thursday_lunchtime(freezer: FrozenDateTimeFactory) -> None:
    """Natural light's day phase (Cool bright), the evening routine's day."""
    freezer.move_to(f"{THURSDAY} 13:00:00+01:00")


@pytest.fixture(autouse=True)
def no_sleep() -> Generator[None]:
    with patch("custom_components.hue_lite.engine.asyncio.sleep", AsyncMock()):
        yield


@dataclass
class Home:
    labels: dict[str, str]
    turn_on: list[ServiceCall]
    turn_off: list[ServiceCall]


@pytest.fixture
async def home(hass: HomeAssistant) -> Home:
    """Living room (Evening, Coming home), bedroom (Evening, Wake up,
    Nightlight), an excluded garden light, a hidden light and a light group.
    Light actions are recorded rather than run, so states only change when a
    test sets them."""
    await hass.config.async_set_time_zone("Europe/London")
    labels = {role: lr.async_get(hass).async_create(spec[1]).label_id for role, spec in ROLES.items()}
    for area in ("Living room", "Bedroom"):
        ar.async_get(hass).async_create(area)

    ent_reg = er.async_get(hass)

    def light(object_id: str, area: str | None = None, roles: tuple[str, ...] = (), **extra: object) -> None:
        entry = ent_reg.async_get_or_create("light", "test", object_id, suggested_object_id=object_id)
        ent_reg.async_update_entity(entry.entity_id, area_id=area, labels={labels[r] for r in roles}, **extra)
        hass.states.async_set(entry.entity_id, "off", {"friendly_name": object_id.replace("_", " ").title()})

    light("living_room", "living_room", ("evening", "coming_home"))
    light("bedroom", "bedroom", ("evening", "wake_up", "nightlight"))
    light("garden", None, ("exclude",))
    light("cupboard", "bedroom", hidden_by=er.RegistryEntryHider.USER)
    hass.states.async_set("light.all", "off", {"entity_id": ["light.living_room", "light.bedroom"]})
    hass.states.async_set("zone.home", "1")

    return Home(labels, async_mock_service(hass, "light", "turn_on"), async_mock_service(hass, "light", "turn_off"))


@pytest.fixture
def entry(home: Home) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hue-Lite",
        options={
            **{label_option(role): label_id for role, label_id in home.labels.items()},
            CONF_PRESENCE: "zone.home",
            CONF_DIMMERS: True,
        },
    )


@pytest.fixture
async def hue_lite(hass: HomeAssistant, entry: MockConfigEntry) -> HueLite:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data
