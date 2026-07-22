"""
Tests for GET /healthz (unauthenticated liveness check).

The /healthz route is registered directly on the FastAPI app (not the
api_router), so it must return 200 even when AUTH_TOKEN is configured and
no Authorization header is sent.
"""
import pytest
import backend.auth
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_healthz_returns_200_without_auth_header(monkeypatch):
    """GET /healthz must return 200 even when AUTH_TOKEN is set and no header is sent."""
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "super-secret-token")
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_healthz_response_body_is_status_ok(monkeypatch):
    """GET /healthz must return exactly {"status": "ok"}."""
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "super-secret-token")
    resp = client.get("/healthz")
    assert resp.json() == {"status": "ok"}


def test_healthz_works_when_auth_token_empty():
    """GET /healthz must also work in unauthenticated (dev) mode."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
