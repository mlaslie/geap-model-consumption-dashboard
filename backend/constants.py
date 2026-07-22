"""Shared constants with no intra-package dependencies (safe to import anywhere)."""
from typing import Dict

# Budget period -> trailing-day window used for BigQuery aggregation.
PERIOD_DAYS: Dict[str, int] = {
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}
