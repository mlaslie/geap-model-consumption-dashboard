from __future__ import annotations

import os
import json
import logging
import re
import time
from typing import Dict, List, Any, Optional, Tuple
from backend.config import settings

logger = logging.getLogger(__name__)

# Row cap for the raw usage feed (/api/usage — charts and tables). The view's
# tier/region/day grain multiplies row counts, and chart ranges reach 1 year,
# so this is sized generously; the API still signals truncation when hit.
# Budget/alert paths use get_user_model_totals (no LIMIT) and are never capped.
USAGE_ROW_LIMIT = 5000

# Pattern for safe BigQuery identifier components (project, dataset, view names)
_SAFE_IDENTIFIER_RE = re.compile(r'^[A-Za-z0-9_.-]+$')

# ---------------------------------------------------------------------------
# TTL cache for get_token_usage_logs — keyed by days argument
# ---------------------------------------------------------------------------
_CACHE_TTL: float = 30.0  # seconds
_usage_cache: Dict[Optional[int], tuple] = {}

# ---------------------------------------------------------------------------
# TTL cache for get_user_model_totals — keyed by days argument (always int)
# Kept separate from _usage_cache so the two paths never collide.
# ---------------------------------------------------------------------------
_totals_cache: Dict[int, tuple] = {}


def _validate_identifier(value: str, name: str) -> None:
    """Validate that a BigQuery identifier component is safe to interpolate into SQL."""
    if not _SAFE_IDENTIFIER_RE.match(value):
        raise ValueError(
            f"Invalid BigQuery identifier for {name!r}: {value!r}. "
            "Must match ^[A-Za-z0-9_.-]+$"
        )


# Path to the local pricing configuration file — exposed so callers (main.py,
# tests) can reference or monkeypatch it without duplicating the computation.
PRICING_PATH: str = os.path.join(os.path.dirname(__file__), "pricing.json")


def load_pricing_config() -> Dict[str, Dict[str, float]]:
    """
    Loads model input/output pricing configurations from PRICING_PATH.
    """
    # Google Standard-tier global rates, July 2026.
    # Optional fields (input_cost_per_million_gt_200k, non_global_multiplier)
    # are omitted where not applicable so the in-code default mirrors the file.
    default_pricing = {
        "gemini-2.5-pro": {
            "input_cost_per_million": 1.25,
            "input_cost_per_million_gt_200k": 2.50,
            "output_cost_per_million": 10.00,
        },
        "gemini-2.5-flash": {
            "input_cost_per_million": 0.30,
            "output_cost_per_million": 2.50,
        },
        "gemini-2.5-flash-lite": {
            "input_cost_per_million": 0.10,
            "output_cost_per_million": 0.40,
        },
        "gemini-3-flash-preview": {
            "input_cost_per_million": 0.50,
            "output_cost_per_million": 3.00,
        },
        "gemini-3.1-flash-lite": {
            "input_cost_per_million": 0.25,
            "output_cost_per_million": 1.50,
            "non_global_multiplier": 1.1,
        },
        "gemini-3.1-pro-preview": {
            "input_cost_per_million": 2.00,
            "input_cost_per_million_gt_200k": 4.00,
            "output_cost_per_million": 12.00,
        },
        "gemini-3.5-flash": {
            "input_cost_per_million": 1.50,
            "output_cost_per_million": 9.00,
            "non_global_multiplier": 1.1,
        },
        "gemini-3.5-flash-lite": {
            "input_cost_per_million": 0.30,
            "output_cost_per_million": 2.50,
            "non_global_multiplier": 1.1,
        },
        "gemini-3.6-flash": {
            "input_cost_per_million": 1.50,
            "output_cost_per_million": 7.50,
        },
    }
    if not os.path.exists(PRICING_PATH):
        return default_pricing
    try:
        with open(PRICING_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Error loading pricing.json, using defaults: %s", e)
        return default_pricing


def normalize_model_name(model_name: str) -> str:
    """
    Strips standard model prefixes to match pricing.json keys.
    e.g., 'publishers/google/models/gemini-2.5-pro' -> 'gemini-2.5-pro'
    Returns 'unknown' for empty/None input rather than silently misattributing
    to a real model name.
    """
    if not model_name:
        return "unknown"
    model_name = model_name.lower()
    if "publishers/google/models/" in model_name:
        model_name = model_name.replace("publishers/google/models/", "")
    return model_name


def resolve_pricing(
    model_name: str, pricing: Dict[str, Any]
) -> Tuple[Optional[Dict[str, float]], str]:
    """
    Resolves pricing rates for a model name, returning (rates_or_None, match_kind).

    match_kind is one of:
      "exact"   — exact key match on the normalized name
      "prefix"  — longest-prefix match
      "default" — no match found; caller should apply fallback rates
    """
    normalized = normalize_model_name(model_name)

    if normalized in pricing:
        return pricing[normalized], "exact"

    # Longest-prefix match — deterministic, not dict-order dependent
    best_key = ""
    for k in pricing:
        if normalized.startswith(k) and len(k) > len(best_key):
            best_key = k
    if best_key:
        return pricing[best_key], "prefix"

    return None, "default"


def calculate_estimated_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    pricing: Dict[str, Any],
    tier: str = "le200k",
    region: str = "global",
) -> float:
    """
    Dynamically computes cost based on loaded pricing parameters.

    Resolution order:
      1. Exact match on the normalized name.
      2. Longest-prefix match (e.g. 'gemini-2.5-flash-001' -> 'gemini-2.5-flash').
      3. Default flash-class rates with a warning naming the unknown model.

    tier: 'le200k' (default, <=200 K context) or 'gt200k' (>200 K context).
          When 'gt200k' and the matched pricing entry contains
          'input_cost_per_million_gt_200k', that rate is used for input tokens;
          otherwise falls back to 'input_cost_per_million'.

    region: 'global' (default) or any non-'global' value (regional endpoint).
            When non-global, both input and output rates are multiplied by
            'non_global_multiplier' from the pricing entry (default 1.0 when absent).
    """
    matched_pricing, match_kind = resolve_pricing(model_name, pricing)

    if match_kind == "default":
        # Unknown model — use conservative defaults and surface the gap.
        # Updated to current flash-class standard rates (1.50 in / 7.50 out);
        # the previous defaults (0.075 / 0.30) were ~20x stale.
        logger.warning(
            "Unknown model %r: no pricing entry found. Using default flash rates.",
            normalize_model_name(model_name),
        )
        input_rate = 1.50
        output_rate = 7.50
        # No per-model gt200k rate is known for unlisted models (le200k rates
        # apply to all context sizes), but the regional premium is a uniform
        # Google-wide +10%, so apply it to keep regional traffic comparable.
        if region != "global":
            input_rate *= 1.1
            output_rate *= 1.1
    else:
        # .get() + None check (not `in`): a hand-edited pricing.json can carry
        # an explicit null, which must fall back to the base rate rather than
        # poison the multiplication with None.
        gt_rate = matched_pricing.get("input_cost_per_million_gt_200k")
        if tier == "gt200k" and gt_rate is not None:
            input_rate = gt_rate
        else:
            input_rate = matched_pricing.get("input_cost_per_million", 1.50)
        output_rate = matched_pricing.get("output_cost_per_million", 7.50)
        if region != "global":
            multiplier = matched_pricing.get("non_global_multiplier") or 1.0
            input_rate *= multiplier
            output_rate *= multiplier

    cost = (input_tokens / 1_000_000 * input_rate) + (output_tokens / 1_000_000 * output_rate)
    return round(cost, 6)


