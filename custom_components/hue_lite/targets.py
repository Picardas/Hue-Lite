"""Which lights an action, routine or dimmer means, and which lights Hue-Lite
leaves alone."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.auth.models import User
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids

LIGHT = "light"


def excluded(hass: HomeAssistant, entity_id: str, exclude_label: str) -> bool:
    """Whether Hue-Lite leaves a light alone: it's hidden or a config entity,
    or it, its device or its area has the Exclude from lighting label."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return False
    if entry.hidden or entry.entity_category or exclude_label in entry.labels:
        return True
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    if device and exclude_label in device.labels:
        return True
    area_id = entry.area_id or (device.area_id if device else None)
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return bool(area and exclude_label in area.labels)


def _takes_part(hass: HomeAssistant, entity_id: str, exclude_label: str) -> bool:
    if excluded(hass, entity_id, exclude_label):
        return False
    # Light groups would double up with their members.
    state = hass.states.get(entity_id)
    return state is not None and not isinstance(state.attributes.get(ATTR_ENTITY_ID), list)


def taking_part(hass: HomeAssistant, exclude_label: str) -> list[str]:
    """Every light Hue-Lite uses, reachable or not: the lights the dashboard shows."""
    return sorted(entity_id for entity_id in hass.states.async_entity_ids(LIGHT) if _takes_part(hass, entity_id, exclude_label))


def resolve(hass: HomeAssistant, target: Mapping[str, Any] | None, exclude_label: str) -> list[str]:
    """The lights a target means (entity_id, area_id, floor_id, device_id,
    label_id, by Home Assistant's own rules, so a label on a device or area
    counts too), or every light when it's empty. Lights named directly are
    used as they are; the others skip excluded lights and light groups.
    Unavailable lights are always left out."""
    selection = TargetSelection(dict(target or {}))
    if selection.has_any_target:
        selected = async_extract_referenced_entity_ids(hass, selection)
        named, found = selected.referenced, selected.indirectly_referenced
    else:
        named, found = set(), set(hass.states.async_entity_ids(LIGHT))
    found = {entity_id for entity_id in found if _takes_part(hass, entity_id, exclude_label)}
    return sorted(
        entity_id
        for entity_id in found | named
        if entity_id.startswith(f"{LIGHT}.")
        and (state := hass.states.get(entity_id)) is not None
        and state.state != STATE_UNAVAILABLE
    )


def may_control(user: User, lights: list[str]) -> bool:
    """Whether a user may control all these lights, as Home Assistant checks
    for its own light actions: an admin always may. Hue-Lite changes lights
    as itself, which Home Assistant doesn't check, so it asks this first."""
    return user.is_admin or all(user.permissions.check_entity(light, POLICY_CONTROL) for light in lights)
