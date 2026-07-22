"""
Tests for GET /api/alerts.

Monkeypatches get_user_model_totals_cached (the function _fetch_period_user_totals
in main.py actually calls) and load_budgets so no BQ/GCS traffic occurs. The
autouse clear_usage_cache fixture in conftest.py ensures both TTL caches never
serve stale data.
"""
import pytest
import backend.main
import backend.auth
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


# ---------------------------------------------------------------------------
# Helpers
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


def _day_budget(identity, limit, threshold=50.0, period="day", b_type="token"):
    return {
        "identity": identity,
        "period": period,
        "type": b_type,
        "limit": limit,
        "alert_threshold_percentage": threshold,
        "hard_limit_enabled": False,
    }


# ---------------------------------------------------------------------------
# Period enforcement: 'day' rule fetches with days=1
# ---------------------------------------------------------------------------

def test_day_period_fetches_with_days_1(monkeypatch):
    """A 'day'-period budget rule must cause a fetch with days=1."""
    days_seen = []

    def fake_fetch(days):
        days_seen.append(days)
        return []

    budgets = {
        "global_default": _day_budget("global_default", limit=1_000_000, period="day"),
        "alice@example.com": _day_budget("alice@example.com", limit=500_000, period="day"),
    }

    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", fake_fetch)
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)

    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    # days=1 must have been passed (only once, since deduplication collapses same period)
    assert 1 in days_seen


def test_month_period_fetches_with_days_30(monkeypatch):
    days_seen = []

    def fake_fetch(days):
        days_seen.append(days)
        return []

    budgets = {
        "global_default": _day_budget("global_default", limit=1_000_000, period="month"),
    }

    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", fake_fetch)
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)

    client.get("/api/alerts")
    assert 30 in days_seen


# ---------------------------------------------------------------------------
# Threshold crossing -> warning severity
# ---------------------------------------------------------------------------

def test_threshold_crossing_produces_warning(monkeypatch):
    """Consuming at 60 % of a 1 000 token/day limit (threshold 50 %) -> warning."""
    rows = [_make_totals_row("bob@example.com", tokens=600, cost=0.0001)]
    budgets = {
        "global_default": _day_budget("global_default", limit=10_000_000, period="day"),
        "bob@example.com": _day_budget("bob@example.com", limit=1_000, threshold=50.0, period="day"),
    }

    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)

    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    alerts = resp.json()["data"]
    bob_alerts = [a for a in alerts if a["identity"] == "bob@example.com"]
    assert len(bob_alerts) == 1
    assert bob_alerts[0]["severity"] == "warning"


# ---------------------------------------------------------------------------
# Exceeding limit -> danger severity
# ---------------------------------------------------------------------------

def test_exceeding_limit_produces_danger(monkeypatch):
    """Consuming 1 100 tokens against a 1 000-token limit -> danger."""
    rows = [_make_totals_row("carol@example.com", tokens=1_100, cost=0.0002)]
    budgets = {
        "global_default": _day_budget("global_default", limit=10_000_000, period="day"),
        "carol@example.com": _day_budget("carol@example.com", limit=1_000, threshold=50.0, period="day"),
    }

    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)

    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    alerts = resp.json()["data"]
    carol_alerts = [a for a in alerts if a["identity"] == "carol@example.com"]
    assert len(carol_alerts) == 1
    assert carol_alerts[0]["severity"] == "danger"


# ---------------------------------------------------------------------------
# global_default applies only to users without custom budgets
# ---------------------------------------------------------------------------

def test_global_default_skips_users_with_custom_rule(monkeypatch):
    """
    User 'dave' has a custom rule; user 'eve' does not.
    Both exceed the global threshold. Only eve should appear in alerts via the
    global_default path (dave has his own rule evaluated separately).
    """
    rows = [
        _make_totals_row("dave@example.com", tokens=800, cost=0.0001),
        _make_totals_row("eve@example.com", tokens=800, cost=0.0001),
    ]
    budgets = {
        "global_default": _day_budget(
            "global_default", limit=1_000, threshold=50.0, period="day", b_type="token"
        ),
        # Dave has a custom rule with a very high limit so he produces no alert
        "dave@example.com": _day_budget(
            "dave@example.com", limit=100_000_000, threshold=50.0, period="day"
        ),
    }

    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)

    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    alerts = resp.json()["data"]

    identities = [a["identity"] for a in alerts]
    # Eve should appear (global_default rule applies)
    assert "eve@example.com" in identities
    # Dave must NOT appear via global_default (has custom rule with huge limit)
    global_alerts_for_dave = [
        a for a in alerts
        if a["identity"] == "dave@example.com" and a.get("is_global_default")
    ]
    assert global_alerts_for_dave == []


# ---------------------------------------------------------------------------
# Alert response schema keys
# ---------------------------------------------------------------------------

def test_alert_schema_keys(monkeypatch):
    """Each alert object must contain the expected schema keys."""
    rows = [_make_totals_row("frank@example.com", tokens=800, cost=0.0001)]
    budgets = {
        "global_default": _day_budget("global_default", limit=10_000_000, period="day"),
        "frank@example.com": _day_budget("frank@example.com", limit=1_000, threshold=50.0, period="day"),
    }

    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)

    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    alerts = resp.json()["data"]
    frank_alerts = [a for a in alerts if a["identity"] == "frank@example.com"]
    assert len(frank_alerts) == 1

    required_keys = {
        "identity",
        "metric",
        "limit",
        "consumed",
        "percentage",
        "threshold_percentage",
        "period",
        "hard_limit_enabled",
        "severity",
    }
    alert = frank_alerts[0]
    assert required_keys.issubset(set(alert.keys())), (
        f"Missing keys: {required_keys - set(alert.keys())}"
    )


# ---------------------------------------------------------------------------
# No alerts when below threshold
# ---------------------------------------------------------------------------

def test_no_alert_below_threshold(monkeypatch):
    rows = [_make_totals_row("grace@example.com", tokens=100, cost=0.00001)]
    budgets = {
        "global_default": _day_budget("global_default", limit=10_000_000, period="day"),
        "grace@example.com": _day_budget("grace@example.com", limit=1_000, threshold=50.0, period="day"),
    }

    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)

    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    grace_alerts = [a for a in resp.json()["data"] if a["identity"] == "grace@example.com"]
    assert grace_alerts == []


# ---------------------------------------------------------------------------
# No truncation: alerts path considers all rows even beyond 1000
# ---------------------------------------------------------------------------

def test_alerts_considers_all_rows_beyond_1000(monkeypatch):
    """
    With 1 500 synthetic user rows returned by get_user_model_totals_cached,
    ALL users must be considered — no 1000-row cap.
    Alerts are triggered for every user since each has 600 tokens against a
    1 000-token limit with a 50 % threshold.
    """
    num_users = 1500
    rows = [
        _make_totals_row(f"user{i}@example.com", tokens=600, cost=0.0001)
        for i in range(num_users)
    ]
    # global_default only — all 1500 users fall under it
    budgets = {
        "global_default": _day_budget(
            "global_default", limit=1_000, threshold=50.0, period="day", b_type="token"
        ),
    }

    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)

    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    alerts = resp.json()["data"]
    # All 1500 users must have generated an alert
    assert len(alerts) == num_users, (
        f"Expected {num_users} alerts but got {len(alerts)} "
        "(likely truncated at 1000 if the old code path is still used)"
    )
