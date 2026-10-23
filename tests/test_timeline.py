"""The timelines and scene recognition, which match SPEC.md's Jinja macros."""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.hue_lite.timeline import (
    PRESETS,
    EveningSettings,
    Light,
    SunTimes,
    current_scene,
    evening_routine,
    matches,
    natural_light,
    next_scene,
)

SUN = SunTimes()  # dawn 06:00, sunrise 06:30, noon 12:00, sunset 18:00, dusk 18:30
SETTINGS = EveningSettings(
    start_before_sunset=90, brighten=60, wind_down=120, nightlight=30,
    bedtime_weeknight=23 * 60, bedtime_weekend=30,
)


def at(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


@pytest.mark.parametrize(
    ("time", "phase", "light"),
    [
        ("03:00", "night", PRESETS["rest"]),
        ("05:59", "night", PRESETS["rest"]),
        ("06:00", "sunrise", PRESETS["rest"]),  # the 5-minute fade into Energize starts at dawn
        ("06:05", "sunrise", PRESETS["energize"]),
        ("07:29", "sunrise", PRESETS["energize"]),
        ("07:30", "morning", PRESETS["energize"]),
        ("12:00", "day", PRESETS["cool_bright"]),
        ("16:29", "day", PRESETS["cool_bright"]),
        ("16:30", "sunset", PRESETS["cool_bright"]),
        ("18:00", "evening", PRESETS["relax"]),
        ("19:30", "night", PRESETS["rest"]),
    ],
)
def test_natural_light(time: str, phase: str, light: Light) -> None:
    target = natural_light(at(time), SUN)
    assert target.phase == phase
    assert target.light == light.rounded()


def test_natural_light_blends() -> None:
    # Halfway through the morning (07:30 -> 12:00) is halfway from Energize to Cool bright.
    target = natural_light(at("09:45"), SUN)
    assert target.light.brightness_pct == 100
    assert target.light.xy_color == pytest.approx(((0.3143 + 0.3812) / 2, (0.3301 + 0.3794) / 2), abs=1e-4)
    # Halfway through the evening (18:00 -> 19:30) is halfway from Relax to Rest.
    assert natural_light(at("18:45"), SUN).light.brightness_pct == round((56 + 35) / 2)


def evening(when: str) -> tuple[str, int, str, str]:
    e = evening_routine(datetime.fromisoformat(when), SUN.sunset, SETTINGS)
    return e.phase, e.light.brightness_pct, e.start, e.bedtime


def test_evening_routine_weeknight() -> None:
    # Thursday: starts 90 min before sunset, bedtime 23:00.
    assert evening("2026-10-22T12:00") == ("day", 5, "16:30", "23:00")
    assert evening("2026-10-22T16:29") == ("day", 5, "16:30", "23:00")
    assert evening("2026-10-22T16:30") == ("evening", 5, "16:30", "23:00")
    assert evening("2026-10-22T17:00") == ("evening", round(5 + 95 / 2), "16:30", "23:00")
    assert evening("2026-10-22T17:30")[:2] == ("evening", 100)
    # Wind down from 21:00: Cool bright -> Relax by 21:45, Relax -> Rest by 22:30.
    assert evening("2026-10-22T21:00")[:2] == ("wind_down", 100)
    assert evening("2026-10-22T21:45")[:2] == ("wind_down", 56)
    assert evening("2026-10-22T22:30")[:2] == ("nightlight", 35)
    assert evening("2026-10-22T23:00")[0] == "night"
    assert evening("2026-10-23T04:59")[0] == "night"
    assert evening("2026-10-23T05:00")[0] == "day"


def test_evening_routine_weekend_bedtime_after_midnight() -> None:
    # Friday night's bedtime is 00:30 on Saturday, and still belongs to Friday.
    assert evening("2026-10-23T23:30")[::3] == ("wind_down", "00:30")
    assert evening("2026-10-24T00:15")[::3] == ("nightlight", "00:30")
    assert evening("2026-10-24T00:30")[0] == "night"
    # Sunday night is a weeknight again.
    assert evening("2026-10-25T23:00")[::3] == ("night", "23:00")


def state(xy: tuple[float, float], brightness_pct: float, color_mode: str = "xy") -> dict:
    return {"color_mode": color_mode, "xy_color": xy, "brightness": round(brightness_pct * 2.55)}


def test_matches() -> None:
    relax = PRESETS["relax"]
    assert matches(state((0.50, 0.42), 56), relax)
    assert matches(state((0.52, 0.40), 58), relax)
    assert not matches(state((0.54, 0.42), 56), relax)  # colour too far off
    assert not matches(state((0.50, 0.42), 60), relax)  # brightness too far off
    assert not matches(state((0.50, 0.42), 56, "color_temp"), relax)
    assert not matches({"color_mode": "xy", "brightness": 143}, relax)


def test_current_and_next_scene() -> None:
    rest = state((0.561, 0.4042), 35)
    assert current_scene([], set()) == ""
    assert current_scene([("light.a", rest), ("light.b", rest)], {"light.a", "light.b"}) == "natural"
    assert current_scene([("light.a", rest), ("light.b", rest)], {"light.a"}) == "rest"
    assert current_scene([("light.a", rest), ("light.b", state((0.3, 0.3), 100))], set()) == ""

    assert next_scene("") == "natural"
    assert next_scene("natural") == "energize"
    assert next_scene("rest") == "nightlight"
    assert next_scene("nightlight") == "natural"
