"""The routines: the evening routine, wake up, Coming & Going, and Away, and
catching up on them after a restart."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
import random
from typing import TYPE_CHECKING

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Context, CoreState, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.sun import is_up
from homeassistant.util import dt as dt_util

from .const import CATCH_UP, EVENING_PHASES, LEAVING_DELAY, MIMIC_MINUTES, NATURAL, ROUTINE, SUNRISE, WAKE_UP
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
        # Away: the lights it's switched on (kept across restarts, so it can
        # still turn them off), and when it next switches one.
        self.mimicked: set[str] = set()
        self._next_mimic: datetime | None = None

    @callback
    def async_start(self) -> list[CALLBACK_TYPE]:
        self._checked = dt_util.now()
        return [
            async_track_state_change_event(self.lite.hass, [self.lite.presence], self._presence_changed),
            async_at_started(self.lite.hass, self._catch_up),
        ]

    @property
    def _started(self) -> bool:
        """Whether Home Assistant has finished starting. Until then the lights
        may not answer, so the routines wait for _catch_up."""
        return self.lite.hass.state is CoreState.running

    @callback
    def _catch_up(self, _hass: HomeAssistant) -> None:
        """Once Home Assistant has started, do what fell due in the last
        CATCH_UP (while it was restarting), which would otherwise be missed:
        start a wake-up (fading in faster, still reaching Energize at the
        wake-up time) or the evening routine, or at bedtime fade out the
        routine's lights. Further back, they're just let go. Away's lights go
        off once its evening's over, unless someone's home, when they're
        left. What's been done already (before Hue-Lite was reloaded) isn't
        done again."""
        lite, now = self.lite, dt_util.now()
        if lite.evening.phase in EVENING_PHASES:
            start = self._at(now.date(), lite.evening.start, now)
            if now - CATCH_UP < start <= now and not lite.in_scene(ROUTINE):
                self._start_evening()
        else:
            bedtimes = [self._at(day, lite.evening.bedtime, now) for day in (now.date() - timedelta(days=1), now.date())]
            if now - CATCH_UP < max(b for b in bedtimes if b <= now):
                lite.spawn(self._bedtime())
            else:
                lite.set_scene(lite.in_scene(ROUTINE), "")
        home = bool(_people(lite.hass.states.get(lite.presence)))
        if home or lite.evening.phase not in EVENING_PHASES:
            self._stop_mimicking(turn_off=not home)
        self._checked = min(self._checked, now - CATCH_UP)
        self.tick(now)

    @callback
    def async_stop(self) -> None:
        if self._leaving:
            self._leaving()
            self._leaving = None

    # ---- Evening routine -----------------------------------------------------

    @callback
    def evening_moved(self, was: Evening) -> None:
        """The routine's timeline has been worked out again: start it, move
        its lights, or fade them out once the evening's over. That's
        usually bedtime, but a bedtime after 05:00 ends it in the day, and a
        later start set during the evening ends it until then."""
        if not self._started:
            return
        lite = self.lite
        phase = lite.evening.phase
        if was.phase == "day" and phase in EVENING_PHASES:
            self._start_evening()
        elif phase in EVENING_PHASES:
            lite.follow(ROUTINE)
        elif was.phase in EVENING_PHASES:
            lite.spawn(self._bedtime())
            self._stop_mimicking(turn_off=True)

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
        other nights' switch doesn't change tonight. Switching Away off turns
        off the lights it switched on."""
        if key == "mimic_presence" and not value:
            self._stop_mimicking(turn_off=True)
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
        if not self._started:
            return
        since, self._checked = self._checked, now
        s = self.lite.settings
        fade, off_after = timedelta(minutes=s["wake_up_fade_minutes"]), timedelta(minutes=s["wake_up_off_after_minutes"])
        day = since.date() - timedelta(days=1)
        while day <= now.date() + timedelta(days=1):
            weekend = day.isoweekday() in (6, 7)
            wake = self._at(day, s["wake_up_weekend" if weekend else "wake_up_weekday"], now)
            woke = self.lite.last_wake_up
            started = woke is not None and woke >= wake - fade - timedelta(minutes=1)
            if since < wake - fade <= now and s["wake_up_weekends" if weekend else "wake_up_weekdays"] and not started:
                self._start_wake_up(wake)
            if since < wake + off_after <= now and s["wake_up_auto_off"]:
                self._wake_up_over()
            day += timedelta(days=1)
        self._mimic(now)

    @staticmethod
    def _at(day: date, clock: str, now: datetime) -> datetime:
        """`clock` on `day` in the local time zone, as an instant."""
        return dt_util.as_utc(datetime.combine(day, time.fromisoformat(clock), tzinfo=now.tzinfo))

    @callback
    def _start_wake_up(self, wake: datetime) -> None:
        """Wake up takes its lights that are off: one that's on already is in
        use (and with Auto, one light left on mustn't stop the rest)."""
        lights = self.lite.routine_lights("wake_up")
        on = self.lite.on(lights)
        if off := [light for light in lights if light not in on]:
            self.lite.spawn(self._wake_up(off, wake))

    async def _wake_up(self, lights: list[str], wake: datetime) -> None:
        """A sunrise: from Nightlight's dim deep red through Rest's orange and
        Relax's warm white up to Energize, reaching it at the `wake` time
        (over the fade-in, or what's left of it after a restart).
        The lights are marked as wake-up's (which takes them out of Natural
        light and the routine, so neither moves them during the fade) until
        they're changed or turned off; one that is stays as it is."""
        lite = self.lite
        # When it was due to start (later lights on Auto join it on that time).
        lite.last_wake_up = wake - timedelta(minutes=lite.settings["wake_up_fade_minutes"])
        lite.set_scene(lights, WAKE_UP)
        await lite.sender.send(lights, PRESETS["nightlight"], 0)
        await asyncio.sleep(2)
        fade = max((wake - dt_util.utcnow()).total_seconds(), 60)
        for i, (preset, share) in enumerate(SUNRISE):
            if not (lights := [light for light in lights if lite.scenes.get(light) == WAKE_UP]):
                return
            await lite.sender.send(lights, PRESETS[preset], fade * share / 100)
            if i < len(SUNRISE) - 1:
                await asyncio.sleep(fade * share / 100)

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
            # Someone's home: Away stops, leaving its lights as they are.
            self._stop_mimicking(turn_off=False)
            self._someone_arrived(new.context, first=before == 0)

    @callback
    def _someone_arrived(self, parent: Context, first: bool) -> None:
        """Turn on the Coming home lights that are off, and the First one
        home lights if the house was empty (optionally only after dark).
        Lights the evening routine takes join it while it's on, except in
        its Nightlight stretch before bed, when they'd go off again soon
        after; the rest come on in Natural light. That's the routines'
        doing, not a scene someone picked, so lights on Auto stay on it."""
        lite = self.lite
        s = lite.settings
        if not s["coming_home_enabled"] or (s["coming_home_only_dark"] and is_up(lite.hass)):
            return
        every, once = set(lite.labelled("coming_home")), set(lite.labelled("first_home"))
        wanted = (every | once) if first else (every - once)
        lights = sorted(light for light in wanted if lite.hass.states.is_state(light, STATE_OFF))
        evening = self.evening_on and lite.evening.phase in ("evening", "wind_down")
        taken = set(lite.routine_lights("evening")) if evening else set()
        if routine := [light for light in lights if light in taken]:
            lite.set_scene(routine, ROUTINE)
            lite.spawn(lite.apply(ROUTINE, routine, 2, parent))
        if natural := [light for light in lights if light not in taken]:
            lite.set_scene(natural, NATURAL)
            lite.spawn(lite.apply(NATURAL, natural, 2, parent))

    @callback
    def _everyone_left(self, _now: datetime) -> None:
        self._leaving = None
        lite = self.lite
        if lite.settings["leaving_home_enabled"] and (on := lite.on(lite.lights())):
            lite.spawn(lite.sender.call("turn_off", on, transition=2))

    # ---- Away ----------------------------------------------------------------

    @callback
    def _mimic(self, now: datetime) -> None:
        """While nobody's home in the evening (from the evening routine's
        start until bedtime, whether or not it's on tonight), now and then
        switch one of its lights on, at its light, or off, so the house looks
        lived in."""
        lite = self.lite
        if not (
            lite.settings["mimic_presence"]
            and _people(lite.hass.states.get(lite.presence)) == 0
            and lite.evening.phase in EVENING_PHASES
        ):
            return
        if self._next_mimic is not None and now < self._next_mimic:
            return
        self._next_mimic = now + timedelta(minutes=random.randint(*MIMIC_MINUTES))
        if not (lights := lite.routine_lights("evening")):
            return
        light = random.choice(lights)
        if lite.hass.states.is_state(light, STATE_ON):
            self.mimicked.discard(light)
            lite.spawn(lite.sender.call("turn_off", [light], transition=2))
        else:
            self.mimicked.add(light)
            lite.spawn(lite.sender.send([light], lite.evening.light, 2))
        lite.save()

    @callback
    def _stop_mimicking(self, turn_off: bool) -> None:
        """Stop Away, turning off the lights it switched on (at bedtime, or
        when it's switched off) or leaving them (when someone's home)."""
        lights, self.mimicked, self._next_mimic = sorted(self.mimicked), set(), None
        if lights:
            self.lite.save()
        if turn_off and (on := self.lite.on(lights)):
            self.lite.spawn(self.lite.sender.call("turn_off", on, transition=60))
