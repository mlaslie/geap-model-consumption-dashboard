"""
Tests for the burn-rate fields added to GET /api/budget-status (Feature 1).

Covers:
- New fields are present and correctly typed for active users.
- burn_window_days is min(7, PERIOD_DAYS[period]).
- days_to_breach is null when there is no burn.
- days_to_breach is 0 when already at/over limit.
- projected_period_pct is null when burn is zero.
- Computed values match _compute_burn for known inputs.
- Zero-usage configured identity carries the new fields (all-zero/null).
"""
import pytest
import backend.auth
import backend.main
from fastapi.testclient import TestClient
from backend.main import app
from backend.constants import PERIOD_DAYS

client = TestClient(app, raise_server_exceptions=False)

BURN_FIELDS = {
    "recent_daily_burn",
    "burn_window_days",
    "days_to_breach",
    "projected_period_pct",
}


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(user, tokens, cost, model="gemini-2.5-flash"):
    return {
        "user_email": user,
        "model_name": model,
        "input_tokens": tokens // 2,
        "output_tokens": tokens // 2,
        "thoughts_tokens": 0,
        "total_tokens": tokens,
        "call_count": 1,
        "estimated_cost_usd": cost,
        "pricing_match": "exact",
    }


def _rule(identity, period="month", b_type="token", limit=10_000, threshold=80.0):
    return {
        "identity": identity,
        "period": period,
        "type": b_type,
        "limit": limit,
        "alert_threshold_percentage": threshold,
        "hard_limit_enabled": False,
    }


# ---------------------------------------------------------------------------
# New fields are present in the response
# ---------------------------------------------------------------------------

def test_burn_fields_present_for_active_user(monkeypatch):
    """Every active user entry must include the four burn-rate fields."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "alice@example.com": _rule("alice@example.com", period="month", limit=10_000),
    }
    rows = [_make_row("alice@example.com", tokens=1_000, cost=0.01)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    entry = resp.json()["data"]["alice@example.com"]
    missing = BURN_FIELDS - set(entry.keys())
    assert not missing, f"Missing burn fields: {missing}"


# ---------------------------------------------------------------------------
# Field types are correct
# ---------------------------------------------------------------------------

def test_burn_fields_types_active_user(monkeypatch):
    """recent_daily_burn and burn_window_days must be numeric; others float or null."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "bob@example.com": _rule("bob@example.com", period="month", limit=10_000),
    }
    rows = [_make_row("bob@example.com", tokens=2_000, cost=0.02)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    entry = resp.json()["data"]["bob@example.com"]

    assert isinstance(entry["recent_daily_burn"], (int, float))
    assert isinstance(entry["burn_window_days"], int)
    # days_to_breach and projected_period_pct may be float or null
    dtb = entry["days_to_breach"]
    ppp = entry["projected_period_pct"]
    assert dtb is None or isinstance(dtb, (int, float))
    assert ppp is None or isinstance(ppp, (int, float))


# ---------------------------------------------------------------------------
# burn_window_days = min(7, PERIOD_DAYS[period])
# ---------------------------------------------------------------------------

def test_burn_window_days_is_7_for_month_period(monkeypatch):
    """month period → burn_window_days = min(7, 30) = 7."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "carol@example.com": _rule("carol@example.com", period="month", limit=10_000),
    }
    rows = [_make_row("carol@example.com", tokens=1_000, cost=0.01)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    entry = resp.json()["data"]["carol@example.com"]
    assert entry["burn_window_days"] == 7


def test_burn_window_days_is_1_for_day_period(monkeypatch):
    """day period → burn_window_days = min(7, 1) = 1."""
    budgets = {
        "global_default": _rule("global_default", period="day", limit=100_000),
        "dave@example.com": _rule("dave@example.com", period="day", limit=5_000),
    }
    rows = [_make_row("dave@example.com", tokens=500, cost=0.005)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    entry = resp.json()["data"]["dave@example.com"]
    assert entry["burn_window_days"] == 1


def test_burn_window_days_is_7_for_year_period(monkeypatch):
    """year period → burn_window_days = min(7, 365) = 7."""
    budgets = {
        "global_default": _rule("global_default", period="year", limit=10_000_000),
    }
    rows = [_make_row("eve@example.com", tokens=5_000, cost=0.05)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    entry = resp.json()["data"]["eve@example.com"]
    assert entry["burn_window_days"] == 7


# ---------------------------------------------------------------------------
# days_to_breach is None when burn = 0
# ---------------------------------------------------------------------------

def test_days_to_breach_null_when_zero_burn(monkeypatch):
    """When the recent window has no consumption, days_to_breach must be null."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "frank@example.com": _rule("frank@example.com", period="month", limit=10_000),
    }
    # Period data has tokens; burn window will also return the same rows
    # but with 0 tokens so burn = 0.  Achieve this by returning empty rows
    # for all days arguments.
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: [])

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    # frank has a custom rule so appears with consumed=0
    entry = resp.json()["data"].get("frank@example.com")
    assert entry is not None
    assert entry["days_to_breach"] is None


