"""Sending commands to the lights: they don't pile up behind a slow bulb."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from homeassistant.core import Context, HomeAssistant

from custom_components.hue_lite.engine import HueLite

from .conftest import Home


async def test_commands_dont_pile_up_behind_a_slow_bulb(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    """Zigbee sends to a bulb one request at a time, so a bulb that's slow to
    answer would hold up every command after it. While one is on its way,
    newer ones for that light replace each other, and only the latest goes."""
    answer = asyncio.Event()
    sent: list[tuple[list[str], int]] = []

    async def slow_bulbs(service: str, data: dict[str, Any], parent: Context | None, lights: list[str]) -> None:
        sent.append((lights, data["brightness_pct"]))
        await answer.wait()

    async def until_sent(count: int) -> None:
        # Not asyncio.sleep: the tests replace it (see conftest's no_sleep).
        for _ in range(100):
            if len(sent) >= count:
                return
            turn = hass.loop.create_future()
            hass.loop.call_soon(turn.set_result, None)
            await turn

    living_room, bedroom = "light.living_room", "light.bedroom"
    with patch.object(hue_lite, "_send", slow_bulbs):
        first = hass.async_create_task(hue_lite.call("turn_on", [living_room], brightness_pct=10))
        await until_sent(1)
        # The living room is still being sent its first: these wait, the later
        # replacing the earlier. The bedroom isn't busy, so it's sent straight away.
        await hue_lite.call("turn_on", [living_room], brightness_pct=20)
        third = hass.async_create_task(hue_lite.call("turn_on", [living_room, bedroom], brightness_pct=30))
        await until_sent(2)
        assert sent == [([living_room], 10), ([bedroom], 30)]

        answer.set()
        await asyncio.gather(first, third)
    assert sent == [([living_room], 10), ([bedroom], 30), ([living_room], 30)]
