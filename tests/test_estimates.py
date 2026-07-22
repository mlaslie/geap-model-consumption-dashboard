"""
Tests for GET /api/estimates, POST /api/estimates, and DELETE /api/estimates/{name}.

load_estimates and save_estimates are monkeypatched on backend.main to an
in-memory dict store so tests never touch the filesystem or GCS.
"""
import pytest
import backend.auth
import backend.main
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


@pytest.fixture
def in_memory_store(monkeypatch):
    """
    Replaces backend.main.load_estimates / save_estimates with in-memory stubs.
    Returns the underlying dict so tests can inspect it directly.
    """
    store: dict = {}

    def fake_load():
        return dict(store)

    def fake_save(data):
        store.clear()
        store.update(data)
        return True

    monkeypatch.setattr(backend.main, "load_estimates", fake_load)
    monkeypatch.setattr(backend.main, "save_estimates", fake_save)
    return store


# ---------------------------------------------------------------------------
# GET /api/estimates
# ---------------------------------------------------------------------------

def test_get_estimates_empty(in_memory_store):
    resp = client.get("/api/estimates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["data"] == {}


def test_get_estimates_returns_stored_entries(in_memory_store):
    in_memory_store["plan-a"] = {
        "name": "plan-a",
        "items": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    resp = client.get("/api/estimates")
    assert resp.status_code == 200
    assert "plan-a" in resp.json()["data"]


# ---------------------------------------------------------------------------
# POST /api/estimates — happy path
# ---------------------------------------------------------------------------

VALID_ITEM = {
    "model": "gemini-2.5-pro",
    "input_tokens": 1000000,
    "output_tokens": 500000,
    "term": "month",
    "note": "Monthly AI spend",
}

VALID_PAYLOAD = {
    "name": "q1-budget",
    "items": [VALID_ITEM],
}


def test_post_estimate_happy_path(in_memory_store):
    resp = client.post("/api/estimates", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    stored = body["data"]
    assert stored["name"] == "q1-budget"
    assert len(stored["items"]) == 1
    assert "updated_at" in stored


def test_post_estimate_updated_at_is_set(in_memory_store):
    resp = client.post("/api/estimates", json=VALID_PAYLOAD)
    stored = resp.json()["data"]
    # updated_at must be an ISO 8601 UTC timestamp
    assert stored["updated_at"].endswith("+00:00") or stored["updated_at"].endswith("Z")


def test_post_estimate_stored_under_name_key(in_memory_store):
    client.post("/api/estimates", json=VALID_PAYLOAD)
    assert "q1-budget" in in_memory_store
    assert in_memory_store["q1-budget"]["name"] == "q1-budget"


def test_post_estimate_upserts_existing(in_memory_store):
    client.post("/api/estimates", json=VALID_PAYLOAD)
    updated_payload = {
        "name": "q1-budget",
        "items": [
            {**VALID_ITEM, "input_tokens": 2000000},
        ],
    }
    resp = client.post("/api/estimates", json=updated_payload)
    assert resp.status_code == 200
    assert in_memory_store["q1-budget"]["items"][0]["input_tokens"] == 2000000
    assert len(in_memory_store) == 1  # still just one entry


def test_post_estimate_item_note_is_optional(in_memory_store):
    payload = {
        "name": "note-test",
        "items": [
            {
                "model": "gemini-2.5-flash",
                "input_tokens": 0,
                "output_tokens": 0,
                "term": "day",
            }
        ],
    }
    resp = client.post("/api/estimates", json=payload)
    assert resp.status_code == 200
    item = resp.json()["data"]["items"][0]
    assert item["note"] == ""


def test_post_estimate_name_strips_whitespace(in_memory_store):
    payload = {"name": "  padded-name  ", "items": [VALID_ITEM]}
    resp = client.post("/api/estimates", json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "padded-name"
    assert "padded-name" in in_memory_store


# ---------------------------------------------------------------------------
# POST /api/estimates — validation failures
# ---------------------------------------------------------------------------

def test_post_empty_name_returns_422(in_memory_store):
    payload = {"name": "", "items": [VALID_ITEM]}
    resp = client.post("/api/estimates", json=payload)
    assert resp.status_code == 422


def test_post_whitespace_only_name_returns_422(in_memory_store):
    payload = {"name": "   ", "items": [VALID_ITEM]}
    resp = client.post("/api/estimates", json=payload)
    assert resp.status_code == 422


def test_post_zero_items_returns_422(in_memory_store):
    payload = {"name": "empty", "items": []}
    resp = client.post("/api/estimates", json=payload)
    assert resp.status_code == 422


def test_post_too_many_items_returns_422(in_memory_store):
    items = [VALID_ITEM] * 51  # max is 50
    payload = {"name": "overloaded", "items": items}
    resp = client.post("/api/estimates", json=payload)
    assert resp.status_code == 422


def test_post_negative_input_tokens_returns_422(in_memory_store):
    bad_item = {**VALID_ITEM, "input_tokens": -1}
    resp = client.post("/api/estimates", json={"name": "bad", "items": [bad_item]})
    assert resp.status_code == 422


def test_post_negative_output_tokens_returns_422(in_memory_store):
    bad_item = {**VALID_ITEM, "output_tokens": -100}
    resp = client.post("/api/estimates", json={"name": "bad", "items": [bad_item]})
    assert resp.status_code == 422


def test_post_invalid_term_returns_422(in_memory_store):
    bad_item = {**VALID_ITEM, "term": "quarter"}
    resp = client.post("/api/estimates", json={"name": "bad", "items": [bad_item]})
    assert resp.status_code == 422


def test_post_note_too_long_returns_422(in_memory_store):
    bad_item = {**VALID_ITEM, "note": "x" * 501}
    resp = client.post("/api/estimates", json={"name": "bad", "items": [bad_item]})
    assert resp.status_code == 422


def test_post_note_at_max_length_accepted(in_memory_store):
    item = {**VALID_ITEM, "note": "a" * 500}
    resp = client.post("/api/estimates", json={"name": "long-note", "items": [item]})
    assert resp.status_code == 200


def test_post_name_too_long_returns_422(in_memory_store):
    payload = {"name": "n" * 101, "items": [VALID_ITEM]}
    resp = client.post("/api/estimates", json=payload)
    assert resp.status_code == 422


def test_post_model_empty_returns_422(in_memory_store):
    bad_item = {**VALID_ITEM, "model": ""}
    resp = client.post("/api/estimates", json={"name": "bad", "items": [bad_item]})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/estimates/{name}
# ---------------------------------------------------------------------------

def test_delete_existing_estimate(in_memory_store):
    client.post("/api/estimates", json=VALID_PAYLOAD)
    assert "q1-budget" in in_memory_store

    resp = client.delete("/api/estimates/q1-budget")
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}
    assert "q1-budget" not in in_memory_store


def test_delete_missing_estimate_returns_404(in_memory_store):
    resp = client.delete("/api/estimates/nonexistent-plan")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_delete_url_encoded_name(in_memory_store):
    """Name with spaces must survive URL encoding/decoding."""
    payload = {
        "name": "my plan",
        "items": [VALID_ITEM],
    }
    client.post("/api/estimates", json=payload)
    assert "my plan" in in_memory_store

    # TestClient handles URL encoding automatically when using the path directly
    resp = client.delete("/api/estimates/my plan")
    assert resp.status_code == 200
    assert "my plan" not in in_memory_store


def test_delete_only_removes_target(in_memory_store):
    """Deleting one estimate must not affect others."""
    client.post("/api/estimates", json={**VALID_PAYLOAD, "name": "plan-a"})
    client.post("/api/estimates", json={**VALID_PAYLOAD, "name": "plan-b"})

    resp = client.delete("/api/estimates/plan-a")
    assert resp.status_code == 200
    assert "plan-a" not in in_memory_store
    assert "plan-b" in in_memory_store
