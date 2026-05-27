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

# Minimum seconds between reconnect attempts when the pump refuses login.
# Repeated failed handshakes allocate half-open sessions on the pump, so
# back off progressively until the pump frees old slots.
RECONNECT_BASE_COOLDOWN = 30.0
RECONNECT_MAX_COOLDOWN = 10 * 60.0


class PoolComfortCoordinator(DataUpdateCoordinator[PoolDiagnostics]):
    """Coordinator that opens a fresh session for every poll cycle.

    The pump firmware expires sessions after roughly 4-5 minutes regardless
    of keepalive pings.  A persistent-session model therefore cycles through
    ``connect → use → die → detect → reconnect`` every few minutes, and
    each dead-but-not-yet-freed session occupies a slot on the pump's tiny
    session table.  After enough cycles the table fills up and the pump
    stops accepting new logins until it is power-cycled.

    This coordinator avoids the problem entirely: each 30 s poll opens a
    fresh session, runs a single query, and closes immediately.  At most
    one slot is "in flight" at a time, and the pump can reclaim it within
    seconds instead of waiting for its session-expiry timer.
    """

    def __init__(self, hass: HomeAssistant, host: str, password: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.host = host
        self.password = password
        self._lock = threading.Lock()
        self._last_connect_attempt: float = 0
        self._connect_failures = 0
        self._last_local_port = 0

    async def _async_update_data(self) -> PoolDiagnostics:
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except Exception as exc:
            raise UpdateFailed(str(exc)) from exc

    # ------------------------------------------------------------------
    # Core: connect → query → close  (one shot per poll)
    # ------------------------------------------------------------------

    def _fetch(self) -> PoolDiagnostics:
        with self._lock:
            client = self._open()
            try:
                return client.query_diagnostics()
            finally:
                self._close(client)

    def _open(self) -> PoolComfortClient:
        """Open a fresh session, respecting backoff on repeated failures."""
        now = time.monotonic()
        cooldown = self._reconnect_cooldown()
        elapsed = now - self._last_connect_attempt
        if self._connect_failures > 0 and elapsed < cooldown:
            raise RuntimeError(
                f"reconnect cooldown: {cooldown - elapsed:.0f}s remaining"
            )
        self._last_connect_attempt = now
        client = PoolComfortClient(
            self.host,
            password=self.password,
            timeout=DEFAULT_TIMEOUT,
            local_port=self._last_local_port,
            keepalive=False,
        )
        try:
            client.connect()
        except Exception:
            self._connect_failures += 1
            _LOGGER.warning(
                "Failed to open Pool Comfort session to %s (attempt %d, "
                "next retry in %.0fs)",
                self.host,
                self._connect_failures,
                self._reconnect_cooldown(),
            )
            raise
        # Success — reset backoff and remember port for reuse.
        self._connect_failures = 0
        if client._sock is not None:
            try:
                self._last_local_port = client._sock.getsockname()[1]
            except OSError:
                pass
        return client

    def _close(self, client: PoolComfortClient) -> None:
        if client._sock is not None:
            try:
                self._last_local_port = client._sock.getsockname()[1]
            except OSError:
                pass
        client.close()

    def _reconnect_cooldown(self) -> float:
        if self._connect_failures <= 0:
            return 0
        return min(
            RECONNECT_MAX_COOLDOWN,
            RECONNECT_BASE_COOLDOWN * (2 ** min(self._connect_failures - 1, 5)),
        )

    # ------------------------------------------------------------------
    # SET commands (temperature, mode, power)
    # ------------------------------------------------------------------

    async def async_apply(self, action) -> None:
        await self.hass.async_add_executor_job(self._apply, action)
        await self.async_request_refresh()

    def _apply(self, action) -> None:
        with self._lock:
            client = self._open()
            try:
                action(client)
            finally:
                self._close(client)

    # ------------------------------------------------------------------
    # Cleanup (integration unload)
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        pass  # No persistent resources to clean up.
