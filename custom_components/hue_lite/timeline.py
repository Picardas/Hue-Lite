"""The presets, the colour scenes and the two timelines: the Natural light
scene and the evening routine.

Plain functions of the time of day, with no Home Assistant imports, so they can
be tested on their own. Times of day are minutes after midnight.
"""

from __future__ import annotations

from collections.abc import Sequence
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

    def scaled(self, level: float) -> Light:
        """This at `level` times the brightness (between 1% and 100%)."""
        if level == 1:
            return self
        return Light(min(max(round(self.brightness_pct * level), 1), 100), self.xy_color, self.kelvin)

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
# a colour temperature instead (Energize, Read and Relax as in Hue's
# white-ambiance scenes, Rest and Nightlight at 2000 K, as warm as most bulbs
# go): converting the xy colours would make Nightlight's deep red a cool 4100 K.
PRESETS: dict[str, Light] = {
    "energize": Light(100, (0.3143, 0.3301), 6410),
    "cool_bright": Light(100, (0.3812, 0.3794), 4000),
    "read": Light(100, (0.445, 0.4067), 2890),
    "relax": Light(56, (0.5019, 0.4152), 2237),
    "rest": Light(35, (0.561, 0.4042), 2000),
    "nightlight": Light(1, (0.67, 0.32), 2000),
}


@dataclass(frozen=True)
class Palette:
    """A colour scene, like Hue's: its colours, each an xy colour and a
    brightness, are spread across the colour bulbs and drift from one to the
    next every `drift_minutes`, so the bulbs differ in brightness too and
    change it slowly. White bulbs get `white`. With one colour it's static,
    like a preset."""

    colours: tuple[tuple[float, float, float], ...]
    white: Light
    drift_minutes: int = 3

    def colour(self, index: int) -> Light:
        """The light for a colour bulb at `index` (round the palette)."""
        x, y, brightness = self.colours[index % len(self.colours)]
        return Light(brightness, (x, y), self.white.kelvin)

    @property
    def drifts(self) -> bool:
        return len(self.colours) > 1


def kelvin_for(xy: tuple[float, float]) -> int:
    """The colour temperature nearest an xy colour (McCamy's formula), within
    what white bulbs do (2000-6500 K): what a scene's white bulbs get."""
    x, y = xy
    n = (x - 0.3320) / (0.1858 - y) if y != 0.1858 else 0
    kelvin = 449 * n**3 + 3525 * n**2 + 6823.3 * n + 5520.33
    return round(min(max(kelvin, 2000), 6500))


def palette_from_stored(scene: dict[str, Any]) -> Palette:
    """One of your own scenes, as stored (already checked): its colours as
    [x, y, brightness], `white` as [brightness, kelvin], and its drift."""
    brightness, kelvin = scene["white"]
    colours = tuple((x, y, b) for x, y, b in scene["colours"])
    return Palette(colours, Light(brightness, colours[0][:2], kelvin), scene["drift_minutes"])