# Load pricing once at module level
PRICING = load_pricing_config()


def reload_pricing() -> Dict:
    """
    Re-reads pricing from PRICING_PATH via load_pricing_config(), updates the
    module-level PRICING dict IN PLACE, and clears both TTL caches (cached rows
    embed computed costs so they become stale whenever pricing changes).

    Returns the new dict contents.

    Designed so tests can monkeypatch PRICING_PATH and have this function
    (and load_pricing_config) use the monkeypatched value at call time.
    """
    new_pricing = load_pricing_config()
    PRICING.clear()
    PRICING.update(new_pricing)
    _usage_cache.clear()
    _totals_cache.clear()
    return dict(PRICING)


def get_token_usage_logs(days: int | None = None) -> List[Dict[str, Any]]:
    """
    Returns the user token chargebacks/logs strictly from BigQuery.
    If days is provided, restricts results to the trailing window of that many
    calendar days using a parameterized query (not f-string interpolation).
    Raises an exception directly on failure.

    NOTE: requires the view created by setup_bigquery_view.py, which now
    includes the pricing_tier and region columns. Re-running setup_bigquery_view.py
    is REQUIRED before deploying this version.

    Each returned row includes a "pricing_match" field: "exact" | "prefix" | "default",
    indicating how the pricing rate was resolved for that model.
    """
    from google.cloud import bigquery

    project_id = settings.BIGQUERY_PROJECT_ID
    if not project_id:
        raise ValueError(
            "GCP Project ID is not configured. Please set BIGQUERY_PROJECT_ID in the environment."
        )

    dataset = settings.BIGQUERY_DATASET
    view = settings.BIGQUERY_VIEW

    # Validate identifiers before f-string interpolation into SQL
    _validate_identifier(project_id, "BIGQUERY_PROJECT_ID")
    _validate_identifier(dataset, "BIGQUERY_DATASET")
    _validate_identifier(view, "BIGQUERY_VIEW")

    where_clause = ""
    query_params: list = []
    if days is not None:
        where_clause = (
            "WHERE call_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)"
        )
        query_params = [bigquery.ScalarQueryParameter("days", "INT64", days)]

    query = f"""
        SELECT
            call_timestamp,
            project_id,
            user_email,
            model_name,
            input_tokens,
            output_tokens,
            total_tokens,
            call_count,
            pricing_tier,
            region
        FROM `{project_id}.{dataset}.{view}`
        {where_clause}
        ORDER BY call_timestamp DESC
        LIMIT {USAGE_ROW_LIMIT}
    """

    client = bigquery.Client(project=project_id)
    if query_params:
        job_config = bigquery.QueryJobConfig(query_parameters=query_params)
        query_job = client.query(query, job_config=job_config)
    else:
        query_job = client.query(query)

    results = query_job.result()

    logs = []
    for row in results:
        _, match_kind = resolve_pricing(row.model_name, PRICING)
        cost = calculate_estimated_cost(
            row.model_name,
            row.input_tokens,
            row.output_tokens,
            PRICING,
            tier=row.pricing_tier,
            region=row.region,
        )
        logs.append({
            "call_timestamp": row.call_timestamp.isoformat() if row.call_timestamp else None,
            "project_id": row.project_id,
            "user_email": row.user_email,
            # Normalized so UI filters/legends merge full publisher paths
            # (publishers/google/models/x) with their short names.
            "model_name": normalize_model_name(row.model_name),
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "total_tokens": row.total_tokens,
            "call_count": row.call_count,
            "pricing_tier": row.pricing_tier,
            "region": row.region,
            "estimated_cost_usd": cost,
            "pricing_match": match_kind,
        })
    return logs


