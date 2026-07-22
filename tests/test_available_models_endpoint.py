"""
Tests for GET /api/available-models.

Covers:
- Success path returns sorted model list
- ModelCatalogError → 503
- TTL cache: second call uses cache (list_available_models not re-invoked)
- ?force=true bypasses TTL cache
"""
import pytest
import backend.auth
import backend.main
from fastapi.testclient import TestClient
from backend.main import app
from backend.model_catalog import ModelCatalogError

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


@pytest.fixture(autouse=True)
def reset_available_models_cache():
    """Clear the module-level model catalog cache before and after each test."""
    backend.main._available_models_cache = None
    yield
    backend.main._available_models_cache = None


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_success_returns_model_list(monkeypatch):
    models = ["gemini-2.5-flash", "gemini-2.5-pro"]
    monkeypatch.setattr(backend.main, "list_available_models", lambda: models)
    resp = client.get("/api/available-models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["data"]["models"] == models


def test_success_response_shape(monkeypatch):
    monkeypatch.setattr(backend.main, "list_available_models", lambda: ["gemini-flash"])
    resp = client.get("/api/available-models")
    body = resp.json()
    assert "status" in body
    assert "data" in body
    assert "models" in body["data"]
    assert isinstance(body["data"]["models"], list)


# ---------------------------------------------------------------------------
# Error path — ModelCatalogError → 503
# ---------------------------------------------------------------------------

def test_model_catalog_error_returns_503(monkeypatch):
    def _raise():
        raise ModelCatalogError("SDK not available")

    monkeypatch.setattr(backend.main, "list_available_models", _raise)
    resp = client.get("/api/available-models")
    assert resp.status_code == 503
    assert "Model catalog unavailable" in resp.json()["detail"]


def test_unexpected_exception_returns_503(monkeypatch):
    def _raise():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(backend.main, "list_available_models", _raise)
    resp = client.get("/api/available-models")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# TTL cache: second call doesn't re-invoke list_available_models
# ---------------------------------------------------------------------------

def test_cache_prevents_second_call(monkeypatch):
    call_count = []

    def _stub():
        call_count.append(1)
        return ["gemini-2.5-flash"]

    monkeypatch.setattr(backend.main, "list_available_models", _stub)

    resp1 = client.get("/api/available-models")
    resp2 = client.get("/api/available-models")

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert len(call_count) == 1, "list_available_models should be called only once due to TTL cache"


def test_force_bypasses_cache(monkeypatch):
    call_count = []

    def _stub():
        call_count.append(1)
        return ["gemini-2.5-flash"]

    monkeypatch.setattr(backend.main, "list_available_models", _stub)

    resp1 = client.get("/api/available-models")
    resp2 = client.get("/api/available-models?force=true")

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert len(call_count) == 2, "force=true should bypass the cache and re-invoke"


def test_force_false_uses_cache(monkeypatch):
    """Explicit force=false behaves the same as omitting the param (uses cache)."""
    call_count = []

    def _stub():
        call_count.append(1)
        return ["gemini-2.5-pro"]

    monkeypatch.setattr(backend.main, "list_available_models", _stub)

    client.get("/api/available-models")
    client.get("/api/available-models?force=false")

    assert len(call_count) == 1
