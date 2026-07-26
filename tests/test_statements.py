"""
Tests for GET /api/statements?month=YYYY-MM (Feature 2).

Covers:
- month validation: bad format, month 13, future month, 20 years ago → 400
- valid month → 200 with correct top-level structure
- year-boundary correctness (2026-12 → period_end_exclusive 2027-01-01)
- totals equal the sum of per_principal
- per_model sorted by cost desc
- unpriced models surfaced in unpriced_models list
- pricing snapshot present with required keys
- empty month returns zeroed totals (not an error)
- budget_snapshot present and includes global_default
"""
import pytest
import backend.auth
import backend.main
import backend.bq_client
from fastapi.testclient import TestClient
from backend.main import app
from datetime import datetime, timezone

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    user, model, input_tokens, output_tokens,
    cost, calls=1, thoughts_tokens=0, pricing_match="exact"
):
    return {
        "user_email": user,
        "model_name": model,
        "pricing_tier": "le200k",
        "region": "global",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thoughts_tokens": thoughts_tokens,
        "total_tokens": input_tokens + output_tokens,
        "call_count": calls,
        "estimated_cost_usd": cost,
        "pricing_match": pricing_match,
    }


def _default_budgets():
    return {
        "global_default": {
            "identity": "global_default",
            "period": "month",
            "type": "token",
            "limit": 10_000_000,
            "alert_threshold_percentage": 50.0,
            "hard_limit_enabled": False,
        }
    }


def _patch(monkeypatch, rows, budgets=None):
    if budgets is None:
        budgets = _default_budgets()
    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(
        backend.bq_client,
        "get_user_model_totals_range_cached",
        lambda start, end: rows,
    )


# ---------------------------------------------------------------------------
# Month validation — 400 responses
# ---------------------------------------------------------------------------

class TestMonthValidation:

    def test_empty_month_is_400(self):
        resp = client.get("/api/statements?month=")
        assert resp.status_code == 400

    def test_no_month_param_is_400(self):
        resp = client.get("/api/statements")
        assert resp.status_code == 400

    def test_bad_format_no_dash(self):
        resp = client.get("/api/statements?month=202607")
        assert resp.status_code == 400

    def test_bad_format_text(self):
        resp = client.get("/api/statements?month=abc-def")
        assert resp.status_code == 400

    def test_month_00_rejected(self):
        resp = client.get("/api/statements?month=2026-00")
        assert resp.status_code == 400

    def test_month_13_rejected(self):
        resp = client.get("/api/statements?month=2026-13")
        assert resp.status_code == 400

    def test_month_99_rejected(self):
        resp = client.get("/api/statements?month=2026-99")
        assert resp.status_code == 400

    def test_future_month_rejected(self, monkeypatch):
        """Any month strictly after the current calendar month must be 400."""
        now = datetime.now(timezone.utc)
        # Construct a month guaranteed to be in the future
        future_year = now.year + 1
        future_month = f"{future_year:04d}-01"
        _patch(monkeypatch, [])
        resp = client.get(f"/api/statements?month={future_month}")
        assert resp.status_code == 400

    def test_current_month_accepted(self, monkeypatch):
        """Current calendar month is valid (not a future month)."""
        now = datetime.now(timezone.utc)
        current_month = f"{now.year:04d}-{now.month:02d}"
        _patch(monkeypatch, [])
        resp = client.get(f"/api/statements?month={current_month}")
        assert resp.status_code == 200

    def test_20_years_ago_rejected(self, monkeypatch):
        """Month more than 10 years in the past must be 400."""
        now = datetime.now(timezone.utc)
        old_year = now.year - 20
        old_month = f"{old_year:04d}-{now.month:02d}"
        _patch(monkeypatch, [])
        resp = client.get(f"/api/statements?month={old_month}")
        assert resp.status_code == 400

    def test_valid_past_month_accepted(self, monkeypatch):
        """A month a few years ago (within 10-year window) must be 200."""
        _patch(monkeypatch, [])
        resp = client.get("/api/statements?month=2024-01")
        assert resp.status_code == 200

    def test_december_is_valid(self, monkeypatch):
        _patch(monkeypatch, [])
        resp = client.get("/api/statements?month=2025-12")
        assert resp.status_code == 200

    def test_january_is_valid(self, monkeypatch):
        _patch(monkeypatch, [])
        resp = client.get("/api/statements?month=2025-01")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Year-boundary correctness (December → January)
