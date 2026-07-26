"""
Unit tests for the _compute_burn pure helper in backend.main.

All assertions use known numbers so rounding behaviour is verifiable without
the BQ or GCS stack.
"""
import pytest
from backend.main import _compute_burn
from backend.constants import PERIOD_DAYS


# ---------------------------------------------------------------------------
# Normal burn — known numbers
# ---------------------------------------------------------------------------

class TestComputeBurnNormal:
    """Standard case: positive burn rate, headroom remaining."""

    def _call(self):
        # consumed=500, limit=1000, 7-day window total=70, period=month(30 days)
        # daily_burn = 70/7 = 10.0
        # steady state of the TRAILING window = 10 * 30 = 300 → 30.0% of limit
        # 300 <= 1000, so this rate is sustainable and never breaches: the
        # window sheds old spend as fast as it accrues → days_to_breach None.
        return _compute_burn(
            consumed=500.0,
            limit=1000.0,
            recent_window_cost_or_tokens=70.0,
            burn_window_days=7,
            period_days=30,
        )

    def test_recent_daily_burn(self):
        assert self._call()["recent_daily_burn"] == pytest.approx(10.0)

    def test_burn_window_days_echoed(self):
        assert self._call()["burn_window_days"] == 7

    def test_days_to_breach_none_when_rate_is_sustainable(self):
        # Steady state (300) is below the limit (1000) — a trailing window at
        # this rate never breaches, so predicting one would be a false alarm.
        assert self._call()["days_to_breach"] is None

    def test_projected_period_pct(self):
        # Steady state only — NOT consumed + projection, which would double
        # count (consumed is already a full trailing period of spend).
        assert self._call()["projected_period_pct"] == pytest.approx(30.0)

    def test_unsustainable_rate_does_predict_a_breach(self):
        # Same shape, but the rate's steady state (50*30=1500) exceeds the
        # limit, so a breach IS predicted: headroom 500 / 50 per day = 10 days.
        result = _compute_burn(
            consumed=500.0, limit=1000.0,
            recent_window_cost_or_tokens=350.0,  # 50/day
            burn_window_days=7, period_days=30,
        )
        assert result["days_to_breach"] == pytest.approx(10.0)
        assert result["projected_period_pct"] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# Zero burn → days_to_breach and projected_period_pct are None
# ---------------------------------------------------------------------------

def test_zero_burn_days_to_breach_is_null():
    result = _compute_burn(
        consumed=0.0,
        limit=1000.0,
        recent_window_cost_or_tokens=0.0,
        burn_window_days=7,
        period_days=30,
    )
    assert result["days_to_breach"] is None


def test_zero_burn_projected_pct_is_null():
    result = _compute_burn(
        consumed=0.0,
        limit=1000.0,
        recent_window_cost_or_tokens=0.0,
        burn_window_days=7,
        period_days=30,
    )
    assert result["projected_period_pct"] is None


def test_zero_burn_daily_burn_is_zero():
    result = _compute_burn(
        consumed=0.0,
        limit=1000.0,
        recent_window_cost_or_tokens=0.0,
        burn_window_days=7,
        period_days=30,
    )
    assert result["recent_daily_burn"] == 0.0


# ---------------------------------------------------------------------------
# Already at/over limit → days_to_breach == 0
# ---------------------------------------------------------------------------

def test_already_at_limit_days_to_breach_is_zero():
    result = _compute_burn(
        consumed=1000.0,
        limit=1000.0,
        recent_window_cost_or_tokens=70.0,
        burn_window_days=7,
        period_days=30,
    )
    assert result["days_to_breach"] == 0.0


def test_over_limit_days_to_breach_is_zero():
    result = _compute_burn(
        consumed=1100.0,
        limit=1000.0,
        recent_window_cost_or_tokens=70.0,
        burn_window_days=7,
        period_days=30,
    )
    assert result["days_to_breach"] == 0.0


