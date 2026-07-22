"""
Tests for POST /api/budgets validation using FastAPI TestClient.

Auth is disabled in all tests by setting AUTH_TOKEN to empty so the tests can
focus on budget business-logic validation.
"""
import pytest
import backend.auth
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)

# A valid budget rule payload used as the baseline
VALID_RULE = {
    "identity": "global_default",
    "period": "month",
    "type": "token",
    "limit": 10_000_000,
    "alert_threshold_percentage": 50.0,
    "hard_limit_enabled": False,
}


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    """Disable authentication for all tests in this module."""
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


@pytest.fixture()
def stub_save_budgets(monkeypatch):
    """Stub save_budgets in main to avoid real filesystem/GCS writes."""
    import backend.main
    monkeypatch.setattr(backend.main, "save_budgets", lambda data: True)


# ---------------------------------------------------------------------------
# Rejection tests
# ---------------------------------------------------------------------------

def test_empty_dict_rejected():
    resp = client.post("/api/budgets", json={})
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_missing_global_default_rejected():
    payload = {
        "user@example.com": {
            "identity": "user@example.com",
            "period": "month",
            "type": "token",
            "limit": 1_000_000,
            "alert_threshold_percentage": 80.0,
            "hard_limit_enabled": False,
        }
    }
    resp = client.post("/api/budgets", json=payload)
    assert resp.status_code == 400
    assert "global_default" in resp.json()["detail"]


def test_key_identity_mismatch_rejected():
    payload = {
        "global_default": {
            "identity": "WRONG_IDENTITY",
            "period": "month",
            "type": "token",
            "limit": 1_000_000,
            "alert_threshold_percentage": 50.0,
            "hard_limit_enabled": False,
        }
    }
    resp = client.post("/api/budgets", json=payload)
    assert resp.status_code == 400
    assert "identity" in resp.json()["detail"]


def test_bad_period_rejected():
    rule = dict(VALID_RULE, period="fortnight")
    resp = client.post("/api/budgets", json={"global_default": rule})
    assert resp.status_code == 422


def test_bad_type_rejected():
    rule = dict(VALID_RULE, type="credits")
    resp = client.post("/api/budgets", json={"global_default": rule})
    assert resp.status_code == 422


def test_zero_limit_rejected():
    rule = dict(VALID_RULE, limit=0)
    resp = client.post("/api/budgets", json={"global_default": rule})
    assert resp.status_code == 422


def test_negative_limit_rejected():
    rule = dict(VALID_RULE, limit=-100)
    resp = client.post("/api/budgets", json={"global_default": rule})
    assert resp.status_code == 422


def test_threshold_below_1_rejected():
    rule = dict(VALID_RULE, alert_threshold_percentage=0.5)
    resp = client.post("/api/budgets", json={"global_default": rule})
    assert resp.status_code == 422


def test_threshold_above_100_rejected():
    rule = dict(VALID_RULE, alert_threshold_percentage=101.0)
    resp = client.post("/api/budgets", json={"global_default": rule})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Acceptance test
# ---------------------------------------------------------------------------

def test_valid_payload_accepted(stub_save_budgets):
    payload = {"global_default": VALID_RULE}
    resp = client.post("/api/budgets", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"


def test_valid_payload_with_extra_user_accepted(stub_save_budgets):
    payload = {
        "global_default": VALID_RULE,
        "alice@example.com": {
            "identity": "alice@example.com",
            "period": "week",
            "type": "money",
            "limit": 50.0,
            "alert_threshold_percentage": 75.0,
            "hard_limit_enabled": True,
        },
    }
    resp = client.post("/api/budgets", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