# Hue-Lite's colour scenes. Each colour is (x, y, brightness %), inside Hue's
# gamut C. Each scene mixes a few hues at different brightnesses, so a room's
# bulbs differ, and drifts at its own pace: the lively ones faster, the calm
# ones slower. Golden hour, Nebula and Aurora started from colours in Hue's
# scene library (Savanna sunset, Golden pond and Amber bloom; Nebula,
# Tropical twilight and Moonlight; Arctic aurora, Blue planet and Forest
# adventure), made more vivid.
PALETTES: dict[str, Palette] = {
    # A sunset: gold, amber and deep orange, with coral and a bright peach glow.
    "golden_hour": Palette(
        ((0.5100, 0.4400, 85), (0.5550, 0.4000, 70), (0.6050, 0.3650, 60), (0.5600, 0.3350, 50), (0.4900, 0.3900, 90)),
        Light(70, (0.4801, 0.4309), 2500),
        drift_minutes=3,
    ),
    # Deep space: blue, periwinkle and violet, with magenta and hot pink.
    "nebula": Palette(
        ((0.1650, 0.0700, 25), (0.1850, 0.1500, 35), (0.2450, 0.1050, 30), (0.3500, 0.1650, 40), (0.4500, 0.2150, 45)),
        Light(25, (0.561, 0.4042), 2000),
        drift_minutes=2,
    ),
    # Northern lights: green, teal and cyan, with a violet fringe and a
    # yellow-green flare, shimmering.
    "aurora": Palette(
        ((0.1800, 0.6500, 65), (0.1720, 0.4500, 55), (0.1720, 0.3200, 50), (0.2600, 0.1300, 40), (0.3000, 0.5800, 70)),
        Light(50, (0.3812, 0.3794), 4000),
        drift_minutes=2,
    ),
    # Firelight: deep reds glowing low, orange and amber flaring brighter,
    # flickering on every minute.
    "ember": Palette(
        ((0.6800, 0.3150, 25), (0.6500, 0.3050, 30), (0.6300, 0.3500, 45), (0.5900, 0.3800, 75), (0.5500, 0.4050, 60)),
        Light(35, (0.6150, 0.3650), 2000),
        drift_minutes=1,
    ),
    # Woodland: emerald and leaf greens, moss and lime, with golden sunlight
    # coming through.
    "forest": Palette(
        ((0.2000, 0.6200, 35), (0.2600, 0.6100, 50), (0.3200, 0.5500, 40), (0.3800, 0.5300, 60), (0.4600, 0.4600, 70)),
        Light(30, (0.4599, 0.4106), 2700),
        drift_minutes=3,
    ),
    # Night: dim warm and cool whites, a pale moon blue, blush and lavender,
    # slow and calm.
    "moonlight": Palette(
        ((0.4599, 0.4106, 30), (0.3500, 0.3600, 35), (0.2700, 0.2800, 22), (0.4200, 0.3150, 25), (0.3000, 0.2300, 18)),
        Light(25, (0.4599, 0.4106), 2700),
        drift_minutes=4,
    ),
}

# What a Hue dimmer's On button steps through, and round again.
SCENE_CYCLE = ("natural", "energize", "cool_bright", "read", "relax", "rest", "nightlight")


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
    """The evening routine's phase and light, with tonight's start and bedtime
    (HH:MM), and whether tonight is a Fri or Sat night."""

    start: str
    bedtime: str
    weekend: bool

    def moved_from(self, other: Target) -> bool:
        if not isinstance(other, Evening):
            return True
        return super().moved_from(other) or (self.start, self.bedtime, self.weekend) != (
            other.start,
            other.bedtime,
            other.weekend,
        )


def evening_routine(now: datetime, sunset: int, s: EveningSettings) -> Evening:
    """Where the evening routine is and the light it wants for its lights:

    day         from 05:00 until the evening starts (set before sunset)
    evening     dim, brightening to Cool bright
    wind_down   Cool bright -> Relax -> Rest, ending when Nightlight starts
    nightlight  Rest (lights labelled Nightlight: Nightlight)
    night       from bedtime until 05:00: lights fade out at bedtime

    The evening runs from noon to noon, so a bedtime after midnight belongs to
    the evening before (Friday's evening ends at 01:00 on Saturday). Friday and
    Saturday nights use the weekend bedtime (and switch).
    """
    p = PRESETS
    morning = now.hour < 12
    m = now.hour * 60 + now.minute + (1440 if morning else 0)
    evening_day = now - timedelta(days=1) if morning else now

    start = sunset - s.start_before_sunset
    weekend = evening_day.isoweekday() in (5, 6)
    bedtime = s.bedtime_weekend if weekend else s.bedtime_weeknight
    if bedtime < 720:
        bedtime += 1440
    wind_start = bedtime - s.wind_down
    nightlight_start = bedtime - s.nightlight

    # In the hour repeated when the clocks go back, the second time round is
    # really an hour on: a bedtime in the first is still past.
    if m >= bedtime or (now.fold and m + 60 >= bedtime):
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
    return Evening(phase, light.rounded(), hhmm(start), hhmm(bedtime), weekend)


# ---- Scene cycling -----------------------------------------------------------


def next_scene(current: str, cycle: Sequence[str] = SCENE_CYCLE) -> str:
    """The scene after `current` in a dimmer's cycle; its first after
    anything not in it."""
    if current in cycle:
        return cycle[(list(cycle).index(current) + 1) % len(cycle)]
    return cycle[0]
