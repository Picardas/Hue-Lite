"""The presets and the two timelines: the Natural light scene and the evening routine.

Plain functions of the time of day, with no Home Assistant imports, so they can
be tested on their own. Times of day are minutes after midnight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class Light:
    """A brightness, with an xy colour for colour bulbs and a colour
    temperature for white ones."""

    brightness_pct: float
    xy_color: tuple[float, float]
    kelvin: float

    def rounded(self) -> Light:
        return Light(
            round(self.brightness_pct), (round(self.xy_color[0], 4), round(self.xy_color[1], 4)), round(self.kelvin)
        )

    def colour_data(self) -> dict[str, Any]:
        """light.turn_on data for bulbs with colour."""
        return {"brightness_pct": self.brightness_pct, "xy_color": list(self.xy_color)}

    def white_data(self) -> dict[str, Any]:
        """light.turn_on data for the rest (Home Assistant drops the colour
        temperature for dimmable-only bulbs)."""
        return {"brightness_pct": self.brightness_pct, "color_temp_kelvin": self.kelvin}


# The values the Hue app uses on colour bulbs, as xy colours (which is how Hue
# gets e.g. Energize's blue tinge), from Hue's scene library:
# https://gist.github.com/Hypfer/a0a8b5b9429831a7306ec4300077eaaa
# Nightlight is deliberately redder than Hue's (0.561, 0.4042). White bulbs get
# a colour temperature instead (Energize and Relax as in Hue's white-ambiance
# scenes, Rest and Nightlight at 2000 K, as warm as most bulbs go): converting
# the xy colours would make Nightlight's deep red a cool 4100 K.
PRESETS: dict[str, Light] = {
    "energize": Light(100, (0.3143, 0.3301), 6410),
    "cool_bright": Light(100, (0.3812, 0.3794), 4000),
    "relax": Light(56, (0.5019, 0.4152), 2237),
    "rest": Light(35, (0.561, 0.4042), 2000),
    "nightlight": Light(1, (0.67, 0.32), 2000),
}

# What a Hue dimmer's On button steps through, and round again.
SCENE_CYCLE = ("natural", "energize", "cool_bright", "relax", "rest", "nightlight")


def noticeable(a: Light, b: Light) -> bool:
    """Whether going from a to b is a visible change: 1% brightness, 0.003 in
    either xy coordinate or 50 K. Smaller steps aren't worth a Zigbee command."""
    return (
        abs(a.brightness_pct - b.brightness_pct) >= 1
        or max(abs(a.xy_color[0] - b.xy_color[0]), abs(a.xy_color[1] - b.xy_color[1])) >= 0.003
        or abs(a.kelvin - b.kelvin) >= 50
    )


def blend(a: Light, b: Light, f: float) -> Light:
    """a at f = 0, b at f = 1, linear in between."""
    f = min(max(f, 0), 1)
    return Light(
        a.brightness_pct + (b.brightness_pct - a.brightness_pct) * f,
        (a.xy_color[0] + (b.xy_color[0] - a.xy_color[0]) * f, a.xy_color[1] + (b.xy_color[1] - a.xy_color[1]) * f),
        a.kelvin + (b.kelvin - a.kelvin) * f,
    )


