"""Hue-Lite's engine: the settings, which scene each light is in, and
following the clock. The routines are in routines.py, the Hue dimmers in
dimmers.py, choosing lights in targets.py and sending them commands in
lights.py.

Each light Hue-Lite has put into a scene remembers it in `scenes`: Natural
light, the evening routine, a preset, a colour scene (whose colours drift),
or the wake-up fade-in. Picking another scene replaces it; changing the light
yourself or from an automation, or activating one of Home Assistant's own
scenes with it, forgets it. Turning a light off keeps its scene, so a light in
Natural light or the routine picks it up again when it comes back on. Dimming
a light in a scene keeps it there, at that level (`dimmed`), until a scene is
picked again.

A light is on Auto while the evening routine or wake up has it, or while it's
in `auto` (put there from its Auto button); those the routines take as if
they had their labels. Picking a scene for a light or changing it takes it
off Auto; turning it on or off doesn't.
"""

from __future__ import annotations

from collections.abc import Coroutine, Mapping
from datetime import date, datetime, time
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_AREA_ID, ATTR_ENTITY_ID, ATTR_LABEL_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
    label_registry as lr,
)
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import TrackStates, async_track_state_change_event, async_track_state_change_filtered, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from . import dimmers
from .const import (
    CONF_DASHBOARD,
    CONF_DIMMERS,
    CONF_PRESENCE,
    DEFAULT_PRESENCE,
    DEFAULT_SETTINGS,
    DOMAIN,
    EVENING_PHASES,
    NAME,
    NATURAL,
    ROLES,
    ROUTINE,
    SIGNAL_SHARED,
    STORAGE_KEY,
    STORAGE_VERSION,
    TIMES,
    WAKE_UP,
    label_option,
)
from .lights import Sender
from .routines import Routines
from .targets import LIGHT, excluded, resolve
from .timeline import (
    PALETTES,
    PRESETS,
    SCENE_CYCLE,
    Evening,
    EveningSettings,
    Light,
    Palette,
    SunTimes,
    Target,
    evening_routine,
    minutes,
    natural_light,
    next_scene,
    palette_from_stored,
)

type HueLiteConfigEntry = ConfigEntry[HueLite]

# Every scene a light can be in, and the ones with a tile on the dashboard.
TILE_SCENES = frozenset({NATURAL, *PRESETS, *PALETTES})
SCENES = TILE_SCENES | {ROUTINE, WAKE_UP}

# A dimmer's scenes until they're set ({order, hidden}, as the dashboard
# edits them): Natural light and the presets, as Hue's On button cycles.
DIMMER_DEFAULT = {"order": [*SCENE_CYCLE, *PALETTES], "hidden": list(PALETTES)}

# Your own scenes: their ids start with this, and there are at most this many.
CUSTOM_PREFIX = "custom_"
MAX_CUSTOM = 50


def _setup(value: Any, tiles: frozenset[str] | set[str] = TILE_SCENES) -> dict[str, list[str]] | None:
    """A stored set of scenes ({order, hidden}), or None if it isn't one;
    scenes not among `tiles` are left out."""
    if not isinstance(value, dict):
        return None

    def ids(key: str) -> list[str]:
        found = value.get(key)
        return [s for s in found if s in tiles] if isinstance(found, list) else []

    return {"order": ids("order"), "hidden": ids("hidden")}


def custom_scene(value: Any) -> dict[str, Any] | None:
    """One of your own scenes, checked, or None if it isn't one: a name, an
    icon, 1-8 colours as [x, y, brightness %] (one: static), `white` as
    [brightness %, kelvin] for bulbs without colour, and minutes per drift."""
    if not isinstance(value, dict):
        return None
    name, icon, colours, white = value.get("name"), value.get("icon"), value.get("colours"), value.get("white")
    drift = value.get("drift_minutes", 3)

    def number(x: Any, low: float, high: float) -> bool:
        return type(x) in (int, float) and low <= x <= high

    def colour(c: Any) -> bool:
        return isinstance(c, list) and len(c) == 3 and number(c[0], 0, 1) and number(c[1], 0, 1) and number(c[2], 1, 100)

    if not (
        isinstance(name, str)
        and 1 <= len(name.strip()) <= 30
        and isinstance(icon, str)
        and len(icon) <= 64
        and isinstance(colours, list)
        and 1 <= len(colours) <= 8
        and all(colour(c) for c in colours)
        and isinstance(white, list)
        and len(white) == 2
        and number(white[0], 1, 100)
        and number(white[1], 2000, 6500)
        and type(drift) is int
        and 1 <= drift <= 60
    ):
        return None
    return {"name": name.strip(), "icon": icon, "colours": colours, "white": white, "drift_minutes": drift}


