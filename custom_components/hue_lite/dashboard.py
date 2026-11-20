"""The Lights dashboard: serves its script (a dashboard strategy and its cards)
to every page, and over the websocket tells it how Hue-Lite is set up and
keeps the settings every dashboard shares (each room's and light's scenes,
each dimmer's lights and scenes, and your own scenes), sending them to the
dashboards open as they change. The scene editor saves, deletes and tries
out your own scenes through it too."""

from collections.abc import Callable
import functools
import hashlib
import logging
from pathlib import Path
import re
from typing import Any

import probatio as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import color as color_util
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN, SIGNAL_SHARED
from .dimmers import is_dimmer
from .engine import HueLite, async_get_loaded
from .lights import light_rgb, rgb_color
from .shared import DEFAULT_ORDER, MAX_CUSTOM, MAX_LAYOUTS, TILE_SCENES, custom_scene, scene_set
from .targets import LIGHT, light_area, may_control, taking_part
from .timeline import PALETTES, PRESETS, kelvin_for, palette_from_stored

_LOGGER = logging.getLogger(__name__)

# Hue-Lite's own websocket error codes (Home Assistant's are in websocket_api).
ERR_NOT_LOADED = "not_loaded"
ERR_UNKNOWN_SCENE = "unknown_scene"
ERR_TOO_MANY = "too_many"
ERR_INVALID = "invalid"
ERR_NAME_TAKEN = "name_taken"
ERR_NO_SCENES = "no_scenes"

FRONTEND = Path(__file__).parent / "frontend"
SCRIPT = FRONTEND / "hue-lite.js"
URL = f"/{DOMAIN}/hue-lite.js"
# What every page loads: it loads the script, trying again if it doesn't arrive.
LOADER = FRONTEND / "hue-lite-loader.js"
LOADER_URL = f"/{DOMAIN}/hue-lite-loader.js"
DATA_URL: HassKey[str] = HassKey(f"{DOMAIN}_dashboard_url")

# Each preset's and colour scene's colours as a colour bulb reports them (for
# their tiles), with their brightness and a colour scene's pace (for a copy
# made in the scene editor).
SCENE_COLOURS = {
    **{
        preset: {"rgb_colors": [light_rgb(light)], "brightness": [round(light.brightness_pct)]}
        for preset, light in PRESETS.items()
    },
    **{
        scene: {
            "rgb_colors": [rgb_color((x, y)) for x, y, _ in palette.colours],
            "brightness": [round(b) for _, _, b in palette.colours],
            "drift_minutes": palette.drift_minutes,
        }
        for scene, palette in PALETTES.items()
    },
}


async def async_register(hass: HomeAssistant) -> None:
    """Serve the script and its loader (versioned by their content, so
    browsers pick up a new one) and register the websocket commands. Once per
    run."""
    content = b"".join([await hass.async_add_executor_job(path.read_bytes) for path in (LOADER, SCRIPT)])
    hass.data[DATA_URL] = f"{LOADER_URL}?v={hashlib.sha256(content).hexdigest()[:8]}"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(URL, str(SCRIPT), cache_headers=True),
            StaticPathConfig(LOADER_URL, str(LOADER), cache_headers=True),
        ]
    )
    for command in (
        websocket_config,
        websocket_claim_dashboard,
        websocket_subscribe,
        websocket_set_scenes,
        websocket_set_dimmer,
        websocket_save_scene,
        websocket_preview_scene,
        websocket_end_preview,
        websocket_delete_scene,
    ):
        websocket_api.async_register_command(hass, command)


@callback
def async_add_script(hass: HomeAssistant) -> Callable[[], None]:
    """Load the script (through its loader) on every frontend page while
    Hue-Lite is set up."""
    url = hass.data[DATA_URL]
    add_extra_js_url(hass, url)
    return lambda: remove_extra_js_url(hass, url)


type Handler = Callable[[HomeAssistant, websocket_api.ActiveConnection, dict[str, Any], HueLite], None]


