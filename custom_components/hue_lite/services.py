"""Hue-Lite's actions: a preset, the Natural light scene, and the next scene
(a Hue dimmer's On button). Each takes a light target; none means every light."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID, ATTR_FLOOR_ID, ATTR_LABEL_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .engine import HueLite, async_get_loaded
from .timeline import PRESETS

_IDS = vol.All(cv.ensure_list, [cv.string])

TARGET = {
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    vol.Optional(ATTR_AREA_ID): _IDS,
    vol.Optional(ATTR_FLOOR_ID): _IDS,
    vol.Optional(ATTR_DEVICE_ID): _IDS,
    vol.Optional(ATTR_LABEL_ID): _IDS,
}


@callback
def async_register(hass: HomeAssistant) -> None:
    def hue_lite() -> HueLite:
        if (running := async_get_loaded(hass)) is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
        return running

    async def apply_preset(call: ServiceCall) -> None:
        running = hue_lite()
        await running.async_preset(running.lights(call.data), call.data["preset"])

    async def natural_light(call: ServiceCall) -> None:
        running = hue_lite()
        await running.async_natural_light(running.lights(call.data), call.data["follow"])

    async def next_scene(call: ServiceCall) -> None:
        running = hue_lite()
        await running.async_next_scene(running.lights(call.data))

    hass.services.async_register(
        DOMAIN, "apply_preset", apply_preset, vol.Schema({vol.Required("preset"): vol.In(list(PRESETS)), **TARGET})
    )
    hass.services.async_register(
        DOMAIN, "natural_light", natural_light, vol.Schema({vol.Optional("follow", default=True): cv.boolean, **TARGET})
    )
    hass.services.async_register(DOMAIN, "next_scene", next_scene, vol.Schema(TARGET))
