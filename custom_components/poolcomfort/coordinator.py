from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from poolcomfort_local import PoolComfortClient, PoolState

from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)


class PoolComfortCoordinator(DataUpdateCoordinator[PoolState]):
    def __init__(self, hass: HomeAssistant, host: str, password: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.host = host
        self.password = password

    async def _async_update_data(self) -> PoolState:
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except Exception as exc:
            raise UpdateFailed(str(exc)) from exc

    def _fetch(self) -> PoolState:
        client = PoolComfortClient(self.host, password=self.password, timeout=DEFAULT_TIMEOUT)
        client.connect()
        try:
            return client.query_state()
        finally:
            client.close()

    async def async_apply(self, action) -> None:
        await self.hass.async_add_executor_job(self._apply, action)
        await self.async_request_refresh()

    def _apply(self, action) -> None:
        client = PoolComfortClient(self.host, password=self.password, timeout=DEFAULT_TIMEOUT)
        client.connect()
        try:
            action(client)
        finally:
            client.close()
