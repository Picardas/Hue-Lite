"""Sending commands to the lights: they don't pile up behind a slow bulb,
and one that doesn't answer doesn't stop the rest."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.hue_lite.engine import HueLite

from .conftest import Home


async def test_commands_dont_pile_up_behind_a_slow_bulb(hass: HomeAssistant, home: Home, hue_lite: HueLite) -> None:
    """Zigbee sends to a bulb one request at a time, so a bulb that's slow to
    answer would hold up every command after it. While one is on its way,
    newer ones for that light replace each other, and only the latest goes."""
    sender = hue_lite.sender
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
    with patch.object(sender, "_call", slow_bulbs):
        first = hass.async_create_task(sender.call("turn_on", [living_room], brightness_pct=10))
        await until_sent(1)
        # The living room is still being sent its first: these wait, the later
        # replacing the earlier. The bedroom isn't busy, so it's sent straight away.
        await sender.call("turn_on", [living_room], brightness_pct=20)
        third = hass.async_create_task(sender.call("turn_on", [living_room, bedroom], brightness_pct=30))
        await until_sent(2)
        assert sent == [([living_room], 10), ([bedroom], 30)]

        answer.set()
        await asyncio.gather(first, third)
    assert sent == [([living_room], 10), ([bedroom], 30), ([living_room], 30)]


async def test_a_bulb_that_doesnt_answer(hass: HomeAssistant, home: Home, hue_lite: HueLite, caplog: pytest.LogCaptureFixture) -> None:
    """It's logged, and the next command for it still goes."""
    tried: list[ServiceCall] = []

    async def unreachable(call: ServiceCall) -> None:
        tried.append(call)
        raise HomeAssistantError("Failed to send request: device did not respond")

    hass.services.async_register("light", "turn_off", unreachable)
    for _ in range(2):
        await hue_lite.sender.call("turn_off", ["light.bedroom"], transition=1)
    assert len(tried) == 2
    assert "Couldn't turn off light.bedroom: Failed to send request: device did not respond" in caplog.text
