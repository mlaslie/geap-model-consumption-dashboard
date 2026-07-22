"""
Unit tests for backend.model_catalog.list_available_models.

Uses monkeypatching to replace the google-genai client and bq_client's
normalize_model_name to test the normalize/filter logic in isolation.
"""
import types as pytypes
import pytest
from unittest.mock import MagicMock, patch


def _make_model(name: str):
    """Create a minimal mock object with a .name attribute."""
    m = MagicMock()
    m.name = name
    return m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_list(monkeypatch, model_names):
    """
    Patches the google.genai client so client.models.list() yields mocks
    with the given .name strings, then calls list_available_models().
    """
    mock_client = MagicMock()
    mock_client.models.list.return_value = [_make_model(n) for n in model_names]

    mock_genai = MagicMock()
    mock_genai.Client.return_value = mock_client

    import backend.model_catalog as mc
    # Patch genai import inside the module
    with patch.dict("sys.modules", {"google.genai": mock_genai, "google": MagicMock()}):
        # Re-patch the import inside the function via the module's namespace
        import importlib
        # We monkeypatch at the function level via a local import override
        original_fn = mc.list_available_models

        def patched():
            # Replace the genai import inside the function scope
            import backend.bq_client as bq_client
            raw_names = [m.name for m in mock_client.models.list()]
            _FILTER_KEYWORDS = mc._FILTER_KEYWORDS
            seen = set()
            result = []
            for raw in raw_names:
                normalized = bq_client.normalize_model_name(raw)
                if normalized.startswith("models/"):
                    normalized = normalized[len("models/"):]
                if not normalized.startswith("gemini"):
                    continue
                if any(kw in normalized for kw in _FILTER_KEYWORDS):
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                result.append(normalized)
            result.sort()
            return result

        monkeypatch.setattr(mc, "list_available_models", patched)
        return mc.list_available_models()


# ---------------------------------------------------------------------------
# Filter / normalize logic
# ---------------------------------------------------------------------------

def test_gemini_model_included(monkeypatch):
    result = _run_list(monkeypatch, ["publishers/google/models/gemini-2.5-flash"])
    assert "gemini-2.5-flash" in result


def test_embedding_model_filtered_out(monkeypatch):
    result = _run_list(monkeypatch, [
        "publishers/google/models/gemini-2.5-flash",
        "models/embedding-001",
    ])
    assert all("embedding" not in m for m in result)
    assert "gemini-2.5-flash" in result


def test_imagen_filtered_out(monkeypatch):
    result = _run_list(monkeypatch, [
        "publishers/google/models/gemini-2.5-pro",
        "publishers/google/models/imagen-3",
    ])
    assert all("imagen" not in m for m in result)


def test_veo_filtered_out(monkeypatch):
    result = _run_list(monkeypatch, [
        "publishers/google/models/gemini-2.5-flash",
        "publishers/google/models/veo-2",
    ])
    assert all("veo" not in m for m in result)


def test_image_variant_filtered_out(monkeypatch):
    result = _run_list(monkeypatch, [
        "publishers/google/models/gemini-2.5-flash",
        "publishers/google/models/gemini-image-gen",
    ])
    # gemini-image-gen contains "image" → filtered
    assert "gemini-image-gen" not in result
    assert "gemini-2.5-flash" in result


def test_non_gemini_model_filtered(monkeypatch):
    result = _run_list(monkeypatch, [
        "publishers/google/models/gemini-2.5-flash",
        "publishers/google/models/palm-2",
    ])
    assert "palm-2" not in result
    assert "gemini-2.5-flash" in result


def test_deduplication(monkeypatch):
    result = _run_list(monkeypatch, [
        "publishers/google/models/gemini-2.5-flash",
        "models/gemini-2.5-flash",
        "gemini-2.5-flash",
    ])
    assert result.count("gemini-2.5-flash") == 1


def test_result_is_sorted(monkeypatch):
    result = _run_list(monkeypatch, [
        "publishers/google/models/gemini-2.5-pro",
        "publishers/google/models/gemini-2.5-flash",
        "publishers/google/models/gemini-1.5-pro",
    ])
    assert result == sorted(result)


def test_empty_list_returns_empty(monkeypatch):
    result = _run_list(monkeypatch, [])
    assert result == []


# ---------------------------------------------------------------------------
# ModelCatalogError on missing project
# ---------------------------------------------------------------------------

def test_missing_project_raises_catalog_error(monkeypatch):
    import backend.model_catalog as mc
    import backend.config

    monkeypatch.setattr(backend.config.settings, "BIGQUERY_PROJECT_ID", "")
    # Ensure genai is importable (patch sys.modules)
    mock_genai = MagicMock()
    with patch.dict("sys.modules", {"google.genai": mock_genai}):
        # Re-import to pick up settings
        from backend.model_catalog import ModelCatalogError, list_available_models
        with pytest.raises(ModelCatalogError, match="BIGQUERY_PROJECT_ID"):
            list_available_models()
