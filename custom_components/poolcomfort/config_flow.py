from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from poolcomfort_local import PoolComfortClient

from .const import DEFAULT_PASSWORD, DEFAULT_TIMEOUT, DOMAIN

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
    }
)


def _try_connect(host: str, password: str) -> str:
    client = PoolComfortClient(host, password=password, timeout=DEFAULT_TIMEOUT)
    client.connect()
    try:
        state = client.query_state()
    finally:
        client.close()
    if state.serial is None:
        raise RuntimeError("connected but no serial returned")
    return state.serial


class PoolComfortConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            password = user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD)
            try:
                serial = await self.hass.async_add_executor_job(_try_connect, host, password)
            except TimeoutError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Pool Comfort {serial}", data=user_input)
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)
