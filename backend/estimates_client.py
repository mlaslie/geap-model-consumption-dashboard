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


# Path to the local estimates storage file (fallback when GCS is not configured)
LOCAL_ESTIMATES_FILE = "estimates.json"

# Default when no estimates have been saved yet
DEFAULT_ESTIMATES: Dict[str, Any] = {}


def load_estimates() -> Dict[str, Any]:
    """
    Loads estimates from GCS if GCS_BUCKET_NAME is configured.
    Otherwise, loads from a local JSON file.
    Initialises an empty dict if neither source exists.

    Fail-closed when GCS is configured: if the GCS read raises (network/permission
    error), this function logs at ERROR and re-raises as RuntimeError so the API
    returns 500 rather than serving divergent local state. A missing blob is NOT
    treated as a failure — it initialises an empty dict and saves it instead.
    """
    if settings.GCS_BUCKET_NAME:
        try:
            from google.cloud import storage
            storage_client = storage.Client()
            bucket = storage_client.bucket(settings.GCS_BUCKET_NAME)
            blob = bucket.blob("config/estimates.json")
            if blob.exists():
                data = blob.download_as_text()
                return json.loads(data)
            else:
                # Blob absent — initialise with an empty dict and persist it
                save_estimates(dict(DEFAULT_ESTIMATES))
                return dict(DEFAULT_ESTIMATES)
        except Exception as e:
            logger.error(
                "Failed to load estimates from GCS bucket '%s': %s",
                settings.GCS_BUCKET_NAME, e
            )
            raise RuntimeError(
                f"Failed to load estimates from GCS bucket '{settings.GCS_BUCKET_NAME}': "
                f"{type(e).__name__}"
            ) from e

    # Local fallback (GCS not configured)
    if os.path.exists(LOCAL_ESTIMATES_FILE):
        try:
            with open(LOCAL_ESTIMATES_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(
                "Failed to read local estimates file: %s. Returning empty defaults.", e
            )
            return dict(DEFAULT_ESTIMATES)
    else:
        # Create file with empty dict defaults
        save_estimates(dict(DEFAULT_ESTIMATES))
        return dict(DEFAULT_ESTIMATES)


def save_estimates(estimates_data: Dict[str, Any]) -> bool:
    """
    Saves estimates and returns a success flag with the following semantics:

    - If GCS_BUCKET_NAME is configured: success reflects the GCS write. A local
      write is performed first as a best-effort cache; a local-write failure alone
      does not fail the operation, but is logged at ERROR. A GCS write failure sets
      success=False and is also logged at ERROR with a divergence warning.
    - If GCS_BUCKET_NAME is not configured: success reflects the local write.
    """
    local_ok = False
    try:
        _atomic_write_json(LOCAL_ESTIMATES_FILE, estimates_data)
        local_ok = True
    except Exception as e:
        logger.error("Failed to save estimates locally: %s", e)

    if settings.GCS_BUCKET_NAME:
        try:
            from google.cloud import storage
            storage_client = storage.Client()
            bucket = storage_client.bucket(settings.GCS_BUCKET_NAME)
            blob = bucket.blob("config/estimates.json")
            blob.upload_from_string(
                data=json.dumps(estimates_data, indent=4),
                content_type="application/json"
            )
            logger.info(
                "Successfully uploaded estimates config to GCS bucket: %s",
                settings.GCS_BUCKET_NAME
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to save estimates to GCS bucket '%s': %s. "
                "Local state may diverge across instances.",
                settings.GCS_BUCKET_NAME, e
            )
            return False

    # No GCS configured — success = local write success
    return local_ok
