from __future__ import annotations

import logging
import threading
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import PoolComfortClient
from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_TIMEOUT, DOMAIN
from .protocol import PoolDiagnostics

_LOGGER = logging.getLogger(__name__)

# Minimum seconds between reconnect attempts to avoid flooding the pump's
# session table. The pump has a finite number of session slots and does not
# release dead sessions immediately. Repeated failed handshakes can allocate
# half-open sessions, so back off progressively when the pump refuses login.
RECONNECT_BASE_COOLDOWN = 60.0
RECONNECT_MAX_COOLDOWN = 30 * 60.0


class PoolComfortCoordinator(DataUpdateCoordinator[PoolDiagnostics]):
    def __init__(self, hass: HomeAssistant, host: str, password: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.host = host
        self.password = password
        self._client: PoolComfortClient | None = None
        self._client_lock = threading.Lock()
        self._last_connect_attempt: float = -RECONNECT_BASE_COOLDOWN
        self._connect_failures = 0

    async def _async_update_data(self) -> PoolDiagnostics:
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except Exception as exc:
            raise UpdateFailed(str(exc)) from exc

    def _ensure_client(self) -> PoolComfortClient:
        if self._client is not None:
            return self._client
        now = time.monotonic()
        cooldown = self._reconnect_cooldown()
        elapsed = now - self._last_connect_attempt
        if elapsed < cooldown:
            raise RuntimeError(
                f"reconnect cooldown: {cooldown - elapsed:.0f}s remaining"
            )
        self._last_connect_attempt = now
        _LOGGER.debug("Opening new session to %s", self.host)
        client = PoolComfortClient(self.host, password=self.password, timeout=DEFAULT_TIMEOUT)
        try:
            client.connect()
        except Exception:
            self._connect_failures += 1
            _LOGGER.warning(
                "Failed to open Pool Comfort session to %s; reconnect cooldown is now %.0fs",
                self.host,
                self._reconnect_cooldown(),
            )
            raise
        self._connect_failures = 0
        self._client = client
        return client

    def _reconnect_cooldown(self) -> float:
        if self._connect_failures <= 0:
            return RECONNECT_BASE_COOLDOWN
        return min(
            RECONNECT_MAX_COOLDOWN,
            RECONNECT_BASE_COOLDOWN * (2 ** min(self._connect_failures, 5)),
        )

    def _close_client(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _fetch(self) -> PoolDiagnostics:
        with self._client_lock:
            try:
                client = self._ensure_client()
                return client.query_diagnostics()
            except Exception:
                self._close_client()
                raise

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
                raise

    def shutdown(self) -> None:
        with self._client_lock:
            self._close_client()
