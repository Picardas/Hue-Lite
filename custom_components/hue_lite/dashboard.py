"""The Lights dashboard: serves its script (a dashboard strategy and its cards)
to every page, and over the websocket tells it how Hue-Lite is set up and
keeps the settings every dashboard shares (each room's and light's scenes,
each dimmer's lights and scenes), sending them to the dashboards open as
they change."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_SHARED
from .dimmers import is_dimmer
from .engine import TILE_SCENES, HueLite, async_get_loaded
from .lights import rgb_color
from .targets import LIGHT
from .timeline import PALETTES, PRESETS

_LOGGER = logging.getLogger(__name__)

SCRIPT = Path(__file__).parent / "frontend" / "hue-lite.js"
URL = f"/{DOMAIN}/hue-lite.js"
DATA_URL = f"{DOMAIN}_dashboard_url"

# Each preset's and colour scene's colours as a colour bulb reports them, for their tiles.
SCENE_COLOURS = {
    **{preset: {"rgb_colors": [rgb_color(light.xy_color)]} for preset, light in PRESETS.items()},
    **{scene: {"rgb_colors": [rgb_color(xy) for xy in palette.colours]} for scene, palette in PALETTES.items()},
}


async def async_register(hass: HomeAssistant) -> None:
    """Serve the script (versioned by its content, so browsers pick up a new
    one) and register the websocket command. Once per run."""
    content = await hass.async_add_executor_job(SCRIPT.read_bytes)
    hass.data[DATA_URL] = f"{URL}?v={hashlib.sha256(content).hexdigest()[:8]}"
    await hass.http.async_register_static_paths([StaticPathConfig(URL, str(SCRIPT), True)])
    for command in (websocket_config, websocket_claim_dashboard, websocket_subscribe, websocket_set_scenes, websocket_set_dimmer):
        websocket_api.async_register_command(hass, command)


@callback
def async_add_script(hass: HomeAssistant) -> Callable[[], None]:
    """Load the script on every frontend page while Hue-Lite is set up."""
    url = hass.data[DATA_URL]
    add_extra_js_url(hass, url)
    return lambda: remove_extra_js_url(hass, url)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/config"})
@callback
def websocket_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    if (hue_lite := async_get_loaded(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Hue-Lite isn't set up")
        return
    connection.send_result(msg["id"], _config(hue_lite))


@callback
def _config(hue_lite: HueLite) -> dict[str, Any]:
    """What the dashboard needs: the role labels, the labels it can show
    (role labels and labels on lights, their devices and areas; Home Assistant
    only lists every label to admins), Hue-Lite's entity ids, and the scenes'
    colours."""
    hass = hue_lite.hass
    devices, areas = dr.async_get(hass), ar.async_get(hass)
    wanted = set(hue_lite.labels.values())
    for entry in er.async_get(hass).entities.values():
        if entry.domain != LIGHT:
            continue
        wanted |= entry.labels
        device = devices.async_get(entry.device_id) if entry.device_id else None
        if device:
            wanted |= device.labels
        area_id = entry.area_id or (device.area_id if device else None)
        if area_id and (area := areas.async_get_area(area_id)):
            wanted |= area.labels
    return {
        "roles": hue_lite.labels,
        "labels": [
            {"label_id": label.label_id, "name": label.name, "icon": label.icon}
            for label in lr.async_get(hass).async_list_labels()
            if label.label_id in wanted
        ],
        "entities": hue_lite.entity_ids,
        "presets": SCENE_COLOURS,
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
@callback
def websocket_claim_dashboard(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The Lights dashboard is added by an admin's browser, with Home
    Assistant's own dashboard commands. The first to claim it adds it (or
    finds one already there); `release` gives the claim back if that failed,
    with the `error`, which goes in the log."""
    if (hue_lite := async_get_loaded(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Hue-Lite isn't set up")
        return
    if error := msg.get("error"):
        _LOGGER.warning("Couldn't add the Lights dashboard (it'll be tried again): %s", error)
    connection.send_result(msg["id"], {"claimed": hue_lite.claim_dashboard(msg["release"])})


# The most rooms and lights whose scenes are kept, so the shared settings
# can't grow without end.
MAX_LAYOUTS = 500
SCENE_IDS = vol.All(cv.ensure_list, [vol.In(TILE_SCENES)], vol.Length(max=len(TILE_SCENES)))
SCENES = vol.Any(None, {vol.Required("order"): SCENE_IDS, vol.Required("hidden"): SCENE_IDS})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def websocket_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The shared settings, now and whenever they change (from any dashboard,
    and after Hue-Lite reloads)."""
    if async_get_loaded(hass) is None:
        connection.send_error(msg["id"], "not_loaded", "Hue-Lite isn't set up")
        return

    @callback
    def send() -> None:
        if hue_lite := async_get_loaded(hass):
            connection.send_message(websocket_api.event_message(msg["id"], hue_lite.shared()))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_SHARED, send)
    connection.send_result(msg["id"])
    send()


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/set_scenes", vol.Required("key"): vol.All(str, vol.Length(1, 255)), vol.Required("scenes"): SCENES}
)
@callback
def websocket_set_scenes(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Set a room's or light's scenes for every dashboard, or with None put
    them back to the default. Anyone can: it's only which scenes show."""
    if (hue_lite := async_get_loaded(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Hue-Lite isn't set up")
        return
    if msg["scenes"] is not None and msg["key"] not in hue_lite.layouts and len(hue_lite.layouts) >= MAX_LAYOUTS:
        connection.send_error(msg["id"], "too_many", "Too many rooms and lights with their own scenes")
        return
    hue_lite.set_layout(msg["key"], msg["scenes"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_dimmer",
        vol.Required("device_id"): str,
        vol.Optional("lights"): vol.Any(None, vol.All(cv.entity_ids, vol.Length(max=100))),
        vol.Optional("scenes"): SCENES,
    }
)
@websocket_api.require_admin
@callback
def websocket_set_dimmer(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Choose the lights a Hue dimmer controls (none: its area) or the scenes
    its On button steps through (None: the default). Admins only: a dimmer
    controls lights for whoever presses it."""
    if (hue_lite := async_get_loaded(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Hue-Lite isn't set up")
        return
    device = dr.async_get(hass).async_get(msg["device_id"])
    if device is None or not is_dimmer(device):
        connection.send_error(msg["id"], "not_found", "That isn't a Hue dimmer")
        return
    if lights := msg.get("lights"):
        lights = [light for light in lights if light.startswith(f"{LIGHT}.")]
    changes = {key: msg[key] for key in ("lights", "scenes") if key in msg}
    if "lights" in changes:
        changes["lights"] = lights or None
    hue_lite.set_dimmer(device.id, **changes)
    connection.send_result(msg["id"])
