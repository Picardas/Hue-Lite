"""Fixtures and helpers: a home with a few lights, areas and labels, and
Hue-Lite set up in it."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr

from custom_components.hue_lite.const import CONF_DIMMERS, CONF_PRESENCE, DOMAIN, ROLES, label_option
from custom_components.hue_lite.engine import HueLite
from custom_components.hue_lite.timeline import SunTimes

# Thursday, with the sun at the timeline's defaults (dawn 06:00, sunrise 06:30,
# noon 12:00, sunset 18:00, dusk 18:30).
THURSDAY = "2026-10-22"
FRIDAY = "2026-10-23"


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
    with patch("custom_components.hue_lite.routines.asyncio.sleep", AsyncMock()):
        yield


@dataclass
class Home:
    labels: dict[str, str]
    turn_on: list[ServiceCall]
    turn_off: list[ServiceCall]

    def touched(self) -> set[str]:
        """Every light sent a command."""
        found: set[str] = set()
        for call in [*self.turn_on, *self.turn_off]:
            ids = call.data["entity_id"]
            found.update([ids] if isinstance(ids, str) else ids)
        return found


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

    def light(object_id: str, area: str | None = None, roles: tuple[str, ...] = (), **extra: object) -> None:
        add_light(hass, object_id, area, {labels[r] for r in roles}, **extra)

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
    return await set_up(hass, entry)


# ---- Helpers ------------------------------------------------------------------


def add_light(
    hass: HomeAssistant,
    object_id: str,
    area: str | None = None,
    labels: set[str] = frozenset(),
    device_id: str | None = None,
    **update: object,
) -> str:
    """A light in the registry (with an area, labels, a device, ...), switched off."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("light", "test", object_id, suggested_object_id=object_id, device_id=device_id)
    registry.async_update_entity(entry.entity_id, area_id=area, labels=set(labels), **update)
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": object_id.replace("_", " ").title()})
    return entry.entity_id


def add_device(hass: HomeAssistant, name: str, model: str = "", area: str | None = None, labels: set[str] = frozenset()) -> dr.DeviceEntry:
    other = MockConfigEntry(domain="zha")
    other.add_to_hass(hass)
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(config_entry_id=other.entry_id, identifiers={("zha", name)}, model=model)
    return devices.async_update_device(device.id, area_id=area, labels=set(labels))


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> HueLite:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, when: str) -> None:
    """Move the clock to `when` (HH:MM on Thursday, or a full date and time)
    and let Hue-Lite act."""
    freezer.move_to(f"{THURSDAY} {when}:00+01:00" if len(when) == 5 else when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


def on(hass: HomeAssistant, hue_lite: HueLite, *lights: str) -> None:
    """Lights reporting that Hue-Lite turned them on."""
    for light in lights:
        hass.states.async_set(light, "on", {}, context=ours(hue_lite))


def ours(hue_lite: HueLite) -> Context:
    """The context of Hue-Lite's latest light command."""
    return Context(id=hue_lite.sender._ours[-1])


def natural(hass: HomeAssistant) -> list[str]:
    return hass.states.get("sensor.hue_lite_natural_light").attributes["lights"]


def routine(hass: HomeAssistant) -> list[str]:
    return hass.states.get("sensor.hue_lite_evening_routine").attributes["lights"]


async def switch(hass: HomeAssistant, entity_id: str, on: bool = True) -> None:
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True)


async def action(hass: HomeAssistant, service: str, data: dict | None = None, context: Context | None = None) -> None:
    await hass.services.async_call(DOMAIN, service, data or {}, blocking=True, context=context)
