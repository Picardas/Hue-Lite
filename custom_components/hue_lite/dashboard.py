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

from .const import DOMAIN
from .engine import async_get_loaded

SCRIPT = Path(__file__).parent / "frontend" / "hue-lite.js"
URL = f"/{DOMAIN}/hue-lite.js"
DATA_URL = f"{DOMAIN}_dashboard_url"


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
    """Hue-Lite's role labels, all labels, presets and entity ids."""
    if (hue_lite := async_get_loaded(hass)) is None:
        connection.send_error(msg["id"], "not_loaded", "Hue-Lite isn't set up")
        return
    connection.send_result(msg["id"], hue_lite.frontend_config())
