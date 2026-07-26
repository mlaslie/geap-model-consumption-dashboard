"""
Tests for _compute_config_health (unit) and GET /api/config-health (endpoint).

Monkeypatches backend.main.get_user_model_totals_cached, backend.main.load_budgets,
and the load_logging_config imported inside the endpoint so no BQ/GCS traffic occurs.
The autouse clear_usage_cache fixture in conftest.py keeps both caches clean.
"""
import pytest
import backend.main
import backend.auth
import backend.bq_client
import backend.logging_client
from fastapi.testclient import TestClient
from backend.main import app, _compute_config_health

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    monkeypatch.setattr(backend.auth.settings, "AUTH_TOKEN", "")


# ---------------------------------------------------------------------------
# Row builder helpers
# ---------------------------------------------------------------------------

def _row(user="alice@example.com", model="gemini-2.5-flash", pricing_match="exact", cost=0.10):
    return {
        "user_email": user,
        "model_name": model,
        "input_tokens": 1000,
        "output_tokens": 500,
        "total_tokens": 1500,
        "call_count": 1,
        "estimated_cost_usd": cost,
        "pricing_match": pricing_match,
        "pricing_tier": "le200k",
        "region": "global",
    }


def _budget(identity, period="month", b_type="token", limit=1_000_000):
    return {
        "identity": identity,
        "period": period,
        "type": b_type,
        "limit": limit,
        "alert_threshold_percentage": 80.0,
        "hard_limit_enabled": False,
    }


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

class TestComputeConfigHealth:

    def test_unused_budget_detected(self):
        """A configured identity with no usage in the window appears in unused_budgets."""
        budgets = {
            "global_default": _budget("global_default"),
            "ghost@example.com": _budget("ghost@example.com"),
        }
        logging_config = {}
        usage_rows = [_row(user="active@example.com")]
        result = _compute_config_health(budgets, logging_config, usage_rows)
        ids = [x["identity"] for x in result["unused_budgets"]]
        assert "ghost@example.com" in ids

    def test_active_budget_not_flagged(self):
        """A configured identity that has usage must NOT appear in unused_budgets."""
        budgets = {
            "global_default": _budget("global_default"),
            "alice@example.com": _budget("alice@example.com"),
        }
        logging_config = {}
        usage_rows = [_row(user="alice@example.com")]
        result = _compute_config_health(budgets, logging_config, usage_rows)
        ids = [x["identity"] for x in result["unused_budgets"]]
        assert "alice@example.com" not in ids

    def test_global_default_never_in_unused_budgets(self):
        """global_default is always excluded from unused_budgets even with zero usage."""
        budgets = {"global_default": _budget("global_default")}
        result = _compute_config_health(budgets, {}, [])
        ids = [x["identity"] for x in result["unused_budgets"]]
        assert "global_default" not in ids

    def test_logged_unused_model_detected(self):
        """A model with logging=True but no usage in the window is flagged."""
        logging_config = {"gemini-2.5-pro": True, "gemini-2.5-flash": True}
        usage_rows = [_row(model="gemini-2.5-flash")]
        result = _compute_config_health({}, logging_config, usage_rows)
        assert "gemini-2.5-pro" in result["logged_unused_models"]
        assert "gemini-2.5-flash" not in result["logged_unused_models"]

    def test_unlogged_used_model_detected(self):
        """A model in usage that is False (or absent) in logging_config is flagged."""
        logging_config = {"gemini-2.5-flash": False}
        usage_rows = [_row(model="gemini-2.5-flash")]
        result = _compute_config_health({}, logging_config, usage_rows)
        assert "gemini-2.5-flash" in result["unlogged_used_models"]

    def test_logged_and_used_model_not_in_unlogged(self):
        """A model in usage with logging=True must NOT appear in unlogged_used_models."""
        logging_config = {"gemini-2.5-flash": True}
        usage_rows = [_row(model="gemini-2.5-flash")]
        result = _compute_config_health({}, logging_config, usage_rows)
        assert "gemini-2.5-flash" not in result["unlogged_used_models"]

    def test_unpriced_used_model_detected(self):
        """A model in usage whose pricing_match=='default' is flagged as unpriced."""
        usage_rows = [_row(model="new-model-x", pricing_match="default")]
        result = _compute_config_health({}, {}, usage_rows)
        assert "new-model-x" in result["used_unpriced_models"]

    def test_priced_model_not_in_unpriced(self):
        """A model with pricing_match 'exact' or 'prefix' must NOT appear in used_unpriced_models."""
        usage_rows = [
            _row(model="gemini-2.5-flash", pricing_match="exact"),
            _row(model="gemini-2.5-pro-001", pricing_match="prefix"),
        ]
        result = _compute_config_health({}, {}, usage_rows)
        assert "gemini-2.5-flash" not in result["used_unpriced_models"]
        assert "gemini-2.5-pro-001" not in result["used_unpriced_models"]

    def test_all_clean_returns_empty_lists(self):
        """When everything is properly configured, all four lists are empty."""
        budgets = {
            "global_default": _budget("global_default"),
            "alice@example.com": _budget("alice@example.com"),
        }
        logging_config = {"gemini-2.5-flash": True}
        usage_rows = [_row(user="alice@example.com", model="gemini-2.5-flash", pricing_match="exact")]
        result = _compute_config_health(budgets, logging_config, usage_rows)
        assert result["unused_budgets"] == []
        assert result["logged_unused_models"] == []
        assert result["used_unpriced_models"] == []
        assert result["unlogged_used_models"] == []

    def test_usage_window_days_is_30(self):
        """The helper must always report usage_window_days=30."""
        result = _compute_config_health({}, {}, [])
        assert result["usage_window_days"] == 30

    def test_lists_are_sorted(self):
        """All list fields must be returned in sorted order."""
        budgets = {
            "global_default": _budget("global_default"),
            "zoe@example.com": _budget("zoe@example.com"),
            "ann@example.com": _budget("ann@example.com"),
        }
        logging_config = {
            "model-z": True,
            "model-a": True,
        }
        usage_rows = []  # nothing used → ann, zoe are unused; model-z, model-a are unused
        result = _compute_config_health(budgets, logging_config, usage_rows)
        unused_ids = [x["identity"] for x in result["unused_budgets"]]
        assert unused_ids == sorted(unused_ids)
        assert result["logged_unused_models"] == sorted(result["logged_unused_models"])


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

