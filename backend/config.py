import os
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

class Settings:
    PORT: int = int(os.getenv("PORT", 8000))
    GCS_BUCKET_NAME: str = os.getenv("GCS_BUCKET_NAME", "")
    
    # Vertex AI
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    VERTEX_REGION: str = os.getenv("VERTEX_REGION", "global")
    
    # BigQuery
    BIGQUERY_PROJECT_ID: str = os.getenv("BIGQUERY_PROJECT_ID", "")
    BIGQUERY_DATASET: str = os.getenv("BIGQUERY_DATASET", "vertex_ai_user_telemetry")
    BIGQUERY_VIEW: str = os.getenv("BIGQUERY_VIEW", "user_token_chargebacks")

    # Authentication
    AUTH_TOKEN: str = os.getenv("PORTAL_AUTH_TOKEN", "")

    # Logging behavior
    APPLY_LOGGING_ON_STARTUP: bool = os.getenv("APPLY_LOGGING_ON_STARTUP", "false").lower() in ("1", "true", "yes")
    LOGGING_SAMPLING_RATE: float = float(os.getenv("LOGGING_SAMPLING_RATE", "1.0"))

    # CORS
    CORS_ALLOW_ORIGINS: str = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

settings = Settings()
