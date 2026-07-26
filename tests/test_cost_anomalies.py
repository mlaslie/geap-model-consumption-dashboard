"""
Tests for _compute_cost_anomalies (unit) and GET /api/cost-anomalies (endpoint).

Monkeypatches backend.main.get_user_model_totals_cached so no BQ traffic occurs.
The autouse clear_usage_cache fixture in conftest.py keeps both caches clean.
"""
import pytest
import backend.main
import backend.auth
from fastapi.testclient import TestClient
from backend.main import app, _compute_cost_anomalies

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


# ---------------------------------------------------------------------------
# Row builder helpers
# ---------------------------------------------------------------------------

def _row(user, cost, model="gemini-2.5-flash", pricing_match="exact"):
    return {
        "user_email": user,
        "model_name": model,
        "input_tokens": 1000,
        "output_tokens": 500,
        "total_tokens": 1500,
        "call_count": 1,
        "estimated_cost_usd": cost,
        "pricing_match": pricing_match,
    }


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

class TestComputeCostAnomalies:

    def test_spike_detected(self):
        """Today is 10× the baseline daily average → anomaly flagged."""
        # baseline_days=7; today = $1.00; baseline window cost = $1 (today) + $0.70 (7 days × $0.10)
        today_rows = [_row("alice@example.com", 1.00)]
        baseline_rows = [
            _row("alice@example.com", 1.70),  # total window: $1 today + $0.70 baseline
        ]
        result = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days=7)
        # baseline_daily_avg = 0.70 / 7 = 0.10; ratio = 1.00 / 0.10 = 10.0
        assert result["is_anomaly"] is True
        assert result["ratio"] == 10.0
        assert result["today_cost_usd"] == 1.00
        assert result["baseline_daily_avg_usd"] == round(0.70 / 7, 4)

    def test_normal_day_not_flagged(self):
        """Today is ~1× the baseline daily average → no anomaly."""
        today_rows = [_row("bob@example.com", 0.10)]
        # baseline window: $0.10 today + 7 * $0.10 baseline = $0.80
        baseline_rows = [_row("bob@example.com", 0.80)]
        result = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days=7)
        # ratio = 0.10 / 0.10 = 1.0
        assert result["is_anomaly"] is False
        assert result["ratio"] == 1.0

    def test_zero_baseline_material_spend_is_flagged_as_new_spend(self):
        """No baseline history + material spend today = the case anomaly
        detection exists to catch (new principal / new workload). No ratio can
        be computed, but it must still be flagged, with reason 'new_spend'."""
        today_rows = [_row("carol@example.com", 5.00)]
        # baseline window contains only today's cost → baseline_total = 0
        baseline_rows = [_row("carol@example.com", 5.00)]
        result = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days=7)
        assert result["ratio"] is None            # undefined: nothing to divide by
        assert result["is_anomaly"] is True
        assert result["reason"] == "new_spend"
        carol = next(u for u in result["per_user"] if u["user_email"] == "carol@example.com")
        assert carol["is_anomaly"] is True
        assert carol["reason"] == "new_spend"

    def test_zero_baseline_trivial_spend_not_flagged(self):
        """New spend below the new-spend floor stays quiet — with no trend to
        corroborate it, a few cents is not worth alerting on."""
        today_rows = [_row("dana@example.com", 0.05)]
        baseline_rows = [_row("dana@example.com", 0.05)]
        result = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days=7)
        assert result["is_anomaly"] is False
        assert result["reason"] is None

    def test_spike_reason_is_spike_not_new_spend(self):
        """A ratio-based detection reports reason 'spike'."""
        today_rows = [_row("erin@example.com", 10.00)]
        baseline_rows = [_row("erin@example.com", 10.00 + 7.00)]  # $1/day baseline
        result = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days=7)
        assert result["is_anomaly"] is True
        assert result["reason"] == "spike"

    def test_new_principal_flagged_even_when_fleet_looks_normal(self):
        """A brand-new principal spending materially is flagged per-user even
        when the fleet total is unremarkable — the case that motivated this."""
        today_rows = [_row("steady@example.com", 10.00), _row("brandnew@example.com", 3.00)]
        # steady has a long history; brandnew has none
        baseline_rows = [_row("steady@example.com", 10.00 + 70.00), _row("brandnew@example.com", 3.00)]
        result = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days=7)
        assert result["is_anomaly"] is False  # fleet ratio is ~1.2x, below threshold
        by_user = {u["user_email"]: u for u in result["per_user"]}
        assert by_user["steady@example.com"]["is_anomaly"] is False
        assert by_user["brandnew@example.com"]["is_anomaly"] is True
        assert by_user["brandnew@example.com"]["reason"] == "new_spend"

    def test_tiny_cost_below_floor_not_flagged(self):
        """
        Even a huge ratio (>3×) is NOT flagged when today_cost < $0.01.
        This prevents noise from trivially small amounts that are technically
        anomalous but operationally irrelevant.
        """
        # today = $0.005 (5 milli-dollars), baseline_daily_avg = $0.001 → ratio = 5.0
        today_rows = [_row("dave@example.com", 0.005)]
        # baseline window total: $0.005 (today) + 7 * $0.001 = $0.012
        baseline_rows = [_row("dave@example.com", 0.012)]
        result = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days=7)
        # ratio = 0.005 / 0.001 = 5.0 >= 3 but today_cost < 0.01
        assert result["ratio"] == 5.0
        assert result["is_anomaly"] is False

    def test_per_user_isolates_spiking_user(self):
        """
        alice spikes (10×), bob is normal (~1×).  Only alice should be flagged.
        """
        # Today rows: alice=$1.00, bob=$0.10
        today_rows = [
            _row("alice@example.com", 1.00),
            _row("bob@example.com", 0.10),
        ]
        # Baseline window rows (both present, alice's past is small, bob is steady)
        # alice: total window = $1 (today) + $0.70 (7 days × $0.10 baseline)
        # bob:   total window = $0.10 (today) + $0.70 (7 days × $0.10 baseline)
        baseline_rows = [
            _row("alice@example.com", 1.70),
            _row("bob@example.com", 0.80),
        ]
        result = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days=7)
        per_user = {u["user_email"]: u for u in result["per_user"]}
        assert per_user["alice@example.com"]["is_anomaly"] is True
        assert per_user["bob@example.com"]["is_anomaly"] is False

    def test_baseline_excludes_today_arithmetic(self):
        """
        Explicit arithmetic check: today=$2, window_total=$9, baseline_days=7.
        baseline_total = 9 - 2 = 7; baseline_daily_avg = 7/7 = 1.0; ratio = 2/1 = 2.0.
        2.0 < 3.0 (threshold) → not anomaly.
        """
        today_rows = [_row("eve@example.com", 2.00)]
        baseline_rows = [_row("eve@example.com", 9.00)]  # window total = $9
        result = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days=7)
        assert result["baseline_daily_avg_usd"] == 1.0
        assert result["ratio"] == 2.0
        assert result["is_anomaly"] is False  # 2.0 < 3.0


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

