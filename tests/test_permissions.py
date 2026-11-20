"""Who may do what, checked through Home Assistant's real websocket and
http server, with real users: an admin, an ordinary user and a read-only
one (who may control no lights)."""

import logging
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser, setup_test_component_platform
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.models import Credentials
from homeassistant.components import websocket_api
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import label_registry as lr
from homeassistant.setup import async_setup_component

from custom_components.hue_lite.const import CONF_PRESENCE, DOMAIN, ROLES, label_option
from custom_components.hue_lite.dashboard import ERR_NAME_TAKEN, MAX_PREVIEW
from custom_components.hue_lite.engine import HueLite

from .conftest import add_device, set_up

SEA = [{"rgb": [0, 0, 255], "brightness": 20}]
RED = [{"rgb": [255, 0, 0], "brightness": 100}]


@pytest.fixture(autouse=True)
async def no_frontend(hass: HomeAssistant) -> None:
    """Home Assistant's real http server, so commands go through its
    websocket (and their schemas and permission checks) as a browser's do;
    the frontend is still stood in for."""
    assert await async_setup_component(hass, "http", {})
    hass.config.components.add("frontend")
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_: None, [])


async def _token(hass: HomeAssistant, user: MockUser) -> str:
    credential = Credentials(
        id=f"credential-{user.id}",
        auth_provider_type="homeassistant",
        auth_provider_id=None,
        data={"username": user.id},
        is_new=False,
    )
    user.credentials.append(credential)
    refresh = await hass.auth.async_create_refresh_token(user, "https://example.com/app", credential=credential)
    return hass.auth.async_create_access_token(refresh)


@pytest.fixture
async def ordinary_user(hass: HomeAssistant, local_auth: Any) -> MockUser:
    """A user who isn't an admin, and may control every light."""
    return MockUser(groups=[await hass.auth.async_get_group(GROUP_ID_USER)]).add_to_hass(hass)


@pytest.fixture
async def dashboards(
    hass: HomeAssistant,
    hue_lite: HueLite,
    hass_ws_client: WebSocketGenerator,
    hass_admin_user: MockUser,
    ordinary_user: MockUser,
    hass_read_only_user: MockUser,
) -> dict[str, Any]:
    """With Hue-Lite set up, an open dashboard for each: the admin, the
    ordinary user and the read-only one."""
    users = {"admin": hass_admin_user, "user": ordinary_user, "reader": hass_read_only_user}
    return {who: await hass_ws_client(hass, await _token(hass, user)) for who, user in users.items()}


async def send(dashboard: Any, **msg: Any) -> dict[str, Any]:
    """Send a command and wait for its answer."""
    await dashboard.send_json_auto_id(msg)
    return await dashboard.receive_json()


def error(answer: dict[str, Any]) -> str | None:
    return None if answer["success"] else answer["error"]["code"]


async def save(dashboard: Any, **scene: Any) -> dict[str, Any]:
    return await send(dashboard, type=f"{DOMAIN}/save_scene", icon="", **scene)


# ---- Read-only users ---------------------------------------------------------


async def test_only_users_who_may_control_lights_change_rooms_scenes(
    hass: HomeAssistant, hue_lite: HueLite, dashboards: dict[str, Any]
) -> None:
    hide_relax = {"type": f"{DOMAIN}/set_scenes", "key": "living_room", "scenes": {"order": [], "hidden": ["relax"]}}
    # Someone who may only look can't hide the living room's scenes on
    # everyone's dashboard...
    assert error(await send(dashboards["reader"], **hide_relax)) == websocket_api.ERR_UNAUTHORIZED
    assert hue_lite.layouts == {}
    # ...but anyone who may control lights can, as before.
    assert error(await send(dashboards["user"], **hide_relax)) is None
    assert hue_lite.layouts["living_room"]["hidden"] == ["relax"]


async def test_only_users_who_may_control_lights_make_scenes(
    hass: HomeAssistant, hue_lite: HueLite, dashboards: dict[str, Any]
) -> None:
    """So a read-only user can't use up the 50 scenes of your own either."""
    assert error(await save(dashboards["reader"], name="Junk", colours=RED)) == websocket_api.ERR_UNAUTHORIZED
    assert hue_lite.custom == {}
    assert error(await save(dashboards["user"], name="Sea", colours=SEA)) is None
    assert len(hue_lite.custom) == 1