def hhmm(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def minutes(value: str) -> int:
    """Minutes after midnight of "HH:MM" (or "HH:MM:SS")."""
    hour, minute = value.split(":")[:2]
    return int(hour) * 60 + int(minute)


@dataclass(frozen=True)
class Target:
    """Where a timeline is (its phase) and the light it wants."""

    phase: str
    light: Light

    def moved_from(self, other: Target) -> bool:
        """Whether this differs visibly from `other`: another phase or a
        noticeable change of light."""
        return self.phase != other.phase or noticeable(self.light, other.light)


# ---- Natural light scene -----------------------------------------------------


@dataclass(frozen=True)
class SunTimes:
    """Today's sun events (defaults for when the sun doesn't rise or set)."""

    dawn: int = 6 * 60
    sunrise: int = 6 * 60 + 30
    noon: int = 12 * 60
    sunset: int = 18 * 60
    dusk: int = 18 * 60 + 30


def natural_light(minute: int, sun: SunTimes) -> Target:
    """The Natural light scene, following the sun like Hue's:

    night    an hour after dusk until dawn: Rest (Nightlight lights: Nightlight)
    sunrise  dawn to an hour after sunrise: Rest -> Energize over 5 minutes, then Energize
    morning  until solar noon: Energize -> Cool bright
    day      solar noon until 90 min before sunset: Cool bright
    sunset   the last 90 min before sunset: Cool bright -> Relax
    evening  sunset until an hour after dusk: Relax -> Rest
    """
    p = PRESETS
    m = minute
    dawn_end = sun.sunrise + 60
    dusk_end = sun.dusk + 60

    if m < sun.dawn or m >= dusk_end:
        phase, light = "night", p["rest"]
    elif m < dawn_end:
        phase, light = "sunrise", blend(p["rest"], p["energize"], (m - sun.dawn) / 5)
    elif m < sun.noon:
        phase, light = "morning", blend(p["energize"], p["cool_bright"], (m - dawn_end) / max(sun.noon - dawn_end, 1))
    elif m < sun.sunset - 90:
        phase, light = "day", p["cool_bright"]
    elif m < sun.sunset:
        phase, light = "sunset", blend(p["cool_bright"], p["relax"], (m - sun.sunset + 90) / 90)
    else:
        phase, light = "evening", blend(p["relax"], p["rest"], (m - sun.sunset) / max(dusk_end - sun.sunset, 1))
    return Target(phase, light.rounded())


# ---- Evening routine ---------------------------------------------------------


@dataclass(frozen=True)
class EveningSettings:
    """The evening routine's settings: durations and bedtimes in minutes."""

    start_before_sunset: int
    brighten: int
    wind_down: int
    nightlight: int
    bedtime_weeknight: int
    bedtime_weekend: int


@dataclass(frozen=True)
class Evening(Target):
    """The evening routine's phase and light, with tonight's start and bedtime (HH:MM)."""

    start: str
    bedtime: str

    def moved_from(self, other: Target) -> bool:
        if not isinstance(other, Evening):
            return True
        return super().moved_from(other) or (self.start, self.bedtime) != (other.start, other.bedtime)


def evening_routine(now: datetime, sunset: int, s: EveningSettings) -> Evening:
    """Where the evening routine is and the light it wants for its lights:

    day         from 05:00 until the evening starts (set before sunset)
    evening     dim, brightening to Cool bright
    wind_down   Cool bright -> Relax -> Rest, ending when Nightlight starts
    nightlight  Rest (lights labelled Nightlight: Nightlight)
    night       from bedtime until 05:00: lights fade out at bedtime

    The evening runs from noon to noon, so a bedtime after midnight belongs to
    the evening before (Friday's evening ends at 01:00 on Saturday). Friday and
    Saturday nights use the weekend bedtime.
    """
    p = PRESETS
    morning = now.hour < 12
    m = now.hour * 60 + now.minute + (1440 if morning else 0)
    evening_day = now - timedelta(days=1) if morning else now

    start = sunset - s.start_before_sunset
    bedtime = s.bedtime_weekend if evening_day.isoweekday() in (5, 6) else s.bedtime_weeknight
    if bedtime < 720:
        bedtime += 1440
    wind_start = bedtime - s.wind_down
    nightlight_start = bedtime - s.nightlight

    if m >= bedtime:
        # After bedtime it's night until 05:00, then day again.
        phase = "day" if morning and now.hour >= 5 else "night"
    elif m < start:
        phase = "day"
    elif m >= nightlight_start:
        phase = "nightlight"
    elif m >= wind_start:
        phase = "wind_down"
    else:
        phase = "evening"

    # Fade up from 5% to full over the brighten period...
    ramp = 5 + (p["cool_bright"].brightness_pct - 5) * min(max((m - start) / max(s.brighten, 1), 0), 1)
    # ...and wind down: first half Cool bright -> Relax, second half Relax -> Rest.
    span = nightlight_start - wind_start
    down = (m - wind_start) / span if span > 0 else (1 if m >= wind_start else 0)
    down = min(max(down, 0), 1)
    light = blend(p["cool_bright"], p["relax"], down * 2) if down <= 0.5 else blend(p["relax"], p["rest"], down * 2 - 1)
    light = Light(min(ramp, light.brightness_pct), light.xy_color, light.kelvin)
    return Evening(phase, light.rounded(), hhmm(start), hhmm(bedtime))


# ---- Scene cycling -----------------------------------------------------------


def next_scene(current: str) -> str:
    """The scene after `current` in the dimmer cycle; Natural light after
    anything unrecognised."""
    if current in SCENE_CYCLE:
        return SCENE_CYCLE[(SCENE_CYCLE.index(current) + 1) % len(SCENE_CYCLE)]
    return SCENE_CYCLE[0]