def get_user_model_totals(days: int) -> List[Dict[str, Any]]:
    """
    Returns SQL-side aggregated totals per (user_email, model_name) for the
    trailing *days*-day window.  No LIMIT is applied — the result set is bounded
    by users × models which is always manageable.

    Requires the daily-grain view created by setup_bigquery_view.py.

    The view groups by (user_email, model_name, pricing_tier, region) so the
    result has finer granularity than (user, model) alone.  Callers that sum
    per-user or per-model accumulate across tiers/regions transparently.

    Each returned row:
        user_email, model_name, pricing_tier, region,
        input_tokens, output_tokens, total_tokens,
        call_count,
        estimated_cost_usd, pricing_match

    NOTE: requires the view created by setup_bigquery_view.py with the
    pricing_tier and region columns. Re-running setup_bigquery_view.py is
    REQUIRED before deploying this version.
    """
    from google.cloud import bigquery

    project_id = settings.BIGQUERY_PROJECT_ID
    if not project_id:
        raise ValueError(
            "GCP Project ID is not configured. Please set BIGQUERY_PROJECT_ID in the environment."
        )

    dataset = settings.BIGQUERY_DATASET
    view = settings.BIGQUERY_VIEW

    _validate_identifier(project_id, "BIGQUERY_PROJECT_ID")
    _validate_identifier(dataset, "BIGQUERY_DATASET")
    _validate_identifier(view, "BIGQUERY_VIEW")

    query = f"""
        SELECT
            user_email,
            model_name,
            pricing_tier,
            region,
            SUM(input_tokens)  AS input_tokens,
            SUM(output_tokens) AS output_tokens,
            SUM(total_tokens)  AS total_tokens,
            SUM(call_count)    AS call_count
        FROM `{project_id}.{dataset}.{view}`
        WHERE call_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        GROUP BY user_email, model_name, pricing_tier, region
    """
    query_params = [bigquery.ScalarQueryParameter("days", "INT64", days)]
    job_config = bigquery.QueryJobConfig(query_parameters=query_params)

    client = bigquery.Client(project=project_id)
    query_job = client.query(query, job_config=job_config)
    results = query_job.result()

    rows = []
    for row in results:
        _, match_kind = resolve_pricing(row.model_name, PRICING)
        cost = calculate_estimated_cost(
            row.model_name,
            row.input_tokens,
            row.output_tokens,
            PRICING,
            tier=row.pricing_tier,
            region=row.region,
        )
        rows.append({
            "user_email": row.user_email,
            "model_name": normalize_model_name(row.model_name),
            "pricing_tier": row.pricing_tier,
            "region": row.region,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "total_tokens": row.total_tokens,
            "call_count": row.call_count,
            "estimated_cost_usd": cost,
            "pricing_match": match_kind,
        })
    return rows


def get_user_model_totals_cached(days: int) -> List[Dict[str, Any]]:
    """
    Cached wrapper around get_user_model_totals.

    Results are keyed by the 'days' argument in _totals_cache (separate from
    _usage_cache) and cached for _CACHE_TTL seconds (default 30 s).
    On cache miss or expiry, delegates to get_user_model_totals and stores
    the result.
    """
    now = time.monotonic()
    entry = _totals_cache.get(days)
    if entry is not None:
        ts, data = entry
        if now - ts < _CACHE_TTL:
            return data

    data = get_user_model_totals(days=days)
    _totals_cache[days] = (now, data)
    return data


def get_token_usage_logs_cached(days: int | None = None) -> List[Dict[str, Any]]:
    """
    Cached wrapper around get_token_usage_logs.

    Results are keyed by the 'days' argument and cached for _CACHE_TTL seconds
    (module-level constant, default 30 s). On cache miss or expiry, delegates
    to get_token_usage_logs and stores the result.
    """
    now = time.monotonic()
    entry = _usage_cache.get(days)
    if entry is not None:
        ts, data = entry
        if now - ts < _CACHE_TTL:
            return data

    data = get_token_usage_logs(days=days)
    _usage_cache[days] = (now, data)
    return data
