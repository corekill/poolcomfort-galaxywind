from __future__ import annotations

from datetime import datetime
import logging
import socket
import threading
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import PoolComfortClient
from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_TIMEOUT, DOMAIN, STALE_AFTER
from .protocol import PoolDiagnostics

_LOGGER = logging.getLogger(__name__)

# Cooldown between reconnect attempts after a failed handshake, growing
# linearly (1 min x failures) and capped so we recover quickly once the pump
# frees a slot.
RECONNECT_BASE_COOLDOWN = 60.0
RECONNECT_MAX_COOLDOWN = 3 * 60.0

# The pump firmware sometimes wedges completely: it keeps answering discovery
# on UDP 8818 while ignoring every login on 1194, and only a power cycle
# clears it.  Retrying every 3 minutes for hours neither helps nor is free —
# a handshake that dies half way can leave another slot allocated — so once
# this many attempts in a row have failed, back off hard.
WEDGED_AFTER_FAILURES = 10
WEDGED_COOLDOWN = 10 * 60.0


class PoolComfortCoordinator(DataUpdateCoordinator[PoolDiagnostics]):
    """Persistent-session coordinator with conservative reconnection.

    **Why persistent sessions?**
    The pump has a tiny session table and reclaims abandoned slots very
    slowly, so opening a session per poll (v2.0.0) filled the table within
    minutes.  One long-lived session with keepalive pings is the least-bad
    option; everything here is aimed at keeping the number of sessions we
    ever create as close to one as possible.

    **Polling**
    1. Reuse the existing session for the query.
    2. If that fails, reconnect — reusing the same local UDP source port, so
       a pump that keys its session table by ``(address, port)`` can replace
       our old entry instead of allocating another one.
    3. Repeated failures back off: 1, 2, 3 minutes, then 10 minutes once the
       pump looks wedged.
    4. While polls fail, keep returning the **last successful diagnostics**
       so entities hold their last reading instead of going ``unavailable``.
       The freshness of that data is published separately — see
       ``last_success``, ``is_connected`` and ``data_age`` — because stale
       data that looks live is worse than no data at all.

    **User-initiated SET commands** always attempt, regardless of cooldown:
    the cooldown exists to stop *our polling* from flooding the pump, and
    must never silently swallow a button press.
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
        self._last_local_port = 0

        # Published health state.  These describe the *connection*, not the
        # pump, so they stay truthful while `data` is deliberately stale.
        self.last_success: datetime | None = None
        self.consecutive_failures = 0
        self.sessions_opened = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    # Health, as seen by the diagnostic entities
    # ------------------------------------------------------------------

    @property
    def data_age(self) -> float | None:
        """Seconds since the last poll that actually reached the pump."""
        if self.last_success is None:
            return None
        return (dt_util.utcnow() - self.last_success).total_seconds()

    @property
    def is_connected(self) -> bool:
        age = self.data_age
        return age is not None and age < STALE_AFTER.total_seconds()

    # ------------------------------------------------------------------
    # DataUpdateCoordinator entry point
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> PoolDiagnostics:
        try:
            data = await self.hass.async_add_executor_job(self._fetch)
        except Exception as exc:
            self.consecutive_failures += 1
            self.last_error = str(exc)
            # Hold the last reading rather than flipping every entity to
            # "unavailable"; the health entities report the staleness.
            if self._last_good_data is not None:
                _LOGGER.debug("Poll failed (%s), holding last known state", exc)
                return self._last_good_data
            raise UpdateFailed(str(exc)) from exc

        self._last_good_data = data
        self.last_success = dt_util.utcnow()
        self.consecutive_failures = 0
        self.last_error = None
        return data

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
                    "Pool Comfort query failed (%s), reopening session next poll", exc
                )
                self._close_client()
                raise

    def _get_or_open_client(self, *, respect_cooldown: bool) -> PoolComfortClient:
        """Return a live client, opening a session if we do not have one.

        ``respect_cooldown=False`` is for user-initiated actions: always try
        at least once so a button press is not silently rejected.
        """
        if self._client is not None:
            return self._client

        now = time.monotonic()
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
            local_port=self._bindable_port(self._last_local_port),
            keepalive=True,
        )
        try:
            client.connect()
        except Exception:
            client.close()
            self._connect_failures += 1
            self._log_connect_failure()
            raise

        self._connect_failures = 0
        self._client = client
        self.sessions_opened += 1
        self._remember_port(client)
        _LOGGER.info(
            "Pool Comfort session opened to %s (session #%d since restart)",
            self.host,
            self.sessions_opened,
        )
        return client

    def _bindable_port(self, port: int) -> int:
        """Return ``port`` if we can still bind it, else 0 for a fresh one.

        Reusing the previous source port lets the pump replace our old
        session instead of stacking up another one, but we must never let a
        stuck port lock the integration out.
        """
        if not port:
            return 0
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("", port))
            return port
        except OSError:
            _LOGGER.debug("Source port %d no longer bindable, using a new one", port)
            return 0
        finally:
            probe.close()

    def _remember_port(self, client: PoolComfortClient) -> None:
        if client._sock is None:
            return
        try:
            self._last_local_port = client._sock.getsockname()[1]
        except OSError:
            pass

    def _close_client(self) -> None:
        if self._client is None:
            return
        self._remember_port(self._client)
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass
        self._client = None

    def _reconnect_cooldown(self) -> float:
        if self._connect_failures <= 0:
            return 0
        if self._connect_failures >= WEDGED_AFTER_FAILURES:
            return WEDGED_COOLDOWN
        return min(RECONNECT_MAX_COOLDOWN, RECONNECT_BASE_COOLDOWN * self._connect_failures)

    def _log_connect_failure(self) -> None:
        # A wedged pump can fail for hours; warn about the onset, then only
        # occasionally, so the log stays readable.
        message = (
            "Pool Comfort connect failed (#%d), next poll retry in %.0fs"
            if self._connect_failures < WEDGED_AFTER_FAILURES
            else (
                "Pool Comfort still not accepting logins (#%d attempts); the pump "
                "has most likely wedged and needs a power cycle. Retrying every %.0fs"
            )
        )
        args = (self._connect_failures, self._reconnect_cooldown())
        if self._connect_failures <= 3 or self._connect_failures % 20 == 0:
            _LOGGER.warning(message, *args)
        else:
            _LOGGER.debug(message, *args)

    # ------------------------------------------------------------------
    # SET commands (temperature, mode, power)
    # ------------------------------------------------------------------

    async def async_apply(self, action) -> None:
        await self.hass.async_add_executor_job(self._apply, action)
        await self.async_request_refresh()

    def _apply(self, action) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    action(self._client)
                    return
                except (TimeoutError, RuntimeError, OSError):
                    self._close_client()

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
        # Called from the event loop on unload, so it must not wait on the
        # fetch lock.  A poll running concurrently just sees the socket go
        # away and fails harmlessly.
        self._close_client()
