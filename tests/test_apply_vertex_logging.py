"""
Tests for apply_vertex_logging in backend.logging_client.

Covers:
- 409 / already-exists conflict → success=True, friendly error message
- Generic exception → success=False, sanitized "<ClassName>: configuration apply failed"
- SDK init failure → all models marked failed
- All succeed → all_succeeded=True, all errors None

The vertexai SDK is mocked via sys.modules so the real package is not needed.
"""
import sys
import pytest
from unittest.mock import MagicMock, patch
from backend.logging_client import apply_vertex_logging


@pytest.fixture()
def mock_vertexai():
    """
    Patch sys.modules so apply_vertex_logging's dynamic imports resolve to mocks.
    Yields (mock_vertexai_module, mock_generative_models_module).
    """
    mock_vai = MagicMock()
    mock_preview = MagicMock()
    mock_gm_module = MagicMock()
    mock_preview.generative_models = mock_gm_module

    modules = {
        "vertexai": mock_vai,
        "vertexai.preview": mock_preview,
        "vertexai.preview.generative_models": mock_gm_module,
    }

    with patch.dict(sys.modules, modules):
        yield mock_vai, mock_gm_module


# ---------------------------------------------------------------------------
# 409 / already-exists → success=True
# ---------------------------------------------------------------------------

def test_409_in_error_string_is_counted_as_success(mock_vertexai):
    """Exception whose str contains '409' → success=True, friendly error message."""
    _, mock_gm = mock_vertexai

    mock_model = MagicMock()
    mock_model.set_request_response_logging_config.side_effect = Exception(
        "ALREADY_EXISTS: 409 resource already exists"
    )
    mock_gm.GenerativeModel.return_value = mock_model

    result = apply_vertex_logging({"my-model": True})

    assert result["results"]["my-model"]["success"] is True
    assert result["results"]["my-model"]["error"] == "already active (409 conflict)"
    assert result["all_succeeded"] is True


def test_already_exists_in_error_string_is_counted_as_success(mock_vertexai):
    """Exception whose lower-case str contains 'already exists' → same friendly message."""
    _, mock_gm = mock_vertexai

    mock_model = MagicMock()
    mock_model.set_request_response_logging_config.side_effect = Exception(
        "Conflict: logging config already exists for this model"
    )
    mock_gm.GenerativeModel.return_value = mock_model

    result = apply_vertex_logging({"my-model": False})

    assert result["results"]["my-model"]["success"] is True
    assert result["results"]["my-model"]["error"] == "already active (409 conflict)"


# ---------------------------------------------------------------------------
# Generic exception → success=False, sanitized message
# ---------------------------------------------------------------------------

def test_generic_exception_sanitized_to_classname(mock_vertexai):
    """
    An exception that is not a 409/already-exists → success=False with
    error '<ClassName>: configuration apply failed'.
    """
    _, mock_gm = mock_vertexai

    class PermissionDeniedError(Exception):
        pass

    mock_model = MagicMock()
    mock_model.set_request_response_logging_config.side_effect = PermissionDeniedError(
        "user does not have permission"
    )
    mock_gm.GenerativeModel.return_value = mock_model

    result = apply_vertex_logging({"my-model": True})

    assert result["results"]["my-model"]["success"] is False
    assert result["results"]["my-model"]["error"] == "PermissionDeniedError: configuration apply failed"
    assert result["all_succeeded"] is False


def test_generic_exception_does_not_leak_internal_detail(mock_vertexai):
    """The sanitized error string must NOT include the original exception message."""
    _, mock_gm = mock_vertexai

    mock_model = MagicMock()
    mock_model.set_request_response_logging_config.side_effect = RuntimeError(
        "INTERNAL: secret server path /var/run/secret-token leaked"
    )
    mock_gm.GenerativeModel.return_value = mock_model

    result = apply_vertex_logging({"my-model": True})

    error_msg = result["results"]["my-model"]["error"]
    assert "secret" not in error_msg
    assert "configuration apply failed" in error_msg


# ---------------------------------------------------------------------------
# SDK init failure → all models failed
# ---------------------------------------------------------------------------

def test_sdk_init_failure_marks_all_models_failed(mock_vertexai):
    """If vertexai.init raises, every model in the config must be marked as failed."""
    mock_vai, _ = mock_vertexai

    class SDKInitError(Exception):
        pass

    mock_vai.init.side_effect = SDKInitError("SDK cannot initialize")

    config = {"model-a": True, "model-b": False}
    result = apply_vertex_logging(config)

    assert result["all_succeeded"] is False
    for model_id in config:
        assert result["results"][model_id]["success"] is False


# ---------------------------------------------------------------------------
# All succeed
# ---------------------------------------------------------------------------

def test_all_models_succeed_returns_all_succeeded_true(mock_vertexai):
    """When all models' set_request_response_logging_config calls succeed, all_succeeded=True."""
    _, mock_gm = mock_vertexai

    mock_model = MagicMock()
    # Default MagicMock: no side_effect, method returns a mock (success)
    mock_gm.GenerativeModel.return_value = mock_model

    config = {"model-a": True, "model-b": False}
    result = apply_vertex_logging(config)

    assert result["all_succeeded"] is True
    for model_id in config:
        assert result["results"][model_id]["success"] is True
        assert result["results"][model_id]["error"] is None


# ---------------------------------------------------------------------------
# Mixed results: one 409, one success, one generic error
# ---------------------------------------------------------------------------

def test_mixed_results_all_succeeded_false_when_any_fail(mock_vertexai):
    """all_succeeded must be False when at least one model genuinely fails."""
    _, mock_gm = mock_vertexai

    call_count = {"n": 0}

    def side_effect_by_call(*args, **kwargs):
        call_count["n"] += 1
        n = call_count["n"]
        if n == 1:
            pass  # first model: success
        elif n == 2:
            raise Exception("409 conflict")
        else:
            raise ValueError("some other error")

    mock_model = MagicMock()
    mock_model.set_request_response_logging_config.side_effect = side_effect_by_call
    mock_gm.GenerativeModel.return_value = mock_model

    config = {"model-ok": True, "model-409": True, "model-err": False}
    result = apply_vertex_logging(config)

    # model-ok: success=True, error=None
    assert result["results"]["model-ok"]["success"] is True
    # model-409: treated as success
    assert result["results"]["model-409"]["success"] is True
    # model-err: genuine failure
    assert result["results"]["model-err"]["success"] is False
    # Because at least one failed
    assert result["all_succeeded"] is False
