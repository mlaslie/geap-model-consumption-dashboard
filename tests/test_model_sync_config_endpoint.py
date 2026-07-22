"""
Tests for GET/POST /api/model-sync-config.

Covers:
- GET default returns auto_sync_on_startup=False
- POST true persists and returns saved data
- POST invalid body → 422
- POST save failure → 500
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


# ---------------------------------------------------------------------------
# GET /api/model-sync-config
# ---------------------------------------------------------------------------

def test_get_default_returns_false(monkeypatch):
    monkeypatch.setattr(
        backend.logging_client,
        "load_model_sync_config",
        lambda: {"auto_sync_on_startup": False},
    )
    resp = client.get("/api/model-sync-config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["data"]["auto_sync_on_startup"] is False


def test_get_response_shape(monkeypatch):
    monkeypatch.setattr(
        backend.logging_client,
        "load_model_sync_config",
        lambda: {"auto_sync_on_startup": True},
    )
    resp = client.get("/api/model-sync-config")
    body = resp.json()
    assert "status" in body
    assert "data" in body
    assert "auto_sync_on_startup" in body["data"]


# ---------------------------------------------------------------------------
# POST /api/model-sync-config
# ---------------------------------------------------------------------------

def test_post_true_persists(monkeypatch):
    saved = []

    def _save(cfg):
        saved.append(cfg)
        return True

    monkeypatch.setattr(backend.logging_client, "save_model_sync_config", _save)
    resp = client.post("/api/model-sync-config", json={"auto_sync_on_startup": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["data"]["auto_sync_on_startup"] is True
    assert saved == [{"auto_sync_on_startup": True}]


def test_post_false_persists(monkeypatch):
    saved = []

    def _save(cfg):
        saved.append(cfg)
        return True

    monkeypatch.setattr(backend.logging_client, "save_model_sync_config", _save)
    resp = client.post("/api/model-sync-config", json={"auto_sync_on_startup": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["auto_sync_on_startup"] is False


def test_post_invalid_body_missing_field_returns_422():
    resp = client.post("/api/model-sync-config", json={})
    assert resp.status_code == 422


def test_post_invalid_body_wrong_type_returns_422():
    # Pydantic v2 coerces strings like "yes"/"true" to bool.
    # Use a nested object, which cannot be coerced to bool, to trigger 422.
    resp = client.post("/api/model-sync-config", json={"auto_sync_on_startup": {"nested": "x"}})
    assert resp.status_code == 422


def test_post_save_failure_returns_500(monkeypatch):
    monkeypatch.setattr(backend.logging_client, "save_model_sync_config", lambda cfg: False)
    resp = client.post("/api/model-sync-config", json={"auto_sync_on_startup": True})
    assert resp.status_code == 500