def _with_hue_lite(handler: Handler) -> websocket_api.WebSocketCommandHandler:
    """A command for the running Hue-Lite, which it's handed; while Hue-Lite
    isn't set up, the answer is an error."""

    @callback
    @functools.wraps(handler)
    def command(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        if (hue_lite := async_get_loaded(hass)) is None:
            connection.send_error(msg["id"], ERR_NOT_LOADED, "Hue-Lite isn't set up")
        else:
            handler(hass, connection, msg, hue_lite)

    return command


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/config"})
@_with_hue_lite
def websocket_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], hue_lite: HueLite) -> None:
    connection.send_result(msg["id"], _config(hue_lite))


@callback
def _config(hue_lite: HueLite) -> dict[str, Any]:
    """What the dashboard needs: the role labels, the labels it can show
    (role labels and labels on lights, their devices and areas; Home Assistant
    only lists every label to admins), Hue-Lite's entity ids, the lights that
    take part and their areas, and the scenes' colours."""
    hass = hue_lite.hass
    devices, areas = dr.async_get(hass), ar.async_get(hass)
    wanted = set(hue_lite.labels.values())
    light_areas: dict[str, str | None] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.domain != LIGHT:
            continue
        wanted |= entry.labels
        device = devices.async_get(entry.device_id) if entry.device_id else None
        if device:
            wanted |= device.labels
        area_id = light_area(hass, entry, device)
        light_areas[entry.entity_id] = area_id
        if area_id and (area := areas.async_get_area(area_id)):
            wanted |= area.labels
    lights = taking_part(hass, hue_lite.labels["exclude"])
    return {
        "roles": hue_lite.labels,
        "labels": [
            {"label_id": label.label_id, "name": label.name, "icon": label.icon}
            for label in lr.async_get(hass).async_list_labels()
            if label.label_id in wanted
        ],
        "entities": hue_lite.entity_ids,
        # The lights that take part, for the dashboard to show, and the area
        # each is in (a child device's can come from its parent).
        "lights": lights,
        "areas": {light: light_areas.get(light) for light in lights},
        "presets": SCENE_COLOURS,
        # The built-in scenes' usual order.
        "scenes": DEFAULT_ORDER,
        "dimmers": [
            {"id": device.id, "name": device.name_by_user or device.name or "Hue dimmer", "area_id": device.area_id}
            for device in devices.devices
            if is_dimmer(device)
        ]
        if hue_lite.use_dimmers
        else [],
        # Whether an admin's browser should add the Lights dashboard (see
        # websocket_claim_dashboard).
        "add_dashboard": hue_lite.use_dashboard and not hue_lite.dashboard_added,
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/claim_dashboard",
        vol.Optional("release", default=False): bool,
        vol.Optional("error"): vol.All(str, vol.Length(max=300)),
    }
)
@websocket_api.require_admin
@_with_hue_lite
def websocket_claim_dashboard(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], hue_lite: HueLite
) -> None:
    """The Lights dashboard is added by an admin's browser, with Home
    Assistant's own dashboard commands. The first to claim it adds it (or
    finds one already there); `release` gives the claim back if that failed,
    with the `error`, which goes in the log."""
    if error := msg.get("error"):
        # Control characters become spaces, so the browser's text stays on one log line.
        _LOGGER.warning("Couldn't add the Lights dashboard (it'll be tried again): %s", re.sub(r"[\x00-\x1f\x7f]", " ", error))
    connection.send_result(msg["id"], {"claimed": hue_lite.claim_dashboard(msg["release"])})


# Scene ids: the built-in ones or your own (checked when the command's
# handled: see _known).
SCENE_IDS = vol.All(cv.ensure_list, [vol.All(str, vol.Length(1, 64))], vol.Length(max=len(TILE_SCENES) + MAX_CUSTOM))
SCENE_SET = vol.Any(None, {vol.Required("order"): SCENE_IDS, vol.Required("hidden"): SCENE_IDS})


def _known(hue_lite: HueLite, scenes: dict[str, list[str]] | None) -> bool:
    """Whether a set of scenes ({order, hidden}) names only scenes there are."""
    tiles = hue_lite.tile_scenes()
    return scenes is None or all(s in tiles for s in (*scenes["order"], *scenes["hidden"]))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@_with_hue_lite
