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

from .const import CATCH_UP, EVENING_PHASES, LATE_ARRIVAL, LEAVING_DELAY, MIMIC_MINUTES, NATURAL, ROUTINE, SUNRISE, WAKE_UP
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
        # Whether everyone's been gone LEAVING_DELAY: the moment Leaving home,
        # taking lights off Auto and Away start from, until someone's home.
        self._empty = False
        # Lights that came on for someone getting home late, and when they go off.
        self._late: set[str] = set()
        self._late_off: CALLBACK_TYPE | None = None
        # The end of the last wake-up check: each check covers the time since.
        self._checked = dt_util.now()
        # Away: the lights it's switched on (kept across restarts, so it can
        # still turn them off), and when it next switches one.
        self.mimicked: set[str] = set()
        self._next_mimic: datetime | None = None
        # How many sunrises are still being sent.
        self._waking = 0

    @callback
    def async_start(self) -> list[CALLBACK_TYPE]:
        self._checked = dt_util.now()
        return [
            async_track_state_change_event(self.lite.hass, [self.lite.presence], self._presence_changed),
            async_at_started(self.lite.hass, self._catch_up),
        ]

    @property
    def _home(self) -> int | None:
        """How many people are home; None while the zone's unknown."""
        return _people(self.lite.hass.states.get(self.lite.presence))

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
                self._bedtime()
            else:
                lite.set_scene(lite.in_scene(ROUTINE), "")
        home = bool(self._home)
        if home or lite.evening.phase not in EVENING_PHASES:
            self._stop_mimicking(turn_off=not home)
        # Nobody home: the house counts as empty again once that's lasted
        # (without Leaving home's switching off or taking lights off Auto,
        # done when they left).
        if self._home == 0 and not self._leaving:
            self._leaving = async_call_later(lite.hass, LEAVING_DELAY, self._still_empty)
        self._checked = min(self._checked, now - CATCH_UP)
        self.tick(now)

    @callback
    def async_stop(self) -> None:
        for cancel in (self._leaving, self._late_off):
            if cancel:
                cancel()
        self._leaving = self._late_off = None

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
            self._bedtime()
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
        (WAKE_UP) from its fade-in until it ends (even once it's let go of its
        own lights), or nothing ('', so off)."""
        lite = self.lite
        if self.evening_on and lite.evening.phase in EVENING_PHASES and self._home:
            return ROUTINE
        if (end := self.wake_up_end) and dt_util.utcnow() < end:
            return WAKE_UP
        return ""

    @property
    def wake_up_end(self) -> datetime | None:
        """When the last wake-up ends: at its wake-up time, or if its lights
        turn off after it, then."""
        if (woke := self.lite.last_wake_up) is None:
            return None
        s = self.lite.settings
        off_after = s["wake_up_off_after_minutes"] if s["wake_up_auto_off"] else 0
        return woke + timedelta(minutes=s["wake_up_fade_minutes"] + off_after)

    async def match(self, scene: str, lights: list[str]) -> None:
        """Set lights just put on Auto as the routines ask (see `asking`): the
        evening routine's light, wake up's (Energize by the wake-up time), or
        off."""
        lite = self.lite
        if scene == ROUTINE:
            await self._join_evening(lights, 2)
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
        if not self.evening_on or not self._home:
            return
        lite.followed[ROUTINE] = lite.evening
        if lights := lite.routine_lights("evening"):
            lite.set_scene(lights, ROUTINE)
            lite.spawn(self._join_evening(lights, 5))

    async def _join_evening(self, lights: list[str], transition: float) -> None:
        """Bring lights the routine has just taken to its light. While it's
        brightening, ones that are on (set by hand, say) fade from how they
        are to where it'll be once it's brightened, getting there then, so
        they don't drop to its dim start; the rest come on at its light now."""
        lite, now = self.lite, dt_util.now()
        start = self._at(now.date(), lite.evening.start, now)
        brightened = start + timedelta(minutes=lite.settings["evening_brighten_minutes"])
        on = lite.on(lights) if start <= now < brightened else []
        if on and (later := lite.evening_at(brightened)).phase == "evening":
            await asyncio.gather(
                lite.ease(on, later.light, brightened),
                lite.apply(ROUTINE, [light for light in lights if light not in on], transition),
            )
        else:
            await lite.apply(ROUTINE, lights, transition)

    @callback
    def _bedtime(self) -> None:
        """Fade out the routine's lights, which it's then done with for
        tonight. Lights moved to another scene during the evening (Natural
        light included) aren't the routine's any more, so they're left alone."""
        lite = self.lite
        lite.switch_off_still_in(lite.in_scene(ROUTINE), ROUTINE, lite.settings["bedtime_fade_minutes"] * 60)

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
        self._let_go_after_wake_up(now)
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
        self._waking += 1
        try:
            await lite.sender.send(lights, PRESETS["nightlight"], 0)
            await asyncio.sleep(2)
            fade = max((wake - dt_util.utcnow()).total_seconds(), 60)
            for i, (preset, share) in enumerate(SUNRISE):
                if not (lights := [light for light in lights if lite.scenes.get(light) == WAKE_UP]):
                    return
                await lite.sender.send(lights, PRESETS[preset], fade * share / 100)
                if i < len(SUNRISE) - 1:
                    await asyncio.sleep(fade * share / 100)
        finally:
            self._waking -= 1

    @callback
    def _let_go_after_wake_up(self, now: datetime) -> None:
        """Once the wake-up has ended (and its sunrise has been sent, which
        after a restart can run past the wake-up time), it lets go of its
        lights, leaving them as they are: they're no longer on Auto unless
        they were put on it."""
        lite = self.lite
        if not self._waking and (end := self.wake_up_end) and end <= now and (lights := lite.in_scene(WAKE_UP)):
            lite.set_scene(lights, "")

    @callback
    def _wake_up_over(self) -> None:
        """Turn off the lights still as this morning's wake-up left them: not
        ones put into a scene or changed since."""
        lite = self.lite
        woke = lite.last_wake_up
        if woke is None or woke < dt_util.utcnow() - timedelta(hours=6):
            return
        lite.switch_off_still_in(lite.in_scene(WAKE_UP), WAKE_UP, 60)

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
        if after:
            self._empty = False
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
        doing, not a scene someone picked, so lights on Auto stay on it.
        Getting home in the Nightlight stretch or after bedtime, the lights
        light the way for LATE_ARRIVAL, then go off (ones changed meanwhile
        stay as they are); a later arrival gives them longer."""
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
            if lite.evening.phase in ("nightlight", "night"):
                self._late.update(natural)
                if self._late_off:
                    self._late_off()
                self._late_off = async_call_later(lite.hass, LATE_ARRIVAL, self._late_over)

    @callback
    def _late_over(self, _now: datetime) -> None:
        """Lights that came on for a late arrival go off."""
        lights, self._late, self._late_off = sorted(self._late), set(), None
        self.lite.switch_off_still_in(lights, NATURAL, 60)

    @callback
    def _everyone_left(self, _now: datetime) -> None:
        """Everyone's been gone LEAVING_DELAY: Leaving home switches the
        lights off, and lights on Auto come off it, so the dashboard doesn't
        show lights switched off while you're out as on Auto. Ones still on
        (Leaving home is off) stay on Auto, so bedtime still fades them out.
        The routines' labels take their lights again next time."""
        self._still_empty(_now)
        lite = self.lite
        leaving = lite.settings["leaving_home_enabled"]
        on = lite.on(lite.lights())
        lite.async_set_auto([light for light in lite.on_auto() if leaving or light not in on], False)
        if leaving and on:
            lite.spawn(lite.sender.call("turn_off", on, transition=2))

    @callback
    def _still_empty(self, _now: datetime) -> None:
        self._leaving = None
        self._empty = True

    # ---- Away ----------------------------------------------------------------

    @callback
    def _mimic(self, now: datetime) -> None:
        """While the house is empty in the evening (from the evening routine's
        start until bedtime, whether or not it's on tonight), now and then
        switch one of the Evening lights on, at the routine's light, or one
        it switched on off again, so the house looks lived in. Lights it
        didn't switch on (a lamp left on) are left alone."""
        lite = self.lite
        if not (lite.settings["mimic_presence"] and self._empty and lite.evening.phase in EVENING_PHASES):
            return
        if self._next_mimic is not None and now < self._next_mimic:
            return
        self._next_mimic = now + timedelta(minutes=random.randint(*MIMIC_MINUTES))
        on = set(lite.on(evening := lite.labelled("evening")))
        if not (lights := [light for light in evening if light in self.mimicked or light not in on]):
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
