"""Where the Natural light scene and the evening routine are, their lights,
and which scene each light is in."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import NATURAL, ROUTINE
from .engine import HueLiteConfigEntry
from .entity import HueLiteEntity
from .lights import rgb_color


async def async_setup_entry(
    hass: HomeAssistant, entry: HueLiteConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hue_lite = entry.runtime_data
    async_add_entities(
        [
            NaturalLightSensor(hue_lite, "natural_light"),
            EveningRoutineSensor(hue_lite, "evening_routine"),
            LightScenesSensor(hue_lite, "light_scenes"),
        ]
    )


class _EngineSensor(HueLiteEntity, SensorEntity):
    """Updated whenever the timelines or scene lists change."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hue_lite.async_add_listener(self.async_write_ha_state))


class _TimelineSensor(_EngineSensor):
    """State: the phase. Attributes: the light it wants now (with the RGB
    colour a colour bulb reports for it, for the dashboard), and `lights`,
    the lights in it."""

    _attr_device_class = SensorDeviceClass.ENUM
    # The light lists change with every scene pick; history has no use for them.
    _unrecorded_attributes = frozenset({"lights"})
    _scene: str

    @property
    def native_value(self) -> str:
        return self.hue_lite.target(self._scene).phase

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        light = self.hue_lite.target(self._scene).light
        return {
            "brightness_pct": light.brightness_pct,
            "xy_color": list(light.xy_color),
            "rgb_color": rgb_color(light.xy_color),
            "color_temp_kelvin": light.kelvin,
            "lights": self.hue_lite.in_scene(self._scene),
        }


class NaturalLightSensor(_TimelineSensor):
    _attr_options = ["night", "sunrise", "morning", "day", "sunset", "evening"]
    _scene = NATURAL


class EveningRoutineSensor(_TimelineSensor):
    _attr_options = ["day", "evening", "wind_down", "nightlight", "night"]
    _scene = ROUTINE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        evening = self.hue_lite.evening
        on = self.hue_lite.routines.evening_on
        return {
            **super().extra_state_attributes,
            "start": evening.start,
            "bedtime": evening.bedtime,
            "summary": f"On {evening.start} · off {evening.bedtime}" if on else "Off tonight",
        }


class LightScenesSensor(_EngineSensor):
    """How many lights are in a scene; `scenes` says which scene each is in
    (natural, routine, wake_up or a preset id) and `auto` lists the lights on
    Auto (put on it, or held by the evening routine or wake up), for the
    dashboard's highlights and Auto buttons. `custom_scenes` gives each of
    your own scenes' name by its id, for `apply_preset` in automations."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"scenes", "auto", "custom_scenes"})

    @property
    def native_value(self) -> int:
        return len(self.hue_lite.scenes)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "scenes": dict(sorted(self.hue_lite.scenes.items())),
            "auto": self.hue_lite.on_auto(),
            "custom_scenes": {scene_id: scene["name"] for scene_id, scene in sorted(self.hue_lite.custom.items(), key=lambda s: s[1]["name"])},
        }
