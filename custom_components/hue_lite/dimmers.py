"""Hue Dimmer Switches (RWL02x, paired with ZHA): each controls the lights in
its area. On: next scene. Off: off. Up and Down: 10% brighter or dimmer, for
the lights that are on (Down stops at about 10%), which counts as a change by
hand."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import ATTR_AREA_ID
from homeassistant.core import CALLBACK_TYPE, Context, Event, callback
from homeassistant.helpers import device_registry as dr

from .const import DIMMER_COMMANDS, DIMMER_MODEL_PREFIX

if TYPE_CHECKING:
    from .engine import HueLite


@callback
def async_listen(lite: HueLite) -> CALLBACK_TYPE:
    @callback
    def pressed(event: Event) -> None:
        command = event.data.get("command")
        if command not in DIMMER_COMMANDS:
            return
        device = dr.async_get(lite.hass).async_get(event.data.get("device_id") or "")
        if device is None or not (device.model or "").startswith(DIMMER_MODEL_PREFIX) or device.area_id is None:
            return
        lights = lite.lights({ATTR_AREA_ID: [device.area_id]})
        if lights:
            lite.spawn(_press(lite, command.split("_")[0], lights, event.context))

    return lite.hass.bus.async_listen("zha_event", pressed)


async def _press(lite: HueLite, button: str, lights: list[str], parent: Context) -> None:
    if button == "on":
        await lite.async_next_scene(lights, parent)
    elif button == "off":
        await lite.call("turn_off", lights, parent=parent, transition=1)
    else:
        up = button == "up"
        dimming = [
            light
            for light in lite.on(lights)
            if up or ((state := lite.hass.states.get(light)) and (state.attributes.get("brightness") or 0) > 26)
        ]
        if dimming:
            lite.set_scene(dimming, "")
            await lite.call("turn_on", dimming, parent=parent, brightness_step_pct=10 if up else -10, transition=0.4)
