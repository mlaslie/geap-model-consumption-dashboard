"""
Tests for POST /api/chat.

Validates:
- >40 messages → 422
- >8 000-char content → 422
- AssistantUnavailableError → 503
- Success path returns {status, reply}
"""
import pytest
import backend.auth
import backend.main
from fastapi.testclient import TestClient
from backend.main import app
from backend.ai_assistant import AssistantUnavailableError

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


def _msg(role="user", content="hello"):
    return {"role": role, "content": content}


# ---------------------------------------------------------------------------
# Pydantic / FastAPI validation (422)
# ---------------------------------------------------------------------------

def test_too_many_messages_rejected():
    """41 messages should fail with 422 (max_length=40 on messages list)."""
    payload = {"messages": [_msg() for _ in range(41)]}
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 422


def test_exactly_40_messages_ok(monkeypatch):
    monkeypatch.setattr(backend.main, "query_finops_assistant", lambda msgs: "ok")
    payload = {"messages": [_msg() for _ in range(40)]}
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 200


def test_content_over_8000_chars_rejected():
    """A single message with content > 8 000 chars should be rejected with 422."""
    long_content = "x" * 8001
    payload = {"messages": [{"role": "user", "content": long_content}]}
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 422


def test_content_exactly_8000_chars_ok(monkeypatch):
    monkeypatch.setattr(backend.main, "query_finops_assistant", lambda msgs: "ok")
    content = "y" * 8000
    payload = {"messages": [{"role": "user", "content": content}]}
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AssistantUnavailableError → 503
# ---------------------------------------------------------------------------

def test_assistant_unavailable_returns_503(monkeypatch):
    def raise_unavailable(msgs):
        raise AssistantUnavailableError("SDK not available")

    monkeypatch.setattr(backend.main, "query_finops_assistant", raise_unavailable)
    payload = {"messages": [_msg()]}
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_success_returns_status_and_reply(monkeypatch):
    monkeypatch.setattr(
        backend.main, "query_finops_assistant", lambda msgs: "The cost is $1.23"
    )
    payload = {"messages": [_msg(content="What is my cost?")]}
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "reply" in body
    assert body["reply"] == "The cost is $1.23"


def test_success_with_multi_turn_conversation(monkeypatch):
    monkeypatch.setattr(backend.main, "query_finops_assistant", lambda msgs: "reply")
    payload = {
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "model", "content": "Hi there"},
            {"role": "user", "content": "What is my budget?"},
        ]
    }
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


# ---------------------------------------------------------------------------
# Per-process sliding-window rate limit
# ---------------------------------------------------------------------------

def test_rate_limit_11th_call_returns_429(monkeypatch):
    """Pre-filling the deque with 10 recent timestamps makes the next call hit 429."""
    import time
    now = time.monotonic()
    # Fill with 10 timestamps all within the 60-second window
    backend.main._chat_request_times.extend([now - 1.0] * 10)

    monkeypatch.setattr(backend.main, "query_finops_assistant", lambda msgs: "ok")
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 429
    assert "rate limit" in resp.json()["detail"].lower()


def test_rate_limit_resets_after_window(monkeypatch):
    """Timestamps older than 60 s are pruned; a single call after they expire must succeed."""
    import time
    # Fill deque with 10 timestamps older than the 60-second window
    old_time = time.monotonic() - 61.0
    backend.main._chat_request_times.extend([old_time] * 10)

    monkeypatch.setattr(backend.main, "query_finops_assistant", lambda msgs: "ok")
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 200