# ---------------------------------------------------------------------------

def test_december_period_end_is_next_january(monkeypatch):
    """2025-12 must produce period_end_exclusive='2026-01-01' (year boundary)."""
    _patch(monkeypatch, [])
    resp = client.get("/api/statements?month=2025-12")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["period_start"] == "2025-12-01"
    assert data["period_end_exclusive"] == "2026-01-01"


def test_january_period_end_is_february(monkeypatch):
    """2025-01 must produce period_end_exclusive='2025-02-01'."""
    _patch(monkeypatch, [])
    resp = client.get("/api/statements?month=2025-01")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["period_start"] == "2025-01-01"
    assert data["period_end_exclusive"] == "2025-02-01"


def test_february_period_end_handles_leap_year(monkeypatch):
    """2024-02 (leap year) → period_end_exclusive='2024-03-01'."""
    _patch(monkeypatch, [])
    resp = client.get("/api/statements?month=2024-02")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["period_start"] == "2024-02-01"
    assert data["period_end_exclusive"] == "2024-03-01"


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

def test_response_top_level_keys(monkeypatch):
    """Response data must include all required top-level fields."""
    _patch(monkeypatch, [])
    resp = client.get("/api/statements?month=2025-06")
    assert resp.status_code == 200
    data = resp.json()["data"]

    required = {
        "month", "period_start", "period_end_exclusive", "generated_at_utc",
        "totals", "per_principal", "per_model",
        "pricing_assumptions", "unpriced_models", "budget_snapshot",
    }
    missing = required - set(data.keys())
    assert not missing, f"Missing top-level keys: {missing}"


def test_totals_keys(monkeypatch):
    """totals must contain all required sub-fields."""
    _patch(monkeypatch, [])
    resp = client.get("/api/statements?month=2025-06")
    totals = resp.json()["data"]["totals"]
    required = {"cost_usd", "input_tokens", "output_tokens", "thoughts_tokens",
                "total_tokens", "calls", "principals"}
    missing = required - set(totals.keys())
    assert not missing, f"Missing totals keys: {missing}"


# ---------------------------------------------------------------------------
# Empty month returns zeroed totals (not an error)
# ---------------------------------------------------------------------------

def test_empty_month_returns_zero_totals(monkeypatch):
    """When BQ returns no rows for the period, totals should be all-zero."""
    _patch(monkeypatch, rows=[])
    resp = client.get("/api/statements?month=2025-06")
    assert resp.status_code == 200
    data = resp.json()["data"]
    totals = data["totals"]
    assert totals["cost_usd"] == 0.0
    assert totals["input_tokens"] == 0
    assert totals["output_tokens"] == 0
    assert totals["thoughts_tokens"] == 0
    assert totals["total_tokens"] == 0
    assert totals["calls"] == 0
    assert totals["principals"] == 0
    assert data["per_principal"] == []
    assert data["per_model"] == []
    assert data["unpriced_models"] == []


# ---------------------------------------------------------------------------
# totals equal sum of per_principal
# ---------------------------------------------------------------------------

def test_totals_equal_sum_of_per_principal(monkeypatch):
    """totals fields must equal the aggregate of all per_principal entries."""
    rows = [
        _make_row("alice@example.com", "gemini-2.5-flash", 1000, 500, 0.10, calls=3),
        _make_row("alice@example.com", "gemini-2.5-pro", 500, 200, 0.05, calls=1),
        _make_row("bob@example.com", "gemini-2.5-flash", 2000, 1000, 0.20, calls=5,
                  thoughts_tokens=100),
    ]
    _patch(monkeypatch, rows)

    resp = client.get("/api/statements?month=2025-06")
    assert resp.status_code == 200
    data = resp.json()["data"]

    totals = data["totals"]
    pp = data["per_principal"]

    sum_cost = round(sum(p["cost_usd"] for p in pp), 6)
    sum_input = sum(p["input_tokens"] for p in pp)
    sum_output = sum(p["output_tokens"] for p in pp)
    sum_thoughts = sum(p["thoughts_tokens"] for p in pp)
    sum_total = sum(p["total_tokens"] for p in pp)
    sum_calls = sum(p["calls"] for p in pp)

    assert totals["cost_usd"] == pytest.approx(sum_cost, rel=1e-5)
    assert totals["input_tokens"] == sum_input
    assert totals["output_tokens"] == sum_output
    assert totals["thoughts_tokens"] == sum_thoughts
    assert totals["total_tokens"] == sum_total
    assert totals["calls"] == sum_calls
    assert totals["principals"] == len(pp)


