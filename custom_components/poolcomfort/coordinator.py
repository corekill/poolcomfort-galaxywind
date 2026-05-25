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

# If the pump hasn't sent any packet for this long despite our 1.5 s
# keepalive pings, the session is almost certainly dead.  Must be longer
# than DEFAULT_SCAN_INTERVAL so a normal polling gap doesn't trigger it.
SESSION_STALE_SECONDS = 45.0


class PoolComfortCoordinator(DataUpdateCoordinator[PoolDiagnostics]):
    def __init__(self, hass: HomeAssistant, host: str, password: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.host = host
        self.password = password
        self._client: PoolComfortClient | None = None
        self._client_lock = threading.Lock()
        self._last_connect_attempt: float = -RECONNECT_BASE_COOLDOWN
        self._connect_failures = 0
        self._consecutive_timeouts = 0
        self._last_local_port = 0

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
        _LOGGER.debug("Opening new session to %s (local port %s)", self.host, self._last_local_port or "auto")
        client = PoolComfortClient(self.host, password=self.password, timeout=DEFAULT_TIMEOUT, local_port=self._last_local_port)
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
        # Remember the local port so reconnections reuse it. The pump may
        # identify sessions partly by source port; reusing it lets the pump
        # reclaim the old slot instead of allocating a new one.
        if client._sock is not None:
            try:
                self._last_local_port = client._sock.getsockname()[1]
            except OSError:
                pass
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
            # Save the local port before closing so we can reuse it.
            if self._client._sock is not None:
                try:
                    self._last_local_port = self._client._sock.getsockname()[1]
                except OSError:
                    pass
            self._client.close()
            self._client = None

    def _is_session_stale(self) -> bool:
        """True when the pump stopped responding despite our keepalive pings."""
        client = self._client
        if client is None or client._last_recv == 0:
            return False
        return (time.monotonic() - client._last_recv) > SESSION_STALE_SECONDS

    def _fetch(self) -> PoolDiagnostics:
        with self._client_lock:
            # --- proactive staleness check ---
            # Our keepalive thread pings the pump every 1.5 s and the reader
            # thread timestamps every received packet.  If nothing came back
            # for SESSION_STALE_SECONDS the session is dead on the pump side.
            # Close and reconnect *immediately* (no cooldown — the pump
            # already freed the slot when it expired our session).
            if self._is_session_stale():
                silence = time.monotonic() - self._client._last_recv  # type: ignore[union-attr]
                _LOGGER.info(
                    "Session to %s stale (no packet for %.0fs), reconnecting",
                    self.host,
                    silence,
                )
                self._close_client()
                # This was a normal session expiry, not a connect failure.
                # Allow immediate reconnect — no backoff needed.
                self._connect_failures = 0
                self._last_connect_attempt = 0
                self._consecutive_timeouts = 0

            try:
                client = self._ensure_client()
                result = client.query_diagnostics()
                self._consecutive_timeouts = 0
                return result
            except TimeoutError:
                # Pump didn't respond but the UDP session might still be
                # alive on the pump side.  Don't tear it down yet — the
                # staleness check above will catch truly dead sessions on
                # the next poll cycle.
                self._consecutive_timeouts += 1
                if self._consecutive_timeouts >= 4:
                    _LOGGER.warning(
                        "%d consecutive timeouts from %s; forcing reconnect",
                        self._consecutive_timeouts,
                        self.host,
                    )
                    self._close_client()
                    self._consecutive_timeouts = 0
                    # Session was dead — allow immediate reconnect.
                    self._connect_failures = 0
                    self._last_connect_attempt = 0
                else:
                    _LOGGER.debug(
                        "Timeout %d/4 from %s, keeping session alive",
                        self._consecutive_timeouts,
                        self.host,
                    )
                raise
            except Exception:
                self._consecutive_timeouts = 0
                self._close_client()
                raise

    async def async_apply(self, action) -> None:
        await self.hass.async_add_executor_job(self._apply, action)
        await self.async_request_refresh()

    def _apply(self, action) -> None:
        with self._client_lock:
            if self._is_session_stale():
                self._close_client()
                self._connect_failures = 0
                self._last_connect_attempt = 0
                self._consecutive_timeouts = 0
            try:
                client = self._ensure_client()
                action(client)
                self._consecutive_timeouts = 0
            except TimeoutError:
                self._consecutive_timeouts += 1
                if self._consecutive_timeouts >= 4:
                    self._close_client()
                    self._consecutive_timeouts = 0
                    self._connect_failures = 0
                    self._last_connect_attempt = 0
                raise
            except Exception:
                self._consecutive_timeouts = 0
                self._close_client()
                raise

    def shutdown(self) -> None:
        with self._client_lock:
            self._close_client()
