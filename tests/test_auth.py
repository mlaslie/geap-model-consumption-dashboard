"""
Tests for the require_auth dependency (backend.auth).

Uses FastAPI TestClient against the real app.  The settings object is the
same singleton used by backend.auth at call time, so patching
backend.auth.settings.AUTH_TOKEN is sufficient.
"""
import pytest
import backend.auth
import backend.main
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def set_auth_token(monkeypatch):
    """Set AUTH_TOKEN to a known value and provide it to tests."""
    token = "super-secret-test-token"
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", token)
    return token


@pytest.fixture()
def clear_auth_token(monkeypatch):
    """Clear AUTH_TOKEN so the app runs in unauthenticated dev mode."""
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


@pytest.fixture()
def stub_usage(monkeypatch):
    """Stub BQ fetch so the usage endpoint returns without real network calls."""
    monkeypatch.setattr(backend.main, "get_token_usage_logs_cached", lambda days=None: [])


# ---------------------------------------------------------------------------
# AUTH_TOKEN set — authentication required
# ---------------------------------------------------------------------------

def test_no_auth_header_returns_401(set_auth_token, stub_usage):
    resp = client.get("/api/usage")
    assert resp.status_code == 401


def test_no_auth_header_includes_www_authenticate(set_auth_token, stub_usage):
    resp = client.get("/api/usage")
    assert "www-authenticate" in {k.lower() for k in resp.headers}


def test_wrong_token_returns_401(set_auth_token, stub_usage):
    resp = client.get("/api/usage", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_malformed_scheme_returns_401(set_auth_token, stub_usage):
    """Sending 'Token xyz' instead of 'Bearer xyz' must be rejected."""
    resp = client.get(
        "/api/usage",
        headers={"Authorization": f"Token {set_auth_token}"},
    )
    assert resp.status_code == 401


def test_correct_bearer_token_passes_auth(set_auth_token, stub_usage):
    resp = client.get(
        "/api/usage",
        headers={"Authorization": f"Bearer {set_auth_token}"},
    )
    assert resp.status_code == 200


def test_correct_token_response_has_data_key(set_auth_token, stub_usage):
    resp = client.get(
        "/api/usage",
        headers={"Authorization": f"Bearer {set_auth_token}"},
    )
    assert resp.status_code == 200
    assert "data" in resp.json()


# ---------------------------------------------------------------------------
# AUTH_TOKEN empty — unauthenticated dev mode, no auth required
# ---------------------------------------------------------------------------

def test_no_auth_required_when_token_empty(clear_auth_token, stub_usage):
    """When AUTH_TOKEN is empty, requests without Authorization should succeed."""
    resp = client.get("/api/usage")
    assert resp.status_code == 200
