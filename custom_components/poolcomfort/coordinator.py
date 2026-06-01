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

# Cooldown between *polling* reconnect attempts after a failed handshake.
# Grows linearly (1 min × failures) to avoid pounding a pump that's still
# busy freeing slots, capped at 3 min so we always recover within a few
# minutes once the pump becomes available again.
RECONNECT_BASE_COOLDOWN = 60.0
RECONNECT_MAX_COOLDOWN = 3 * 60.0

# If we've been failing for this long, drop the failure counter and start
# fresh.  Prevents permanent lockout if the pump comes back silently and
# we missed it because we were still sleeping in the longest cooldown.
FAILURE_RESET_AFTER = 30 * 60.0


class PoolComfortCoordinator(DataUpdateCoordinator[PoolDiagnostics]):
    """Persistent-session coordinator with conservative reconnection.

    **Why persistent sessions?**
    The pump firmware has a tiny session table and is very slow to reclaim
    abandoned slots.  Creating a new session every 30 s poll fills the
    table within minutes because the pump never frees old slots fast
    enough.  Keeping one long-lived session with keepalive pings is the
    least-bad option.

    **Polling strategy (gets called every 30 s by HA)**
    1. Reuse the existing session for the query.
    2. If that fails (session died), try one immediate reconnect.
    3. If the reconnect also fails (table full), enter a 1-minute cooldown.
       Successive failures lengthen the cooldown linearly to 3 minutes.
    4. After 30 minutes of continuous failure, reset the counter and start
       trying every minute again — avoids permanent lockout.
    5. During any failure, return the **last successful diagnostics** so
       entities stay at their last reading instead of going ``unavailable``.

    **User-initiated SET commands (climate.set_temperature etc.)**
    Always attempt, regardless of polling cooldown.  The cooldown exists
    to keep our background polling from flooding the pump — it must not
    block the user from pressing a button.
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
            client = self._get_or_open_client(respect_cooldown=True)
            try:
                return client.query_diagnostics()
            except (TimeoutError, RuntimeError, OSError) as exc:
                _LOGGER.info(
                    "Pool Comfort query failed (%s), session will be reopened next poll", exc,
                )
                self._close_client()
                raise

    def _get_or_open_client(self, *, respect_cooldown: bool) -> PoolComfortClient:
        """Return a live client, opening a new session if needed.

        Set ``respect_cooldown=False`` for user-initiated actions: we always
        try at least once so a button press isn't silently rejected.
        """
        if self._client is not None:
            return self._client

        now = time.monotonic()

        # If we've been stuck failing for a while, forgive the old failures
        # and treat this as a fresh attempt.  Otherwise we'd sit forever in
        # the 3-minute cooldown after the pump came back.
        if (
            self._connect_failures > 0
            and now - self._last_connect_attempt > FAILURE_RESET_AFTER
        ):
            _LOGGER.info(
                "Pool Comfort: forgiving %d old failures after %.0f min idle",
                self._connect_failures,
                (now - self._last_connect_attempt) / 60,
            )
            self._connect_failures = 0

        if respect_cooldown and self._connect_failures > 0:
            cooldown = self._reconnect_cooldown()
            elapsed = now - self._last_connect_attempt
            if elapsed < cooldown:
                raise RuntimeError(
                    f"reconnect cooldown: {cooldown - elapsed:.0f}s remaining "
                    f"(failure #{self._connect_failures})"
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
                "Pool Comfort connect failed (#%d), next poll retry in %.0fs",
                self._connect_failures,
                self._reconnect_cooldown(),
            )
            raise

        self._connect_failures = 0
        self._client = client
        _LOGGER.info("Pool Comfort session opened to %s", self.host)
        return client

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
        # Linear: 1 min, 2 min, 3 min — capped at 3 min.
        return min(
            RECONNECT_MAX_COOLDOWN,
            RECONNECT_BASE_COOLDOWN * self._connect_failures,
        )

    # ------------------------------------------------------------------
    # SET commands (temperature, mode, power)
    # ------------------------------------------------------------------

    async def async_apply(self, action) -> None:
        await self.hass.async_add_executor_job(self._apply, action)
        await self.async_request_refresh()

    def _apply(self, action) -> None:
        with self._lock:
            # First try the existing session — fast path.
            if self._client is not None:
                try:
                    action(self._client)
                    return
                except (TimeoutError, RuntimeError, OSError):
                    self._close_client()

            # Open a fresh session.  User actions bypass the polling
            # cooldown so a button press never silently dies waiting.
            client = self._get_or_open_client(respect_cooldown=False)
            try:
                action(client)
            except (TimeoutError, RuntimeError, OSError):
                self._close_client()
                raise

    # ------------------------------------------------------------------
    # Cleanup (integration unload)
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        self._close_client()