# ---------------------------------------------------------------------------
# per_model sorted by cost desc
# ---------------------------------------------------------------------------

def test_per_model_sorted_by_cost_desc(monkeypatch):
    """per_model list must be sorted in descending order of cost_usd."""
    rows = [
        _make_row("alice@example.com", "gemini-2.5-flash", 1000, 500, 0.05),
        _make_row("alice@example.com", "gemini-2.5-pro", 500, 200, 0.50),
        _make_row("bob@example.com", "gemini-2.5-flash-lite", 100, 50, 0.01),
    ]
    _patch(monkeypatch, rows)

    resp = client.get("/api/statements?month=2025-06")
    per_model = resp.json()["data"]["per_model"]
    costs = [m["cost_usd"] for m in per_model]
    assert costs == sorted(costs, reverse=True), f"per_model not sorted desc: {costs}"


# ---------------------------------------------------------------------------
# per_principal sorted by cost desc
# ---------------------------------------------------------------------------

def test_per_principal_sorted_by_cost_desc(monkeypatch):
    """per_principal must be sorted descending by cost_usd."""
    rows = [
        _make_row("alice@example.com", "gemini-2.5-flash", 1000, 500, 0.05),
        _make_row("bob@example.com", "gemini-2.5-pro", 500, 200, 0.50),
    ]
    _patch(monkeypatch, rows)

    resp = client.get("/api/statements?month=2025-06")
    pp = resp.json()["data"]["per_principal"]
    costs = [p["cost_usd"] for p in pp]
    assert costs == sorted(costs, reverse=True)
    # bob (0.50) must come first
    assert pp[0]["user_email"] == "bob@example.com"


# ---------------------------------------------------------------------------
# per_principal models list is sorted
# ---------------------------------------------------------------------------

def test_per_principal_models_sorted(monkeypatch):
    """models list inside each per_principal entry must be sorted."""
    rows = [
        _make_row("alice@example.com", "gemini-2.5-pro", 500, 200, 0.50),
        _make_row("alice@example.com", "gemini-2.5-flash", 1000, 500, 0.05),
    ]
    _patch(monkeypatch, rows)

    resp = client.get("/api/statements?month=2025-06")
    pp = resp.json()["data"]["per_principal"]
    alice = next(p for p in pp if p["user_email"] == "alice@example.com")
    assert alice["models"] == sorted(alice["models"])


# ---------------------------------------------------------------------------
# Unpriced models surfaced
# ---------------------------------------------------------------------------

def test_unpriced_models_surfaced(monkeypatch):
    """Models with pricing_match='default' must appear in unpriced_models."""
    rows = [
        _make_row("alice@example.com", "some-unknown-model", 1000, 500, 0.0,
                  pricing_match="default"),
        _make_row("bob@example.com", "gemini-2.5-flash", 500, 200, 0.05,
                  pricing_match="exact"),
    ]
    _patch(monkeypatch, rows)

    resp = client.get("/api/statements?month=2025-06")
    data = resp.json()["data"]
    assert "some-unknown-model" in data["unpriced_models"]
    assert "gemini-2.5-flash" not in data["unpriced_models"]


def test_no_unpriced_models_returns_empty_list(monkeypatch):
    rows = [
        _make_row("alice@example.com", "gemini-2.5-flash", 1000, 500, 0.10,
                  pricing_match="exact"),
    ]
    _patch(monkeypatch, rows)

    resp = client.get("/api/statements?month=2025-06")
    assert resp.json()["data"]["unpriced_models"] == []


