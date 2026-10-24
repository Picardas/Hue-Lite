"""Setup asks three things (home zone, dimmers, the Lights dashboard) and
finds or creates the role labels; the options flow can point each role at
another label."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.selector import BooleanSelector, EntitySelector, EntitySelectorConfig, LabelSelector

from .const import CONF_DASHBOARD, CONF_DIMMERS, CONF_PRESENCE, DEFAULT_PRESENCE, DOMAIN, NAME, ROLES, label_option


def _schema(labels: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if labels:
        fields.update({vol.Required(label_option(role)): LabelSelector() for role in ROLES})
    fields[vol.Required(CONF_PRESENCE, default=DEFAULT_PRESENCE)] = EntitySelector(EntitySelectorConfig(domain="zone"))
    fields[vol.Required(CONF_DIMMERS, default=True)] = BooleanSelector()
    fields[vol.Required(CONF_DASHBOARD, default=True)] = BooleanSelector()
    return vol.Schema(fields)


@callback
def role_labels(hass: HomeAssistant, roles: list[str] | None = None) -> dict[str, str]:
    """Each role's label (or these roles'): an existing one with its usual
    id or name, or a new one."""
    registry = lr.async_get(hass)
    options = {}
    for role in roles or ROLES:
        label_id, name, icon, color, description = ROLES[role]
        label = registry.async_get_label(label_id) or next(
            (label for label in registry.async_list_labels() if label.name.casefold() == name.casefold()), None
        )
        if label is None:
            label = registry.async_create(name, icon=icon, color=color, description=description)
        options[label_option(role)] = label.label_id
    return options


class HueLiteConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    # 1.2 added the First one home label.
    MINOR_VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={}, options={**user_input, **role_labels(self.hass)})
        return self.async_show_form(step_id="user", data_schema=_schema(labels=False))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HueLiteOptionsFlow()


class HueLiteOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(_schema(labels=True), self.config_entry.options),
        )
