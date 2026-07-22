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

print("Initializing Vertex AI...")
vertexai.init(project=PROJECT_ID, location=LOCATION)

print(f"Loading model {MODEL_ID}...")
model = GenerativeModel(MODEL_ID)

print("Sending generate_content request...")
try:
    response = model.generate_content("Hello! This is a test query to trigger auditing and payload logs.")
    print("Response text:", response.text)
    print("Trigger completed successfully!")
except Exception as e:
    print("Failed to run prediction:", e)
