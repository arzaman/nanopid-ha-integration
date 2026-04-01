"""Config flow for NanoPID integration."""
from __future__ import annotations

import re

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant  # noqa: F401

from .const import CONF_DEVICE_MAC, CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME, DOMAIN

MAC_RE = re.compile(r"^[0-9a-fA-F]{12}$")

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_MAC): str,
        vol.Optional(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
    }
)


class NanoPIDConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for NanoPID Controller."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = user_input[CONF_DEVICE_MAC].lower().strip()
            if not MAC_RE.match(mac):
                errors[CONF_DEVICE_MAC] = "invalid_mac"
            else:
                # Normalise MAC in stored data
                user_input[CONF_DEVICE_MAC] = mac

                # Prevent duplicate entries for the same device
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()

                device_name = user_input.get(CONF_DEVICE_NAME) or DEFAULT_DEVICE_NAME
                return self.async_create_entry(title=device_name, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
