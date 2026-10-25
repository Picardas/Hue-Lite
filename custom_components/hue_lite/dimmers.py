"""Hue Dimmer Switches (RWL02x, paired with ZHA): each controls the lights in
its area, or the ones chosen for it on the dashboard. On: the next of its
scenes (as chosen on the dashboard; by default Natural light and the
presets), stepping on with quick presses. Off: off, so On starts again. Up and Down: 10% brighter or dimmer, for the lights that
are on (Down stops at about 10%), keeping them in their scene at that level."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
        if device is None or not is_dimmer(device):
            return
        if lights := lite.dimmer_lights(device):
            lite.spawn(_press(lite, command.split("_")[0], lights, event.context, lite.dimmer_cycle(device.id)))

    return lite.hass.bus.async_listen("zha_event", pressed)


def is_dimmer(device: dr.DeviceEntry) -> bool:
    return (device.model or "").startswith(DIMMER_MODEL_PREFIX)


async def _press(lite: HueLite, button: str, lights: list[str], parent: Context, cycle: list[str]) -> None:
    if button == "on":
        await lite.async_next_scene(lights, parent, cycle)
    elif button == "off":
        lite.forget_steps(lights)
        await lite.sender.call("turn_off", lights, parent=parent, transition=1)
    else:
        up = button == "up"
        dimming = [
            light
            for light in lite.on(lights)
            if up or ((state := lite.hass.states.get(light)) and (state.attributes.get("brightness") or 0) > 26)
        ]
        if dimming:
            await lite.async_set_brightness(dimming, step_pct=10 if up else -10, parent=parent)