async def test_a_read_only_user_cant_change_or_delete_a_scene(
    hass: HomeAssistant, hue_lite: HueLite, dashboards: dict[str, Any]
) -> None:
    """Not even one on no lights at all."""
    sea = (await save(dashboards["admin"], name="Sea", colours=SEA))["result"]["id"]
    assert error(await save(dashboards["reader"], scene=sea, name="Sea", colours=RED)) == websocket_api.ERR_UNAUTHORIZED
    assert error(await send(dashboards["reader"], type=f"{DOMAIN}/delete_scene", scene=sea)) == websocket_api.ERR_UNAUTHORIZED
    assert hue_lite.custom[sea]["colours"][0][2] == 20


# ---- Scenes on dimmers -------------------------------------------------------


async def test_only_an_admin_changes_a_scene_a_dimmer_steps_through(
    hass: HomeAssistant, hue_lite: HueLite, dashboards: dict[str, Any]
) -> None:
    """A dimmer changes lights for whoever presses it, so only admins set
    them, and only admins change the scenes they step through."""
    dimmer = add_device(hass, "dimmer", model="RWL021", area="living_room")
    # The admin makes Bedtime on the living room's dimmer.
    bedtime = (await save(dashboards["admin"], name="Bedtime", colours=SEA, show={"dimmers": [dimmer.id]}))["result"]["id"]
    # An ordinary user can't recolour it, or delete it (the dimmer would then
    # step through something else).
    assert error(await save(dashboards["user"], scene=bedtime, name="Bedtime", colours=RED)) == websocket_api.ERR_UNAUTHORIZED
    assert error(await send(dashboards["user"], type=f"{DOMAIN}/delete_scene", scene=bedtime)) == websocket_api.ERR_UNAUTHORIZED
    assert hue_lite.custom[bedtime]["colours"][0][2] == 20
    # Once the admin hides it on the dimmer, On doesn't step through it, and
    # anyone may change it again.
    dimmer_scenes = hue_lite.dimmer_settings[dimmer.id]["scenes"]
    set_dimmer = {"type": f"{DOMAIN}/set_dimmer", "device_id": dimmer.id}
    assert error(await send(dashboards["admin"], **set_dimmer, scenes={**dimmer_scenes, "hidden": [*dimmer_scenes["hidden"], bedtime]})) is None
    assert error(await save(dashboards["user"], scene=bedtime, name="Bedtime", colours=RED)) is None
    assert hue_lite.custom[bedtime]["colours"][0][2] == 100


# ---- Names -------------------------------------------------------------------


async def test_scene_names_are_kept_apart(hass: HomeAssistant, hue_lite: HueLite, dashboards: dict[str, Any]) -> None:
    """Actions can name your own scenes, so no two share a name (ignoring
    case and spaces at either end). Anyone may rename one that isn't on a
    dimmer, as they may change its colours."""
    bedtime = (await save(dashboards["admin"], name="Bedtime", colours=SEA))["result"]["id"]
    # An ordinary user renames the admin's Bedtime: that's allowed.
    assert error(await save(dashboards["user"], scene=bedtime, name="Night", colours=SEA)) is None
    assert hue_lite.custom[bedtime]["name"] == "Night"
    # A second Night, however it's written, is refused...
    assert error(await save(dashboards["user"], name=" night ", colours=RED)) == ERR_NAME_TAKEN
    # ...while saving Night again under its own name is fine.
    assert error(await save(dashboards["user"], scene=bedtime, name="Night", colours=RED)) is None
    assert hue_lite.scene_named("night") == bedtime


# ---- What's sent -------------------------------------------------------------


async def test_a_scene_named_in_an_action_is_short_and_on_one_line(
    hass: HomeAssistant, hue_lite: HueLite, dashboards: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """An unknown scene's name is quoted in the error Home Assistant logs, so
    a long or multi-line one is refused before it gets there."""
    forged = "x\n2026-11-20 03:00:00.000 ERROR (MainThread) [homeassistant.components.http.ban] Login attempt failed"
    for preset in (forged, "x" * 65):
        answer = await send(dashboards["reader"], type="call_service", domain=DOMAIN, service="apply_preset", service_data={"preset": preset})
        assert error(answer) == websocket_api.ERR_INVALID_FORMAT
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR and "Login attempt failed" in record.getMessage()]
    # A scene that isn't there, named on one line, is still just reported.
    answer = await send(dashboards["user"], type="call_service", domain=DOMAIN, service="apply_preset", service_data={"preset": "x" * 64})
    assert error(answer) == websocket_api.ERR_SERVICE_VALIDATION_ERROR


