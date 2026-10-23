"""Where the Natural light scene and the evening routine are, and their lights."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import NATURAL, ROUTINE
from .engine import HueLiteConfigEntry
from .entity import HueLiteEntity
from .timeline import Target


async def async_setup_entry(
    hass: HomeAssistant, entry: HueLiteConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hue_lite = entry.runtime_data
    async_add_entities([NaturalLightSensor(hue_lite, "natural_light"), EveningRoutineSensor(hue_lite, "evening_routine")])


class _TimelineSensor(HueLiteEntity, SensorEntity):
    """State: the phase. Attributes: the light it wants now, and `lights`, the
    lights in it."""

    _attr_device_class = SensorDeviceClass.ENUM
    _scene: str

    @property
    def _target(self) -> Target:
        raise NotImplementedError

    @property
    def native_value(self) -> str:
        return self._target.phase

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        light = self._target.light
        return {
            "brightness_pct": light.brightness_pct,
            "xy_color": list(light.xy_color),
            "lights": sorted(self.hue_lite.members[self._scene]),
        }


class NaturalLightSensor(_TimelineSensor):
    _attr_options = ["night", "sunrise", "morning", "day", "sunset", "evening"]
    _scene = NATURAL

    @property
    def _target(self) -> Target:
        return self.hue_lite.natural


class EveningRoutineSensor(_TimelineSensor):
    _attr_options = ["day", "evening", "wind_down", "nightlight", "night"]
    _scene = ROUTINE

    @property
    def _target(self) -> Target:
        return self.hue_lite.evening

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        evening = self.hue_lite.evening
        return {
            **super().extra_state_attributes,
            "start": evening.start,
            "bedtime": evening.bedtime,
            "summary": f"On {evening.start} · off {evening.bedtime}",
        }