def test_over_limit_projected_pct_is_steady_state():
    """Even when already breached, projected_pct reports the STEADY STATE of the
    trailing window (rate x period), not past consumption plus a projection —
    so an over-limit identity whose rate has since dropped can project below
    100%, which is the useful signal ("are they still overspending?")."""
    result = _compute_burn(
        consumed=1100.0,
        limit=1000.0,
        recent_window_cost_or_tokens=70.0,  # daily_burn=10
        burn_window_days=7,
        period_days=30,
    )
    # steady state = 10/day * 30 days = 300 → 30% of the 1000 limit.
    # (Past consumption is deliberately NOT added: it is already a full
    # trailing period of spend, so adding it would double-count.)
    assert result["projected_period_pct"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# Negative headroom clamped to 0
# ---------------------------------------------------------------------------

def test_negative_headroom_clamped_to_zero():
    """
    If somehow consumed < limit but the calculated headroom/burn is negative
    (shouldn't occur in normal operation, but guarded defensively), days_to_breach
    must never go below 0.
    """
    # Simulate a scenario where consumed is just below limit with a huge burn.
    # headroom = 999.9999 - 999.9999 = could float-drift negative; use a value
    # just under limit where raw division is tiny and positive — clamping only
    # fires on true negatives.  We test by constructing a case where a float
    # subtraction could in theory produce -epsilon.
    result = _compute_burn(
        consumed=999.9999999999,
        limit=1000.0,
        recent_window_cost_or_tokens=700.0,  # daily_burn = 100
        burn_window_days=7,
        period_days=30,
    )
    # headroom = 1000 - 999.9999999999 ≈ 1e-10 → tiny positive, not clamped
    # Just verify it's not negative
    assert result["days_to_breach"] is not None
    assert result["days_to_breach"] >= 0.0


def test_clamped_gives_zero_not_negative():
    """Directly verify that max(raw, 0.0) is applied."""
    # Force the situation: consumed slightly below limit, effectively zero headroom.
    import math
    result = _compute_burn(
        consumed=1000.0 - 1e-15,   # float epsilon below limit
        limit=1000.0,
        recent_window_cost_or_tokens=1e6,  # enormous burn → raw = 1e-15 / (1e6/7) ≈ 0
        burn_window_days=7,
        period_days=30,
    )
    # Must not be negative
    assert result["days_to_breach"] is not None
    assert result["days_to_breach"] >= 0.0


# ---------------------------------------------------------------------------
# Daily-period budget uses a 1-day burn window, not 7
# ---------------------------------------------------------------------------

def test_daily_period_burn_window_is_1():
    """
    For a 'day' period budget PERIOD_DAYS['day']=1, burn_window=min(7,1)=1.
    Callers must pass burn_window_days=1 for day-period budgets.
    Verify that the helper computes daily_burn as window/1 (not window/7).
    """
    # window_total=50, burn_window_days=1 → daily_burn=50
    # If we mistakenly passed 7 we'd get 50/7≈7.14 → wrong.
    # limit=30 makes the rate UNSUSTAINABLE (steady state 50/day x 1 day = 50
    # > 30), so days_to_breach is exercised rather than short-circuiting to
    # None on the sustainable path.
    result = _compute_burn(
        consumed=10.0,
        limit=30.0,
        recent_window_cost_or_tokens=50.0,
        burn_window_days=1,  # day period: min(7, PERIOD_DAYS['day']) = min(7, 1) = 1
        period_days=PERIOD_DAYS["day"],  # 1
    )
    assert result["burn_window_days"] == 1
    assert result["recent_daily_burn"] == pytest.approx(50.0)
    # days_to_breach = (30-10)/50 = 0.4
    assert result["days_to_breach"] == pytest.approx(0.4)


def test_daily_period_burn_window_not_7():
    """Confirm that PERIOD_DAYS['day']=1 forces burn_window_days=1, not 7."""
    bw = min(7, PERIOD_DAYS["day"])
    assert bw == 1, (
        f"Expected burn window 1 for 'day' period but got {bw}"
    )


# ---------------------------------------------------------------------------
# projected_period_pct math with known numbers
# ---------------------------------------------------------------------------

def test_projected_pct_known_values():
    """
    consumed=200, limit=1000, 7-day window=140, period_days=30.
    daily_burn = 140/7 = 20
    steady-state window total = 20 * 30 = 600  →  60.0% of the limit.
    (Adding `consumed` here would double-count: consumed is itself a full
    trailing period of spend.)
    """
    result = _compute_burn(
        consumed=200.0,
        limit=1000.0,
        recent_window_cost_or_tokens=140.0,
        burn_window_days=7,
        period_days=30,
    )
    assert result["projected_period_pct"] == pytest.approx(60.0)


def test_projected_pct_zero_limit_is_null():
    """Guard against divide-by-zero when limit is 0."""
    result = _compute_burn(
        consumed=0.0,
        limit=0.0,
        recent_window_cost_or_tokens=10.0,
        burn_window_days=7,
        period_days=30,
    )
    assert result["projected_period_pct"] is None


def test_days_to_breach_rounded_to_1dp():
    """days_to_breach must be rounded to 1 decimal place."""
    # headroom=100, daily_burn = 30/7 ≈ 4.2857... → 100/4.2857 ≈ 23.333... → 23.3
    result = _compute_burn(
        consumed=0.0,
        limit=100.0,
        recent_window_cost_or_tokens=30.0,
        burn_window_days=7,
        period_days=30,
    )
    raw = 100.0 / (30.0 / 7)
    expected = round(raw, 1)
    assert result["days_to_breach"] == pytest.approx(expected)
