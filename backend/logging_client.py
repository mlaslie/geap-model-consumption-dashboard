import os
import json
import tempfile
import logging
from typing import Dict, Any
from backend.config import settings

logger = logging.getLogger(__name__)

def _atomic_write_json(path: str, data) -> None:
    """Write JSON via a unique temp file + os.replace so concurrent readers
    never observe a truncated/partial file (plain open(path, "w") truncates
    first)."""
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4, allow_nan=False)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


LOCAL_LOGGING_FILE = "logging_config.json"
LOCAL_MODEL_SYNC_FILE = "model_sync.json"

DEFAULT_MODEL_SYNC_CONFIG = {"auto_sync_on_startup": False}

DEFAULT_LOGGING_CONFIG = {
    "gemini-2.5-pro": True,
    "gemini-2.5-flash": False,
    "gemini-3.1-flash-lite": False,
    "gemini-3-flash-preview": False,
    "gemini-3.1-pro-preview": False,
    "gemini-3.5-flash": False,
    "gemini-3.6-flash": False,
}

def load_logging_config() -> Dict[str, bool]:
    """
    Loads model logging status from GCS if configured,
    otherwise loads from a local JSON file.

    Fail-closed when GCS is configured: if the GCS read raises (network/permission
    error), this function logs at ERROR and re-raises as RuntimeError so the API
    returns 500 rather than serving divergent local state. A missing blob is NOT
    treated as a failure — it initialises defaults instead.
    """
    if settings.GCS_BUCKET_NAME:
        try:
            from google.cloud import storage
            storage_client = storage.Client()
            bucket = storage_client.bucket(settings.GCS_BUCKET_NAME)
            blob = bucket.blob("config/logging_config.json")
            if blob.exists():
                data = blob.download_as_text()
                return json.loads(data)
            else:
                save_logging_config(DEFAULT_LOGGING_CONFIG)
                return DEFAULT_LOGGING_CONFIG
        except Exception as e:
            logger.error(
                "Failed to load logging config from GCS bucket '%s': %s",
                settings.GCS_BUCKET_NAME, e
            )
            raise RuntimeError(
                f"Failed to load logging config from GCS bucket '{settings.GCS_BUCKET_NAME}': "
                f"{type(e).__name__}"
            ) from e

    # Local fallback (GCS not configured)
    if os.path.exists(LOCAL_LOGGING_FILE):
        try:
            with open(LOCAL_LOGGING_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to read local logging config: %s.", e)
            return DEFAULT_LOGGING_CONFIG
    else:
        save_logging_config(DEFAULT_LOGGING_CONFIG)
        return DEFAULT_LOGGING_CONFIG

def save_logging_config(config_data: Dict[str, bool]) -> bool:
    """
    Saves model logging status and returns a success flag with the following semantics:

    - If GCS_BUCKET_NAME is configured: success reflects the GCS write. A local
      write is performed first as a best-effort cache; a local-write failure alone
      does not fail the operation, but is logged at ERROR. GCS write failure sets
      success=False and is also logged at ERROR with a divergence warning.
    - If GCS_BUCKET_NAME is not configured: success reflects the local write.
    """
    local_ok = False
    try:
        _atomic_write_json(LOCAL_LOGGING_FILE, config_data)
        local_ok = True
    except Exception as e:
        logger.error("Failed to save logging config locally: %s", e)

    if settings.GCS_BUCKET_NAME:
        try:
            from google.cloud import storage
            storage_client = storage.Client()
            bucket = storage_client.bucket(settings.GCS_BUCKET_NAME)
            blob = bucket.blob("config/logging_config.json")
            blob.upload_from_string(
                data=json.dumps(config_data, indent=4),
                content_type="application/json"
            )
            logger.info(
                "Successfully uploaded logging config to GCS bucket: %s",
                settings.GCS_BUCKET_NAME
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to save logging config to GCS bucket '%s': %s. "
                "Local state may diverge across instances.",
                settings.GCS_BUCKET_NAME, e
            )
            return False

    # No GCS configured — success = local write success
    return local_ok

def load_model_sync_config() -> dict:
    """
    Loads model-sync settings from GCS (blob "config/model_sync.json") when
    GCS_BUCKET_NAME is set, otherwise from a local file.

    Fail-closed when GCS is configured — same semantics as load_logging_config:
    a missing blob initialises defaults; a read error re-raises as RuntimeError.
    """
    if settings.GCS_BUCKET_NAME:
        try:
            from google.cloud import storage
            storage_client = storage.Client()
            bucket = storage_client.bucket(settings.GCS_BUCKET_NAME)
            blob = bucket.blob("config/model_sync.json")
            if blob.exists():
                data = blob.download_as_text()
                return json.loads(data)
            else:
                save_model_sync_config(DEFAULT_MODEL_SYNC_CONFIG)
                return dict(DEFAULT_MODEL_SYNC_CONFIG)
        except Exception as e:
            logger.error(
                "Failed to load model-sync config from GCS bucket '%s': %s",
                settings.GCS_BUCKET_NAME, e,
            )
            raise RuntimeError(
                f"Failed to load model-sync config from GCS bucket "
                f"'{settings.GCS_BUCKET_NAME}': {type(e).__name__}"
            ) from e

    # Local fallback
    if os.path.exists(LOCAL_MODEL_SYNC_FILE):
        try:
            with open(LOCAL_MODEL_SYNC_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to read local model-sync config: %s.", e)
            return dict(DEFAULT_MODEL_SYNC_CONFIG)
    else:
        save_model_sync_config(DEFAULT_MODEL_SYNC_CONFIG)
        return dict(DEFAULT_MODEL_SYNC_CONFIG)


def save_model_sync_config(cfg: dict) -> bool:
    """
    Saves model-sync settings.  Mirrors save_logging_config semantics:
    - GCS configured → GCS write is authoritative; local write is best-effort cache.
    - No GCS → local write is authoritative.
    Returns True on success, False on failure.
    """
    local_ok = False
    try:
        _atomic_write_json(LOCAL_MODEL_SYNC_FILE, cfg)
        local_ok = True
    except Exception as e:
        logger.error("Failed to save model-sync config locally: %s", e)

    if settings.GCS_BUCKET_NAME:
        try:
            from google.cloud import storage
            storage_client = storage.Client()
            bucket = storage_client.bucket(settings.GCS_BUCKET_NAME)
            blob = bucket.blob("config/model_sync.json")
            blob.upload_from_string(
                data=json.dumps(cfg, indent=4),
                content_type="application/json",
            )
            logger.info(
                "Saved model-sync config to GCS bucket: %s",
                settings.GCS_BUCKET_NAME,
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to save model-sync config to GCS bucket '%s': %s. "
                "Local state may diverge across instances.",
                settings.GCS_BUCKET_NAME, e,
            )
            return False

    return local_ok


def apply_vertex_logging(config_data: Dict[str, bool]) -> dict:
    """
    Applies the logging configuration directly to Vertex AI foundation models.

    Returns a dict matching the contract:
        {"results": {model_id: {"success": bool, "error": str | None}}, "all_succeeded": bool}

    No exception escapes this function. Top-level SDK import/init failure marks
    every model as failed with a sanitized error message.

    Per-model error sanitization (browser-safe):
      - 409 / already-exists conflict → success=True, error="already active (409 conflict)"
        (the config is already applied; logged at INFO)
      - Any other exception → success=False, error="<ExceptionClassName>: configuration apply failed"
        (full details logged server-side via logger.exception/warning)
    """
    results: Dict[str, Any] = {}

    # Clamp sampling rate to (0, 1]
    raw_rate = getattr(settings, "LOGGING_SAMPLING_RATE", 1.0)
    sampling_rate = max(1e-9, min(float(raw_rate), 1.0))

    try:
        import vertexai
        from vertexai.preview.generative_models import GenerativeModel
        vertexai.init(project=settings.BIGQUERY_PROJECT_ID, location=settings.VERTEX_REGION)
    except Exception as e:
        sanitized = f"{type(e).__name__}: configuration apply failed"
        logger.error("Failed to initialize Vertex AI SDK: %s", e)
        for model_id in config_data:
            results[model_id] = {"success": False, "error": sanitized}
        return {"results": results, "all_succeeded": False}

    for model_id, enabled in config_data.items():
        logger.info("Applying payload logging config: %s = %s", model_id, enabled)
        try:
            model = GenerativeModel(model_id)
            model.set_request_response_logging_config(
                enabled=enabled,
                sampling_rate=sampling_rate,
                bigquery_destination=(
                    f"bq://{settings.BIGQUERY_PROJECT_ID}"
                    f".{settings.BIGQUERY_DATASET}.request_response_logging"
                )
            )
            logger.info("Successfully applied payload logging config for model: %s", model_id)
            results[model_id] = {"success": True, "error": None}
        except Exception as model_err:
            err_str = str(model_err)
            # 409 / already-exists: config is already applied — count as success
            if "409" in err_str or "already exists" in err_str.lower():
                logger.info(
                    "Logging config for model '%s' already active (409 conflict): %s",
                    model_id, model_err
                )
                results[model_id] = {"success": True, "error": "already active (409 conflict)"}
            else:
                logger.warning(
                    "Failed to apply payload logging config for model '%s': %s",
                    model_id, model_err
                )
                sanitized = f"{type(model_err).__name__}: configuration apply failed"
                results[model_id] = {"success": False, "error": sanitized}

    all_succeeded = all(v["success"] for v in results.values())
    if all_succeeded:
        logger.info(
            "Vertex AI foundation model payload logging configurations successfully updated."
        )

    return {"results": results, "all_succeeded": all_succeeded}
