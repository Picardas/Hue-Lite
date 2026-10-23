"""The Lights dashboard: serves its script (a dashboard strategy and its cards)
to every page, and tells it how Hue-Lite is set up over the websocket."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, label_registry as lr

from .const import DOMAIN
from .engine import HueLite, async_get_loaded
from .lights import rgb_color
from .targets import LIGHT
from .timeline import PALETTES, PRESETS

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
    websocket_api.async_register_command(hass, websocket_config)


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
    }
