"""Hue-Lite's engine: the settings, which lights are in which scene, and the
listeners that run the scenes and routines (what the YAML setup's automations did).

Each light Hue-Lite has put into a scene remembers it in `scenes`: the Natural
light scene, the evening routine, or a preset. Picking another scene replaces
it, and changing the light by hand forgets it. Remembering, rather than
recognising a preset from the light's colour, works for white and dimmable
bulbs too. Turning a light off keeps its scene, so a light in Natural light or
the routine picks it up again when it comes back on.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Coroutine, Mapping
from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_FLOOR_ID,
    ATTR_LABEL_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import (
    TrackStates,
    async_call_later,
    async_track_state_change_event,
    async_track_state_change_filtered,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date, is_up
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DIMMERS,
    CONF_PRESENCE,
    DEFAULT_PRESENCE,
    DEFAULT_SETTINGS,
    DIMMER_COMMANDS,
    DIMMER_MODEL_PREFIX,
    DOMAIN,
    EVENING_PHASES,
    LEAVING_DELAY,
    NAME,
    NATURAL,
    ROLES,
    ROUTINE,
    STORAGE_KEY,
    STORAGE_VERSION,
    TIMES,
    label_option,
)
from .timeline import (
    PRESETS,
    Evening,
    EveningSettings,
    Light,
    SunTimes,
    Target,
    evening_routine,
    minutes,
    natural_light,
    next_scene,
)

_LOGGER = logging.getLogger(__name__)

LIGHT = "light"
COLOUR_MODES = frozenset({"hs", "xy", "rgb", "rgbw", "rgbww"})

type HueLiteConfigEntry = ConfigEntry[HueLite]


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


def _people(state: State | None) -> int | None:
    """The number of people a zone's state says are in it; None while it's
    unknown or unavailable."""
    try:
        return int(state.state) if state else None
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
        self.dimmers: bool = entry.options.get(CONF_DIMMERS, True)
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        # Light -> the scene it's in: NATURAL, ROUTINE or a preset id.
        self.scenes: dict[str, str] = {}
        # Hue-Lite's entity ids by key, for the dashboard (filled in as they're added).
        self.entity_ids: dict[str, str] = {}
        self.device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=NAME, entry_type=DeviceEntryType.SERVICE)
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._last_wake_up: datetime | None = None
        self._sun: tuple[date, SunTimes] | None = None
        # The target each scene's lights were last moved to.
        self._followed: dict[str, Target] = {}
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unwatch: CALLBACK_TYPE | None = None
        self._watched: frozenset[str] = frozenset()
        self._leaving: CALLBACK_TYPE | None = None
        # Contexts of Hue-Lite's own light changes, so they're not taken for changes by hand.
        self._ours: deque[str] = deque(maxlen=100)

    # ---- Lifecycle -----------------------------------------------------------

    async def async_load(self) -> None:
        """Load the saved settings and scene lists, and work out where the timelines are."""
        data = await self._store.async_load() or {}
        # A stored value of the wrong kind (a bad time, say) would break every
        # minute's tick, so those keep their defaults.
        for key, value in data.get("settings", {}).items():
            if key in self.settings and type(value) is type(self.settings[key]):
                if key in TIMES and not _is_time(value):
                    continue
                self.settings[key] = value
        # Lights excluded (or hidden) since they were saved are let go.
        self.scenes.update(
            {
                light: scene
                for light, scene in data.get("scenes", {}).items()
                if isinstance(scene, str) and not self._excluded(light)
            }
        )
        if woke := data.get("last_wake_up"):
            self._last_wake_up = dt_util.parse_datetime(woke)
        self.natural, self.evening = self._targets(dt_util.now())
        self._followed = {NATURAL: self.natural, ROUTINE: self.evening}
        self._shown = (self.natural, self.evening)

    @callback
    def async_start(self) -> None:
        """Start following the clock, the home zone, the dimmers, the lights in
        scenes and renamed or removed lights."""
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))
        self._unsubs.append(async_track_state_change_event(self.hass, [self.presence], self._presence_changed))
        self._unsubs.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated))
        scenes = async_track_state_change_filtered(self.hass, TrackStates(False, set(), {"scene"}), self._scene_activated)
        self._unsubs.append(scenes.async_remove)
        if self.dimmers:
            self._unsubs.append(self.hass.bus.async_listen("zha_event", self._zha_event))
        self._watch_lights()

    @callback
    def async_stop(self) -> None:
        for unsub in (*self._unsubs, self._unwatch, self._leaving):
            if unsub:
                unsub()
        self._unsubs.clear()
        self._unwatch = self._leaving = None
        self._watched = frozenset()

    def _data(self) -> dict[str, Any]:
        return {
            "settings": self.settings,
            "scenes": dict(sorted(self.scenes.items())),
            "last_wake_up": self._last_wake_up.isoformat() if self._last_wake_up else None,
        }

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._data, 5)

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    # ---- Entities ------------------------------------------------------------

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call `update` whenever the timelines or scene lists change."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    @callback
    def async_set_setting(self, key: str, value: Any) -> None:
        """Change a setting; the timelines follow straight away."""
        self.settings[key] = value
        self._save()
        self._refresh()

    @callback
    def frontend_config(self) -> dict[str, Any]:
        """What the dashboard needs: the role labels, the labels it can show
        (role labels and labels on lights; Home Assistant only lists every
        label to admins) and Hue-Lite's entity ids."""
        wanted = set(self.labels.values()) | {
            label
            for entry in er.async_get(self.hass).entities.values()
            if entry.domain == LIGHT
            for label in entry.labels
        }
        return {
            "roles": self.labels,
            "labels": [
                {"label_id": label.label_id, "name": label.name, "icon": label.icon}
                for label in lr.async_get(self.hass).async_list_labels()
                if label.label_id in wanted
            ],
            "entities": self.entity_ids,
        }

    def in_scene(self, scene: str) -> list[str]:
        """The lights in a scene (NATURAL, ROUTINE or a preset id)."""
        return sorted(light for light, current in self.scenes.items() if current == scene)

    def target(self, scene: str) -> Target:
        """Where a followed scene (NATURAL or ROUTINE) is, and the light it wants now."""
        return self.natural if scene == NATURAL else self.evening

    # ---- Choosing lights -----------------------------------------------------

    @callback
    def lights(self, target: Mapping[str, Any] | None = None) -> list[str]:
        """The lights a target means: entity_id, area_id, floor_id, device_id
        and label_id, as in an action's target, or every light when it's empty.
        Lights given by id are used as they are; the others skip hidden lights,
        light groups and lights labelled Exclude from lighting. Unavailable
        lights are always left out."""
        target = target or {}
        given = set(target.get(ATTR_ENTITY_ID) or ())
        areas, floors, devices, labels = (
            set(target.get(key) or ()) for key in (ATTR_AREA_ID, ATTR_FLOOR_ID, ATTR_DEVICE_ID, ATTR_LABEL_ID)
        )
        found: set[str] = set()
        if areas or floors or devices or labels:
            found = self._expand(areas, floors, devices, labels)
        elif not given:
            found = set(self.hass.states.async_entity_ids(LIGHT))
        found = {entity_id for entity_id in found if self._takes_part(entity_id)}
        return sorted(
            entity_id
            for entity_id in found | given
            if entity_id.startswith(f"{LIGHT}.")
            and (state := self.hass.states.get(entity_id)) is not None
            and state.state != STATE_UNAVAILABLE
        )

    def _expand(self, areas: set[str], floors: set[str], devices: set[str], labels: set[str]) -> set[str]:
        """Entity ids in the areas (and their floors), on the devices or with the labels."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        areas = areas | {area.id for floor in floors for area in ar.async_entries_for_floor(area_reg, floor)}
        found: set[str] = set()
        for area in areas:
            found |= {entry.entity_id for entry in er.async_entries_for_area(ent_reg, area)}
            # A device's entities are in its area unless they have one of their own.
            for device in dr.async_entries_for_area(dev_reg, area):
                found |= {e.entity_id for e in er.async_entries_for_device(ent_reg, device.id) if e.area_id is None}
        for device in devices:
            found |= {entry.entity_id for entry in er.async_entries_for_device(ent_reg, device)}
        for label in labels:
            found |= {entry.entity_id for entry in er.async_entries_for_label(ent_reg, label)}
        return found

    def _excluded(self, entity_id: str) -> bool:
        """Whether a light is left out of Hue-Lite: labelled Exclude from
        lighting, hidden, or a config/diagnostic entity."""
        entry = er.async_get(self.hass).async_get(entity_id)
        return entry is not None and bool(entry.hidden or entry.entity_category or self.labels["exclude"] in entry.labels)

    def _takes_part(self, entity_id: str) -> bool:
        if self._excluded(entity_id):
            return False
        # Light groups would double up with their members.
        state = self.hass.states.get(entity_id)
        return state is not None and not isinstance(state.attributes.get(ATTR_ENTITY_ID), list)

    def _labelled(self, role: str) -> list[str]:
        """The lights with a role's label (Evening, Wake up, ...)."""
        return self.lights({ATTR_LABEL_ID: [self.labels[role]]})

    def _on(self, lights: list[str]) -> list[str]:
        return [entity_id for entity_id in lights if self.hass.states.is_state(entity_id, STATE_ON)]

    # ---- Scenes --------------------------------------------------------------

    @callback
    def _set_scene(self, lights: list[str], scene: str) -> None:
        """Lights are now in `scene` (NATURAL, ROUTINE or a preset id), or with
        '' in none (changed by hand, or done with). Excluded lights are never
        kept in one: named in an action they're set once, not followed."""
        changed = False
        for light in lights:
            if self.scenes.get(light, "") == scene or (scene and self._excluded(light)):
                continue
            changed = True
            if scene:
                self.scenes[light] = scene
            else:
                del self.scenes[light]
        if changed:
            self._save()
            self._watch_lights()
            self._notify()

    def current_scene(self, lights: list[str]) -> str:
        """The scene every light that's on is in, or '' when none are on or
        they're in different scenes (or none)."""
        scenes = {self.scenes.get(light, "") for light in self._on(lights)}
        return scenes.pop() if len(scenes) == 1 else ""

    async def async_preset(self, lights: list[str], preset: str, parent: Context | None = None) -> None:
        if not lights:
            return
        self._set_scene(lights, preset)
        await self._send(lights, PRESETS[preset], 0.4, parent)

    async def async_natural_light(self, lights: list[str], follow: bool = True, parent: Context | None = None) -> None:
        """Put lights into the Natural light scene (turning them on), or with
        follow off take the ones in it out (leaving other scenes alone)."""
        if not follow:
            self._set_scene([light for light in lights if self.scenes.get(light) == NATURAL], "")
        elif lights:
            self._set_scene(lights, NATURAL)
            await self._apply(NATURAL, lights, 2, parent)

    async def async_next_scene(self, lights: list[str], parent: Context | None = None) -> None:
        """Like pressing a Hue dimmer's On button again: Natural light,
        Energize, Cool bright, Relax, Rest, Nightlight, and round. Lights that
        are off, or in something unrecognised, go to Natural light."""
        if not lights:
            return
        scene = next_scene(self.current_scene(lights))
        if scene == NATURAL:
            await self.async_natural_light(lights, parent=parent)
        else:
            await self.async_preset(lights, scene, parent)

    async def _apply(self, scene: str, lights: list[str], transition: float, parent: Context | None = None) -> None:
        """Send the Natural light scene's or the routine's current light to
        the lights, turning them on. Nightlight-labelled lights get Nightlight
        during Natural light's night and the routine's nightlight phase."""
        target = self.target(scene)
        night = target.phase == ("night" if scene == NATURAL else "nightlight")
        nightlights = set(self._labelled("nightlight")) if night else set()
        await self._send([light for light in lights if light in nightlights], PRESETS["nightlight"], transition, parent)
        await self._send([light for light in lights if light not in nightlights], target.light, transition, parent)

    async def _send(self, lights: list[str], light: Light, transition: float, parent: Context | None = None) -> None:
        """Turn lights on at `light`: its xy colour on bulbs with colour, its
        colour temperature on the rest."""
        colour = [entity_id for entity_id in lights if self._has_colour(entity_id)]
        white = [entity_id for entity_id in lights if entity_id not in colour]
        if colour:
            await self._call("turn_on", colour, parent=parent, **light.colour_data(), transition=transition)
        if white:
            await self._call("turn_on", white, parent=parent, **light.white_data(), transition=transition)

    def _has_colour(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        modes = state.attributes.get("supported_color_modes") if state else None
        # Unknown: send colour, which Home Assistant converts if it has to.
        return modes is None or bool(COLOUR_MODES.intersection(modes))

    async def _call(self, service: str, lights: list[str], *, parent: Context | None = None, **data: Any) -> None:
        """Call a light action as Hue-Lite: a context with no user, so the
        change isn't taken for one made by hand, linked to what caused it (an
        action call, a dimmer press) so the logbook can say. One unreachable
        bulb is logged and doesn't hold up the rest."""
        context = Context(parent_id=parent.id if parent else None)
        self._ours.append(context.id)
        try:
            await self.hass.services.async_call(
                LIGHT, service, {ATTR_ENTITY_ID: lights, **data}, blocking=True, context=context
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Couldn't %s %s: %s", service.replace("_", " "), ", ".join(lights), err)

    @callback
    def _spawn(self, target: Coroutine[Any, Any, None]) -> None:
        self.entry.async_create_background_task(self.hass, target, f"{DOMAIN} lights")

    @callback
    def _watch_lights(self) -> None:
        """Follow state changes of the lights in a scene (re-subscribing only
        when that set of lights changes)."""
        watched = frozenset(self.scenes)
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
        """A light in Natural light or the routine that's turned on picks up its
        scene's current light; changing a light that's on yourself (from the
        dashboard, a light dialog, ...) takes it out of its scene."""
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None or new.state != STATE_ON or new.context.id in self._ours:
            return
        light = new.entity_id
        scene = self.scenes.get(light)
        if old.state != STATE_ON:
            if scene == NATURAL or (scene == ROUTINE and self.evening.phase in EVENING_PHASES):
                self._spawn(self._apply(scene, [light], 2))
        elif new.context.user_id is not None:
            self._set_scene([light], "")

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
        self._check_wake_up(dt_util.now())

    @callback
    def _refresh(self) -> None:
        """Work out where both timelines are now, move the lights that follow
        them, and run the evening routine's start and bedtime."""
        evening = self.evening
        self.natural, self.evening = self._targets(dt_util.now())
        # The sensors change as visibly as the lights do, not every minute.
        shown_natural, shown_evening = self._shown
        if self.natural.moved_from(shown_natural) or self.evening.moved_from(shown_evening):
            self._shown = (self.natural, self.evening)
            self._notify()

        self._follow(NATURAL)
        was, phase = evening.phase, self.evening.phase
        if was == "day" and phase in EVENING_PHASES:
            # The start turns the routine's lights on at its current light.
            self._followed[ROUTINE] = self.evening
            self._spawn(self._start_evening())
        elif phase in EVENING_PHASES:
            self._follow(ROUTINE)
        elif was in EVENING_PHASES and phase == "night":
            self._spawn(self._bedtime())

    @callback
    def _follow(self, scene: str) -> None:
        """Move a scene's lights that are on to its current light, once that
        has changed phase or moved noticeably since they were last moved. Slow
        blends then send a command every few minutes rather than every minute."""
        target, last = self.target(scene), self._followed.get(scene)
        if last is not None and not target.moved_from(last):
            return
        self._followed[scene] = target
        if lights := self._on(self.in_scene(scene)):
            self._spawn(self._apply(scene, lights, 60))

    async def _start_evening(self) -> None:
        """The evening starts: the routine takes the Evening lights and turns
        them on dim, if it's on and someone's home."""
        if not self.settings["evening_routine_enabled"] or not _people(self.hass.states.get(self.presence)):
            return
        if lights := self._labelled("evening"):
            self._set_scene(lights, ROUTINE)
            await self._apply(ROUTINE, lights, 5)

    async def _bedtime(self) -> None:
        """Bedtime: fade out the routine's lights, which it's then done with
        for tonight. Lights moved to another scene during the evening (Natural
        light included) aren't in its list any more, so they're left alone."""
        if not (lights := self.in_scene(ROUTINE)):
            return
        self._set_scene(lights, "")
        if on := self._on(lights):
            await self._call("turn_off", on, transition=self.settings["bedtime_fade_minutes"] * 60)

    # ---- Wake up -------------------------------------------------------------

    @callback
    def _check_wake_up(self, now: datetime) -> None:
        """Start the wake-up fade-in or turn the lights off afterwards when it's
        time. Both can be on another day than the wake-up itself (a 00:15 wake
        up fades in from 23:45), so each uses that wake-up day's time and
        toggle."""
        s = self.settings
        minute = now.replace(tzinfo=None, second=0, microsecond=0)
        fade, off_after = timedelta(minutes=s["wake_up_fade_minutes"]), timedelta(minutes=s["wake_up_off_after_minutes"])
        for day in (minute.date() - timedelta(days=1), minute.date(), minute.date() + timedelta(days=1)):
            weekend = day.isoweekday() in (6, 7)
            wake = datetime.combine(day, time.fromisoformat(s["wake_up_weekend" if weekend else "wake_up_weekday"]))
            if minute == wake - fade and s["wake_up_weekends" if weekend else "wake_up_weekdays"]:
                lights = self._labelled("wake_up")
                if lights and not self._on(lights):
                    self._spawn(self._wake_up(lights))
            woke = self._last_wake_up
            if (
                minute == wake + off_after
                and s["wake_up_auto_off"]
                and woke is not None
                and woke > dt_util.utcnow() - timedelta(hours=6)
                and (on := self._on(self._labelled("wake_up")))
            ):
                self._spawn(self._call("turn_off", on, transition=60))

    async def _wake_up(self, lights: list[str]) -> None:
        """Fade from a dim warm glow up to Energize, reaching it at the wake-up
        time. Like picking a scene, this takes the lights out of Natural light
        and the routine, so neither moves them during the fade."""
        self._last_wake_up = dt_util.utcnow()
        self._set_scene(lights, "")
        await self._call("turn_on", lights, brightness=1, color_temp_kelvin=2200)
        await asyncio.sleep(2)
        await self._send(lights, PRESETS["energize"], self.settings["wake_up_fade_minutes"] * 60)

    # ---- Coming and going ----------------------------------------------------

    @callback
    def _presence_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None or old.state == new.state:
            return
        if self._leaving:
            self._leaving()
            self._leaving = None
        if new.state == "0":
            # Gone for a while, so a brief location glitch doesn't count.
            self._leaving = async_call_later(self.hass, LEAVING_DELAY, self._everyone_left)
        # Only a real count going up: not the zone coming back from unknown.
        before, after = _people(old), _people(new)
        if before is not None and after is not None and after > before:
            self._someone_arrived(new.context)

    @callback
    def _someone_arrived(self, parent: Context) -> None:
        """Put the Coming home lights that are off into Natural light
        (optionally only after dark)."""
        s = self.settings
        if not s["coming_home_enabled"] or (s["coming_home_only_dark"] and is_up(self.hass)):
            return
        lights = [entity_id for entity_id in self._labelled("coming_home") if self.hass.states.is_state(entity_id, STATE_OFF)]
        if lights:
            self._spawn(self.async_natural_light(lights, parent=parent))

    @callback
    def _everyone_left(self, _now: datetime) -> None:
        self._leaving = None
        if self.settings["leaving_home_enabled"] and (on := self._on(self.lights())):
            self._spawn(self._call("turn_off", on, transition=2))

    # ---- Home Assistant's own scenes ---------------------------------------------

    @callback
    def _scene_activated(self, event: Event[EventStateChangedData]) -> None:
        """One of Home Assistant's own scenes (scene editor, scenes.yaml) was
        activated. Like picking a scene in Hue, that takes its lights out of
        Hue-Lite's scenes, so Natural light and the routine don't override it.
        Home Assistant records the activation before it sets the lights."""
        old, new = event.data["old_state"], event.data["new_state"]
        # Appearing, disappearing or coming back after a reload isn't an activation.
        if old is None or new is None or old.state == new.state or STATE_UNAVAILABLE in (old.state, new.state):
            return
        lights = new.attributes.get(ATTR_ENTITY_ID) or ()
        if isinstance(lights, str):
            lights = [lights]
        self._set_scene([light for light in lights if light.startswith(f"{LIGHT}.")], "")

    # ---- Renamed and removed lights --------------------------------------------

    @callback
    def _registry_updated(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        """A light keeps its scene when its entity id changes; one that's
        deleted, or excluded or hidden, is let go."""
        data = event.data
        light = data["entity_id"]
        if not light.startswith(f"{LIGHT}."):
            return
        if data["action"] == "remove":
            self._set_scene([light], "")
            return
        if data["action"] != "update":
            return
        if (old := data.get("old_entity_id")) and (scene := self.scenes.get(old)):
            self._set_scene([old], "")
            self._set_scene([light], scene)
        if light in self.scenes and self._excluded(light):
            self._set_scene([light], "")

    # ---- Hue dimmer switches -------------------------------------------------

    @callback
    def _zha_event(self, event: Event) -> None:
        """A Hue Dimmer Switch (paired with ZHA) controls the lights in its area."""
        command = event.data.get("command")
        if command not in DIMMER_COMMANDS:
            return
        device = dr.async_get(self.hass).async_get(event.data.get("device_id") or "")
        if device is None or not (device.model or "").startswith(DIMMER_MODEL_PREFIX) or device.area_id is None:
            return
        lights = self.lights({ATTR_AREA_ID: [device.area_id]})
        self._spawn(self._dimmer(command.split("_")[0], lights, event.context))

    async def _dimmer(self, button: str, lights: list[str], parent: Context) -> None:
        """On: next scene. Off: off. Up and Down: 10% brighter or dimmer, with
        Down stopping at about 10%. Dimming counts as a change by hand."""
        if not lights:
            return
        if button == "on":
            await self.async_next_scene(lights, parent)
        elif button == "off":
            await self._call("turn_off", lights, parent=parent, transition=1)
        else:
            dimming = lights if button == "up" else [
                s.entity_id
                for entity_id in self._on(lights)
                if (s := self.hass.states.get(entity_id)) and (s.attributes.get("brightness") or 0) > 26
            ]
            if dimming:
                self._set_scene(dimming, "")
                step = 10 if button == "up" else -10
                await self._call("turn_on", dimming, parent=parent, brightness_step_pct=step, transition=0.4)
