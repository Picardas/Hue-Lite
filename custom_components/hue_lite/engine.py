"""Hue-Lite's engine: the settings, which scene each light is in, and
following the clock. The routines are in routines.py, the Hue dimmers in
dimmers.py, choosing lights in targets.py and sending them commands in
lights.py.

Each light Hue-Lite has put into a scene remembers it in `scenes`: Natural
light, the evening routine, a preset, a colour scene (whose colours drift),
one of your own scenes (`custom`, static or drifting like a colour scene), or
the wake-up fade-in. Picking another scene replaces it; changing the light
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

import asyncio
from collections.abc import Coroutine, Iterable, Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_LABEL_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
    label_registry as lr,
)
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
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
    STORAGE_KEY,
    STORAGE_VERSION,
    TIMES,
    WAKE_UP,
    label_option,
)
from .lights import Sender
from .routines import Routines
from .shared import CUSTOM_PREFIX, MAX_CUSTOM, TILE_SCENES, SharedSettings, custom_scene, renamed, scene_set, stored_dimmer
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

# A dimmer press this soon after the last steps on from it, though its lights
# haven't said they're on yet.
QUICK_PRESS = timedelta(seconds=4)

# Every scene a light can be in.
SCENES = TILE_SCENES | {ROUTINE, WAKE_UP}


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
    nights, both starting as it was; renamed scenes get their new ids), with
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
            if isinstance(was, str) and ((scene := renamed(was)) in SCENES or scene in custom)
        },
        "custom_scenes": custom,
        "auto": [light for light in auto if isinstance(light, str)],
        "mimicked": [light for light in part("mimicked", list) if isinstance(light, str)],
        "dimmed": {
            light: float(level)
            for light, level in dimmed.items()
            if type(level) in (int, float) and 0 < level <= 100
        },
        "last_wake_up": _instant(data.get("last_wake_up")),
        "dashboard_added": data.get("dashboard_added") is True,
        "layouts": {key: setup for key, value in layouts.items() if (setup := scene_set(value, tiles))},
        "dimmers": {
            device: found
            for device, value in dimmer_settings.items()
            if isinstance(value, dict) and (found := stored_dimmer(value, tiles))
        },
    }



def _instant(value: Any) -> datetime | None:
    """A stored time with its time zone, or None."""
    try:
        when = dt_util.parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return when if when and when.tzinfo else None


class HueLite(SharedSettings):
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
        # Light -> the scene it's in: NATURAL, ROUTINE, WAKE_UP, a preset, a colour scene or one of yours.
        self.scenes: dict[str, str] = {}
        # Lights put on Auto: the routines take them as if they had the
        # routines' labels (see async_set_auto).
        self.auto: set[str] = set()
        # Lights dimmed in their scene -> the share of the scene's brightness
        # they get (see async_set_brightness).
        self.dimmed: dict[str, float] = {}
        # Lights fading from how they were into the routine -> when they get
        # there; following leaves them alone until then (see ease).
        self._easing: dict[str, datetime] = {}
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
        # Lights that were off when their scene changed: they get it as it
        # is now when they're next switched on.
        self._stale: set[str] = set()
        # Lights that have just come back on, to be sent their scenes' lights.
        self._picking_up: set[str] = set()
        # The scene the next scene was last for these lights, and when.
        self._stepped: dict[tuple[str, ...], tuple[str, datetime]] = {}

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
        # Lights excluded since are let go. If bedtime passed while Home
        # Assistant was down, the routine's (and Away's) are faded out or let
        # go once it's started (see Routines.catch_up).
        self.scenes = {light: scene for light, scene in data["scenes"].items() if not self.excluded(light)}
        self.auto = {light for light in data["auto"] if not self.excluded(light)}
        self.dimmed = {light: level for light, level in data["dimmed"].items() if light in self.scenes}
        self.routines.mimicked = {light for light in data["mimicked"] if not self.excluded(light)}

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
            "mimicked": sorted(self.routines.mimicked),
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
        So does one on Auto already (switched on by hand since, say), unless
        a routine has it. Taken off, it leaves the routine or wake up holding
        it, staying as it is; its labels still count next time."""
        lights = [light for light in lights if not self.excluded(light)]
        held = [light for light in lights if self.scenes.get(light) in (ROUTINE, WAKE_UP)]
        if auto:
            joining = [light for light in lights if light not in held]
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

    # ---- Scenes --------------------------------------------------------------

    def is_scene(self, scene: str) -> bool:
        """Whether a preset, a colour scene or one of your own has this id."""
        return scene in PRESETS or scene in self.palettes

    def scene_named(self, scene: str) -> str:
        """A scene's id from its id or, for one of your own, its name (either
        ignoring case and spaces at either end; an id comes first), or
        `scene` as it is when there's no such scene."""
        name = renamed(scene.strip().casefold())
        if self.is_scene(name):
            return name
        return next((scene_id for scene_id, s in self.custom.items() if s["name"].casefold() == name), scene)

    @callback
    def _custom_changed(self) -> None:
        """Your own scenes changed: work out the colour scenes again (a new
        one drifts from its next step), and update the sensors, which list them."""
        self.palettes = {**PALETTES, **{scene_id: palette_from_stored(scene) for scene_id, scene in self.custom.items()}}
        self._drifted = {scene: self._drifted.get(scene, self._drift_step(p)) for scene, p in self.palettes.items()}
        self._notify()

    def in_scene(self, scene: str) -> list[str]:
        """The lights in a scene (NATURAL, ROUTINE, WAKE_UP, a preset, a colour scene or one of yours)."""
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
            self._easing.pop(light, None)
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
        """Someone picked a preset, a colour scene or one of yours: the lights leave Auto
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
        Lights that are off, or in something not in it, go to its first. A
        press soon after the last, before the bulbs have said they're on,
        steps on from the scene that one picked rather than starting again."""
        if not lights:
            return
        current = self.current_scene(lights)
        key, now = tuple(sorted(lights)), dt_util.utcnow()
        last = self._stepped.get(key)
        if (
            last
            and not self.on(lights)
            and now - last[1] < QUICK_PRESS
            and all(self.scenes.get(light) == last[0] for light in lights)
        ):
            current = last[0]
        scene = next_scene(current, cycle or SCENE_CYCLE)
        self._stepped = {k: v for k, v in self._stepped.items() if now - v[1] < QUICK_PRESS}
        self._stepped[key] = (scene, now)
        if scene == NATURAL:
            await self.async_natural_light(lights, parent=parent)
        else:
            await self.async_preset(lights, scene, parent)

    def forget_steps(self, lights: list[str]) -> None:
        """The next On press for these lights starts the cycle again (after Off)."""
        self._stepped.pop(tuple(sorted(lights)), None)

    async def apply(self, scene: str, lights: list[str], transition: float, parent: Context | None = None) -> None:
        """Send Natural light's or the routine's current light to the lights,
        turning them on. Nightlight-labelled lights get Nightlight during
        Natural light's night and the routine's nightlight phase."""
        await self._send_each(self._wanted(lights, scene).items(), transition, parent)

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
        comes on in it at that level; others just get the brightness. A
        light in its wake-up stops brightening there."""
        self.set_scene([light for light in lights if self.scenes.get(light) == WAKE_UP], "")
        brightness: dict[int, list[str]] = {}
        for light in lights:
            self._easing.pop(light, None)
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
        return next(iter(self._wanted([light])), None)

    def _wanted(self, lights: list[str], scene: str | None = None) -> dict[Light, list[str]]:
        """The lights their scenes (or Natural light's or the routine's, as
        `scene`) want for them now, before dimming, each with its lights.
        Lights in none, or in wake up (which is fading them already), are
        left out."""
        groups: dict[Light, list[str]] = {}
        nightlights: dict[str, set[str]] = {}
        for light in lights:
            current = scene or self.scenes.get(light, "")
            if current in PRESETS:
                wanted = PRESETS[current]
            elif palette := self.palettes.get(current):
                wanted = (
                    palette.colour(self._palette_order(current).index(light) + self._drift_step(palette))
                    if self.sender.has_colour(light)
                    else palette.white
                )
            elif current == NATURAL or (current == ROUTINE and self.evening.phase in EVENING_PHASES):
                if current not in nightlights:
                    nightlights[current] = self._nightlights(current)
                wanted = PRESETS["nightlight"] if light in nightlights[current] else self.target(current).light
            else:
                continue
            groups.setdefault(wanted, []).append(light)
        return groups

    def _undim(self, lights: list[str]) -> None:
        """Lights picked for a scene again get its full brightness."""
        for light in lights:
            self.dimmed.pop(light, None)

    async def _send(self, lights: list[str], light: Light, transition: float, parent: Context | None = None) -> None:
        """Send `light` to lights in a scene, each at its own level."""
        by_level: dict[float, list[str]] = {}
        for entity_id in lights:
            by_level.setdefault(self.dimmed.get(entity_id, 1), []).append(entity_id)
        await asyncio.gather(
            *(self.sender.send(group, light.scaled(level), transition, parent) for level, group in by_level.items())
        )

    async def _send_each(
        self, groups: Iterable[tuple[Light, list[str]]], transition: float, parent: Context | None = None
    ) -> None:
        """Send each group of lights its light, all at once, so a slow bulb in
        one doesn't hold up the rest (or let an earlier command land after a
        later one)."""
        await asyncio.gather(*(self._send(group, light, transition, parent) for light, group in groups if group))

    # ---- Colour scenes -------------------------------------------------------

    async def send_palette(self, scene: str, lights: list[str], transition: float, parent: Context | None = None) -> None:
        """Turn lights on in a colour scene. Each colour bulb gets the colour
        (with its brightness) for its place among the scene's colour bulbs,
        moved on one colour every `drift_minutes`, so a room shows several and
        they drift; white bulbs get the scene's white."""
        palette = self.palettes[scene]
        colour = [light for light in lights if self.sender.has_colour(light)]
        await self._spread(palette, lights, self._palette_order(scene, colour), self._drift_step(palette), transition, parent)

    async def async_preview(self, lights: list[str], palette: Palette, parent: Context | None = None) -> None:
        """Show colours being picked for a scene on these lights, spread
        across the colour bulbs as a colour scene's are (white bulbs get its
        white). The lights leave their scene and Auto, as if set by hand."""
        self.set_scene(lights, "", leave_auto=True)
        await self._spread(palette, lights, sorted(light for light in lights if self.sender.has_colour(light)), 0, 0.4, parent)

    async def _spread(
        self, palette: Palette, lights: list[str], order: list[str], step: int, transition: float, parent: Context | None
    ) -> None:
        """Send a colour scene's colours round the colour bulbs in `order`,
        starting `step` colours on, and its white to the other lights."""
        by_colour: dict[int, list[str]] = {}
        for light in lights:
            if light in order:
                by_colour.setdefault((order.index(light) + step) % len(palette.colours), []).append(light)
        groups = [(palette.colour(index), group) for index, group in sorted(by_colour.items())]
        groups.append((palette.white, [light for light in lights if light not in order]))
        await self._send_each(groups, transition, parent)

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
        due = {}
        for scene, palette in self.palettes.items():
            step = self._drift_step(palette)
            if palette.drifts and self._drifted.get(scene) != step:
                self._drifted[scene] = step
                due[scene] = palette
        if not due:
            return
        # The colour bulbs that are on, by scene, in one look at the lights.
        lights: dict[str, list[str]] = {}
        for light in self.on(sorted(light for light, scene in self.scenes.items() if scene in due)):
            if self.sender.has_colour(light):
                lights.setdefault(self.scenes[light], []).append(light)
        for scene, on in lights.items():
            self.spawn(self.send_palette(scene, on, due[scene].drift_minutes * 60))

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
        up its scene's current light. One in another scene comes back on as
        it was, so it needs nothing, unless it was unreachable (a bulb
        switched off at the wall forgets its colour) or its scene has changed
        since. A light changed by someone else (from the dashboard, a light
        dialog, an automation) leaves its scene and Auto: one that's on and
        changed, or one switched on at a brightness of their own (the
        slider). Plain switching on or off by someone just takes a light off
        Auto (out of the routine or wake up holding it), keeping any other
        scene. Bulbs reporting their own state don't count (nor does one
        coming back after its power was cut), and nor does a bulb's follow-up
        report a moment after a command, which Home Assistant gives the
        command's context (so a switch-on from the dashboard reports again as
        if by its user). A wake-up light that's switched off is done with."""
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None:
            return
        light, scene = new.entity_id, self.scenes.get(new.entity_id)
        ours = self.sender.is_ours(new.context)
        by_hand = bool(new.context.user_id or new.context.parent_id) and new.context.id != old.context.id and not ours
        if new.state == STATE_OFF:
            self._easing.pop(light, None)
            if old.state == STATE_ON and (brightness := old.attributes.get("brightness")) is not None:
                self._off_brightness[light] = brightness
            if scene == WAKE_UP:
                self.set_scene([light], "")
            if by_hand and old.state == STATE_ON:
                self.async_set_auto([light], False)
        if new.state != STATE_ON or ours:
            return
        if by_hand and (old.state == STATE_ON or self._own_brightness(new)):
            self.set_scene([light], "", leave_auto=True)
        elif old.state != STATE_ON:
            if by_hand:
                self.async_set_auto([light], False)
                scene = self.scenes.get(light)
            stale = light in self._stale or old.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            self._stale.discard(light)
            if stale or scene in (NATURAL, ROUTINE):
                # Lights coming back together (after a restart, a power cut)
                # are picked up together.
                first = not self._picking_up
                self._picking_up.add(light)
                if first:
                    self.spawn(self._pick_up())

    async def _pick_up(self) -> None:
        """Send lights that have come back on their scenes' lights, a command
        for each light wanted rather than each bulb."""
        await asyncio.sleep(0)  # others coming back at the same moment join in
        lights, self._picking_up = sorted(self._picking_up), set()
        await self._send_each(self._wanted(lights).items(), 2)

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
        """A light keeps its scene, Auto, dimmers and the scenes shown for it
        when its entity id changes; one that's deleted, excluded or hidden is
        let go."""
        data = event.data
        light = data["entity_id"]
        if not light.startswith(f"{LIGHT}."):
            return
        if data["action"] == "remove":
            self.set_scene([light], "", leave_auto=True)
            self._rename_in_dimmers(light, None)
            self._rename_in_layouts(light, None)
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
                self._rename_in_layouts(old, light)
            self._release_excluded()

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
        now = dt_util.utcnow()
        self._easing = {light: until for light, until in self._easing.items() if until > now}
        if lights := [light for light in self.on(self.in_scene(scene)) if light not in self._easing]:
            self.spawn(self.apply(scene, lights, 60))

    async def ease(self, lights: list[str], light: Light, until: datetime) -> None:
        """Fade lights from how they are to `light`, getting there at `until`;
        following their scene leaves them alone until then, unless they're
        switched off or dimmed."""
        for entity_id in lights:
            self._easing[entity_id] = until
        await self._send(lights, light, max((until - dt_util.utcnow()).total_seconds(), 2))

    def evening_at(self, when: datetime) -> Evening:
        """Where the evening routine will be at `when`."""
        return self._targets(dt_util.as_local(when))[1]