def websocket_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], _: HueLite) -> None:
    """The shared settings, now and whenever they change (from any dashboard,
    and after Hue-Lite reloads)."""

    @callback
    def send() -> None:
        if hue_lite := async_get_loaded(hass):
            connection.send_message(websocket_api.event_message(msg["id"], hue_lite.shared()))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_SHARED, send)
    connection.send_result(msg["id"])
    send()


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/set_scenes", vol.Required("key"): vol.All(str, vol.Length(1, 255)), vol.Required("scenes"): SCENE_SET}
)
@_with_hue_lite
def websocket_set_scenes(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], hue_lite: HueLite) -> None:
    """Set a room's or light's scenes for every dashboard, or with None put
    them back to the default. Anyone who may control a light can: it's only
    which scenes show (a read-only user can't)."""
    if not _may_edit_shared(connection, hue_lite):
        connection.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, "You can't control any lights")
        return
    if not _known(hue_lite, msg["scenes"]):
        connection.send_error(msg["id"], ERR_UNKNOWN_SCENE, "There's no such scene")
        return
    if msg["scenes"] is not None and msg["key"] not in hue_lite.layouts and len(hue_lite.layouts) >= MAX_LAYOUTS:
        connection.send_error(msg["id"], ERR_TOO_MANY, "Too many rooms and lights with their own scenes")
        return
    hue_lite.set_layout(msg["key"], scene_set(msg["scenes"], hue_lite.tile_scenes()))
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_dimmer",
        vol.Required("device_id"): str,
        vol.Optional("lights"): vol.Any(None, vol.All(cv.entity_ids, vol.Length(max=100))),
        vol.Optional("scenes"): SCENE_SET,
    }
)
@websocket_api.require_admin
@_with_hue_lite
def websocket_set_dimmer(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], hue_lite: HueLite) -> None:
    """Choose the lights a Hue dimmer controls (none: its area) or the scenes
    its On button steps through (None: the default), which must leave at
    least one shown. Admins only: a dimmer controls lights for whoever
    presses it."""
    device = dr.async_get(hass).async_get(msg["device_id"])
    if device is None or not is_dimmer(device):
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "That isn't a Hue dimmer")
        return
    if not _known(hue_lite, msg.get("scenes")):
        connection.send_error(msg["id"], ERR_UNKNOWN_SCENE, "There's no such scene")
        return
    if lights := msg.get("lights"):
        lights = [light for light in lights if light.startswith(f"{LIGHT}.")]
    changes = {key: msg[key] for key in ("lights", "scenes") if key in msg}
    if changes.get("scenes"):
        changes["scenes"] = scenes = scene_set(changes["scenes"], hue_lite.tile_scenes())
        if not set(scenes["order"]) - set(scenes["hidden"]):
            connection.send_error(msg["id"], ERR_NO_SCENES, "A dimmer needs at least one scene to step through")
            return
    if "lights" in changes:
        changes["lights"] = lights or None
    hue_lite.set_dimmer(device.id, **changes)
    connection.send_result(msg["id"])


# The colours a Hue colour bulb can show (gamut C); picked colours outside it
# are brought inside.
HUE_GAMUT = color_util.GamutType(
    color_util.XYPoint(0.6915, 0.3083), color_util.XYPoint(0.17, 0.7), color_util.XYPoint(0.1532, 0.0475)
)
PERCENT = vol.All(int, vol.Range(1, 100))
# A scene's colours as the editor sends them: RGB with a brightness each, and
# optionally the white for white bulbs.
COLOURS = {
    vol.Required("colours"): vol.All(
        [{vol.Required("rgb"): vol.All([vol.All(int, vol.Range(0, 255))], vol.Length(3, 3)), vol.Required("brightness"): PERCENT}],
        vol.Length(1, 8),
    ),
    vol.Optional("white"): {vol.Required("brightness"): PERCENT, vol.Required("kelvin"): vol.All(int, vol.Range(2000, 6500))},
}


