"""
Tests for POST /api/logging-config.

Covers:
- Invalid key (bad chars) → 400
- Non-bool value → 400
- All models succeed → status 'success' with results
- One model fails → status 'partial_failure' with per-model results
"""
import pytest
import backend.auth
import backend.logging_client
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


@pytest.fixture(autouse=True)
def stub_save(monkeypatch):
    """Prevent logging_config from hitting the filesystem."""
    monkeypatch.setattr(backend.logging_client, "save_logging_config", lambda cfg: True)


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------

def test_invalid_key_with_space_returns_400():
    resp = client.post("/api/logging-config", json={"bad key!": True})
    assert resp.status_code == 400
    assert "bad key!" in resp.json()["detail"]


def test_invalid_key_with_exclamation_returns_400():
    resp = client.post("/api/logging-config", json={"model!": True})
    assert resp.status_code == 400


def test_invalid_key_with_slash_returns_400():
    resp = client.post("/api/logging-config", json={"my/model": True})
    assert resp.status_code == 400


def test_valid_key_passes_validation(monkeypatch):
    monkeypatch.setattr(
        backend.logging_client,
        "apply_vertex_logging",
        lambda cfg: {
            "results": {"gemini-2.5-flash": {"success": True, "error": None}},
            "all_succeeded": True,
        },
    )
    resp = client.post("/api/logging-config", json={"gemini-2.5-flash": True})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Value validation
# ---------------------------------------------------------------------------

def test_string_value_returns_400():
    """String "true" is not a Python bool → 400."""
    resp = client.post("/api/logging-config", json={"gemini-2.5-flash": "true"})
    assert resp.status_code == 400
    assert "boolean" in resp.json()["detail"].lower()


def test_integer_value_returns_400():
    resp = client.post("/api/logging-config", json={"gemini-2.5-flash": 1})
    assert resp.status_code == 400


def test_null_value_returns_400():
    resp = client.post("/api/logging-config", json={"gemini-2.5-flash": None})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# All-success path
# ---------------------------------------------------------------------------

def test_all_success_returns_status_success(monkeypatch):
    results = {
        "gemini-2.5-pro": {"success": True, "error": None},
        "gemini-2.5-flash": {"success": True, "error": None},
    }
    monkeypatch.setattr(
        backend.logging_client,
        "apply_vertex_logging",
        lambda cfg: {"results": results, "all_succeeded": True},
    )
    payload = {"gemini-2.5-pro": True, "gemini-2.5-flash": False}
    resp = client.post("/api/logging-config", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "results" in body


# ---------------------------------------------------------------------------
# Partial failure path
# ---------------------------------------------------------------------------

def test_partial_failure_returns_status_partial_failure(monkeypatch):
    results = {
        "gemini-2.5-pro": {"success": True, "error": None},
        "gemini-2.5-flash": {"success": False, "error": "SDK error"},
    }
    monkeypatch.setattr(
        backend.logging_client,
        "apply_vertex_logging",
        lambda cfg: {"results": results, "all_succeeded": False},
    )
    payload = {"gemini-2.5-pro": True, "gemini-2.5-flash": False}
    resp = client.post("/api/logging-config", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "partial_failure"
    assert "results" in body
    # Per-model results must be included in the response
    assert "gemini-2.5-flash" in body["results"]
    assert body["results"]["gemini-2.5-flash"]["success"] is False


def test_partial_failure_includes_all_per_model_results(monkeypatch):
    results = {
        "model-a": {"success": True, "error": None},
        "model-b": {"success": False, "error": "boom"},
        "model-c": {"success": True, "error": None},
    }
    monkeypatch.setattr(
        backend.logging_client,
        "apply_vertex_logging",
        lambda cfg: {"results": results, "all_succeeded": False},
    )
    payload = {"model-a": True, "model-b": False, "model-c": True}
    resp = client.post("/api/logging-config", json=payload)
    body = resp.json()
    assert set(body["results"].keys()) == {"model-a", "model-b", "model-c"}


# ---------------------------------------------------------------------------
# Empty dict → 400 (added after refactor)
# ---------------------------------------------------------------------------

def test_empty_config_dict_returns_400():
    """POST with an empty dict must be rejected with 400."""
    resp = client.post("/api/logging-config", json={})
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# save_logging_config returning False → 500, apply NOT called
# ---------------------------------------------------------------------------

def test_save_returns_false_gives_500_and_apply_not_called(monkeypatch):
    """
    When save_logging_config returns False the endpoint must 500 and must NOT
    call apply_vertex_logging.
    """
    apply_called = []

    # Override the autouse stub_save fixture's True return with False
    monkeypatch.setattr(backend.logging_client, "save_logging_config", lambda cfg: False)
    monkeypatch.setattr(
        backend.logging_client,
        "apply_vertex_logging",
        lambda cfg: apply_called.append(cfg) or {"results": {}, "all_succeeded": True},
    )

    resp = client.post("/api/logging-config", json={"gemini-2.5-flash": True})
    assert resp.status_code == 500
    assert apply_called == [], "apply_vertex_logging must NOT be called when save fails"
