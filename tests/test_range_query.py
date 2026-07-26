"""
Tests for get_user_model_totals_range and get_user_model_totals_range_cached
in backend.bq_client.

Covers:
- Parameterized query: literal date strings must NOT appear in the SQL body;
  they must appear only as ScalarQueryParameter values.
- _range_cache is keyed by (start, end) tuples separately from _totals_cache
  (keyed by int days).  Populating one does not affect the other.
- get_user_model_totals_range_cached reads from and writes to _range_cache.
- Cached result is returned on second call without hitting BQ again.
"""
import time
import pytest
from unittest.mock import MagicMock, patch, call

import backend.bq_client as bq_client


# ---------------------------------------------------------------------------
# Helper — fake BQ infrastructure
# ---------------------------------------------------------------------------

class _FakeParam:
    """Minimal ScalarQueryParameter stand-in that stores name and value."""
    def __init__(self, name, type_, value):
        self.name = name
        self.type_ = type_
        self.value = value


class _FakeJobConfig:
    def __init__(self, query_parameters=None):
        self.query_parameters = query_parameters or []


def _make_fake_bigquery(captured: dict):
    """
    Returns a fake bigquery module whose Client.query captures the SQL and
    job_config, then returns an empty result.
    """

    class _FakeJob:
        def result(self):
            return []

    class _FakeClient:
        def __init__(self, project=None):
            pass

        def query(self, sql, job_config=None):
            captured["sql"] = sql
            captured["job_config"] = job_config
            return _FakeJob()

    class _FakeBigquery:
        Client = _FakeClient
        ScalarQueryParameter = _FakeParam
        QueryJobConfig = _FakeJobConfig

    return _FakeBigquery()


# ---------------------------------------------------------------------------
# Test: no literal date interpolation in SQL
# ---------------------------------------------------------------------------

class TestNoDateInterpolation:
    """
    The start_date and end_date strings must only appear in query PARAMETERS,
    never in the SQL text itself.
    """

    def _run(self, monkeypatch, start="2026-01-01", end="2026-02-01"):
        monkeypatch.setattr(bq_client.settings, "BIGQUERY_PROJECT_ID", "proj")
        monkeypatch.setattr(bq_client.settings, "BIGQUERY_DATASET", "ds")
        monkeypatch.setattr(bq_client.settings, "BIGQUERY_VIEW", "vw")

        captured: dict = {}
        fake_bq = _make_fake_bigquery(captured)

        with patch("google.cloud.bigquery.Client", new=fake_bq.Client), \
             patch("google.cloud.bigquery.ScalarQueryParameter", new=fake_bq.ScalarQueryParameter), \
             patch("google.cloud.bigquery.QueryJobConfig", new=fake_bq.QueryJobConfig):
            bq_client.get_user_model_totals_range(start, end)

        return captured

    def test_start_date_not_in_sql(self, monkeypatch):
        captured = self._run(monkeypatch, start="2026-01-01", end="2026-02-01")
        assert "2026-01-01" not in captured["sql"], (
            "Literal start_date '2026-01-01' found in the SQL body — "
            "must be a @param not an interpolation"
        )

    def test_end_date_not_in_sql(self, monkeypatch):
        captured = self._run(monkeypatch, start="2026-01-01", end="2026-02-01")
        assert "2026-02-01" not in captured["sql"], (
            "Literal end_date '2026-02-01' found in the SQL body — "
            "must be a @param not an interpolation"
        )

    def test_sql_contains_param_placeholders(self, monkeypatch):
        """The SQL must reference @start_date and @end_date placeholder names."""
        captured = self._run(monkeypatch)
        assert "@start_date" in captured["sql"]
        assert "@end_date" in captured["sql"]

    def test_params_contain_start_date(self, monkeypatch):
        captured = self._run(monkeypatch, start="2026-01-01", end="2026-02-01")
        jc = captured["job_config"]
        param_names = {p.name for p in jc.query_parameters}
        assert "start_date" in param_names

    def test_params_contain_end_date(self, monkeypatch):
        captured = self._run(monkeypatch, start="2026-01-01", end="2026-02-01")
        jc = captured["job_config"]
        param_names = {p.name for p in jc.query_parameters}
        assert "end_date" in param_names

    def test_param_values_are_date_strings(self, monkeypatch):
        start = "2026-03-01"
        end = "2026-04-01"
        captured = self._run(monkeypatch, start=start, end=end)
        jc = captured["job_config"]
        by_name = {p.name: p.value for p in jc.query_parameters}
        assert by_name["start_date"] == start
        assert by_name["end_date"] == end


# ---------------------------------------------------------------------------
# Test: _range_cache is separate from _totals_cache
# ---------------------------------------------------------------------------

