"""
Shared pytest fixtures.

The TTL cache moved from backend.main to backend.bq_client (_usage_cache).
Clear it before and after every test so tests are isolated.

_totals_cache is the parallel cache for get_user_model_totals_cached; it
must be cleared too so tests are isolated from each other.

The per-process sliding-window rate limiter for /api/chat lives in
backend.main._chat_request_times (a collections.deque).  Clear it too so
tests don't bleed call-counts into each other and so the rate-limit test can
pre-fill it with known timestamps.
"""
import pytest
import backend.bq_client
import backend.main


@pytest.fixture(autouse=True)
def clear_usage_cache():
    """Clear all TTL caches in backend.bq_client before and after every test."""
    backend.bq_client._usage_cache.clear()
    backend.bq_client._totals_cache.clear()
    backend.bq_client._range_cache.clear()
    yield
    backend.bq_client._usage_cache.clear()
    backend.bq_client._totals_cache.clear()
    backend.bq_client._range_cache.clear()


@pytest.fixture(autouse=True)
def reset_chat_rate_limiter():
    """Clear the chat rate-limiter deque before and after every test."""
    backend.main._chat_request_times.clear()
    yield
    backend.main._chat_request_times.clear()
