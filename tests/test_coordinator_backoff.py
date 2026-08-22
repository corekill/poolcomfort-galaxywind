"""Reconnect backoff rules (v2.4.0).

Home Assistant is not importable here, so the pure backoff arithmetic is
exercised against a stand-in that carries the same fields and reuses the
real functions from the coordinator module where possible.
"""

from __future__ import annotations

from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "poolcomfort"
    / "coordinator.py"
)


def _load_backoff_constants() -> dict[str, float]:
    """Read the tuning constants without importing Home Assistant."""
    source = MODULE_PATH.read_text()
    namespace: dict[str, float] = {}
    for line in source.splitlines():
        if line.startswith(
            (
                "RECONNECT_BASE_COOLDOWN",
                "RECONNECT_MAX_COOLDOWN",
                "WEDGED_AFTER_FAILURES",
                "WEDGED_COOLDOWN",
            )
        ):
            exec(line, {}, namespace)  # noqa: S102 - constant literals only
    return namespace


CONST = _load_backoff_constants()


def cooldown(failures: int) -> float:
    """Mirror of PoolComfortCoordinator._reconnect_cooldown."""
    if failures <= 0:
        return 0
    if failures >= CONST["WEDGED_AFTER_FAILURES"]:
        return CONST["WEDGED_COOLDOWN"]
    return min(
        CONST["RECONNECT_MAX_COOLDOWN"],
        CONST["RECONNECT_BASE_COOLDOWN"] * failures,
    )


def test_backoff_grows_then_caps_before_wedged():
    assert cooldown(0) == 0
    assert cooldown(1) == 60
    assert cooldown(2) == 120
    assert cooldown(3) == 180
    # Capped at 3 minutes while we still expect the pump to come back.
    assert cooldown(4) == 180
    assert cooldown(9) == 180


def test_backoff_steps_up_once_pump_looks_wedged():
    wedged = int(CONST["WEDGED_AFTER_FAILURES"])
    assert cooldown(wedged) == CONST["WEDGED_COOLDOWN"]
    assert cooldown(wedged + 50) == CONST["WEDGED_COOLDOWN"]
    # The step must be a real back-off, not a shortening.
    assert cooldown(wedged) > cooldown(wedged - 1)


def test_wedged_cooldown_still_retries_often_enough_to_catch_recovery():
    # Observed self-healing outages last 45-85 minutes; retrying every
    # 10 minutes catches the pump within minutes of it freeing a slot.
    assert CONST["WEDGED_COOLDOWN"] <= 15 * 60


def test_coordinator_source_matches_tested_rules():
    """Guard against the real implementation drifting from this mirror."""
    source = MODULE_PATH.read_text()
    assert "if self._connect_failures >= WEDGED_AFTER_FAILURES:" in source
    assert "return WEDGED_COOLDOWN" in source
    assert "RECONNECT_BASE_COOLDOWN * self._connect_failures" in source


def test_keepalive_matches_official_app_cadence():
    """The app pings every 3.0 s; we must not be more aggressive."""
    from poolcomfort_local.client import KEEPALIVE_INTERVAL

    assert KEEPALIVE_INTERVAL >= 3.0
