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

# After a failed connect attempt the pump's session table is likely full.
# Each half-open slot takes the firmware several minutes to reclaim, so
# retrying sooner just burns another slot.  Start with a 5-minute wait
# (long enough for the pump to free at least one slot) and cap at 10 min.
RECONNECT_BASE_COOLDOWN = 5 * 60.0
RECONNECT_MAX_COOLDOWN = 10 * 60.0


class PoolComfortCoordinator(DataUpdateCoordinator[PoolDiagnostics]):
    """Persistent-session coordinator with conservative reconnection.

    **Why persistent sessions?**
    The pump firmware has a tiny session table and is very slow to reclaim
    abandoned slots.  Creating a new session every 30 s poll (the v2.0.0
    approach) filled the table in under 5 minutes because the pump never
    freed old slots fast enough.

    **Strategy**
    1. Open ONE session with keepalive pings and reuse it for every poll.
    2. When a query fails (session died), try to reconnect immediately.
       If that works the pump had a free slot — back to normal.
    3. If the reconnect *also* fails (table full), enter a 5-minute
       cooldown so the pump has time to reclaim at least one slot.
    4. During the cooldown **return the last successful diagnostics**
       instead of raising ``UpdateFailed``.  Entities keep their last
       known values (slightly stale) rather than going ``unavailable``.
    """

    def __init__(self, hass: HomeAssistant, host: str, password: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.host = host
        self.password = password
        self._lock = threading.Lock()
        self._client: PoolComfortClient | None = None
        self._last_connect_attempt: float = 0
        self._connect_failures: int = 0
        self._last_good_data: PoolDiagnostics | None = None

    # ------------------------------------------------------------------
    # DataUpdateCoordinator entry point
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> PoolDiagnostics:
        try:
            data = await self.hass.async_add_executor_job(self._fetch)
            self._last_good_data = data
            return data
        except Exception as exc:
            # Return last-known data while waiting for the pump to free
            # session slots.  Entities stay at their last reading instead
            # of flipping to "unavailable".
            if self._last_good_data is not None:
                _LOGGER.debug(
                    "Poll failed (%s), returning last known state", exc,
                )
                return self._last_good_data
            raise UpdateFailed(str(exc)) from exc

    # ------------------------------------------------------------------
    # Core fetch: reuse session, reconnect on failure
    # ------------------------------------------------------------------

    def _fetch(self) -> PoolDiagnostics:
        with self._lock:
            # Fast path — existing session is alive.
            if self._client is not None:
                try:
                    return self._client.query_diagnostics()
                except (TimeoutError, RuntimeError, OSError) as exc:
                    _LOGGER.info(
                        "Pool Comfort session lost (%s), reconnecting", exc,
                    )
                    self._close_client()

            # Need a (new) session.
            return self._open_and_query()

    def _open_and_query(self) -> PoolDiagnostics:
        """Open a fresh session and run a single diagnostics query."""
        now = time.monotonic()
        if self._connect_failures > 0:
            cooldown = self._reconnect_cooldown()
            elapsed = now - self._last_connect_attempt
            if elapsed < cooldown:
                raise RuntimeError(
                    f"session cooldown: {cooldown - elapsed:.0f}s remaining "
                    f"(attempt {self._connect_failures})"
                )

        self._last_connect_attempt = now
        client = PoolComfortClient(
            self.host,
            password=self.password,
            timeout=DEFAULT_TIMEOUT,
            keepalive=True,
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

        # Connected — reset failure counter, attach client.
        self._connect_failures = 0
        self._client = client
        _LOGGER.info("Pool Comfort session opened to %s", self.host)
        return client.query_diagnostics()

    def _close_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def _reconnect_cooldown(self) -> float:
        if self._connect_failures <= 0:
            return 0
        # 5 min → 10 min cap.  Only two steps because the pump either
        # frees a slot within 5-10 minutes or it needs a power-cycle.
        return min(
            RECONNECT_MAX_COOLDOWN,
            RECONNECT_BASE_COOLDOWN * (2 ** min(self._connect_failures - 1, 1)),
        )

    # ------------------------------------------------------------------
    # SET commands (temperature, mode, power)
    # ------------------------------------------------------------------

    async def async_apply(self, action) -> None:
        await self.hass.async_add_executor_job(self._apply, action)
        await self.async_request_refresh()

    def _apply(self, action) -> None:
        with self._lock:
            # Try using the existing session first.
            if self._client is not None:
                try:
                    action(self._client)
                    return
                except (TimeoutError, RuntimeError, OSError):
                    self._close_client()

            # Fall back to a fresh session.
            now = time.monotonic()
            if self._connect_failures > 0:
                cooldown = self._reconnect_cooldown()
                elapsed = now - self._last_connect_attempt
                if elapsed < cooldown:
                    raise RuntimeError(
                        f"session cooldown: {cooldown - elapsed:.0f}s remaining"
                    )
            self._last_connect_attempt = now
            client = PoolComfortClient(
                self.host,
                password=self.password,
                timeout=DEFAULT_TIMEOUT,
                keepalive=True,
            )
            try:
                client.connect()
            except Exception:
                self._connect_failures += 1
                raise
            self._connect_failures = 0
            self._client = client
            action(client)

    # ------------------------------------------------------------------
    # Cleanup (integration unload)
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        self._close_client()
