"""
Unit tests for _auto_sync_models — the pure merge helper in backend.main.

Covers:
- New models are added with True
- Existing values are preserved unchanged (both True and False)
- Empty available list → no change, empty newly_added
- Models in config but not in available → untouched
"""
import pytest
from backend.main import _auto_sync_models


def test_new_models_added_with_true():
    available = ["gemini-2.5-flash", "gemini-2.5-pro"]
    config = {}
    merged, newly_added = _auto_sync_models(available, config)
    assert merged == {"gemini-2.5-flash": True, "gemini-2.5-pro": True}
    assert sorted(newly_added) == ["gemini-2.5-flash", "gemini-2.5-pro"]


def test_existing_true_preserved():
    available = ["gemini-2.5-flash", "gemini-2.5-pro"]
    config = {"gemini-2.5-flash": True}
    merged, newly_added = _auto_sync_models(available, config)
    assert merged["gemini-2.5-flash"] is True
    assert "gemini-2.5-pro" in newly_added
    assert "gemini-2.5-flash" not in newly_added


def test_existing_false_preserved():
    """A model explicitly disabled must not be re-enabled by the sync."""
    available = ["gemini-2.5-flash"]
    config = {"gemini-2.5-flash": False}
    merged, newly_added = _auto_sync_models(available, config)
    assert merged["gemini-2.5-flash"] is False
    assert newly_added == []


def test_empty_available_no_change():
    config = {"gemini-2.5-flash": False, "gemini-2.5-pro": True}
    merged, newly_added = _auto_sync_models([], config)
    assert merged == config
    assert newly_added == []


def test_models_not_in_available_untouched():
    """Config entries not in available are preserved as-is."""
    available = ["gemini-2.5-flash"]
    config = {"gemini-old-model": False}
    merged, newly_added = _auto_sync_models(available, config)
    assert merged["gemini-old-model"] is False
    assert merged["gemini-2.5-flash"] is True
    assert newly_added == ["gemini-2.5-flash"]


def test_returns_new_config_not_mutating_original():
    """The original config dict should not be mutated."""
    config = {"gemini-2.5-flash": False}
    original_copy = dict(config)
    _auto_sync_models(["gemini-new-model"], config)
    assert config == original_copy


def test_mixed_scenario():
    available = ["gemini-a", "gemini-b", "gemini-c"]
    config = {"gemini-a": True, "gemini-b": False}
    merged, newly_added = _auto_sync_models(available, config)
    assert merged["gemini-a"] is True   # preserved
    assert merged["gemini-b"] is False  # preserved (disabled)
    assert merged["gemini-c"] is True   # new → enabled
    assert newly_added == ["gemini-c"]
