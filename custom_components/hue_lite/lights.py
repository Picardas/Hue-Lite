"""Talking to the bulbs: sending them their light through Home Assistant's
light actions, called as Hue-Lite (a context of its own, so its changes
aren't taken for someone else's) and without commands piling up behind a slow
bulb; and the colours they report, for the dashboard."""

from __future__ import annotations

import asyncio
from collections import deque
import logging
from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.color import color_xy_to_RGB

from .targets import LIGHT
from .timeline import Light

_LOGGER = logging.getLogger(__name__)

COLOUR_MODES = frozenset({"hs", "xy", "rgb", "rgbw", "rgbww"})

# A light action: its service, data, and what caused it.
type Command = tuple[str, dict[str, Any], Context | None]


def rgb_color(xy: tuple[float, float]) -> list[int]:
    """The RGB colour a colour bulb reports at an xy colour (Home Assistant
    works it out the same way), for the dashboard to show."""
    return list(color_xy_to_RGB(*xy))


class Sender:
    """Calls light actions as Hue-Lite: a context with no user, so the change
    isn't taken for someone else's, linked to what caused it (an action call,
    a dimmer press) so the logbook can say.

    Commands don't pile up behind a slow or unreachable bulb (Zigbee sends to
    a bulb one request at a time, and an unanswered one takes seconds to fail,
    holding up everything after it, yours included): a command for a light
    that's still being sent the last one waits, replacing any already
    waiting, so only the latest goes."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # Contexts of Hue-Lite's own light changes: enough that a bulb's slow
        # report isn't forgotten (and taken for someone else's change) while a
        # big home is sent a burst of commands.
        self._ours: deque[str] = deque(maxlen=1000)
        # Lights a command is on its way to, and the latest command waiting for each.
        self._sending: set[str] = set()
        self._waiting: dict[str, Command] = {}

    def is_ours(self, context: Context) -> bool:
        """Whether a change came from Hue-Lite."""
        return context.id in self._ours

    def has_colour(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        modes = state.attributes.get("supported_color_modes") if state else None
        # Unknown: send colour, which Home Assistant converts if it has to.
        return modes is None or bool(COLOUR_MODES.intersection(modes))

    async def send(self, lights: list[str], light: Light, transition: float, parent: Context | None = None) -> None:
        """Turn lights on at `light`: its xy colour on bulbs with colour, its
        colour temperature on the rest."""
        colour = [entity_id for entity_id in lights if self.has_colour(entity_id)]
        white = [entity_id for entity_id in lights if entity_id not in colour]
        await asyncio.gather(
            *([self.call("turn_on", colour, parent=parent, **light.colour_data(), transition=transition)] if colour else []),
            *([self.call("turn_on", white, parent=parent, **light.white_data(), transition=transition)] if white else []),
        )

    async def call(self, service: str, lights: list[str], *, parent: Context | None = None, **data: Any) -> None:
        """Call a light action (`turn_on` or `turn_off`) for lights."""
        command = (service, data, parent)
        lights = list(dict.fromkeys(lights))
        for light in lights:
            self._waiting[light] = command
        await self._send_waiting([light for light in lights if light not in self._sending])

    async def _send_waiting(self, lights: list[str]) -> None:
        """Send lights their waiting commands (lights waiting on the same one
        together), then any that came in for them meanwhile."""
        while lights:
            commands: dict[int, tuple[Command, list[str]]] = {}
            for light in lights:
                command = self._waiting.pop(light)
                commands.setdefault(id(command), (command, []))[1].append(light)
            self._sending.update(lights)
            try:
                await asyncio.gather(*(self._call(*command, group) for command, group in commands.values()))
            finally:
                self._sending.difference_update(lights)
            lights = [light for light in lights if light in self._waiting]

    async def _call(self, service: str, data: dict[str, Any], parent: Context | None, lights: list[str]) -> None:
        """One light action call. A bulb that doesn't answer (or a command it
        won't take) is logged and doesn't stop the rest."""
        context = Context(parent_id=parent.id if parent else None)
        self._ours.append(context.id)
        try:
            await self.hass.services.async_call(
                LIGHT, service, {ATTR_ENTITY_ID: lights, **data}, blocking=True, context=context
            )
        except (HomeAssistantError, vol.Invalid) as err:
            _LOGGER.warning("Couldn't %s %s: %s", service.replace("_", " "), ", ".join(lights), err)