@callback
def async_get_loaded(hass: HomeAssistant) -> HueLite | None:
    """The running Hue-Lite, if it's set up."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


def _is_time(value: str) -> bool:
    try:
        time.fromisoformat(value)
    except ValueError:
        return False
    return True


def _stored(data: Any) -> dict[str, Any]:
    """The stored data, brought up to date from earlier versions (the evening
    routine's one switch is now one for Sun–Thu nights and one for Fri & Sat
    nights, both starting as it was; the Movie scene is now Nebula), with
    anything of the wrong kind left out: a damaged file, a scene there isn't
    any more, or a setting of the wrong type, which keeps its default (a bad
    time would break every minute's tick)."""
    data = data if isinstance(data, dict) else {}

    def part(key: str, kind: type) -> Any:
        return data[key] if isinstance(data.get(key), kind) else kind()

    settings, scenes, auto = part("settings", dict), part("scenes", dict), part("auto", list)
    layouts, dimmer_settings, dimmed = part("layouts", dict), part("dimmers", dict), part("dimmed", dict)
    if isinstance(was := settings.get("evening_routine_enabled"), bool):
        settings = {"evening_weeknights": was, "evening_weekends": was, **settings}
    renamed = {"movie": "nebula"}
    # Your own scenes first: lights, rooms and dimmers can be in them.
    custom = {
        scene_id: scene
        for scene_id, value in list(part("custom_scenes", dict).items())[:MAX_CUSTOM]
        if isinstance(scene_id, str) and scene_id.startswith(CUSTOM_PREFIX) and (scene := custom_scene(value))
    }
    tiles = TILE_SCENES | custom.keys()
    return {
        "settings": {
            key: value
            for key, value in settings.items()
            if key in DEFAULT_SETTINGS
            and type(value) is type(DEFAULT_SETTINGS[key])
            and (key not in TIMES or _is_time(value))
        },
        "scenes": {
            light: scene
            for light, was in scenes.items()
            if isinstance(was, str) and ((scene := renamed.get(was, was)) in SCENES or scene in custom)
        },
        "custom_scenes": custom,
        "auto": [light for light in auto if isinstance(light, str)],
        "dimmed": {
            light: float(level)
            for light, level in dimmed.items()
            if type(level) in (int, float) and 0 < level <= 100
        },
        "last_wake_up": _instant(data.get("last_wake_up")),
        "dashboard_added": data.get("dashboard_added") is True,
        "layouts": {key: setup for key, value in layouts.items() if (setup := _setup(value, tiles))},
        "dimmers": {
            device: found
            for device, value in dimmer_settings.items()
            if isinstance(value, dict) and (found := _dimmer(value, tiles))
        },
    }


def _dimmer(value: dict[str, Any], tiles: frozenset[str] | set[str]) -> dict[str, Any]:
    """A stored dimmer's settings: the lights it controls and its scenes,
    whichever are set."""
    found: dict[str, Any] = {}
    if isinstance(lights := value.get("lights"), list) and (lights := [x for x in lights if isinstance(x, str)]):
        found["lights"] = lights
    if setup := _setup(value.get("scenes"), tiles):
        found["scenes"] = setup
    return found


def _instant(value: Any) -> datetime | None:
    """A stored time with its time zone, or None."""
    try:
        when = dt_util.parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return when if when and when.tzinfo else None


class HueLite:
    """Runs one Hue-Lite config entry."""

    natural: Target
    evening: Evening
    # The targets the sensors last showed.
    _shown: tuple[Target, Evening]

    def __init__(self, hass: HomeAssistant, entry: HueLiteConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.labels = {role: entry.options.get(label_option(role), spec[0]) for role, spec in ROLES.items()}
        self.presence: str = entry.options.get(CONF_PRESENCE, DEFAULT_PRESENCE)
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        # Light -> the scene it's in: NATURAL, ROUTINE, WAKE_UP, a preset or a colour scene.
        self.scenes: dict[str, str] = {}
        # Lights put on Auto: the routines take them as if they had the
        # routines' labels (see async_set_auto).
        self.auto: set[str] = set()
        # Lights dimmed in their scene -> the share of the scene's brightness
        # they get (see async_set_brightness).
        self.dimmed: dict[str, float] = {}
        # Hue-Lite's entity ids by key, for the dashboard (filled in as they're added).
        self.entity_ids: dict[str, str] = {}
        self.device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=NAME, entry_type=DeviceEntryType.SERVICE)
        self.last_wake_up: datetime | None = None
        # Shared by everyone's dashboard: each room's and light's scenes
        # ({order, hidden}, by the dashboard's key for them), and each Hue
        # dimmer's settings (by device id: the lights it controls instead of
        # its area, and its scenes).
        self.layouts: dict[str, dict[str, list[str]]] = {}
        self.dimmer_settings: dict[str, dict[str, Any]] = {}
        # The target each followed scene's lights were last moved to.
        self.followed: dict[str, Target] = {}
        self.sender = Sender(hass)
        self.routines = Routines(self)
        self.use_dimmers: bool = entry.options.get(CONF_DIMMERS, True)
        # Whether to add the Lights dashboard, and whether it's been added
        # (once: one that's deleted stays deleted, until the option's turned
        # off and on again).
        self.use_dashboard: bool = entry.options.get(CONF_DASHBOARD, True)
        self.dashboard_added = False
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._sun: tuple[date, SunTimes] | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unwatch: CALLBACK_TYPE | None = None
        self._watched: frozenset[str] = frozenset()
        # Each light's brightness when it was last turned off, to tell a plain
        # switch-on from one at a brightness of someone's own.
        self._off_brightness: dict[str, int] = {}
        # Your own scenes, as stored (by id), and every colour scene (the
        # built-in ones and yours; a static one of yours is a one-colour one).
        self.custom: dict[str, dict[str, Any]] = {}
        self.palettes: dict[str, Palette] = dict(PALETTES)
        # Each colour scene's drift step its bulbs were last moved on at.
        self._drifted: dict[str, int] = {}

    # ---- Lifecycle -----------------------------------------------------------

    async def async_load(self) -> None:
        """Load the saved settings and scenes, and work out where the timelines are."""
        data = _stored(await self._store.async_load())
        self.custom = data["custom_scenes"]
        self._custom_changed()
        self.settings.update(data["settings"])
        self.last_wake_up = data["last_wake_up"]
        self.layouts, self.dimmer_settings = data["layouts"], data["dimmers"]
        self.dashboard_added = data["dashboard_added"] and self.use_dashboard
        self.natural, self.evening = self._targets(dt_util.now())
        self.followed = {NATURAL: self.natural, ROUTINE: self.evening}
        self._shown = (self.natural, self.evening)
        # Lights excluded since are let go, and so are the routine's when bedtime
        # passed while Home Assistant was down.
        routine_over = self.evening.phase not in EVENING_PHASES
        self.scenes = {
            light: scene
            for light, scene in data["scenes"].items()
            if not self.excluded(light) and not (routine_over and scene == ROUTINE)
        }
        self.auto = {light for light in data["auto"] if not self.excluded(light)}
        self.dimmed = {light: level for light, level in data["dimmed"].items() if light in self.scenes}

    @callback
    def async_start(self) -> None:
        """Start following the clock, the lights in scenes, Home Assistant's
        own scenes, the registries, the routines and the dimmers."""
        bus = self.hass.bus
        self._unsubs += [
            async_track_time_change(self.hass, self._tick, second=0),
            async_track_state_change_filtered(
                self.hass, TrackStates(False, set(), {"scene"}), self._scene_activated
            ).async_remove,
            bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._entity_registry_updated),
            bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._device_registry_updated),
            bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, self._release_excluded),
            bus.async_listen(lr.EVENT_LABEL_REGISTRY_UPDATED, self._check_labels),
            *self.routines.async_start(),
        ]
        if self.use_dimmers:
            self._unsubs.append(dimmers.async_listen(self))
        self._watch_lights()
        self._check_labels()

    @callback
    def async_stop(self) -> None:
        for unsub in (*self._unsubs, self._unwatch):
            if unsub:
                unsub()
        self._unsubs.clear()
        self._unwatch = None
        self._watched = frozenset()
        self.routines.async_stop()

    def _data(self) -> dict[str, Any]:
        return {
            "settings": self.settings,
            "scenes": dict(sorted(self.scenes.items())),
            "auto": sorted(self.auto),
            "dimmed": dict(sorted(self.dimmed.items())),
            "last_wake_up": self.last_wake_up.isoformat() if self.last_wake_up else None,
            "layouts": self.layouts,
            "dimmers": self.dimmer_settings,
            "dashboard_added": self.dashboard_added,
            "custom_scenes": self.custom,
        }

    @callback
    def save(self) -> None:
        self._store.async_delay_save(self._data, 5)

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    @callback
    def spawn(self, target: Coroutine[Any, Any, None]) -> None:
        """Run light commands in the background (stopped with the entry)."""
        self.entry.async_create_background_task(self.hass, target, f"{DOMAIN} lights")

    # ---- Entities ------------------------------------------------------------

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `update` whenever the timelines or scenes change."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    @callback
    def async_set_setting(self, key: str, value: Any) -> None:
        """Change a setting; the timelines, routines and sensors follow
        straight away. Setting one to what it is already does nothing, so
        an automation switching the evening routine on again doesn't take
        back the lights moved out of it."""
        if self.settings[key] == value:
            return
        self.settings[key] = value
        self.save()
        self._refresh()
        self.routines.setting_changed(key, value)
        self._notify()

    @callback
    def _check_labels(self, _event: Event | None = None) -> None:
        """Raise a Repairs issue for a role whose label has been deleted (that
        role then has no lights), and clear it once there's a label again."""
        registry = lr.async_get(self.hass)
        for role, label_id in self.labels.items():
            issue_id = f"missing_label_{role}"
            if registry.async_get_label(label_id):
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            else:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="missing_label",
                    translation_placeholders={"role": ROLES[role][1]},
                )

    # ---- Choosing lights -----------------------------------------------------

    @callback
    def lights(self, target: Mapping[str, Any] | None = None) -> list[str]:
        """The lights a target means; see targets.resolve."""
        return resolve(self.hass, target, self.labels["exclude"])

    def labelled(self, role: str) -> list[str]:
        """The lights with a role's label (Evening, Wake up, ...)."""
        return self.lights({ATTR_LABEL_ID: [self.labels[role]]})

    def routine_lights(self, role: str) -> list[str]:
        """The lights a routine ("evening" or "wake_up") takes: those with its
        label, and those on Auto."""
        return self.lights({ATTR_LABEL_ID: [self.labels[role]], ATTR_ENTITY_ID: sorted(self.auto)})

    def excluded(self, entity_id: str) -> bool:
        return excluded(self.hass, entity_id, self.labels["exclude"])

    def on(self, lights: list[str]) -> list[str]:
        return [entity_id for entity_id in lights if self.hass.states.is_state(entity_id, STATE_ON)]

    # ---- Auto ----------------------------------------------------------------

    def on_auto(self) -> list[str]:
        """The lights on Auto: put on it, or held by the evening routine or
        wake up now."""
        return sorted(self.auto.union(light for light, scene in self.scenes.items() if scene in (ROUTINE, WAKE_UP)))

    @callback
    def async_set_auto(self, lights: list[str], auto: bool) -> None:
        """Put lights on Auto, or take them off it. Put on it, a light does
        what the routines ask, whether or not it has their labels: it leaves
        the scene it was in and straight away matches them, joining the
        evening routine or wake up if one's running and going off if not.
        Taken off, it leaves the routine or wake up holding it, staying as it
        is; its labels still count next time."""
        lights = [light for light in lights if not self.excluded(light)]
        held = [light for light in lights if self.scenes.get(light) in (ROUTINE, WAKE_UP)]
        if auto:
            joining = [light for light in lights if light not in self.auto and light not in held]
            asking = self.routines.asking
            changed = self._set_auto(lights, True) | self._put(joining, asking)
            if joining:
                self.spawn(self.routines.match(asking, joining))
        else:
            changed = self._set_auto(lights, False) | self._put(held, "")
        if changed:
            self._changed()

    def _set_auto(self, lights: list[str], auto: bool) -> bool:
        """Add lights to `auto` or remove them; whether that changed it."""
        before = len(self.auto)
        if auto:
            self.auto.update(lights)
        else:
            self.auto.difference_update(lights)
        return len(self.auto) != before

    # ---- Shared dashboard settings -------------------------------------------

    def shared(self) -> dict[str, Any]:
        """What every dashboard shares: the rooms' and lights' scenes, the
        dimmers' settings, and a dimmer's scenes until they're set."""
        return {"layouts": self.layouts, "dimmers": self.dimmer_settings, "dimmer_default": DIMMER_DEFAULT}

    @callback
    def set_layout(self, key: str, scenes: dict[str, list[str]] | None) -> None:
        """Set a room's or light's scenes ({order, hidden}), or with None go
        back to its default."""
        if scenes is None:
            self.layouts.pop(key, None)
        else:
            self.layouts[key] = scenes
        self._shared_changed()

    @callback
    def set_dimmer(self, device_id: str, **changes: Any) -> None:
        """Change a dimmer's `lights` (the ones it controls instead of its
        area) or `scenes` ({order, hidden}); None, or no lights, goes back to
        the default."""
        settings = {**self.dimmer_settings.get(device_id, {}), **changes}
        settings = {key: value for key, value in settings.items() if value}
        if settings:
            self.dimmer_settings[device_id] = settings
        else:
            self.dimmer_settings.pop(device_id, None)
        self._shared_changed()

    @callback
    def claim_dashboard(self, release: bool = False) -> bool:
        """Whether this dashboard should add the Lights dashboard: true for
        the first to ask (so two open at once don't both add it), if the
        option's on and it hasn't been added. `release` gives it back, when
        adding it failed."""
        if release:
            claimed, self.dashboard_added = False, False
        else:
            claimed = self.use_dashboard and not self.dashboard_added
            self.dashboard_added = self.dashboard_added or claimed
        self.save()
        return claimed

    @callback
    def _shared_changed(self) -> None:
        self.save()
        async_dispatcher_send(self.hass, SIGNAL_SHARED)

    def dimmer_lights(self, device: dr.DeviceEntry) -> list[str]:
        """The lights a dimmer controls: the ones chosen for it, or its area's."""
        if chosen := self.dimmer_settings.get(device.id, {}).get("lights"):
            return self.lights({ATTR_ENTITY_ID: chosen})
        return self.lights({ATTR_AREA_ID: [device.area_id]}) if device.area_id else []

    def dimmer_cycle(self, device_id: str) -> list[str]:
        """The scenes a dimmer's On button steps through, in order."""
        scenes = self.dimmer_settings.get(device_id, {}).get("scenes", DIMMER_DEFAULT)
        return [scene for scene in scenes["order"] if scene not in scenes["hidden"]]

    # ---- Scenes --------------------------------------------------------------

    def is_scene(self, scene: str) -> bool:
        """Whether a preset, a colour scene or one of your own has this id."""
        return scene in PRESETS or scene in self.palettes

    def tile_scenes(self) -> set[str]:
        """Every scene with a tile: the built-in ones and your own."""
        return TILE_SCENES | self.custom.keys()

    @callback
    def _custom_changed(self) -> None:
        """Your own scenes changed: work out the colour scenes again (a new
        one drifts from its next step)."""
        self.palettes = {**PALETTES, **{scene_id: palette_from_stored(scene) for scene_id, scene in self.custom.items()}}
        self._drifted = {scene: self._drifted.get(scene, self._drift_step(p)) for scene, p in self.palettes.items()}

    def in_scene(self, scene: str) -> list[str]:
        """The lights in a scene (NATURAL, ROUTINE, WAKE_UP, a preset or a colour scene)."""
        return sorted(light for light, current in self.scenes.items() if current == scene)

    def target(self, scene: str) -> Target:
        """Where a followed scene (NATURAL or ROUTINE) is, and the light it wants now."""
        return self.natural if scene == NATURAL else self.evening

    @callback
    def set_scene(self, lights: list[str], scene: str, *, leave_auto: bool = False) -> None:
        """Lights are now in `scene`, or with '' in none (changed, or done
        with). With `leave_auto` they come off Auto too: someone picked a
        scene for them or changed them. Excluded lights are never kept in
        one: named in an action they're set once, not followed."""
        left = leave_auto and self._set_auto(lights, False)
        if self._put(lights, scene) or left:
            self._changed()

    def _put(self, lights: list[str], scene: str) -> bool:
        """Put lights in `scene` ('' for none), at its full brightness;
        whether any changed."""
        changed = False
        for light in lights:
            if self.scenes.get(light, "") == scene or (scene and self.excluded(light)):
                continue
            changed = True
            self.dimmed.pop(light, None)
            if scene:
                self.scenes[light] = scene
            else:
                del self.scenes[light]
        return changed

    @callback
    def _changed(self) -> None:
        """The scenes or Auto changed: save them, follow the lights now in
        them, and update the sensors."""
        self.save()
        self._watch_lights()
        self._notify()

    def current_scene(self, lights: list[str]) -> str:
        """The scene every light that's on is in, or '' when none are on or
        they're in different scenes (or none)."""
        scenes = {self.scenes.get(light, "") for light in self.on(lights)}
        return scenes.pop() if len(scenes) == 1 else ""

    async def async_preset(self, lights: list[str], preset: str, parent: Context | None = None) -> None:
        """Someone picked a preset or a colour scene: the lights leave Auto
        and go into it."""
        if not lights:
            return
        self.set_scene(lights, preset, leave_auto=True)
        self._undim(lights)
        if preset in self.palettes:
            await self.send_palette(preset, lights, 0.4, parent)
        else:
            await self.sender.send(lights, PRESETS[preset], 0.4, parent)

    async def async_natural_light(self, lights: list[str], follow: bool = True, parent: Context | None = None) -> None:
        """Someone picked Natural light: the lights leave Auto, go into it
        and turn on. With follow off, the ones in it are taken out instead
        (leaving other scenes and Auto alone)."""
        if not follow:
            self.set_scene([light for light in lights if self.scenes.get(light) == NATURAL], "")
        elif lights:
            self.set_scene(lights, NATURAL, leave_auto=True)
            self._undim(lights)
            await self.apply(NATURAL, lights, 2, parent)

    async def async_next_scene(
        self, lights: list[str], parent: Context | None = None, cycle: list[str] | None = None
    ) -> None:
        """Like pressing a Hue dimmer's On button again: the next scene in
        `cycle` (by default Natural light, then the presets), and round.
        Lights that are off, or in something not in it, go to its first."""
        if not lights:
            return
        scene = next_scene(self.current_scene(lights), cycle or SCENE_CYCLE)
        if scene == NATURAL:
            await self.async_natural_light(lights, parent=parent)
        else:
            await self.async_preset(lights, scene, parent)

    async def apply(self, scene: str, lights: list[str], transition: float, parent: Context | None = None) -> None:
        """Send Natural light's or the routine's current light to the lights,
        turning them on. Nightlight-labelled lights get Nightlight during
        Natural light's night and the routine's nightlight phase."""
        nightlights = self._nightlights(scene)
        await self._send(
            [light for light in lights if light in nightlights], PRESETS["nightlight"], transition, parent
        )
        await self._send(
            [light for light in lights if light not in nightlights], self.target(scene).light, transition, parent
        )

    def _nightlights(self, scene: str) -> set[str]:
        """The lights Natural light or the routine gives Nightlight now: the
        Nightlight-labelled ones, in Natural light's night or the routine's
        nightlight phase."""
        night = self.target(scene).phase == ("night" if scene == NATURAL else "nightlight")
        return set(self.labelled("nightlight")) if night else set()

    # ---- Dimming -------------------------------------------------------------

    async def async_set_brightness(
        self,
        lights: list[str],
        brightness_pct: int | None = None,
        step_pct: int | None = None,
        parent: Context | None = None,
    ) -> None:
        """Dim or brighten lights, keeping them in their scenes, like Hue: a
        light in one goes on following it at that level (Natural light
        through the day, a colour scene as it drifts) until a scene is
        picked again. `step_pct` moves each light on from its own
        brightness (a dimmer's Up and Down). A light in a scene that's off
        comes on in it at that level; others just get the brightness."""
        brightness: dict[int, list[str]] = {}
        for light in lights:
            state = self.hass.states.get(light)
            on = state is not None and state.state == STATE_ON
            if brightness_pct is None:
                now = round((state.attributes.get("brightness") or 0) / 2.55) if on else 0
                pct = min(max(now + (step_pct or 0), 1), 100)
            else:
                pct = min(max(round(brightness_pct), 1), 100)
            scene_light = self._scene_light(light)
            if scene_light is not None:
                self.dimmed[light] = pct / scene_light.brightness_pct
            if on or scene_light is None:
                brightness.setdefault(pct, []).append(light)
            else:
                await self._send([light], scene_light, 0.4, parent)
        self.save()
        for pct, group in brightness.items():
            await self.sender.call("turn_on", group, parent=parent, brightness_pct=pct, transition=0.4)

    def _scene_light(self, light: str) -> Light | None:
        """The light a light's scene wants for it now, before dimming; None
        when it's in none (or in wake up, which is fading it already)."""
        scene = self.scenes.get(light, "")
        if scene in PRESETS:
            return PRESETS[scene]
        if palette := self.palettes.get(scene):
            if not self.sender.has_colour(light):
                return palette.white
            return palette.colour(self._palette_order(scene).index(light) + self._drift_step(palette))
        if scene == NATURAL or (scene == ROUTINE and self.evening.phase in EVENING_PHASES):
            return PRESETS["nightlight"] if light in self._nightlights(scene) else self.target(scene).light
        return None

    def _undim(self, lights: list[str]) -> None:
        """Lights picked for a scene again get its full brightness."""
        for light in lights:
            self.dimmed.pop(light, None)

    async def _send(self, lights: list[str], light: Light, transition: float, parent: Context | None = None) -> None:
        """Send `light` to lights in a scene, each at its own level."""
        by_level: dict[float, list[str]] = {}
        for entity_id in lights:
            by_level.setdefault(self.dimmed.get(entity_id, 1), []).append(entity_id)
        for level, group in by_level.items():
            await self.sender.send(group, light.scaled(level), transition, parent)

    # ---- Colour scenes -------------------------------------------------------

    async def send_palette(self, scene: str, lights: list[str], transition: float, parent: Context | None = None) -> None:
        """Turn lights on in a colour scene. Each colour bulb gets the colour
        (with its brightness) for its place among the scene's colour bulbs,
        moved on one colour every `drift_minutes`, so a room shows several and
        they drift; white bulbs get the scene's white."""
        palette = self.palettes[scene]
        colour = [light for light in lights if self.sender.has_colour(light)]
        order = self._palette_order(scene, colour)
        step = self._drift_step(palette)
        by_colour: dict[int, list[str]] = {}
        for light in colour:
            by_colour.setdefault((order.index(light) + step) % len(palette.colours), []).append(light)
        for index, group in sorted(by_colour.items()):
            await self._send(group, palette.colour(index), transition, parent)
        await self._send([light for light in lights if light not in colour], palette.white, transition, parent)

    def _palette_order(self, scene: str, joining: list[str] | None = None) -> list[str]:
        """The colour bulbs in a colour scene (with those joining it), in the
        order its colours go round them."""
        return sorted({*(joining or []), *(light for light in self.in_scene(scene) if self.sender.has_colour(light))})

    @staticmethod
    def _drift_step(palette: Palette) -> int:
        return int(dt_util.utcnow().timestamp()) // (palette.drift_minutes * 60)

    @callback
    def _drift(self) -> None:
        """Every `drift_minutes` of each colour scene, start its bulbs that
        are on moving to their next colour, taking until the next move."""
        for scene, palette in self.palettes.items():
            step = self._drift_step(palette)
            if not palette.drifts or self._drifted.get(scene) == step:
                continue
            self._drifted[scene] = step
            if lights := [light for light in self.on(self.in_scene(scene)) if self.sender.has_colour(light)]:
                self.spawn(self.send_palette(scene, lights, palette.drift_minutes * 60))

    # ---- Changes to the lights -----------------------------------------------

    @callback
    def _watch_lights(self) -> None:
        """Follow state changes of the lights in a scene or on Auto
        (re-subscribing only when that set of lights changes)."""
        watched = frozenset(self.scenes.keys() | self.auto)
        if watched == self._watched:
            return
        if self._unwatch:
            self._unwatch()
            self._unwatch = None
        self._watched = watched
        if watched:
            self._unwatch = async_track_state_change_event(self.hass, sorted(watched), self._light_changed)

    @callback
    def _light_changed(self, event: Event[EventStateChangedData]) -> None:
        """A light in Natural light or the routine that's switched on picks
        up its scene's current light (one in a colour scene comes back on in
        its colour and drifts on from there, so it needs nothing). A light
        changed by someone else (from the dashboard, a light dialog, an
        automation) leaves its scene and Auto: one that's on and changed, or
        one switched on at a brightness of their own (the slider). Bulbs
        reporting their own state don't count, and nor does plain switching
        on and off, and nor does a bulb's follow-up report a moment after a
        command, which Home Assistant gives the command's context (so a
        switch-on from the dashboard reports again as if by its user). A
        wake-up light that's switched off is done with."""
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None:
            return
        light, scene = new.entity_id, self.scenes.get(new.entity_id)
        if new.state == STATE_OFF:
            if old.state == STATE_ON and (brightness := old.attributes.get("brightness")) is not None:
                self._off_brightness[light] = brightness
            if scene == WAKE_UP:
                self.set_scene([light], "")
        if new.state != STATE_ON or self.sender.is_ours(new.context):
            return
        by_someone = bool(new.context.user_id or new.context.parent_id)
        follow_up = new.context.id == old.context.id
        if by_someone and not follow_up and (old.state == STATE_ON or self._own_brightness(new)):
            self.set_scene([light], "", leave_auto=True)
        elif old.state != STATE_ON and (scene == NATURAL or (scene == ROUTINE and self.evening.phase in EVENING_PHASES)):
            self.spawn(self.apply(scene, [light], 2))

    def _own_brightness(self, new: State) -> bool:
        """Whether a light was switched on at a brightness other than the one
        it was switched off at (as far as Hue-Lite knows)."""
        before = self._off_brightness.get(new.entity_id)
        brightness = new.attributes.get("brightness")
        return before is not None and brightness is not None and abs(brightness - before) > 2

    @callback
    def _scene_activated(self, event: Event[EventStateChangedData]) -> None:
        """One of Home Assistant's own scenes (scene editor, scenes.yaml) was
        activated. Like picking a scene in Hue, that takes its lights out of
        Hue-Lite's scenes and off Auto, so Natural light and the routines
        don't override it. Home Assistant records the activation before it
        sets the lights."""
        old, new = event.data["old_state"], event.data["new_state"]
        # Appearing, disappearing or coming back after a reload isn't an activation.
        if old is None or new is None or old.state == new.state or STATE_UNAVAILABLE in (old.state, new.state):
            return
        lights = new.attributes.get(ATTR_ENTITY_ID) or ()
        if isinstance(lights, str):
            lights = [lights]
        self.set_scene([light for light in lights if light.startswith(f"{LIGHT}.")], "", leave_auto=True)

    @callback
    def _entity_registry_updated(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        """A light keeps its scene, Auto and dimmers when its entity id
        changes; one that's deleted, excluded or hidden is let go."""
        data = event.data
        light = data["entity_id"]
        if not light.startswith(f"{LIGHT}."):
            return
        if data["action"] == "remove":
            self.set_scene([light], "", leave_auto=True)
            self._rename_in_dimmers(light, None)
        elif data["action"] == "update":
            if old := data.get("old_entity_id"):
                if old in self.scenes or old in self.auto:
                    if scene := self.scenes.pop(old, ""):
                        self.scenes[light] = scene
                    if (level := self.dimmed.pop(old, None)) is not None:
                        self.dimmed[light] = level
                    if old in self.auto:
                        self.auto.remove(old)
                        self.auto.add(light)
                    self._changed()
                self._rename_in_dimmers(old, light)
            self._release_excluded()

    def _rename_in_dimmers(self, old: str, new: str | None) -> None:
        """A light chosen for a dimmer has a new entity id, or with None is
        deleted (a dimmer left with none controls its area again)."""
        for device_id, settings in list(self.dimmer_settings.items()):
            if old in (lights := settings.get("lights", [])):
                renamed = [new if light == old else light for light in lights]
                self.set_dimmer(device_id, lights=[light for light in renamed if light])

    @callback
    def _device_registry_updated(self, event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        """A dimmer that's deleted is forgotten; a device's labels or area
        changing can exclude its lights."""
        if event.data["action"] == "remove" and event.data["device_id"] in self.dimmer_settings:
            self.set_dimmer(event.data["device_id"], lights=None, scenes=None)
        self._release_excluded()

    @callback
    def _release_excluded(self, _event: Event | None = None) -> None:
        """Let go of lights that are now excluded (by their own, their
        device's or their area's label, or hidden)."""
        self.set_scene([light for light in self.scenes.keys() | self.auto if self.excluded(light)], "", leave_auto=True)

    # ---- Timelines -----------------------------------------------------------

    def _sun_times(self, today: date) -> SunTimes:
        if self._sun is None or self._sun[0] != today:
            fallback = SunTimes()

            def at(event: str) -> int:
                when = get_astral_event_date(self.hass, event, today)
                if when is None:
                    return getattr(fallback, event)
                when = dt_util.as_local(when)
                return when.hour * 60 + when.minute

            self._sun = (today, SunTimes(at("dawn"), at("sunrise"), at("noon"), at("sunset"), at("dusk")))
        return self._sun[1]

    def _evening_settings(self) -> EveningSettings:
        s = self.settings
        return EveningSettings(
            start_before_sunset=s["evening_start_before_sunset"],
            brighten=s["evening_brighten_minutes"],
            wind_down=s["evening_wind_down_minutes"],
            nightlight=s["evening_nightlight_minutes"],
            bedtime_weeknight=minutes(s["bedtime_weeknight"]),
            bedtime_weekend=minutes(s["bedtime_weekend"]),
        )

    def _targets(self, now: datetime) -> tuple[Target, Evening]:
        """Where Natural light and the evening routine are at `now`."""
        sun = self._sun_times(now.date())
        return (
            natural_light(now.hour * 60 + now.minute, sun),
            evening_routine(now, sun.sunset, self._evening_settings()),
        )

    @callback
    def _tick(self, _now: datetime) -> None:
        self._refresh()
        self._drift()
        self.routines.tick(dt_util.now())

    @callback
    def _refresh(self) -> None:
        """Work out where both timelines are now, move the lights that follow
        Natural light, and let the routine react to its timeline."""
        evening = self.evening
        self.natural, self.evening = self._targets(dt_util.now())
        # The sensors change as visibly as the lights do, not every minute.
        shown_natural, shown_evening = self._shown
        if self.natural.moved_from(shown_natural) or self.evening.moved_from(shown_evening):
            self._shown = (self.natural, self.evening)
            self._notify()
        self.follow(NATURAL)
        self.routines.evening_moved(evening)

    @callback
    def follow(self, scene: str) -> None:
        """Move a scene's lights that are on to its current light, once that
        has changed phase or moved noticeably since they were last moved. Slow
        blends then send a command every few minutes rather than every minute."""
        target, last = self.target(scene), self.followed.get(scene)
        if last is not None and not target.moved_from(last):
            return
        self.followed[scene] = target
        if lights := self.on(self.in_scene(scene)):
            self.spawn(self.apply(scene, lights, 60))
