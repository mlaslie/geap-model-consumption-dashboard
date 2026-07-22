"""
Tests for get_user_model_totals cost computation and the no-truncation
guarantee for budget-status when totals exceed 1000 rows.

Cost computation is tested by monkeypatching get_user_model_totals_cached
directly at the backend.main layer and driving the /api/budget-status endpoint,
verifying that cost math is correct end-to-end.

A separate test verifies that the budget-status path correctly accumulates all
rows from get_user_model_totals_cached even when 1500 are returned (old code
would have been limited to 1000 via get_token_usage_logs_cached's LIMIT clause).
"""
import pytest
import backend.auth
import backend.main
import backend.bq_client
from backend.bq_client import calculate_estimated_cost, PRICING
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_totals_row(
    user, input_tokens, output_tokens, calls=1, model="gemini-2.5-flash",
    pricing_tier="le200k", region="global"
):
    """Builds a row as get_user_model_totals would return from BQ, with cost
    computed via the real pricing engine so math tests are self-consistent.
    pricing_tier and region are included to match the real function's output
    (the view now exposes both columns)."""
    cost = calculate_estimated_cost(
        model, input_tokens, output_tokens, PRICING,
        tier=pricing_tier, region=region,
    )
    _, match_kind = backend.bq_client.resolve_pricing(model, PRICING)
    return {
        "user_email": user,
        "model_name": model,
        "pricing_tier": pricing_tier,
        "region": region,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "call_count": calls,
        "estimated_cost_usd": cost,
        "pricing_match": match_kind,
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
# Cost computation: money budget percentage uses estimated_cost_usd correctly
# ---------------------------------------------------------------------------

def test_cost_computation_money_budget_percentage(monkeypatch):
    """
    Given a known input/output token split for gemini-2.5-flash,
    the budget-status percentage for a money budget must match the cost
    computed by calculate_estimated_cost.
    """
    input_tok = 1_000_000
    output_tok = 1_000_000
    # expected_cost is computed dynamically from the live pricing engine so the
    # assertion stays valid regardless of the current flash rates in pricing.json.
    expected_cost = calculate_estimated_cost("gemini-2.5-flash", input_tok, output_tok, PRICING)

    rows = [_make_totals_row("cost_user@example.com", input_tok, output_tok, calls=5)]
    budgets = {
        "global_default": _rule("global_default", b_type="money", limit=1.0),
        "cost_user@example.com": _rule(
            "cost_user@example.com", b_type="money", limit=1.0, threshold=10.0
        ),
    }

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]["cost_user@example.com"]

    assert data["consumed"] == pytest.approx(expected_cost, rel=1e-5)
    assert data["percentage"] == pytest.approx(expected_cost / 1.0 * 100, rel=1e-5)


def test_cost_computation_token_budget_percentage(monkeypatch):
    """
    A token budget uses total_tokens (input + output) for the consumed field
    and the percentage must reflect that exactly.
    """
    input_tok = 3_000
    output_tok = 7_000
    total = input_tok + output_tok  # 10_000

    rows = [_make_totals_row("tok_user@example.com", input_tok, output_tok, calls=2)]
    budgets = {
        "global_default": _rule("global_default", limit=1_000_000),
        "tok_user@example.com": _rule(
            "tok_user@example.com", b_type="token", limit=20_000, threshold=10.0
        ),
    }

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]["tok_user@example.com"]

    assert data["consumed"] == total
    assert data["percentage"] == pytest.approx(50.0)  # 10000 / 20000


def test_cost_computation_multi_model_rows_summed_correctly(monkeypatch):
    """
    When a user has two rows (one per model), the budget-status consumed value
    must be the sum of both rows' costs for a money budget.
    """
    row_flash = _make_totals_row("multi@example.com", 500_000, 500_000, model="gemini-2.5-flash")
    row_pro = _make_totals_row("multi@example.com", 100_000, 100_000, model="gemini-2.5-pro")

    expected_cost = row_flash["estimated_cost_usd"] + row_pro["estimated_cost_usd"]

    rows = [row_flash, row_pro]
    budgets = {
        "global_default": _rule("global_default", b_type="money", limit=100.0),
        "multi@example.com": _rule(
            "multi@example.com", b_type="money", limit=100.0, threshold=1.0
        ),
    }

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]["multi@example.com"]

    assert data["consumed"] == pytest.approx(expected_cost, rel=1e-5)


# ---------------------------------------------------------------------------
# No-truncation: budget-status considers all rows even beyond 1000
# ---------------------------------------------------------------------------

def test_budget_status_considers_all_rows_beyond_1000(monkeypatch):
    """
    With 1500 distinct user rows returned by get_user_model_totals_cached,
    ALL users must appear in the budget-status response — no 1000-row cap.
    Each user has tokens > threshold so all should appear with consumed > 0.
    """
    num_users = 1500
    rows = [
        {
            "user_email": f"user{i}@example.com",
            "model_name": "gemini-2.5-flash",
            "pricing_tier": "le200k",
            "region": "global",
            "input_tokens": 500,
            "output_tokens": 500,
            "total_tokens": 1_000,
            "call_count": 1,
            "estimated_cost_usd": 0.0001,
            "pricing_match": "exact",
        }
        for i in range(num_users)
    ]

    budgets = {
        "global_default": _rule("global_default", b_type="token", limit=10_000, threshold=1.0),
    }

    monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
    monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: rows)

    resp = client.get("/api/budget-status")
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert len(data) == num_users, (
        f"Expected {num_users} users in budget-status but got {len(data)} "
        "(likely truncated at 1000 if the old code path is still used)"
    )
    # Spot-check first and last user
    assert "user0@example.com" in data
    assert f"user{num_users - 1}@example.com" in data
    assert data["user0@example.com"]["consumed"] == 1_000