class TestCostAnomaliesEndpoint:

    def _fake_totals(self, today_cost=5.00, window_cost=8.50):
        """Return a closure that yields different row sets for days=1 vs days=8."""
        def _fetch(days):
            if days == 1:
                return [_row("frank@example.com", today_cost)]
            else:
                return [_row("frank@example.com", window_cost)]
        return _fetch

    def test_happy_path_shape(self, monkeypatch):
        """Happy path: response has required keys and correct status."""
        monkeypatch.setattr(backend.main, "get_user_model_totals_cached",
                            self._fake_totals(today_cost=5.00, window_cost=8.50))
        resp = client.get("/api/cost-anomalies?baseline_days=7")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        data = body["data"]
        required = {
            "generated_at_utc", "baseline_days", "threshold_ratio",
            "today_cost_usd", "baseline_daily_avg_usd", "ratio",
            "is_anomaly", "per_user",
        }
        assert required.issubset(data.keys())
        assert data["baseline_days"] == 7
        assert data["threshold_ratio"] == 3.0
        assert isinstance(data["per_user"], list)

    def test_baseline_days_too_low_returns_400(self, monkeypatch):
        monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda d: [])
        resp = client.get("/api/cost-anomalies?baseline_days=1")
        assert resp.status_code == 400

    def test_baseline_days_too_high_returns_400(self, monkeypatch):
        monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda d: [])
        resp = client.get("/api/cost-anomalies?baseline_days=91")
        assert resp.status_code == 400

    def test_valid_baseline_days_returns_200(self, monkeypatch):
        monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: [])
        resp = client.get("/api/cost-anomalies?baseline_days=7")
        assert resp.status_code == 200

    def test_internal_error_returns_500(self, monkeypatch):
        def boom(days):
            raise RuntimeError("db is gone")
        monkeypatch.setattr(backend.main, "get_user_model_totals_cached", boom)
        resp = client.get("/api/cost-anomalies?baseline_days=7")
        assert resp.status_code == 500
