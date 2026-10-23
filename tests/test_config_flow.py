"""Setup finds or creates the role labels; options can remap them."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import label_registry as lr

from custom_components.hue_lite.const import DOMAIN

from .conftest import Home


async def test_user_flow_reuses_and_creates_labels(hass: HomeAssistant) -> None:
    labels = lr.async_get(hass)
    # Existing labels are found by id (as the YAML setup's migrations made them)...
    by_id = labels.async_create("Lighting exclude")
    labels.async_update(by_id.label_id, name="Keep out")
    # ...or by name, whatever the case.
    by_name = labels.async_create("Evening routine")
    labels.async_update(by_name.label_id, name="EVENING")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"presence": "zone.home", "dimmers": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = result["result"].options
    assert options["label_exclude"] == "lighting_exclude"
    assert options["label_evening"] == "evening_routine"
    assert options["dimmers"] is False
    for role, name in (
        ("wake_up", "Wake up"),
        ("nightlight", "Nightlight"),
        ("coming_home", "Coming home"),
    ):
        assert labels.async_get_label(options[f"label_{role}"]).name == name

    # Only one Hue-Lite.
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_remaps_a_label(hass: HomeAssistant, home: Home, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    lamps = lr.async_get(hass).async_create("Lamps")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**entry.options, "label_coming_home": lamps.label_id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    # The entry reloaded with the new label.
    assert entry.runtime_data.labels["coming_home"] == lamps.label_id
