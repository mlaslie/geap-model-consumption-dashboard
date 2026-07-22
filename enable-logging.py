# NOTE: Model IDs used here (e.g. "gemini-2.5-pro") and any associated rates in
# pricing.json should be verified against current Vertex AI documentation before
# production use — names and availability change as models graduate from preview.

import os
import sys
import vertexai
from vertexai.preview.generative_models import GenerativeModel
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID") or os.getenv("PROJECT_ID")
if not PROJECT_ID:
    print(
        "ERROR: PROJECT_ID is not set. "
        "Export BIGQUERY_PROJECT_ID or PROJECT_ID before running this script.",
        file=sys.stderr,
    )
    sys.exit(1)

LOCATION = os.getenv("VERTEX_REGION", "global")
MODEL_ID = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

BQ_PROJECT = os.getenv("BIGQUERY_PROJECT_ID") or PROJECT_ID
BQ_DATASET = os.getenv("BIGQUERY_DATASET", "vertex_ai_user_telemetry")
BQ_TABLE = "request_response_logging"
BQ_DESTINATION = f"bq://{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"

try:
    SAMPLING_RATE = float(os.getenv("LOGGING_SAMPLING_RATE", "1.0"))
except ValueError:
    print(
        "ERROR: LOGGING_SAMPLING_RATE must be a float between 0.0 and 1.0.",
        file=sys.stderr,
    )
    sys.exit(1)

# Initialize Vertex AI
vertexai.init(project=PROJECT_ID, location=LOCATION)

# Load the foundation model
model = GenerativeModel(MODEL_ID)

# Enable request-response logging directly to your BigQuery table
model.set_request_response_logging_config(
    enabled=True,
    sampling_rate=SAMPLING_RATE,
    bigquery_destination=BQ_DESTINATION,
)
print(
    f"Request-response payload logging successfully enabled! "
    f"(model={MODEL_ID}, sampling_rate={SAMPLING_RATE}, destination={BQ_DESTINATION})"
)