async def test_colours_can_be_tried_on_a_big_houses_lights(
    hass: HomeAssistant, hue_lite: HueLite, dashboards: dict[str, Any]
) -> None:
    """Try on the lights works for Whole home in a house with hundreds of
    lights, up to a limit."""
    lights = [f"light.lamp_{i}" for i in range(MAX_PREVIEW + 1)]
    for command in ({"type": f"{DOMAIN}/preview_scene", "colours": SEA}, {"type": f"{DOMAIN}/end_preview", "restore": True}):
        assert error(await send(dashboards["user"], **command, lights=lights[:MAX_PREVIEW])) is None
        assert error(await send(dashboards["user"], **command, lights=lights)) == websocket_api.ERR_INVALID_FORMAT


async def test_the_dashboards_error_stays_on_one_log_line(
    hass: HomeAssistant, hue_lite: HueLite, dashboards: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """When an admin's browser couldn't add the Lights dashboard, its error
    goes in the log as one line, whatever it says."""
    claim = {"type": f"{DOMAIN}/claim_dashboard", "release": True, "error": "boom\n2026-11-20 03:00:00.000 ERROR forged\x1b[31m"}
    assert error(await send(dashboards["admin"], **claim)) is None
    logged = next(record.getMessage() for record in caplog.records if "Lights dashboard" in record.getMessage())
    assert logged.endswith("boom 2026-11-20 03:00:00.000 ERROR forged [31m")


# ---- Light groups ------------------------------------------------------------


class Lamp(LightEntity):
    """A real light (not a recorded action), so a group really passes commands on."""

    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.XY}
    _attr_color_mode = ColorMode.XY
    _attr_is_on = False

    def __init__(self, name: str) -> None:
        self._attr_unique_id = self._attr_name = name

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()


@pytest.fixture
async def grouped(hass: HomeAssistant, local_auth: Any) -> MockUser:
    """Lamps a and b in a light group (light.both), with Hue-Lite set up, and
    a user who may control lamp a and the group, but not lamp b."""
    setup_test_component_platform(hass, "light", [Lamp("a"), Lamp("b")])
    assert await async_setup_component(
        hass, "light", {"light": [{"platform": "test"}, {"platform": "group", "name": "both", "entities": ["light.a", "light.b"]}]}
    )
    await hass.async_block_till_done()
    hass.states.async_set("zone.home", "1")
    labels = {role: lr.async_get(hass).async_create(spec[1]).label_id for role, spec in ROLES.items()}
    entry = MockConfigEntry(domain=DOMAIN, options={**{label_option(r): label for r, label in labels.items()}, CONF_PRESENCE: "zone.home"})
    await set_up(hass, entry)
    user = MockUser(groups=[await hass.auth.async_get_group(GROUP_ID_USER)]).add_to_hass(hass)
    user.mock_policy({"entities": {"entity_ids": {"light.a": True, "light.both": True}}})
    return user


async def test_a_light_group_needs_its_lights_allowed_too(hass: HomeAssistant, grouped: MockUser) -> None:
    """Home Assistant's own light actions refuse a group with a light the
    user may not control; so do Hue-Lite's."""
    as_user = Context(user_id=grouped.id)
    with pytest.raises(Unauthorized):
        await hass.services.async_call("light", "turn_on", {"entity_id": "light.both"}, blocking=True, context=as_user)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "apply_preset", {"preset": "relax", "entity_id": "light.both"}, blocking=True, context=as_user)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("light.b").state == "off"


async def test_colours_arent_tried_on_a_group_with_a_light_the_user_may_not_control(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, grouped: MockUser
) -> None:
    dashboard = await hass_ws_client(hass, await _token(hass, grouped))
    assert error(await send(dashboard, type=f"{DOMAIN}/preview_scene", lights=["light.both", "light.a"], colours=RED)) is None
    await hass.async_block_till_done(wait_background_tasks=True)
    # Lamp a, which they may control, shows the colour; the group, and so
    # lamp b, are left alone.
    assert hass.states.get("light.a").state == "on"
    assert hass.states.get("light.b").state == "off"
