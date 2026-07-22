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


# Path to the local budgets storage file (fallback)
LOCAL_BUDGET_FILE = "budgets.json"

DEFAULT_BUDGETS = {
    "global_default": {
        "identity": "global_default",
        "period": "month",
        "type": "token",  # 'token' or 'money'
        "limit": 10000000,  # 10 Million Tokens
        "alert_threshold_percentage": 50.0,
        "hard_limit_enabled": False
    }
}

def load_budgets() -> Dict[str, Any]:
    """
    Loads budgets from GCS if GCS_BUCKET_NAME is configured.
    Otherwise, loads from a local JSON file.
    Creates default budgets if neither exists.

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
            blob = bucket.blob("config/budgets.json")
            if blob.exists():
                data = blob.download_as_text()
                return json.loads(data)
            else:
                # Initialize GCS with defaults
                save_budgets(DEFAULT_BUDGETS)
                return DEFAULT_BUDGETS
        except Exception as e:
            logger.error(
                "Failed to load budgets from GCS bucket '%s': %s",
                settings.GCS_BUCKET_NAME, e
            )
            raise RuntimeError(
                f"Failed to load budgets from GCS bucket '{settings.GCS_BUCKET_NAME}': "
                f"{type(e).__name__}"
            ) from e

    # Local fallback (GCS not configured)
    if os.path.exists(LOCAL_BUDGET_FILE):
        try:
            with open(LOCAL_BUDGET_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(
                "Failed to read local budgets file: %s. Reinitializing defaults.", e
            )
            return DEFAULT_BUDGETS
    else:
        # Create file with defaults
        save_budgets(DEFAULT_BUDGETS)
        return DEFAULT_BUDGETS

def save_budgets(budgets_data: Dict[str, Any]) -> bool:
    """
    Saves budgets and returns a success flag with the following semantics:

    - If GCS_BUCKET_NAME is configured: success reflects the GCS write. A local
      write is performed first as a best-effort cache; a local-write failure alone
      does not fail the operation, but is logged at ERROR. GCS write failure sets
      success=False and is also logged at ERROR with a divergence warning.
    - If GCS_BUCKET_NAME is not configured: success reflects the local write.
    """
    local_ok = False
    try:
        _atomic_write_json(LOCAL_BUDGET_FILE, budgets_data)
        local_ok = True
    except Exception as e:
        logger.error("Failed to save budgets locally: %s", e)

    if settings.GCS_BUCKET_NAME:
        try:
            from google.cloud import storage
            storage_client = storage.Client()
            bucket = storage_client.bucket(settings.GCS_BUCKET_NAME)
            blob = bucket.blob("config/budgets.json")
            blob.upload_from_string(
                data=json.dumps(budgets_data, indent=4),
                content_type="application/json"
            )
            logger.info(
                "Successfully uploaded budgets config to GCS bucket: %s",
                settings.GCS_BUCKET_NAME
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to save budgets to GCS bucket '%s': %s. "
                "Local state may diverge across instances.",
                settings.GCS_BUCKET_NAME, e
            )
            return False

    # No GCS configured — success = local write success
    return local_ok
