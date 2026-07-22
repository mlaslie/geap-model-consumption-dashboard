"""
Tests for GET /api/usage — specifically the "truncated" bool added after the
refactor.  The flag is True when exactly 1 000 rows are returned (hitting the
LIMIT cap) and False for smaller result sets.
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


def _make_log(user="test@example.com", tokens=100, cost=0.001):
    return {
        "call_timestamp": "2026-01-01T00:00:00",
        "project_id": "test-project",
        "user_email": user,
        "model_name": "gemini-2.5-flash",
        "input_tokens": tokens // 2,
        "output_tokens": tokens // 2,
        "total_tokens": tokens,
        "estimated_cost_usd": cost,
    }


# ---------------------------------------------------------------------------
# truncated=True when exactly 1 000 rows come back
# ---------------------------------------------------------------------------

def test_truncated_true_when_row_cap_hit(monkeypatch):
    """1 000-row stub (the BQ LIMIT) must set truncated=True in the response."""
    import backend.bq_client
    logs = [_make_log() for _ in range(backend.bq_client.USAGE_ROW_LIMIT)]
    monkeypatch.setattr(backend.main, "get_token_usage_logs_cached", lambda days=None: logs)

    resp = client.get("/api/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert "truncated" in body
    assert body["truncated"] is True


# ---------------------------------------------------------------------------
# truncated=False for small result sets
# ---------------------------------------------------------------------------

def test_truncated_false_when_below_row_cap(monkeypatch):
    """A small result set must set truncated=False."""
    logs = [_make_log() for _ in range(5)]
    monkeypatch.setattr(backend.main, "get_token_usage_logs_cached", lambda days=None: logs)

    resp = client.get("/api/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert "truncated" in body
    assert body["truncated"] is False


def test_truncated_false_when_zero_rows(monkeypatch):
    """An empty result must also produce truncated=False."""
    monkeypatch.setattr(backend.main, "get_token_usage_logs_cached", lambda days=None: [])

    resp = client.get("/api/usage")
    assert resp.status_code == 200
    assert resp.json()["truncated"] is False


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

def test_usage_response_contains_expected_keys(monkeypatch):
    """The /api/usage response must include status, data, project_id, truncated."""
    monkeypatch.setattr(backend.main, "get_token_usage_logs_cached", lambda days=None: [])

    resp = client.get("/api/usage")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("status", "data", "project_id", "truncated"):
        assert key in body, f"Missing key: {key}"
