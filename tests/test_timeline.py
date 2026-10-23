"""The timelines, which match the YAML setup's Jinja macros, and the presets."""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.hue_lite.timeline import (
    PRESETS,
    EveningSettings,
    Light,
    SunTimes,
    evening_routine,
    natural_light,
    next_scene,
    noticeable,
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


def test_white_bulbs_get_their_own_colour_temperature() -> None:
    # Warm all the way down: converting Nightlight's red would give ~4100 K.
    assert [p.kelvin for p in PRESETS.values()] == [6410, 4000, 2890, 2237, 2000, 2000]
    assert PRESETS["nightlight"].white_data() == {"brightness_pct": 1, "color_temp_kelvin": 2000}
    # Blends blend the colour temperature too: halfway through the morning.
    assert natural_light(at("09:45"), SUN).light.kelvin == round((6410 + 4000) / 2)


def test_noticeable() -> None:
    relax = PRESETS["relax"]
    assert not noticeable(relax, relax)
    assert not noticeable(relax, Light(56.9, (0.5047, 0.4124), 2286))
    assert noticeable(relax, Light(57, relax.xy_color, relax.kelvin))
    assert noticeable(relax, Light(56, (0.5050, 0.4152), relax.kelvin))
    assert noticeable(relax, Light(56, relax.xy_color, 2287))


def test_moved_from() -> None:
    # The morning blend moves too little in a minute to count, a new phase always does.
    assert not natural_light(at("08:01"), SUN).moved_from(natural_light(at("08:00"), SUN))
    assert natural_light(at("08:10"), SUN).moved_from(natural_light(at("08:00"), SUN))
    assert natural_light(at("12:00"), SUN).moved_from(natural_light(at("11:59"), SUN))
    # The evening routine also moves when tonight's start or bedtime does.
    later = EveningSettings(**{**SETTINGS.__dict__, "bedtime_weeknight": 23 * 60 + 30})
    now = datetime.fromisoformat("2026-10-22T13:00")
    assert evening_routine(now, SUN.sunset, later).moved_from(evening_routine(now, SUN.sunset, SETTINGS))
    assert not evening_routine(now, SUN.sunset, SETTINGS).moved_from(evening_routine(now, SUN.sunset, SETTINGS))


def test_next_scene() -> None:
    assert next_scene("") == "natural"
    assert next_scene("natural") == "energize"
    assert next_scene("cool_bright") == "read"
    assert next_scene("read") == "relax"
    assert next_scene("rest") == "nightlight"
    assert next_scene("nightlight") == "natural"
    # The colour scenes aren't in the cycle: on to Natural light.
    assert next_scene("nebula") == "natural"