# ---------------------------------------------------------------------------
# days_to_breach is 0 when already at/over limit
# ---------------------------------------------------------------------------

def test_days_to_breach_zero_when_at_limit(monkeypatch):
    """When consumed >= limit, days_to_breach must be 0."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "grace@example.com": _rule("grace@example.com", period="month", limit=1_000),
    }
    # 1000 tokens consumed → at the 1000-token limit
    rows = [_make_row("grace@example.com", tokens=1_000, cost=0.01)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    entry = resp.json()["data"]["grace@example.com"]
    assert entry["days_to_breach"] == 0.0


def test_days_to_breach_zero_when_over_limit(monkeypatch):
    """When consumed > limit (over-budget), days_to_breach must also be 0."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "henry@example.com": _rule("henry@example.com", period="month", limit=500),
    }
    rows = [_make_row("henry@example.com", tokens=1_000, cost=0.01)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    entry = resp.json()["data"]["henry@example.com"]
    assert entry["days_to_breach"] == 0.0


# ---------------------------------------------------------------------------
# projected_period_pct is None when burn = 0
# ---------------------------------------------------------------------------

def test_projected_period_pct_null_when_zero_burn(monkeypatch):
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "iris@example.com": _rule("iris@example.com", period="month", limit=10_000),
    }
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: [])

    resp = client.get("/api/budget-status")
    # iris has a custom rule, appears with consumed=0, burn=0
    entry = resp.json()["data"].get("iris@example.com")
    assert entry is not None
    assert entry["projected_period_pct"] is None


# ---------------------------------------------------------------------------
# Computed values match _compute_burn for known inputs
# ---------------------------------------------------------------------------

def test_burn_values_match_compute_burn(monkeypatch):
    """
    With a controlled fetch that returns 700 tokens regardless of days arg,
    burn metrics must match what _compute_burn would produce directly.

    Period: month (30 days), burn_window=7 days.
    tokens consumed in period (30d fetch) = 700
    tokens in burn window (7d fetch) = 700
    recent_daily_burn = 700/7 = 100
    days_to_breach = (10000-700)/100 = 93.0
    projected_pct = (700 + 100*30)/10000*100 = 3700/10000*100 = 37.0
    """
    from backend.main import _compute_burn

    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "jack@example.com": _rule(
            "jack@example.com", period="month", b_type="token", limit=10_000
        ),
    }
    rows = [_make_row("jack@example.com", tokens=700, cost=0.007)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    entry = resp.json()["data"]["jack@example.com"]

    expected = _compute_burn(
        consumed=700.0,
        limit=10_000.0,
        recent_window_cost_or_tokens=700.0,
        burn_window_days=7,
        period_days=30,
    )

    assert entry["recent_daily_burn"] == pytest.approx(expected["recent_daily_burn"])
    assert entry["burn_window_days"] == expected["burn_window_days"]
    assert entry["days_to_breach"] == pytest.approx(expected["days_to_breach"])
    assert entry["projected_period_pct"] == pytest.approx(expected["projected_period_pct"])


# ---------------------------------------------------------------------------
# Zero-usage configured identity carries the burn fields
# ---------------------------------------------------------------------------

def test_zero_usage_identity_has_burn_fields(monkeypatch):
    """A configured identity with no log activity still gets the burn fields."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "kate@example.com": _rule("kate@example.com", period="month", limit=10_000),
    }
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: [])

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    entry = resp.json()["data"]["kate@example.com"]

    missing = BURN_FIELDS - set(entry.keys())
    assert not missing, f"Zero-usage entry is missing burn fields: {missing}"
    assert entry["recent_daily_burn"] == 0.0
    assert entry["burn_window_days"] == 7
    assert entry["days_to_breach"] is None
    assert entry["projected_period_pct"] is None


# ---------------------------------------------------------------------------
# Existing response keys are unchanged
# ---------------------------------------------------------------------------

def test_original_fields_still_present(monkeypatch):
    """Adding burn fields must not remove any pre-existing response keys."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "lena@example.com": _rule("lena@example.com", limit=10_000),
    }
    rows = [_make_row("lena@example.com", tokens=3_000, cost=0.003)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    entry = resp.json()["data"]["lena@example.com"]

    original_keys = {
        "consumed", "limit", "type", "period", "percentage",
        "threshold_percentage", "hard_limit_enabled", "is_global_default",
    }
    missing = original_keys - set(entry.keys())
    assert not missing, f"Original keys missing: {missing}"
