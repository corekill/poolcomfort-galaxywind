from __future__ import annotations

import logging
import threading

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import PoolComfortClient
from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_TIMEOUT, DOMAIN
from .protocol import PoolState

_LOGGER = logging.getLogger(__name__)


class PoolComfortCoordinator(DataUpdateCoordinator[PoolState]):
    def __init__(self, hass: HomeAssistant, host: str, password: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.host = host
        self.password = password
        self._client: PoolComfortClient | None = None
        self._client_lock = threading.Lock()

    async def _async_update_data(self) -> PoolState:
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except Exception as exc:
            raise UpdateFailed(str(exc)) from exc

    def _ensure_client(self) -> PoolComfortClient:
        if self._client is not None:
            return self._client
        client = PoolComfortClient(self.host, password=self.password, timeout=DEFAULT_TIMEOUT)
        client.connect()
        self._client = client
        return client

    def _close_client(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _fetch(self) -> PoolState:
        with self._client_lock:
            try:
                client = self._ensure_client()
                return client.query_state()
            except Exception:
                self._close_client()
                client = self._ensure_client()
                return client.query_state()

    async def async_apply(self, action) -> None:
        await self.hass.async_add_executor_job(self._apply, action)
        await self.async_request_refresh()

    def _apply(self, action) -> None:
        with self._client_lock:
            try:
                client = self._ensure_client()
                action(client)
            except Exception:
                self._close_client()
                client = self._ensure_client()
                action(client)

    def shutdown(self) -> None:
        with self._client_lock:
            self._close_client()