class TestConfigHealthEndpoint:

    def _setup_mocks(self, monkeypatch, budgets=None, logging_cfg=None, usage_rows=None):
        if budgets is None:
            budgets = {"global_default": _budget("global_default")}
        if logging_cfg is None:
            logging_cfg = {"gemini-2.5-flash": True}
        if usage_rows is None:
            usage_rows = [_row()]

        monkeypatch.setattr(backend.main, "load_budgets", lambda: budgets)
        monkeypatch.setattr(backend.main, "get_user_model_totals_cached", lambda days: usage_rows)
        # The endpoint does `from backend.logging_client import load_logging_config`
        # inside the function; patch at the logging_client module level so both
        # the direct import and any cached reference see the replacement.
        monkeypatch.setattr(backend.logging_client, "load_logging_config", lambda: logging_cfg)

    def test_happy_path_shape(self, monkeypatch):
        """Endpoint returns correct status and all required keys."""
        self._setup_mocks(monkeypatch)
        resp = client.get("/api/config-health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        data = body["data"]
        required = {
            "generated_at_utc", "unused_budgets", "logged_unused_models",
            "used_unpriced_models", "unlogged_used_models", "usage_window_days",
        }
        assert required.issubset(data.keys())
        assert data["usage_window_days"] == 30

    def test_internal_error_returns_500(self, monkeypatch):
        def boom():
            raise RuntimeError("gcs is down")
        monkeypatch.setattr(backend.main, "load_budgets", boom)
        resp = client.get("/api/config-health")
        assert resp.status_code == 500
