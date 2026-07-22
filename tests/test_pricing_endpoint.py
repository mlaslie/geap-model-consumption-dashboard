"""
Tests for GET /api/pricing and POST /api/pricing.

Covers:
- GET returns the current in-memory PRICING dict.
- POST rejects an empty dict (400).
- POST rejects a key with invalid characters (400).
- POST rejects a negative rate (422, Pydantic validation).
- POST rejects a missing rate key (422, Pydantic validation).
- POST happy path: writes to (temp) file, updates PRICING in-place, clears both
  TTL caches.

IMPORTANT: tests never write to the real backend/pricing.json.  The happy-path
test monkeypatches bq_client.PRICING_PATH to a temp file so all reads and
writes target that file instead.
"""
import json
import time
import hashlib
import pytest
import backend.auth
import backend.bq_client
from fastapi.testclient import TestClient
from backend.main import app
from pathlib import Path

PRICING_JSON_PATH = Path(__file__).parent.parent / "backend" / "pricing.json"

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


def _pricing_json_checksum() -> str:
    return hashlib.md5(PRICING_JSON_PATH.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# GET /api/pricing
# ---------------------------------------------------------------------------

def test_get_pricing_returns_success():
    resp = client.get("/api/pricing")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert isinstance(body["data"], dict)


def test_get_pricing_contains_expected_models():
    resp = client.get("/api/pricing")
    data = resp.json()["data"]
    # The real pricing.json must include at least these two models
    assert "gemini-2.5-pro" in data
    assert "gemini-2.5-flash" in data


def test_get_pricing_values_have_correct_shape():
    resp = client.get("/api/pricing")
    data = resp.json()["data"]
    for model_id, rates in data.items():
        assert "input_cost_per_million" in rates, f"{model_id} missing input rate"
        assert "output_cost_per_million" in rates, f"{model_id} missing output rate"
        assert isinstance(rates["input_cost_per_million"], float)
        assert isinstance(rates["output_cost_per_million"], float)


# ---------------------------------------------------------------------------
# POST /api/pricing — rejection cases
# ---------------------------------------------------------------------------

def test_post_empty_dict_returns_400():
    resp = client.post("/api/pricing", json={})
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_post_bad_key_with_slash_returns_400():
    payload = {"my/model": {"input_cost_per_million": 1.0, "output_cost_per_million": 5.0}}
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 400
    assert "my/model" in resp.json()["detail"]


def test_post_bad_key_with_space_returns_400():
    payload = {"bad model": {"input_cost_per_million": 1.0, "output_cost_per_million": 5.0}}
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 400


def test_post_bad_key_with_exclamation_returns_400():
    payload = {"model!": {"input_cost_per_million": 1.0, "output_cost_per_million": 5.0}}
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 400


def test_post_negative_input_rate_returns_422():
    payload = {"gemini-2.5-pro": {"input_cost_per_million": -1.0, "output_cost_per_million": 5.0}}
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 422


def test_post_negative_output_rate_returns_422():
    payload = {"gemini-2.5-pro": {"input_cost_per_million": 1.0, "output_cost_per_million": -0.01}}
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 422


def test_post_missing_output_rate_returns_422():
    payload = {"gemini-2.5-pro": {"input_cost_per_million": 1.0}}
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 422


def test_post_missing_input_rate_returns_422():
    payload = {"gemini-2.5-pro": {"output_cost_per_million": 5.0}}
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 422


def test_post_extra_keys_in_value_are_ignored(monkeypatch, tmp_path):
    """Pydantic ignores extra fields; the two required ones are present → 200,
    and the extra field is NOT persisted to the written file."""
    import backend.bq_client
    tmp_pricing = str(tmp_path / "pricing.json")
    monkeypatch.setattr(backend.bq_client, "PRICING_PATH", tmp_pricing)

    payload = {
        "gemini-2.5-pro": {
            "input_cost_per_million": 1.0,
            "output_cost_per_million": 5.0,
            "unexpected_field": "should be ignored",
        }
    }
    try:
        resp = client.post("/api/pricing", json=payload)
        assert resp.status_code == 200
        written = json.loads(Path(tmp_pricing).read_text())
        assert written == {
            "gemini-2.5-pro": {
                "input_cost_per_million": 1.0,
                "output_cost_per_million": 5.0,
            }
        }
    finally:
        backend.bq_client.reload_pricing()  # restore real pricing after monkeypatch undo


def test_post_infinity_rate_rejected_and_nothing_written(monkeypatch, tmp_path):
    """float('inf') >= 0 is True — a bare ge=0 would accept it, poison cost
    math, and break JSON encoding of every later GET. Must 422 with NO write."""
    import backend.bq_client
    tmp_pricing = str(tmp_path / "pricing.json")
    monkeypatch.setattr(backend.bq_client, "PRICING_PATH", tmp_pricing)

    for bad in ("Infinity", "-Infinity", "NaN"):
        payload = {"gemini-2.5-pro": {"input_cost_per_million": bad, "output_cost_per_million": 1.0}}
        resp = client.post("/api/pricing", json=payload)
        assert resp.status_code == 422, f"{bad} should be rejected"
    assert not Path(tmp_pricing).exists(), "rejected payloads must not write the file"


def test_post_rate_above_cap_rejected():
    payload = {"gemini-2.5-pro": {"input_cost_per_million": 100001, "output_cost_per_million": 1.0}}
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/pricing — happy path
# ---------------------------------------------------------------------------

def test_post_happy_path_writes_file_updates_pricing_and_clears_caches(
    monkeypatch, tmp_path
):
    """
    POST with valid pricing:
    - Writes JSON to the monkeypatched PRICING_PATH (not the real file).
    - Updates bq_client.PRICING in-place.
    - Clears both _usage_cache and _totals_cache.
    - Returns {"status":"success","data": <saved dict>}.

    Verifies the real pricing.json is byte-identical before and after.
    """
    checksum_before = _pricing_json_checksum()

    # Save original PRICING state so we can restore it after the test
    original_pricing = dict(backend.bq_client.PRICING)

    # Redirect PRICING_PATH to a temp file in tmp_path
    tmp_pricing = str(tmp_path / "pricing.json")
    monkeypatch.setattr(backend.bq_client, "PRICING_PATH", tmp_pricing)

    # Pre-fill both caches with fake entries to verify they are cleared
    backend.bq_client._usage_cache[None] = (time.monotonic(), [{"fake": "usage"}])
    backend.bq_client._totals_cache[30] = (time.monotonic(), [{"fake": "totals"}])

    new_pricing = {
        "gemini-test-model": {
            "input_cost_per_million": 2.0,
            "output_cost_per_million": 8.0,
        }
    }

    try:
        resp = client.post("/api/pricing", json=new_pricing)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["data"] == new_pricing

        # Verify the temp file was written with the correct content
        written = json.loads(Path(tmp_pricing).read_text())
        assert written == new_pricing

        # Verify PRICING dict was updated in-place
        assert dict(backend.bq_client.PRICING) == new_pricing

        # Verify both caches were cleared
        assert backend.bq_client._usage_cache == {}
        assert backend.bq_client._totals_cache == {}

        # The real pricing.json must be untouched
        assert _pricing_json_checksum() == checksum_before

    finally:
        # Restore the original in-memory PRICING so other tests are not affected
        backend.bq_client.PRICING.clear()
        backend.bq_client.PRICING.update(original_pricing)


def test_post_happy_path_zero_rates_are_accepted(monkeypatch, tmp_path):
    """Zero rates are valid (ge=0 allows 0.0)."""
    original_pricing = dict(backend.bq_client.PRICING)
    tmp_pricing = str(tmp_path / "pricing.json")
    monkeypatch.setattr(backend.bq_client, "PRICING_PATH", tmp_pricing)

    payload = {
        "gemini-free-tier": {
            "input_cost_per_million": 0.0,
            "output_cost_per_million": 0.0,
        }
    }
    try:
        resp = client.post("/api/pricing", json=payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
    finally:
        backend.bq_client.PRICING.clear()
        backend.bq_client.PRICING.update(original_pricing)


def test_post_valid_key_with_dot_and_hyphen(monkeypatch, tmp_path):
    """Keys like 'gemini-2.5-pro' (dots + hyphens) must pass key validation."""
    original_pricing = dict(backend.bq_client.PRICING)
    tmp_pricing = str(tmp_path / "pricing.json")
    monkeypatch.setattr(backend.bq_client, "PRICING_PATH", tmp_pricing)

    payload = {
        "gemini-2.5-pro": {
            "input_cost_per_million": 1.25,
            "output_cost_per_million": 5.0,
        }
    }
    try:
        resp = client.post("/api/pricing", json=payload)
        assert resp.status_code == 200
    finally:
        backend.bq_client.PRICING.clear()
        backend.bq_client.PRICING.update(original_pricing)


# ---------------------------------------------------------------------------
# POST /api/pricing — optional field acceptance and persistence
# ---------------------------------------------------------------------------

def test_post_optional_gt200k_field_persisted(monkeypatch, tmp_path):
    """input_cost_per_million_gt_200k is accepted and written to the file."""
    original_pricing = dict(backend.bq_client.PRICING)
    tmp_pricing = str(tmp_path / "pricing.json")
    monkeypatch.setattr(backend.bq_client, "PRICING_PATH", tmp_pricing)

    payload = {
        "gemini-2.5-pro": {
            "input_cost_per_million": 1.25,
            "input_cost_per_million_gt_200k": 2.50,
            "output_cost_per_million": 10.0,
        }
    }
    try:
        resp = client.post("/api/pricing", json=payload)
        assert resp.status_code == 200
        written = json.loads(Path(tmp_pricing).read_text())
        assert written["gemini-2.5-pro"]["input_cost_per_million_gt_200k"] == 2.50
        # response body also reflects the optional field
        assert resp.json()["data"]["gemini-2.5-pro"]["input_cost_per_million_gt_200k"] == 2.50
    finally:
        backend.bq_client.PRICING.clear()
        backend.bq_client.PRICING.update(original_pricing)


def test_post_optional_non_global_multiplier_persisted(monkeypatch, tmp_path):
    """non_global_multiplier is accepted and written to the file."""
    original_pricing = dict(backend.bq_client.PRICING)
    tmp_pricing = str(tmp_path / "pricing.json")
    monkeypatch.setattr(backend.bq_client, "PRICING_PATH", tmp_pricing)

    payload = {
        "gemini-3.5-flash": {
            "input_cost_per_million": 1.50,
            "output_cost_per_million": 9.0,
            "non_global_multiplier": 1.1,
        }
    }
    try:
        resp = client.post("/api/pricing", json=payload)
        assert resp.status_code == 200
        written = json.loads(Path(tmp_pricing).read_text())
        assert written["gemini-3.5-flash"]["non_global_multiplier"] == pytest.approx(1.1)
    finally:
        backend.bq_client.PRICING.clear()
        backend.bq_client.PRICING.update(original_pricing)


def test_post_non_global_multiplier_below_1_rejected():
    """non_global_multiplier < 1.0 must be rejected with 422."""
    payload = {
        "gemini-3.5-flash": {
            "input_cost_per_million": 1.50,
            "output_cost_per_million": 9.0,
            "non_global_multiplier": 0.5,
        }
    }
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 422


def test_post_non_global_multiplier_above_3_rejected():
    """non_global_multiplier > 3.0 must be rejected with 422."""
    payload = {
        "gemini-3.5-flash": {
            "input_cost_per_million": 1.50,
            "output_cost_per_million": 9.0,
            "non_global_multiplier": 3.5,
        }
    }
    resp = client.post("/api/pricing", json=payload)
    assert resp.status_code == 422


def test_post_exclude_none_no_null_keys_in_file(monkeypatch, tmp_path):
    """When optional fields are not supplied, the saved file must not contain
    null keys — exclude_none=True in model_dump ensures this."""
    original_pricing = dict(backend.bq_client.PRICING)
    tmp_pricing = str(tmp_path / "pricing.json")
    monkeypatch.setattr(backend.bq_client, "PRICING_PATH", tmp_pricing)

    # Send only required fields — optional fields should be absent, not null
    payload = {
        "gemini-2.5-flash": {
            "input_cost_per_million": 0.30,
            "output_cost_per_million": 2.50,
        }
    }
    try:
        resp = client.post("/api/pricing", json=payload)
        assert resp.status_code == 200
        written = json.loads(Path(tmp_pricing).read_text())
        entry = written["gemini-2.5-flash"]
        assert "input_cost_per_million_gt_200k" not in entry, (
            "exclude_none=True must omit absent optional fields, not write null"
        )
        assert "non_global_multiplier" not in entry, (
            "exclude_none=True must omit absent optional fields, not write null"
        )
    finally:
        backend.bq_client.PRICING.clear()
        backend.bq_client.PRICING.update(original_pricing)