class TestRangeCacheSeparate:

    def test_range_cache_is_distinct_object(self):
        """_range_cache must be a different dict from _totals_cache."""
        assert bq_client._range_cache is not bq_client._totals_cache

    def test_range_cache_uses_tuple_keys(self):
        """_range_cache must be keyed by (start, end) tuples, not int days."""
        key = ("2026-01-01", "2026-02-01")
        bq_client._range_cache[key] = (time.monotonic(), [])
        # The key must NOT appear in _totals_cache
        assert key not in bq_client._totals_cache

    def test_totals_cache_uses_int_keys(self):
        """_totals_cache must be keyed by int days, not tuples."""
        bq_client._totals_cache[7] = (time.monotonic(), [])
        # The int key must NOT appear in _range_cache
        assert 7 not in bq_client._range_cache

    def test_populating_one_does_not_affect_other(self):
        """Setting a range-cache entry must leave _totals_cache unchanged."""
        bq_client._totals_cache.clear()
        bq_client._range_cache.clear()

        bq_client._range_cache[("2026-06-01", "2026-07-01")] = (time.monotonic(), [])
        assert len(bq_client._totals_cache) == 0

    def test_reload_pricing_clears_range_cache(self, monkeypatch, tmp_path):
        """reload_pricing() must clear _range_cache alongside the other caches."""
        # Seed _range_cache with a dummy entry
        bq_client._range_cache[("2026-01-01", "2026-02-01")] = (time.monotonic(), ["dummy"])
        assert len(bq_client._range_cache) > 0

        # Monkeypatch PRICING_PATH to a valid (but empty) pricing file so
        # reload_pricing doesn't fail looking for pricing.json.
        pricing_file = tmp_path / "pricing.json"
        pricing_file.write_text('{"gemini-test": {"input_cost_per_million": 1.0, "output_cost_per_million": 2.0}}')
        monkeypatch.setattr(bq_client, "PRICING_PATH", str(pricing_file))

        bq_client.reload_pricing()
        assert len(bq_client._range_cache) == 0, (
            "reload_pricing() must clear _range_cache"
        )


# ---------------------------------------------------------------------------
# Test: get_user_model_totals_range_cached honours TTL and caches by key
# ---------------------------------------------------------------------------

class TestRangeCachedWrapper:

    def _setup_fake_bq(self, monkeypatch, rows=None):
        """Patch BQ so get_user_model_totals_range returns rows without hitting GCP."""
        if rows is None:
            rows = []
        monkeypatch.setattr(bq_client.settings, "BIGQUERY_PROJECT_ID", "proj")
        monkeypatch.setattr(bq_client.settings, "BIGQUERY_DATASET", "ds")
        monkeypatch.setattr(bq_client.settings, "BIGQUERY_VIEW", "vw")

        call_count = {"n": 0}

        def fake_range(start, end):
            call_count["n"] += 1
            return rows

        monkeypatch.setattr(bq_client, "get_user_model_totals_range", fake_range)
        return call_count

    def test_second_call_returns_cached_result(self, monkeypatch):
        """get_user_model_totals_range must be called only once on a second hit."""
        call_count = self._setup_fake_bq(monkeypatch, rows=[{"sentinel": True}])

        result1 = bq_client.get_user_model_totals_range_cached("2026-01-01", "2026-02-01")
        result2 = bq_client.get_user_model_totals_range_cached("2026-01-01", "2026-02-01")

        assert call_count["n"] == 1, "Expected BQ to be called once; cache miss on second call"
        assert result1 == result2

    def test_different_range_gets_separate_cache_entry(self, monkeypatch):
        """Different (start, end) pairs must result in separate cache entries."""
        call_count = self._setup_fake_bq(monkeypatch, rows=[])

        bq_client.get_user_model_totals_range_cached("2026-01-01", "2026-02-01")
        bq_client.get_user_model_totals_range_cached("2026-02-01", "2026-03-01")

        assert call_count["n"] == 2, (
            "Expected 2 BQ calls for 2 distinct date ranges, got {call_count['n']}"
        )

    def test_range_cache_key_is_tuple_not_days(self, monkeypatch):
        """Cache key in _range_cache must be a (start, end) tuple."""
        self._setup_fake_bq(monkeypatch)

        start = "2026-04-01"
        end = "2026-05-01"
        bq_client._range_cache.clear()

        bq_client.get_user_model_totals_range_cached(start, end)

        assert (start, end) in bq_client._range_cache, (
            "_range_cache should be keyed by (start, end) tuple"
        )
        # The same key must NOT appear in _totals_cache
        assert (start, end) not in bq_client._totals_cache

    def test_expired_cache_calls_bq_again(self, monkeypatch):
        """After TTL expiry, the next call must hit BQ again."""
        call_count = self._setup_fake_bq(monkeypatch)

        start, end = "2026-05-01", "2026-06-01"
        # Pre-seed the cache with an already-expired timestamp
        bq_client._range_cache[(start, end)] = (time.monotonic() - bq_client._CACHE_TTL - 1, [])

        bq_client.get_user_model_totals_range_cached(start, end)
        assert call_count["n"] == 1, "Expected 1 BQ call after cache expiry"
