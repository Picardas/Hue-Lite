"""The settings every dashboard shares, kept by Hue-Lite: each room's and
light's scenes (`layouts`), each Hue dimmer's lights and scenes, and your own
scenes; and checking them as they're loaded."""

from __future__ import annotations

from collections.abc import Mapping
import re
import secrets
from typing import Any

from homeassistant.const import ATTR_AREA_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import NATURAL, SIGNAL_SHARED
from .lights import rgb_color
from .timeline import PALETTES, PRESETS, SCENE_CYCLE

# The scenes with a tile on the dashboard.
TILE_SCENES = frozenset({NATURAL, *PRESETS, *PALETTES})

# A dimmer's scenes until they're set ({order, hidden}, as the dashboard
# edits them): Natural light and the presets, as Hue's On button cycles.
DIMMER_DEFAULT = {"order": [*SCENE_CYCLE, *PALETTES], "hidden": list(PALETTES)}

# Your own scenes: their ids start with this, and there are at most this many.
CUSTOM_PREFIX = "custom_"
MAX_CUSTOM = 50

# The built-in tiles' usual order, as the dashboard shows them (sceneTiles).
DEFAULT_ORDER = [*PRESETS, NATURAL, *PALETTES]

# Scenes that have been renamed, by their old ids: saved data and actions
# using an old id get the new one.
RENAMED = {"movie": "nebula", "moonlit": "moonlight"}


# An icon as Home Assistant's icon picker gives it (e.g. mdi:sofa), or none.
ICON = re.compile(r"(?:[a-z0-9-]+:[a-z0-9-]+)?")


def renamed(scene: Any) -> Any:
    """A scene's id now, if it's been renamed."""
    return RENAMED.get(scene, scene) if isinstance(scene, str) else scene


def scene_set(value: Any, tiles: frozenset[str] | set[str] = TILE_SCENES) -> dict[str, list[str]] | None:
    """A set of scenes ({order, hidden}), stored or sent by a dashboard, or
    None if it isn't one; renamed scenes get their new ids, and scenes not
    among `tiles`, or there already, are left out."""
    if not isinstance(value, dict):
        return None

    def ids(key: str) -> list[str]:
        found = value.get(key)
        return list(dict.fromkeys(s for s in map(renamed, found) if s in tiles)) if isinstance(found, list) else []

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
        and ICON.fullmatch(icon)
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


def _showing(setup: dict[str, list[str]], scene_id: str) -> dict[str, list[str]]:
    """A set of scenes ({order, hidden}) with `scene_id` shown, at the end."""
    return {
        "order": [*(s for s in setup["order"] if s != scene_id), scene_id],
        "hidden": [s for s in setup["hidden"] if s != scene_id],
    }


def stored_dimmer(value: dict[str, Any], tiles: frozenset[str] | set[str]) -> dict[str, Any]:
    """A stored dimmer's settings: the lights it controls and its scenes,
    whichever are set."""
    found: dict[str, Any] = {}
    if isinstance(lights := value.get("lights"), list) and (lights := [x for x in lights if isinstance(x, str)]):
        found["lights"] = lights
    if setup := scene_set(value.get("scenes"), tiles):
        found["scenes"] = setup
    return found


class SharedSettings:
    """HueLite's shared settings: the rooms' and lights' scenes, the dimmers'
    settings and your own scenes, changed from any dashboard and sent to
    every one open."""

    hass: HomeAssistant
    layouts: dict[str, dict[str, list[str]]]
    dimmer_settings: dict[str, dict[str, Any]]
    custom: dict[str, dict[str, Any]]
    use_dashboard: bool
    dashboard_added: bool
    _stale: set[str]

    def shared(self) -> dict[str, Any]:
        """What every dashboard shares: the rooms' and lights' scenes, the
        dimmers' settings, a dimmer's scenes until they're set, and your own
        scenes (with the colours a colour bulb reports for them)."""
        return {
            "layouts": self.layouts,
            "dimmers": self.dimmer_settings,
            "dimmer_default": DIMMER_DEFAULT,
            "custom": {
                scene_id: {**scene, "rgb_colors": [rgb_color((x, y)) for x, y, _ in scene["colours"]]}
                for scene_id, scene in self.custom.items()
            },
        }

    @callback
    def save_custom_scene(self, scene_id: str | None, scene: dict[str, Any], show: Mapping[str, list[str]] | None = None) -> str:
        """Add one of your own scenes (no id; its new id is returned) or
        change one (its lights that are on get it straight away, the others
        when they're next switched on). A new one is shown in the rooms' and
        lights' scenes and dimmers in `show` (where it was made), and hidden
        elsewhere until shown there."""
        new = scene_id is None
        if new:
            scene_id = f"{CUSTOM_PREFIX}{secrets.token_hex(3)}"
            while scene_id in self.custom:
                scene_id = f"{CUSTOM_PREFIX}{secrets.token_hex(3)}"
        self.custom[scene_id] = scene
        self._custom_changed()
        if new:
            for key in (show or {}).get("layouts", []):
                self.layouts[key] = _showing(self.layouts.get(key, {"order": DEFAULT_ORDER, "hidden": []}), scene_id)
            for device_id in (show or {}).get("dimmers", []):
                settings = self.dimmer_settings.setdefault(device_id, {})
                settings["scenes"] = _showing(settings.get("scenes", DIMMER_DEFAULT), scene_id)
        else:
            lights = self.in_scene(scene_id)
            on = self.on(lights)
            self._stale.update(light for light in lights if light not in on)
            if on:
                self.spawn(self.send_palette(scene_id, on, 2))
        self._shared_changed()
        return scene_id

    @callback
    def delete_custom_scene(self, scene_id: str) -> None:
        """Delete one of your own scenes: its lights leave it (staying as they
        are), and the rooms, lights and dimmers forget it."""
        del self.custom[scene_id]
        self._custom_changed()
        self.set_scene(self.in_scene(scene_id), "")
        for setup in [*self.layouts.values(), *(s["scenes"] for s in self.dimmer_settings.values() if "scenes" in s)]:
            for key in ("order", "hidden"):
                setup[key] = [s for s in setup[key] if s != scene_id]
        self._shared_changed()

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

    def tile_scenes(self) -> set[str]:
        """Every scene with a tile: the built-in ones and your own."""
        return TILE_SCENES | self.custom.keys()

    def _rename_in_dimmers(self, old: str, new: str | None) -> None:
        """A light chosen for a dimmer has a new entity id, or with None is
        deleted (a dimmer left with none controls its area again)."""
        for device_id, settings in list(self.dimmer_settings.items()):
            if old in (lights := settings.get("lights", [])):
                now = [new if light == old else light for light in lights]
                self.set_dimmer(device_id, lights=[light for light in now if light])

    def _rename_in_layouts(self, old: str, new: str | None) -> None:
        """A light shown on its own (in a room with several, whose scenes are
        kept as `area|light`) has a new entity id, or with None is deleted."""
        if keys := [key for key in self.layouts if key.endswith(f"|{old}")]:
            for key in keys:
                setup = self.layouts.pop(key)
                if new:
                    self.layouts[f"{key.rpartition('|')[0]}|{new}"] = setup
            self._shared_changed()