def _colours(msg: dict[str, Any]) -> dict[str, list]:
    """The editor's colours as stored: each as [x, y, brightness] inside Hue's
    gamut, and `white` as [brightness, kelvin] (by default the first colour's
    nearest white, at the colours' average brightness)."""
    colours = [
        [*(round(v, 4) for v in color_util.color_RGB_to_xy(*c["rgb"], HUE_GAMUT)), c["brightness"]] for c in msg["colours"]
    ]
    white = msg.get("white") or {
        "brightness": round(sum(c["brightness"] for c in msg["colours"]) / len(colours)),
        "kelvin": kelvin_for(tuple(colours[0][:2])),
    }
    return {"colours": colours, "white": [white["brightness"], white["kelvin"]]}


def _may_control(connection: websocket_api.ActiveConnection, hue_lite: HueLite, lights: list[str]) -> bool:
    """Whether the user may control all these lights (see targets.may_control)."""
    return may_control(hue_lite.hass, connection.user, lights)


def _may_edit_shared(connection: websocket_api.ActiveConnection, hue_lite: HueLite) -> bool:
    """Whether the user may change what every dashboard shares: they may
    control some light (a read-only user can't)."""
    user = connection.user
    return user.is_admin or any(user.permissions.check_entity(light, POLICY_CONTROL) for light in hue_lite.hass.states.async_entity_ids(LIGHT))


def _on_a_dimmer(hue_lite: HueLite, scene_id: str) -> bool:
    """Whether some Hue dimmer's On button steps through one of your own
    scenes (it's in the dimmer's scenes, and not hidden there)."""
    return any(
        scene_id in scenes["order"] and scene_id not in scenes["hidden"]
        for settings in hue_lite.dimmer_settings.values()
        if (scenes := settings.get("scenes"))
    )


def _why_not_change(connection: websocket_api.ActiveConnection, hue_lite: HueLite, scene_id: str) -> str | None:
    """Why the user may not change or delete one of your own scenes, or None
    if they may: they must be able to control lights at all, and every light
    in it; and only an admin may change one a dimmer steps through (a dimmer
    controls lights for whoever presses it, so only admins set dimmers)."""
    if not _may_edit_shared(connection, hue_lite):
        return "You can't control any lights"
    if not connection.user.is_admin and _on_a_dimmer(hue_lite, scene_id):
        return "Only an admin can change a scene a dimmer steps through"
    if not _may_control(connection, hue_lite, hue_lite.in_scene(scene_id)):
        return "That scene is on lights you can't control"
    return None


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_scene",
        vol.Optional("scene"): vol.All(str, vol.Length(1, 64)),
        vol.Required("name"): vol.All(str, vol.Length(1, 30)),
        vol.Required("icon"): vol.All(str, vol.Length(max=64)),
        **COLOURS,
        vol.Optional("drift_minutes", default=3): vol.All(int, vol.Range(1, 60)),
        vol.Optional("show"): {
            vol.Optional("layouts", default=list): vol.All([vol.All(str, vol.Length(1, 255))], vol.Length(max=100)),
            vol.Optional("dimmers", default=list): vol.All([str], vol.Length(max=20)),
        },
    }
)
@_with_hue_lite
def websocket_save_scene(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], hue_lite: HueLite) -> None:
    """Add one of your own scenes (no `scene`) or change one (`scene`: its
    id). Its colours come as RGB with a brightness each (one colour: static),
    and white bulbs get `white` (by default: the first colour's nearest white,
    at the colours' average brightness). A new one is shown where `show` says
    (the room, lights and dimmers it was made for).

    Anyone who may control a light can add one, and change one unless it's on
    lights they may not control or a dimmer steps through it (then only an
    admin can). Only an admin's new scene joins a dimmer's, as only admins
    set dimmers. Two of your own scenes can't share a name, as actions can
    name them."""
    scene_id = msg.get("scene")
    if scene_id is not None and scene_id not in hue_lite.custom:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "That isn't one of your scenes")
        return
    if scene_id is not None and (why := _why_not_change(connection, hue_lite, scene_id)):
        connection.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, why)
        return
    if scene_id is None and not _may_edit_shared(connection, hue_lite):
        connection.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, "You can't control any lights")
        return
    if scene_id is None and len(hue_lite.custom) >= MAX_CUSTOM:
        connection.send_error(msg["id"], ERR_TOO_MANY, "Too many scenes of your own")
        return
    scene = custom_scene({"name": msg["name"], "icon": msg["icon"], **_colours(msg), "drift_minutes": msg["drift_minutes"]})
    if scene is None:
        connection.send_error(msg["id"], ERR_INVALID, "That isn't a scene")
        return
    name = scene["name"].casefold()
    if any(other["name"].casefold() == name for other_id, other in hue_lite.custom.items() if other_id != scene_id):
        connection.send_error(msg["id"], ERR_NAME_TAKEN, "There's a scene called that already")
        return
    show = msg.get("show") or {}
    devices = dr.async_get(hass)
    keys = list(dict.fromkeys(show.get("layouts", [])))
    # New rooms and lights only while there's room for them.
    new = [k for k in keys if k not in hue_lite.layouts][: max(MAX_LAYOUTS - len(hue_lite.layouts), 0)]
    show = {
        "layouts": [k for k in keys if k in hue_lite.layouts or k in new],
        "dimmers": [
            d for d in show.get("dimmers", []) if connection.user.is_admin and (device := devices.async_get(d)) and is_dimmer(device)
        ],
    }
    connection.send_result(msg["id"], {"id": hue_lite.save_custom_scene(scene_id, scene, show)})


