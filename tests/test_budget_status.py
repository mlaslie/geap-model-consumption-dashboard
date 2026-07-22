"""
Tests for GET /api/budget-status.

Covers:
- Custom-rule user: is_global_default=False
- Global-default fallback user: is_global_default=True
- Zero-usage configured identity present with consumed=0
- Percentage math (tokens and money)
- Period-keyed fetches: assert the correct days argument per rule period
"""
import pytest
import backend.auth
import backend.main
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

def _make_totals_row(user, tokens, cost, calls=1, model="gemini-2.5-flash"):
    """Build a row in the shape returned by get_user_model_totals_cached."""
    return {
        "user_email": user,
        "model_name": model,
        "input_tokens": tokens // 2,
        "output_tokens": tokens // 2,
        "total_tokens": tokens,
        "call_count": calls,
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
# Custom-rule user -> is_global_default=False
# ---------------------------------------------------------------------------

def test_custom_rule_user_is_not_global_default(monkeypatch):
    """A user with a custom budget rule must have is_global_default=False."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "alice@example.com": _rule("alice@example.com", limit=10_000),
    }
    rows = [_make_totals_row("alice@example.com", tokens=5_000, cost=0.001)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "alice@example.com" in data
    assert data["alice@example.com"]["is_global_default"] is False


# ---------------------------------------------------------------------------
# Global-default fallback user -> is_global_default=True
# ---------------------------------------------------------------------------

def test_global_default_fallback_user_is_global_default(monkeypatch):
    """A user not in budgets must fall back to global_default with is_global_default=True."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
    }
    rows = [_make_totals_row("bob@example.com", tokens=5_000, cost=0.001)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "bob@example.com" in data
    assert data["bob@example.com"]["is_global_default"] is True


# ---------------------------------------------------------------------------
# Zero-usage configured identity
# ---------------------------------------------------------------------------

def test_zero_usage_configured_identity_present_with_consumed_zero(monkeypatch):
    """
    An identity that has a custom budget rule but no log activity for the period
    must appear in the response with consumed=0.0 and percentage=0.0.
    """
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "charlie@example.com": _rule("charlie@example.com", limit=50_000),
    }
    # No rows for charlie
    rows = []

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "charlie@example.com" in data
    assert data["charlie@example.com"]["consumed"] == 0.0
    assert data["charlie@example.com"]["percentage"] == 0.0
    assert data["charlie@example.com"]["is_global_default"] is False


# ---------------------------------------------------------------------------
# Percentage math - token budget
# ---------------------------------------------------------------------------

def test_percentage_math_token_budget(monkeypatch):
    """Consuming 5 000 of a 10 000-token limit must produce percentage=50.0."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "alice@example.com": _rule("alice@example.com", limit=10_000, b_type="token"),
    }
    rows = [_make_totals_row("alice@example.com", tokens=5_000, cost=0.001)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["alice@example.com"]["percentage"] == pytest.approx(50.0)
    assert data["alice@example.com"]["consumed"] == 5000


# ---------------------------------------------------------------------------
# Percentage math - money budget
# ---------------------------------------------------------------------------

def test_percentage_math_money_budget(monkeypatch):
    """Consuming $0.50 of a $2.00 money limit must produce percentage=25.0."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "diana@example.com": _rule("diana@example.com", limit=2.0, b_type="money"),
    }
    rows = [_make_totals_row("diana@example.com", tokens=1_000, cost=0.50)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "diana@example.com" in data
    entry = data["diana@example.com"]
    assert entry["type"] == "money"
    assert entry["percentage"] == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# Period-keyed fetches: assert days arg per call
# ---------------------------------------------------------------------------

def test_period_keyed_fetches_day_uses_days_1(monkeypatch):
    """A 'day'-period budget must cause a fetch with days=1."""
    days_seen = []

    def fake_fetch(days):
        days_seen.append(days)
        return []

    budgets = {
        "global_default": _rule("global_default", period="day", limit=1_000_000),
    }

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", fake_fetch)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    assert 1 in days_seen


def test_period_keyed_fetches_month_uses_days_30(monkeypatch):
    """A 'month'-period budget must cause a fetch with days=30."""
    days_seen = []

    def fake_fetch(days):
        days_seen.append(days)
        return []

    budgets = {
        "global_default": _rule("global_default", period="month", limit=1_000_000),
    }

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", fake_fetch)

    client.get("/api/budget-status")
    assert 30 in days_seen


def test_period_keyed_fetches_multiple_periods_fetch_once_each(monkeypatch):
    """Two distinct periods must each trigger exactly one fetch with the right days arg."""
    days_seen = []

    def fake_fetch(days):
        days_seen.append(days)
        return []

    budgets = {
        "global_default": _rule("global_default", period="month", limit=1_000_000),
        "alice@example.com": _rule("alice@example.com", period="day", limit=5_000),
    }

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", fake_fetch)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    # Both distinct periods must be represented
    assert 1 in days_seen   # day -> 1
    assert 30 in days_seen  # month -> 30
    # Same period should not be fetched more than once (deduplication)
    assert days_seen.count(1) == 1
    assert days_seen.count(30) == 1


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

def test_budget_status_response_shape(monkeypatch):
    """Each entry in the budget-status response must contain the expected keys."""
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "eve@example.com": _rule("eve@example.com", limit=10_000),
    }
    rows = [_make_totals_row("eve@example.com", tokens=3_000, cost=0.0005)]

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "eve@example.com" in data

    required_keys = {
        "consumed", "limit", "type", "period", "percentage",
        "threshold_percentage", "hard_limit_enabled", "is_global_default",
    }
    entry = data["eve@example.com"]
    missing = required_keys - set(entry.keys())
    assert not missing, f"Missing keys: {missing}"
