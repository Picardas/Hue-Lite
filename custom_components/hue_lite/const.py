"""Constants for Hue-Lite."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "hue_lite"
NAME: Final = "Hue-Lite"

STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1

CONF_PRESENCE: Final = "presence"
CONF_DIMMERS: Final = "dimmers"
CONF_DASHBOARD: Final = "dashboard"
DEFAULT_PRESENCE: Final = "zone.home"

# Lights take part through labels. Each role is one label: role -> (label id
# looked for first, name, icon, colour, description). Setup reuses a label with
# that id or name, or creates it; the options flow can point a role at any label.
ROLES: Final = {
    "evening": ("evening", "Evening", "mdi:weather-sunset", "amber", "Taken by the evening routine"),
    "wake_up": ("wake_up", "Wake up", "mdi:weather-sunset-up", "orange", "Faded up at the wake-up time"),
    "nightlight": ("nightlight", "Nightlight", "mdi:weather-night", "red", "Gets Nightlight instead of Rest at night"),
    "coming_home": ("coming_home", "Coming home", "mdi:home-import-outline", "green", "Comes on whenever someone gets home"),
    "first_home": ("first_one_home", "First one home", "mdi:home-account", "light-green", "Comes on only when the first person gets home"),
    "exclude": ("lighting_exclude", "Exclude from lighting", "mdi:lightbulb-off-outline", "grey", "Left out of Hue-Lite"),
}


def label_option(role: str) -> str:
    """The config entry option holding a role's label id."""
    return f"label_{role}"


# Scenes a light can be in besides the presets and colour scenes: Natural light
# and the evening routine are followed over time; wake-up marks lights its
# fade-in has, until they're changed, so turning them off afterwards leaves
# other lights alone.
NATURAL: Final = "natural"
ROUTINE: Final = "routine"
WAKE_UP: Final = "wake_up"

# Colour scenes move each colour bulb on to the next colour this often, over
# the whole time, so they drift slowly.
DRIFT_MINUTES: Final = 3

# The evening routine's phases in which it has lights.
EVENING_PHASES: Final = ("evening", "wind_down", "nightlight")

# Settings, shown as Hue-Lite's switch, time and number entities (and on the
# dashboard). Keys are the entity keys.
SWITCHES: Final = {
    # The evening routine on Sun–Thu nights and on Fri & Sat nights (an
    # evening's night is the one its bedtime ends, as with the bedtimes).
    "evening_weeknights": True,
    "evening_weekends": True,
    "coming_home_enabled": True,
    "coming_home_only_dark": True,
    "leaving_home_enabled": False,
    "wake_up_weekdays": False,
    "wake_up_weekends": False,
    "wake_up_auto_off": False,
}
# Switches that are settings rather than everyday controls.
CONFIG_SWITCHES: Final = ("coming_home_only_dark", "wake_up_auto_off")

TIMES: Final = {
    "wake_up_weekday": "07:00",
    "wake_up_weekend": "09:00",
    "bedtime_weeknight": "23:00",
    "bedtime_weekend": "00:00",
}

# Minutes: key -> (default, min, max, step).
NUMBERS: Final = {
    "wake_up_fade_minutes": (30, 5, 60, 5),
    "wake_up_off_after_minutes": (60, 15, 240, 15),
    "bedtime_fade_minutes": (15, 1, 60, 1),
    "evening_start_before_sunset": (90, 0, 240, 15),
    "evening_brighten_minutes": (60, 5, 180, 5),
    "evening_wind_down_minutes": (120, 30, 240, 15),
    "evening_nightlight_minutes": (30, 0, 90, 5),
}

DEFAULT_SETTINGS: Final = {**SWITCHES, **TIMES, **{key: spec[0] for key, spec in NUMBERS.items()}}

# Hue Dimmer Switches (RWL020/021/022) through ZHA, and the button events used.
DIMMER_MODEL_PREFIX: Final = "RWL0"
DIMMER_COMMANDS: Final = frozenset(
    {"on_short_release", "off_short_release", "up_press", "up_hold", "down_press", "down_hold"}
)

# Sent when the dashboards' shared settings change (scenes, dimmers).
SIGNAL_SHARED: Final = f"{DOMAIN}_shared"

# Everyone gone for this long turns the lights off.
LEAVING_DELAY: Final = 120