# The most lights colours are tried on at once (Whole home in a big house).
MAX_PREVIEW = 500


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/preview_scene", vol.Required("lights"): vol.All(cv.entity_ids, vol.Length(1, MAX_PREVIEW)), **COLOURS}
)
@_with_hue_lite
def websocket_preview_scene(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], hue_lite: HueLite) -> None:
    """Show colours being picked in the scene editor on the lights it was
    opened for, as a colour scene spreads them. The lights leave their scene,
    as if set by hand. Only lights the user may control (as with the
    actions), and not excluded ones (which the dashboard doesn't show)."""
    lights = [
        light
        for light in hue_lite.lights({ATTR_ENTITY_ID: msg["lights"]})
        if not hue_lite.excluded(light) and _may_control(connection, hue_lite, [light])
    ]
    palette = palette_from_stored({**_colours(msg), "drift_minutes": 3})
    hue_lite.spawn(hue_lite.async_preview(lights, palette, connection.context(msg)))
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/end_preview",
        vol.Required("lights"): vol.All(cv.entity_ids, vol.Length(1, MAX_PREVIEW)),
        vol.Required("restore"): bool,
    }
)
@_with_hue_lite
def websocket_end_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], hue_lite: HueLite) -> None:
    """The scene editor closed after trying colours on these lights: saved,
    or cancelled (`restore`), when they go back to how they were. Only
    lights the user may control, as with trying colours."""
    lights = [light for light in msg["lights"] if _may_control(connection, hue_lite, [light])]
    hue_lite.spawn(hue_lite.async_end_preview(lights, msg["restore"], connection.context(msg)))
    connection.send_result(msg["id"])


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/delete_scene", vol.Required("scene"): vol.All(str, vol.Length(1, 64))})
@_with_hue_lite
def websocket_delete_scene(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], hue_lite: HueLite) -> None:
    """Delete one of your own scenes (the built-in ones can't be). Whoever
    may change it can (deleting it takes its lights out of it, and its
    dimmers stop stepping through it)."""
    if msg["scene"] not in hue_lite.custom:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "That isn't one of your scenes")
        return
    if why := _why_not_change(connection, hue_lite, msg["scene"]):
        connection.send_error(msg["id"], websocket_api.ERR_UNAUTHORIZED, why)
        return
    hue_lite.delete_custom_scene(msg["scene"])
    connection.send_result(msg["id"])