# ---------------------------------------------------------------------------
# Pricing assumptions snapshot
# ---------------------------------------------------------------------------

def test_pricing_assumptions_present(monkeypatch):
    """pricing_assumptions must contain rates_as_of_utc, note, and models."""
    _patch(monkeypatch, [])
    resp = client.get("/api/statements?month=2025-06")
    pa = resp.json()["data"]["pricing_assumptions"]
    assert "rates_as_of_utc" in pa
    assert "note" in pa
    assert "models" in pa
    assert isinstance(pa["models"], dict)


def test_pricing_assumptions_models_matches_bq_pricing(monkeypatch):
    """pricing_assumptions.models must be a snapshot of the current PRICING dict."""
    _patch(monkeypatch, [])
    resp = client.get("/api/statements?month=2025-06")
    pa_models = resp.json()["data"]["pricing_assumptions"]["models"]
    # Must not be empty (default pricing always has entries)
    assert len(pa_models) > 0
    # Must have the same keys as bq_client.PRICING
    assert set(pa_models.keys()) == set(backend.bq_client.PRICING.keys())


# ---------------------------------------------------------------------------
# Budget snapshot
# ---------------------------------------------------------------------------

def test_budget_snapshot_present_and_includes_global_default(monkeypatch):
    """budget_snapshot must be a list and include global_default."""
    _patch(monkeypatch, [])
    resp = client.get("/api/statements?month=2025-06")
    bs = resp.json()["data"]["budget_snapshot"]
    assert isinstance(bs, list)
    identities = {entry["identity"] for entry in bs}
    assert "global_default" in identities


def test_budget_snapshot_has_required_keys(monkeypatch):
    """Each budget_snapshot entry must have identity, period, type, limit."""
    _patch(monkeypatch, [])
    resp = client.get("/api/statements?month=2025-06")
    bs = resp.json()["data"]["budget_snapshot"]
    for entry in bs:
        for key in ("identity", "period", "type", "limit"):
            assert key in entry, f"budget_snapshot entry missing key: {key}"


def test_budget_snapshot_includes_custom_rules(monkeypatch):
    """Custom budget rules must also appear in budget_snapshot."""
    budgets = {
        "global_default": {
            "identity": "global_default",
            "period": "month",
            "type": "token",
            "limit": 10_000_000,
            "alert_threshold_percentage": 50.0,
            "hard_limit_enabled": False,
        },
        "alice@example.com": {
            "identity": "alice@example.com",
            "period": "week",
            "type": "money",
            "limit": 50.0,
            "alert_threshold_percentage": 80.0,
            "hard_limit_enabled": False,
        },
    }
    _patch(monkeypatch, [], budgets=budgets)
    resp = client.get("/api/statements?month=2025-06")
    bs = resp.json()["data"]["budget_snapshot"]
    identities = {entry["identity"] for entry in bs}
    assert "alice@example.com" in identities


# ---------------------------------------------------------------------------
# thoughts_tokens carried through
# ---------------------------------------------------------------------------

def test_thoughts_tokens_in_totals(monkeypatch):
    """thoughts_tokens from rows must be summed into totals.thoughts_tokens."""
    rows = [
        _make_row("alice@example.com", "gemini-2.5-pro", 1000, 500, 0.10,
                  thoughts_tokens=200),
        _make_row("bob@example.com", "gemini-2.5-pro", 500, 300, 0.08,
                  thoughts_tokens=50),
    ]
    _patch(monkeypatch, rows)

    resp = client.get("/api/statements?month=2025-06")
    assert resp.json()["data"]["totals"]["thoughts_tokens"] == 250


def test_thoughts_tokens_in_per_principal(monkeypatch):
    """thoughts_tokens must propagate into per_principal entries."""
    rows = [
        _make_row("alice@example.com", "gemini-2.5-pro", 1000, 500, 0.10,
                  thoughts_tokens=123),
    ]
    _patch(monkeypatch, rows)

    resp = client.get("/api/statements?month=2025-06")
    pp = resp.json()["data"]["per_principal"]
    alice = next(p for p in pp if p["user_email"] == "alice@example.com")
    assert alice["thoughts_tokens"] == 123
