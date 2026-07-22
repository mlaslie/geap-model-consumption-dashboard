"""
Tests for save_budgets / load_budgets semantics in backend.gcs_client and
load_logging_config fail-closed behaviour in backend.logging_client.

save_budgets:
- GCS unconfigured, local write succeeds → True
- GCS configured but storage.Client raises → False (local write alone does not count)

load_budgets / load_logging_config (fail-closed):
- GCS configured and storage.Client raises → RuntimeError (never returns stale state)
"""
import json
import pytest
import google.cloud.storage
import backend.gcs_client
import backend.logging_client
from unittest.mock import MagicMock
from backend.gcs_client import save_budgets, load_budgets
from backend.logging_client import load_logging_config

SAMPLE_BUDGETS = {
    "global_default": {
        "identity": "global_default",
        "period": "month",
        "type": "token",
        "limit": 10_000_000,
        "alert_threshold_percentage": 50.0,
        "hard_limit_enabled": False,
    }
}


# ---------------------------------------------------------------------------
# GCS unconfigured — success reflects local write
# ---------------------------------------------------------------------------

def test_no_gcs_local_write_returns_true(monkeypatch, tmp_path):
    """With GCS_BUCKET_NAME empty, a successful local write returns True."""
    monkeypatch.setattr(backend.gcs_client.settings, "GCS_BUCKET_NAME", "")
    monkeypatch.chdir(tmp_path)

    result = save_budgets(SAMPLE_BUDGETS)

    assert result is True
    # Verify the file was actually written
    written = json.loads((tmp_path / "budgets.json").read_text())
    assert written == SAMPLE_BUDGETS


def test_no_gcs_local_write_produces_valid_json(monkeypatch, tmp_path):
    """The written file must be parseable JSON."""
    monkeypatch.setattr(backend.gcs_client.settings, "GCS_BUCKET_NAME", "")
    monkeypatch.chdir(tmp_path)

    save_budgets(SAMPLE_BUDGETS)
    content = (tmp_path / "budgets.json").read_text()
    parsed = json.loads(content)
    assert "global_default" in parsed


# ---------------------------------------------------------------------------
# GCS configured but client raises — success = False
# ---------------------------------------------------------------------------

def test_gcs_configured_client_raises_returns_false(monkeypatch, tmp_path):
    """
    When GCS is configured but storage.Client() raises, save_budgets must
    return False — the GCS path failure overrides the local write success.
    """
    monkeypatch.setattr(backend.gcs_client.settings, "GCS_BUCKET_NAME", "my-test-bucket")
    monkeypatch.chdir(tmp_path)

    # Make storage.Client() itself raise on instantiation
    failing_client = MagicMock(side_effect=Exception("GCS unavailable"))
    monkeypatch.setattr(google.cloud.storage, "Client", failing_client)

    result = save_budgets(SAMPLE_BUDGETS)

    assert result is False


def test_gcs_configured_upload_raises_returns_false(monkeypatch, tmp_path):
    """
    When GCS is configured and the Client instantiates but blob.upload_from_string
    raises, save_budgets must return False.
    """
    monkeypatch.setattr(backend.gcs_client.settings, "GCS_BUCKET_NAME", "my-test-bucket")
    monkeypatch.chdir(tmp_path)

    # Build a mock chain: Client() → bucket() → blob() → upload_from_string raises
    mock_blob = MagicMock()
    mock_blob.upload_from_string.side_effect = Exception("upload failed")
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    mock_client_cls = MagicMock(return_value=mock_storage_client)
    monkeypatch.setattr(google.cloud.storage, "Client", mock_client_cls)

    result = save_budgets(SAMPLE_BUDGETS)

    assert result is False


def test_gcs_configured_upload_succeeds_returns_true(monkeypatch, tmp_path):
    """
    When GCS is configured and the upload succeeds, save_budgets returns True.
    """
    monkeypatch.setattr(backend.gcs_client.settings, "GCS_BUCKET_NAME", "my-test-bucket")
    monkeypatch.chdir(tmp_path)

    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    monkeypatch.setattr(google.cloud.storage, "Client", MagicMock(return_value=mock_storage_client))

    result = save_budgets(SAMPLE_BUDGETS)

    assert result is True
    mock_blob.upload_from_string.assert_called_once()


# ---------------------------------------------------------------------------
# load_budgets fail-closed: GCS configured + storage raises → RuntimeError
# ---------------------------------------------------------------------------

def test_load_budgets_gcs_storage_client_raises_runtime_error(monkeypatch, tmp_path):
    """
    When GCS is configured and storage.Client() raises (network / permission
    failure), load_budgets must raise RuntimeError rather than returning
    potentially stale local state.
    """
    monkeypatch.setattr(backend.gcs_client.settings, "GCS_BUCKET_NAME", "my-test-bucket")
    monkeypatch.chdir(tmp_path)

    failing_client = MagicMock(side_effect=Exception("GCS network error"))
    monkeypatch.setattr(google.cloud.storage, "Client", failing_client)

    with pytest.raises(RuntimeError, match="GCS"):
        load_budgets()


def test_load_budgets_gcs_download_raises_runtime_error(monkeypatch, tmp_path):
    """
    When GCS is configured and blob.download_as_text raises, load_budgets must
    raise RuntimeError (fail-closed: blob exists but content is unreadable).
    """
    monkeypatch.setattr(backend.gcs_client.settings, "GCS_BUCKET_NAME", "my-test-bucket")
    monkeypatch.chdir(tmp_path)

    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.download_as_text.side_effect = Exception("permission denied")
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_storage_client = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    monkeypatch.setattr(google.cloud.storage, "Client", MagicMock(return_value=mock_storage_client))

    with pytest.raises(RuntimeError):
        load_budgets()


# ---------------------------------------------------------------------------
# load_logging_config fail-closed: GCS configured + storage raises → RuntimeError
# ---------------------------------------------------------------------------

def test_load_logging_config_gcs_storage_raises_runtime_error(monkeypatch, tmp_path):
    """
    When GCS is configured and storage.Client() raises, load_logging_config
    must raise RuntimeError (fail-closed semantics match load_budgets).
    """
    monkeypatch.setattr(backend.logging_client.settings, "GCS_BUCKET_NAME", "my-test-bucket")
    monkeypatch.chdir(tmp_path)

    failing_client = MagicMock(side_effect=Exception("GCS network error"))
    monkeypatch.setattr(google.cloud.storage, "Client", failing_client)

    with pytest.raises(RuntimeError, match="GCS"):
        load_logging_config()
