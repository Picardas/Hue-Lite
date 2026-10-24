"""The routines: the evening routine, wake up, and Coming & Going."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, State, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.sun import is_up
from homeassistant.util import dt as dt_util

from .const import EVENING_PHASES, LEAVING_DELAY, NATURAL, ROUTINE, WAKE_UP
from .timeline import PRESETS, Evening

if TYPE_CHECKING:
    from .engine import HueLite


def _people(state: State | None) -> int | None:
    """The number of people a zone's state says are in it; None while it's
    unknown or unavailable."""
    try:
        return int(state.state) if state else None
    except ValueError:
        return None


class Routines:
    def __init__(self, lite: HueLite) -> None:
        self.lite = lite
        self._leaving: CALLBACK_TYPE | None = None
        # The end of the last wake-up check: each check covers the time since.
        self._checked = dt_util.now()

    @callback
    def async_start(self) -> list[CALLBACK_TYPE]:
        self._checked = dt_util.now()
        return [async_track_state_change_event(self.lite.hass, [self.lite.presence], self._presence_changed)]

    @callback
    def async_stop(self) -> None:
        if self._leaving:
            self._leaving()
            self._leaving = None

    # ---- Evening routine -----------------------------------------------------

    @callback
    def evening_moved(self, was: Evening) -> None:
        """The routine's timeline has been worked out again: start it, move
        its lights, or fade them out at bedtime."""
        lite = self.lite
        phase = lite.evening.phase
        if was.phase == "day" and phase in EVENING_PHASES:
            self._start_evening()
        elif phase in EVENING_PHASES:
            lite.follow(ROUTINE)
        elif was.phase in EVENING_PHASES and phase == "night":
            lite.spawn(self._bedtime())

    @property
    def _tonight(self) -> str:
        """The switch for tonight's evening: Sun–Thu or Fri & Sat nights."""
        return "evening_weekends" if self.lite.evening.weekend else "evening_weeknights"

    @property
    def evening_on(self) -> bool:
        """Whether the evening routine is on tonight."""
        return self.lite.settings[self._tonight]

    @callback
    def setting_changed(self, key: str, value: object) -> None:
        """Switching tonight's evening routine off lets its lights go (they
        stay as they are); switching it on during the evening starts it. The
        other nights' switch doesn't change tonight."""
        if key != self._tonight:
            return
        if not value:
            self.lite.set_scene(self.lite.in_scene(ROUTINE), "")
        elif self.lite.evening.phase in EVENING_PHASES:
            self._start_evening()

    @property
    def asking(self) -> str:
        """What the routines ask of a light on Auto right now: the evening
        routine (ROUTINE) on tonight's evening if someone's home, wake up
        (WAKE_UP) from its fade-in until its lights would go off after it, or
        nothing ('', so off)."""
        lite = self.lite
        if self.evening_on and lite.evening.phase in EVENING_PHASES and _people(lite.hass.states.get(lite.presence)):
            return ROUTINE
        if (woke := lite.last_wake_up) and lite.in_scene(WAKE_UP):
            s = lite.settings
            if dt_util.utcnow() < woke + timedelta(minutes=s["wake_up_fade_minutes"] + s["wake_up_off_after_minutes"]):
                return WAKE_UP
        return ""

    async def match(self, scene: str, lights: list[str]) -> None:
        """Set lights just put on Auto as the routines ask (see `asking`): the
        evening routine's light, wake up's (Energize by the wake-up time), or
        off."""
        lite = self.lite
        if scene == ROUTINE:
            await lite.apply(ROUTINE, lights, 2)
        elif scene == WAKE_UP:
            woke = lite.last_wake_up or dt_util.utcnow()
            left = woke + timedelta(minutes=lite.settings["wake_up_fade_minutes"]) - dt_util.utcnow()
            await lite.sender.send(lights, PRESETS["energize"], max(left.total_seconds(), 2))
        elif on := lite.on(lights):
            await lite.sender.call("turn_off", on, transition=2)

    @callback
    def _start_evening(self) -> None:
        """The routine takes its lights (Evening-labelled and on Auto) and
        turns them on at its current light, if it's on tonight and someone's
        home."""
        lite = self.lite
        if not self.evening_on or not _people(lite.hass.states.get(lite.presence)):
            return
        lite.followed[ROUTINE] = lite.evening
        if lights := lite.routine_lights("evening"):
            lite.set_scene(lights, ROUTINE)
            lite.spawn(lite.apply(ROUTINE, lights, 5))

    async def _bedtime(self) -> None:
        """Fade out the routine's lights, which it's then done with for
        tonight. Lights moved to another scene during the evening (Natural
        light included) aren't the routine's any more, so they're left alone."""
        lite = self.lite
        if not (lights := lite.in_scene(ROUTINE)):
            return
        lite.set_scene(lights, "")
        if on := lite.on(lights):
            await lite.sender.call("turn_off", on, transition=lite.settings["bedtime_fade_minutes"] * 60)

    # ---- Wake up -------------------------------------------------------------

    @callback
    def tick(self, now: datetime) -> None:
        """Start the wake-up fade-in, or turn the lights off afterwards, when
        its time has come since the last check. Comparing instants rather
        than clock readings copes with the clocks changing (a time in the
        skipped hour still happens, one in the repeated hour happens once)
        and with a missed tick. Either can fall on another day than the
        wake-up itself (a 00:15 wake-up fades in from 23:45), so each uses that
        wake-up day's time and toggle."""
        since, self._checked = self._checked, now
        s = self.lite.settings
        fade, off_after = timedelta(minutes=s["wake_up_fade_minutes"]), timedelta(minutes=s["wake_up_off_after_minutes"])
        day = since.date() - timedelta(days=1)
        while day <= now.date() + timedelta(days=1):
            weekend = day.isoweekday() in (6, 7)
            wake = self._at(day, s["wake_up_weekend" if weekend else "wake_up_weekday"], now)
            if since < wake - fade <= now and s["wake_up_weekends" if weekend else "wake_up_weekdays"]:
                self._start_wake_up()
            if since < wake + off_after <= now and s["wake_up_auto_off"]:
                self._wake_up_over()
            day += timedelta(days=1)

    @staticmethod
    def _at(day: date, clock: str, now: datetime) -> datetime:
        """`clock` on `day` in the local time zone, as an instant."""
        return dt_util.as_utc(datetime.combine(day, time.fromisoformat(clock), tzinfo=now.tzinfo))

    @callback
    def _start_wake_up(self) -> None:
        lights = self.lite.routine_lights("wake_up")
        if lights and not self.lite.on(lights):
            self.lite.spawn(self._wake_up(lights))

    async def _wake_up(self, lights: list[str]) -> None:
        """Fade from a dim warm glow up to Energize, reaching it at the wake-up
        time. The lights are marked as wake-up's (which takes them out of
        Natural light and the routine, so neither moves them during the fade)
        until they're changed or turned off."""
        lite = self.lite
        lite.last_wake_up = dt_util.utcnow()
        lite.set_scene(lights, WAKE_UP)
        await lite.sender.call("turn_on", lights, brightness=1, color_temp_kelvin=2200)
        await asyncio.sleep(2)
        await lite.sender.send(lights, PRESETS["energize"], lite.settings["wake_up_fade_minutes"] * 60)

    @callback
    def _wake_up_over(self) -> None:
        """Turn off the lights still as this morning's wake-up left them: not
        ones put into a scene or changed since."""
        lite = self.lite
        woke = lite.last_wake_up
        if woke is None or woke < dt_util.utcnow() - timedelta(hours=6):
            return
        lights = lite.in_scene(WAKE_UP)
        lite.set_scene(lights, "")
        if on := lite.on(lights):
            lite.spawn(lite.sender.call("turn_off", on, transition=60))

    # ---- Coming & Going ------------------------------------------------------

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
            self._leaving = async_call_later(self.lite.hass, LEAVING_DELAY, self._everyone_left)
        # Only a real count going up: not the zone coming back from unknown.
        before, after = _people(old), _people(new)
        if before is not None and after is not None and after > before:
            self._someone_arrived(new.context)

    @callback
    def _someone_arrived(self, parent: Context) -> None:
        """Put the Coming home lights that are off into Natural light
        (optionally only after dark). That's the routine's doing, not a scene
        someone picked, so lights on Auto stay on it."""
        lite = self.lite
        s = lite.settings
        if not s["coming_home_enabled"] or (s["coming_home_only_dark"] and is_up(lite.hass)):
            return
        lights = [light for light in lite.labelled("coming_home") if lite.hass.states.is_state(light, STATE_OFF)]
        if lights:
            lite.set_scene(lights, NATURAL)
            lite.spawn(lite.apply(NATURAL, lights, 2, parent))

    @callback
    def _everyone_left(self, _now: datetime) -> None:
        self._leaving = None
        lite = self.lite
        if lite.settings["leaving_home_enabled"] and (on := lite.on(lite.lights())):
            lite.spawn(lite.sender.call("turn_off", on, transition=2))
