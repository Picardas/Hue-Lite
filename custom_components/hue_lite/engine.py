"""Hue-Lite's engine: the settings, which scene each light is in, and
following the clock. The routines are in routines.py, the Hue dimmers in
dimmers.py, choosing lights in targets.py and sending them commands in
lights.py.

Each light Hue-Lite has put into a scene remembers it in `scenes`: Natural
light, the evening routine, a preset, a colour scene (whose colours drift),
or the wake-up fade-in. Picking another scene replaces it; changing the light
yourself or from an automation, or activating one of Home Assistant's own
scenes with it, forgets it. Turning a light off keeps its scene, so a light in
Natural light or the routine picks it up again when it comes back on.

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
from homeassistant.const import ATTR_ENTITY_ID, ATTR_LABEL_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
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
    CONF_DIMMERS,
    CONF_PRESENCE,
    DEFAULT_PRESENCE,
    DEFAULT_SETTINGS,
    DOMAIN,
    DRIFT_MINUTES,
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
from .targets import LIGHT, excluded, resolve
from .timeline import (
    PALETTES,
    PRESETS,
    Evening,
    EveningSettings,
    SunTimes,
    Target,
    evening_routine,
    minutes,
    natural_light,
    next_scene,
)

type HueLiteConfigEntry = ConfigEntry[HueLite]

# Every scene a light can be in.
SCENES = frozenset({NATURAL, ROUTINE, WAKE_UP, *PRESETS, *PALETTES})


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
    if isinstance(was := settings.get("evening_routine_enabled"), bool):
        settings = {"evening_weeknights": was, "evening_weekends": was, **settings}
    renamed = {"movie": "nebula"}
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
            if isinstance(was, str) and (scene := renamed.get(was, was)) in SCENES
        },
        "auto": [light for light in auto if isinstance(light, str)],
        "last_wake_up": _instant(data.get("last_wake_up")),
        "last_evening": _day(data.get("last_evening")),
    }


def _instant(value: Any) -> datetime | None:
    """A stored time with its time zone, or None."""
    try:
        when = dt_util.parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return when if when and when.tzinfo else None


def _day(value: Any) -> date | None:
    """A stored date, or None."""
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


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
        # Hue-Lite's entity ids by key, for the dashboard (filled in as they're added).
        self.entity_ids: dict[str, str] = {}
        self.device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=NAME, entry_type=DeviceEntryType.SERVICE)
        self.last_wake_up: datetime | None = None
        # The evening (by its day) the routine last started on, so it starts
        # once a night however it's started (see Routines._welcome_home).
        self.last_evening: date | None = None
        # The target each followed scene's lights were last moved to.
        self.followed: dict[str, Target] = {}
        self.sender = Sender(hass)
        self.routines = Routines(self)
        self._dimmers: bool = entry.options.get(CONF_DIMMERS, True)
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._sun: tuple[date, SunTimes] | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unwatch: CALLBACK_TYPE | None = None
        self._watched: frozenset[str] = frozenset()
        # Each light's brightness when it was last turned off, to tell a plain
        # switch-on from one at a brightness of someone's own.
        self._off_brightness: dict[str, int] = {}
        # The colour scenes' drift step their bulbs were last moved on at.
        self._drifted = self._drift_step()

    # ---- Lifecycle -----------------------------------------------------------

    async def async_load(self) -> None:
        """Load the saved settings and scenes, and work out where the timelines are."""
        data = _stored(await self._store.async_load())
        self.settings.update(data["settings"])
        self.last_wake_up = data["last_wake_up"]
        self.last_evening = data["last_evening"]
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
            bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._release_excluded),
            bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, self._release_excluded),
            bus.async_listen(lr.EVENT_LABEL_REGISTRY_UPDATED, self._check_labels),
            *self.routines.async_start(),
        ]
        if self._dimmers:
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
            "last_wake_up": self.last_wake_up.isoformat() if self.last_wake_up else None,
            "last_evening": self.last_evening.isoformat() if self.last_evening else None,
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

    # ---- Scenes --------------------------------------------------------------

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
        """Put lights in `scene` ('' for none); whether any changed."""
        changed = False
        for light in lights:
            if self.scenes.get(light, "") == scene or (scene and self.excluded(light)):
                continue
            changed = True
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
        if preset in PALETTES:
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
            await self.apply(NATURAL, lights, 2, parent)

    async def async_next_scene(self, lights: list[str], parent: Context | None = None) -> None:
        """Like pressing a Hue dimmer's On button again: Natural light, then
        the presets in turn, and round. Lights that are off, or in something
        not in that cycle, go to Natural light."""
        if not lights:
            return
        scene = next_scene(self.current_scene(lights))
        if scene == NATURAL:
            await self.async_natural_light(lights, parent=parent)
        else:
            await self.async_preset(lights, scene, parent)

    async def apply(self, scene: str, lights: list[str], transition: float, parent: Context | None = None) -> None:
        """Send Natural light's or the routine's current light to the lights,
        turning them on. Nightlight-labelled lights get Nightlight during
        Natural light's night and the routine's nightlight phase."""
        target = self.target(scene)
        night = target.phase == ("night" if scene == NATURAL else "nightlight")
        nightlights = set(self.labelled("nightlight")) if night else set()
        await self.sender.send([light for light in lights if light in nightlights], PRESETS["nightlight"], transition, parent)
        await self.sender.send([light for light in lights if light not in nightlights], target.light, transition, parent)

    # ---- Colour scenes -------------------------------------------------------

    async def send_palette(self, scene: str, lights: list[str], transition: float, parent: Context | None = None) -> None:
        """Turn lights on in a colour scene. Each colour bulb gets the colour
        for its place among the scene's colour bulbs, moved on one colour
        every DRIFT_MINUTES, so a room shows several and they drift; white
        bulbs get the scene's white."""
        palette, has_colour = PALETTES[scene], self.sender.has_colour
        colour = [light for light in lights if has_colour(light)]
        order = sorted({*colour, *(light for light in self.in_scene(scene) if has_colour(light))})
        step = self._drift_step()
        by_colour: dict[int, list[str]] = {}
        for light in colour:
            by_colour.setdefault((order.index(light) + step) % len(palette.colours), []).append(light)
        for index, group in sorted(by_colour.items()):
            await self.sender.send(group, palette.colour(index), transition, parent)
        await self.sender.send([light for light in lights if light not in colour], palette.white, transition, parent)

    @staticmethod
    def _drift_step() -> int:
        return int(dt_util.utcnow().timestamp()) // (DRIFT_MINUTES * 60)

    @callback
    def _drift(self) -> None:
        """Every DRIFT_MINUTES, start the colour scenes' bulbs that are on
        moving to their next colour, taking until the next move."""
        if (step := self._drift_step()) == self._drifted:
            return
        self._drifted = step
        for scene in PALETTES:
            if lights := [light for light in self.on(self.in_scene(scene)) if self.sender.has_colour(light)]:
                self.spawn(self.send_palette(scene, lights, DRIFT_MINUTES * 60))

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
        on and off. A wake-up light that's switched off is done with."""
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
        if by_someone and (old.state == STATE_ON or self._own_brightness(new)):
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
        """A light keeps its scene and Auto when its entity id changes; one
        that's deleted, excluded or hidden is let go."""
        data = event.data
        light = data["entity_id"]
        if not light.startswith(f"{LIGHT}."):
            return
        if data["action"] == "remove":
            self.set_scene([light], "", leave_auto=True)
        elif data["action"] == "update":
            if (old := data.get("old_entity_id")) and (old in self.scenes or old in self.auto):
                if scene := self.scenes.pop(old, ""):
                    self.scenes[light] = scene
                if old in self.auto:
                    self.auto.remove(old)
                    self.auto.add(light)
                self._changed()
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
