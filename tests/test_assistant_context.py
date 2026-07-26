"""
Tests for the FinOps assistant's injected context.

Regression guard for: asked "who used the most tokens in the last 24 hours"
with no recent usage, the assistant reported 30-day totals as if they were
recent, because the context carried neither a clock nor window-scoped data.
"""
import json
import re
import pytest

import backend.ai_assistant as ai


def _row(user, model="gemini-3.6-flash", tokens=1000, cost=1.5, calls=2):
    return {
        "user_email": user,
        "model_name": model,
        "input_tokens": tokens // 2,
        "output_tokens": tokens // 2,
        "total_tokens": tokens,
        "call_count": calls,
        "estimated_cost_usd": cost,
        "pricing_tier": "le200k",
        "region": "global",
    }


@pytest.fixture
def captured_prompt(monkeypatch):
    """Runs the assistant with a stubbed SDK and returns the system prompt."""
    captured = {}

    class _FakeModels:
        def generate_content(self, model, contents, config):
            captured["system_prompt"] = config.system_instruction

            class _R:
                text = "ok"
            return _R()

    class _FakeClient:
        def __init__(self, **kwargs):
            self.models = _FakeModels()

    monkeypatch.setattr(ai, "_SDK_AVAILABLE", True)
    monkeypatch.setattr(ai.settings, "BIGQUERY_PROJECT_ID", "test-project")
    monkeypatch.setattr(ai, "genai", type("g", (), {"Client": _FakeClient}))

    class _FakePart:
        @staticmethod
        def from_text(text):
            return text

    monkeypatch.setattr(ai, "types", type("t", (), {
        "Content": lambda role, parts: {"role": role, "parts": parts},
        "Part": _FakePart,
        "GenerateContentConfig": lambda **kw: type("C", (), kw)(),
    }))
    return captured


def test_windows_are_separate_and_empty_24h_is_zero(monkeypatch, captured_prompt):
    """30 days of usage but nothing in 24h → the 24h window must read zero."""
    def fake_totals(days):
        return [_row("alice@example.com", tokens=2_000_000, cost=3.2, calls=44)] if days == 30 else []

    monkeypatch.setattr(ai, "get_user_model_totals_cached", fake_totals)
    monkeypatch.setattr(ai, "load_budgets", lambda: {})

    ai.query_finops_assistant([{"role": "user", "content": "who used most tokens in last 24 hours"}])
    prompt = captured_prompt["system_prompt"]

    payload = json.loads(re.search(r"\{.*\n\}", prompt, re.S).group(0))
    windows = payload["usage_by_window"]

    assert windows["last_24_hours"]["total_tokens"] == 0
    assert windows["last_24_hours"]["per_user"] == {}
    assert windows["last_7_days"]["total_tokens"] == 0
    # The 30-day figures still exist — but under their own labelled window.
    assert windows["last_30_days"]["total_tokens"] == 2_000_000
    assert payload["summary_last_30_days"]["total_tokens_consumed"] == 2_000_000


def test_recent_usage_appears_in_all_containing_windows(monkeypatch, captured_prompt):
    def fake_totals(days):
        # Same call is inside every window.
        return [_row("bob@example.com", tokens=500, cost=0.1, calls=1)]

    monkeypatch.setattr(ai, "get_user_model_totals_cached", fake_totals)
    monkeypatch.setattr(ai, "load_budgets", lambda: {})

    ai.query_finops_assistant([{"role": "user", "content": "usage today?"}])
    payload = json.loads(re.search(r"\{.*\n\}", captured_prompt["system_prompt"], re.S).group(0))

    for label in ("last_24_hours", "last_7_days", "last_30_days"):
        w = payload["usage_by_window"][label]
        assert w["total_tokens"] == 500
        assert w["per_user"]["bob@example.com"]["calls"] == 1


def test_prompt_states_current_time_and_window_rules(monkeypatch, captured_prompt):
    monkeypatch.setattr(ai, "get_user_model_totals_cached", lambda days: [])
    monkeypatch.setattr(ai, "load_budgets", lambda: {})

    ai.query_finops_assistant([{"role": "user", "content": "hi"}])
    prompt = captured_prompt["system_prompt"]

    assert "current date and time is" in prompt
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", prompt)
    assert "NEVER substitute one window's numbers for another" in prompt
    assert "unattributed@unknown" in prompt


def test_each_window_queried_once_even_when_budget_period_matches(monkeypatch, captured_prompt):
    """A month budget (30d) must not trigger a second 30-day fetch."""
    calls = []

    def fake_totals(days):
        calls.append(days)
        return [_row("carol@example.com")]

    monkeypatch.setattr(ai, "get_user_model_totals_cached", fake_totals)
    monkeypatch.setattr(ai, "load_budgets", lambda: {
        "global_default": {
            "identity": "global_default", "period": "month", "type": "token",
            "limit": 1_000_000, "alert_threshold_percentage": 50.0,
        }
    })

    ai.query_finops_assistant([{"role": "user", "content": "status"}])
    assert sorted(calls) == sorted(set(calls)), f"duplicate window fetches: {calls}"
    assert set(calls) == {1, 7, 30}


def test_per_user_budget_block_still_uses_budget_period(monkeypatch, captured_prompt):
    def fake_totals(days):
        # Heavier usage in the 30-day (month budget) window than in 24h.
        return [_row("dave@example.com", tokens=900_000 if days == 30 else 100, cost=1.0)]

    monkeypatch.setattr(ai, "get_user_model_totals_cached", fake_totals)
    monkeypatch.setattr(ai, "load_budgets", lambda: {
        "global_default": {
            "identity": "global_default", "period": "month", "type": "token",
            "limit": 1_000_000, "alert_threshold_percentage": 50.0,
        }
    })

    ai.query_finops_assistant([{"role": "user", "content": "budget status"}])
    payload = json.loads(re.search(r"\{.*\n\}", captured_prompt["system_prompt"], re.S).group(0))

    entry = payload["per_user_consumption_and_budgets"]["dave@example.com"]
    assert entry["tokens_consumed"] == 900_000  # month period, not the 24h window
    assert entry["actual_consumption_percentage_of_budget"] == "90.0%"
