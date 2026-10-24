"""Hue-Lite's actions: a preset or colour scene, the Natural light scene, the
next scene (a Hue dimmer's On button), Auto, and dimming that keeps a light's
scene. Each takes a light target; none means every light."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID, ATTR_FLOOR_ID, ATTR_LABEL_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError, Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .engine import HueLite, async_get_loaded

_IDS = vol.All(cv.ensure_list, [cv.string])

TARGET_KEYS = (ATTR_ENTITY_ID, ATTR_AREA_ID, ATTR_FLOOR_ID, ATTR_DEVICE_ID, ATTR_LABEL_ID)
TARGET = {vol.Optional(key): cv.entity_ids if key == ATTR_ENTITY_ID else _IDS for key in TARGET_KEYS}


@callback
def async_register(hass: HomeAssistant) -> None:
    async def targeted(call: ServiceCall) -> tuple[HueLite, list[str]]:
        """The running Hue-Lite and the call's lights, checked against the
        caller's permissions the way Home Assistant checks its own light
        actions: Hue-Lite then changes them as itself, which Home Assistant
        doesn't check. With a target, every light must be allowed; without
        one, only the allowed ones count."""
        if (running := async_get_loaded(hass)) is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
        found = running.lights(call.data)
        if not call.context.user_id:
            return running, found
        if (user := await hass.auth.async_get_user(call.context.user_id)) is None:
            raise UnknownUser(context=call.context)
        if user.is_admin:
            return running, found
        denied = [light for light in found if not user.permissions.check_entity(light, POLICY_CONTROL)]
        if denied and any(call.data.get(key) for key in TARGET_KEYS):
            raise Unauthorized(context=call.context, entity_id=denied[0], permission=POLICY_CONTROL)
        return running, [light for light in found if light not in denied]

    async def apply_preset(call: ServiceCall) -> None:
        running, lights = await targeted(call)
        if not running.is_scene(preset := call.data["preset"]):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unknown_scene", translation_placeholders={"scene": preset}
            )
        await running.async_preset(lights, preset, parent=call.context)

    async def natural_light(call: ServiceCall) -> None:
        running, lights = await targeted(call)
        await running.async_natural_light(lights, call.data["follow"], parent=call.context)

    async def next_scene(call: ServiceCall) -> None:
        running, lights = await targeted(call)
        await running.async_next_scene(lights, parent=call.context)

    async def set_auto(call: ServiceCall) -> None:
        running, lights = await targeted(call)
        running.async_set_auto(lights, call.data["auto"])

    async def set_brightness(call: ServiceCall) -> None:
        running, lights = await targeted(call)
        await running.async_set_brightness(lights, call.data["brightness_pct"], parent=call.context)

    hass.services.async_register(
        DOMAIN,
        "apply_preset",
        apply_preset,
        # Your own scenes too, so they're checked when called.
        vol.Schema({vol.Required("preset"): cv.string, **TARGET}),
    )
    hass.services.async_register(
        DOMAIN, "natural_light", natural_light, vol.Schema({vol.Optional("follow", default=True): cv.boolean, **TARGET})
    )
    hass.services.async_register(DOMAIN, "next_scene", next_scene, vol.Schema(TARGET))
    hass.services.async_register(DOMAIN, "set_auto", set_auto, vol.Schema({vol.Required("auto"): cv.boolean, **TARGET}))
    hass.services.async_register(
        DOMAIN,
        "set_brightness",
        set_brightness,
        vol.Schema({vol.Required("brightness_pct"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)), **TARGET}),
    )
