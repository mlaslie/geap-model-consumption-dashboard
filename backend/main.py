import collections
import json
import os
import re
import tempfile
import threading
import time
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend.config import settings
import backend.bq_client as bq_client
from backend.bq_client import get_token_usage_logs_cached, get_user_model_totals_cached
from backend.gcs_client import load_budgets, save_budgets
from backend.estimates_client import load_estimates, save_estimates
from backend.ai_assistant import query_finops_assistant, AssistantUnavailableError
from backend.auth import require_auth
from backend.model_catalog import list_available_models, ModelCatalogError

logger = logging.getLogger(__name__)

# Period constants — used for budget enforcement (#P0-4, #22).
# Shared with ai_assistant via backend.constants (no circular imports).
from backend.constants import PERIOD_DAYS

# ---------------------------------------------------------------------------
# Per-process sliding-window rate limiter for /api/chat
# NOTE: per-process only (fine for single-instance); use a shared store such
# as Redis or Cloud Memorystore for multi-instance deployments.
# ---------------------------------------------------------------------------
_CHAT_RATE_LIMIT_MAX = 10
_CHAT_RATE_LIMIT_WINDOW = 60.0  # seconds
_chat_request_times: collections.deque = collections.deque()

# ---------------------------------------------------------------------------
# Module-level TTL cache for /api/available-models (5-minute window).
# Tuple of (monotonic_timestamp: float, models: List[str]) or None.
# ---------------------------------------------------------------------------
_AVAILABLE_MODELS_TTL = 300.0  # 5 minutes
_available_models_cache: Optional[tuple] = None  # (ts, List[str])


# ---------------------------------------------------------------------------
# Shared aggregation helper — used by /api/alerts and /api/budget-status
# ---------------------------------------------------------------------------

def _fetch_period_user_totals(budgets: Dict) -> Dict[str, Dict[str, Dict[str, float]]]:
    """
    Given a budgets dict, collects every distinct period referenced by any rule,
    fetches SQL-aggregated totals once per distinct period via the TTL cache,
    and returns a mapping: period -> {user_email -> {tokens: float, cost: float}}.

    Uses get_user_model_totals_cached (no LIMIT) so the result is never
    silently undercounted when a period window contains more than 1 000
    daily-grain rows.
    """
    periods_needed: set = set()
    for rule in budgets.values():
        period = rule.get("period", "month") if isinstance(rule, dict) else rule.period
        if period in PERIOD_DAYS:
            periods_needed.add(period)

    period_user_totals: Dict[str, Dict[str, Dict[str, float]]] = {}
    for period in periods_needed:
        days = PERIOD_DAYS[period]
        rows = get_user_model_totals_cached(days=days)
        user_totals: Dict[str, Dict[str, float]] = {}
        for row in rows:
            user = row["user_email"]
            tokens = row["total_tokens"]
            cost = row["estimated_cost_usd"]
            if user not in user_totals:
                user_totals[user] = {"tokens": 0.0, "cost": 0.0}
            user_totals[user]["tokens"] += tokens
            user_totals[user]["cost"] += cost
        period_user_totals[period] = user_totals
    return period_user_totals


# ---------------------------------------------------------------------------
# Auto-sync merge helper — pure function, easily unit-tested.
# ---------------------------------------------------------------------------

def _auto_sync_models(available: List[str], config: Dict[str, bool]) -> tuple:
    """
    Merges newly discovered models into an existing logging config.

    - Models already in config keep their current bool value unchanged.
    - Newly discovered models are added with True (auto-enable per product spec).
    - Models absent from 'available' are left untouched in config.

    Returns:
        (merged_config: dict, newly_added: List[str])
    """
    merged = dict(config)
    newly_added: List[str] = []
    for model in available:
        if model not in merged:
            merged[model] = True
            newly_added.append(model)
    return merged, newly_added


# ---------------------------------------------------------------------------
# Lifespan — replaces deprecated @app.on_event (#19)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    if not settings.AUTH_TOKEN:
        logger.warning(
            "SECURITY WARNING: AUTH_TOKEN is not configured. "
            "The API is running in unauthenticated (dev) mode. "
            "Do NOT expose this server on a public network."
        )

    if settings.APPLY_LOGGING_ON_STARTUP:
        try:
            from backend.logging_client import load_logging_config, apply_vertex_logging

            config = load_logging_config()
            logger.info("Startup: applying Vertex AI logging configuration…")
            result = apply_vertex_logging(config)
            if result.get("all_succeeded"):
                logger.info("Startup: Vertex AI logging config applied successfully.")
            else:
                logger.warning(
                    "Startup: Vertex AI logging config applied with partial failures: %s",
                    result.get("results"),
                )
        except Exception:
            logger.exception("Startup: failed to auto-apply Vertex AI logging config.")
    else:
        logger.info(
            "Startup: APPLY_LOGGING_ON_STARTUP is not set; "
            "skipping automatic Vertex AI logging config apply."
        )

    # --- auto-sync model catalog (UI-managed; never crashes startup) ---
    try:
        from backend.logging_client import (
            load_model_sync_config,
            load_logging_config,
            save_logging_config,
            apply_vertex_logging,
        )

        sync_cfg = load_model_sync_config()
        if sync_cfg.get("auto_sync_on_startup", False):
            logger.info("Auto-sync: discovering available Gemini models…")
            available = list_available_models()
            current_config = load_logging_config()
            merged, newly_added = _auto_sync_models(available, current_config)
            logger.info(
                "Auto-sync: discovered %d models, %d new (enabled): %s",
                len(available),
                len(newly_added),
                newly_added,
            )
            if newly_added:
                save_logging_config(merged)
                result = apply_vertex_logging(merged)
                if result.get("all_succeeded"):
                    logger.info("Auto-sync: all model logging configs applied successfully.")
                else:
                    logger.warning(
                        "Auto-sync: logging config applied with partial failures: %s",
                        result.get("results"),
                    )
        else:
            logger.info("Auto-sync disabled (auto_sync_on_startup=false).")
    except Exception:
        logger.exception(
            "Auto-sync: failed to sync model catalog at startup — continuing."
        )

    yield
    # --- shutdown (nothing to do) ---


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Vertex AI User Token Usage & FinOps Portal",
    lifespan=lifespan,
)

# CORS — restrict to explicit origins; no wildcard+credentials (#P0-3)
_allowed_origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Healthcheck endpoint — unauthenticated, on the app (not api_router)
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    """Liveness check — returns 200 OK with no downstream checks."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class BudgetRule(BaseModel):
    identity: str
    period: Literal["day", "week", "month", "year"]
    type: Literal["token", "money"]
    limit: float = Field(gt=0)
    alert_threshold_percentage: float = Field(ge=1, le=100)
    hard_limit_enabled: bool = False


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(max_length=40)


class ModelPricing(BaseModel):
    """Pricing rates for a single model (finite, >= 0, sane upper bound).

    allow_inf_nan=False is load-bearing: float("inf") >= 0 is True, so a bare
    ge=0 would accept Infinity, which poisons cost math and cannot be encoded
    in response JSON (500 loop until the file is repaired by hand).

    Optional fields:
      input_cost_per_million_gt_200k — >200 K context tier input rate; when
          absent the standard input_cost_per_million applies to all context sizes.
      non_global_multiplier — regional-endpoint premium multiplier (e.g. 1.1);
          when absent a multiplier of 1.0 is assumed (no premium).
    POST /api/pricing serialises with exclude_none=True so absent optional
    fields do not appear as null keys in pricing.json.
    """
    input_cost_per_million: float = Field(ge=0, le=100000, allow_inf_nan=False)
    input_cost_per_million_gt_200k: Optional[float] = Field(
        default=None, ge=0, le=100000, allow_inf_nan=False
    )
    output_cost_per_million: float = Field(ge=0, le=100000, allow_inf_nan=False)
    non_global_multiplier: Optional[float] = Field(
        default=None, ge=1.0, le=3.0, allow_inf_nan=False
    )


class EstimateItem(BaseModel):
    """A single model usage line item within a financial estimate."""
    model: str = Field(min_length=1, max_length=100)
    input_tokens: int = Field(ge=0, le=10_000_000_000_000)  # 10T/term ceiling
    output_tokens: int = Field(ge=0, le=10_000_000_000_000)
    term: Literal["day", "week", "month", "year"]
    note: str = Field(default="", max_length=500)


class Estimate(BaseModel):
    """A named planning estimate composed of one or more model usage items."""
    name: str = Field(min_length=1, max_length=100)
    items: List[EstimateItem] = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def strip_and_reject_blank_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be empty or whitespace-only")
        return stripped


class ModelSyncConfig(BaseModel):
    auto_sync_on_startup: bool


# ---------------------------------------------------------------------------
# API Router — all /api endpoints protected by require_auth (#P0-1)
# ---------------------------------------------------------------------------
api_router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

# Regex for logging-config key validation (#item 8)
_CONFIG_KEY_RE = re.compile(r"^[A-Za-z0-9.\-]{1,100}$")

# Per-process locks serializing read-modify-write cycles on shared config.
# Sync endpoints run in Starlette's threadpool, so concurrent requests in one
# process are real threads; without these, two upserts can load the same
# snapshot and the last save wins (dropping the other's change). Multi-instance
# deployments still need external coordination — documented in the design doc.
_pricing_write_lock = threading.Lock()
_estimates_lock = threading.Lock()
_budgets_lock = threading.Lock()


@api_router.get("/usage")
def get_usage(days: int = 30):
    """
    Returns the token usage logs from BigQuery for the trailing `days` window
    (default 30) along with the active GCP project ID.  'truncated' signals the
    caller when the result set hit the row cap (bq_client.USAGE_ROW_LIMIT).

    `days` is clamped-validated to 1..366 (chart ranges: today/week/month/
    6 months/year). Each distinct window gets its own TTL cache slot.
    """
    try:
        if not 1 <= days <= 366:
            raise HTTPException(
                status_code=400,
                detail="Query parameter 'days' must be between 1 and 366.",
            )
        logs = get_token_usage_logs_cached(days=days)
        return {
            "status": "success",
            "data": logs,
            "project_id": settings.BIGQUERY_PROJECT_ID,
            "truncated": len(logs) >= bq_client.USAGE_ROW_LIMIT,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error fetching usage logs")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.get("/budgets")
def get_budgets():
    """
    Retrieves the budget rules from GCS/local config.
    """
    try:
        budgets = load_budgets()
        return {"status": "success", "data": budgets}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error loading budgets")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.post("/budgets")
def update_budgets(rules: Dict[str, BudgetRule]):
    """
    Replaces the full set of budget rules and saves them to GCS/local file.

    Validation (#15):
    - Rejects an empty rules dict (would wipe all budgets).
    - Requires a 'global_default' key (the frontend always includes one).
    - Rejects rules where the dict key does not match rule.identity.
    """
    try:
        if not rules:
            raise HTTPException(
                status_code=400,
                detail="Refusing to replace all budgets with an empty set.",
            )

        if "global_default" not in rules:
            raise HTTPException(
                status_code=400,
                detail="A 'global_default' budget rule is required.",
            )

        for key, rule in rules.items():
            if key != rule.identity:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Dict key {key!r} does not match rule.identity "
                        f"{rule.identity!r}."
                    ),
                )

        serialized_rules = {identity: rule.model_dump() for identity, rule in rules.items()}

        with _budgets_lock:
            success = save_budgets(serialized_rules)
        if success:
            return {"status": "success", "message": "Budgets saved successfully"}
        raise HTTPException(status_code=500, detail="Internal server error")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error saving budgets")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.get("/alerts")
def get_alerts():
    """
    Dynamically scans all user consumption logs and active budgets to compute
    alerts.

    Period enforcement (#P0-4): each budget rule's period drives the BQ query
    window.  To avoid one query per rule (#22), logs are fetched once per
    *distinct* period across all active rules; the TTL cache further deduplicates
    calls from concurrent /api/usage refreshes.
    """
    try:
        budgets = load_budgets()
        period_user_totals = _fetch_period_user_totals(budgets)

        active_alerts = []

        # Check each per-user budget rule.
        for identity, rule in budgets.items():
            if identity == "global_default":
                continue

            period = rule.get("period", "month") if isinstance(rule, dict) else rule.period
            user_totals = period_user_totals.get(period, {})

            if identity not in user_totals:
                continue

            limit = rule["limit"] if isinstance(rule, dict) else rule.limit
            b_type = rule["type"] if isinstance(rule, dict) else rule.type
            alert_percentage = (
                rule["alert_threshold_percentage"]
                if isinstance(rule, dict)
                else rule.alert_threshold_percentage
            )
            hard_limit_enabled = (
                rule.get("hard_limit_enabled", False)
                if isinstance(rule, dict)
                else rule.hard_limit_enabled
            )

            consumed_tokens = user_totals[identity]["tokens"]
            consumed_money = user_totals[identity]["cost"]
            actual_consumed = consumed_money if b_type == "money" else consumed_tokens

            alert_value = limit * (alert_percentage / 100.0)
            if actual_consumed >= alert_value:
                pct = round((actual_consumed / limit) * 100.0, 1) if limit > 0 else 0.0
                active_alerts.append(
                    {
                        "identity": identity,
                        "metric": b_type,
                        "limit": limit,
                        "consumed": round(actual_consumed, 4),
                        "percentage": pct,
                        "threshold_percentage": alert_percentage,
                        "period": period,
                        "hard_limit_enabled": hard_limit_enabled,
                        "severity": "danger" if actual_consumed >= limit else "warning",
                    }
                )

        # Check users against the global default (only those without custom rules).
        global_default = budgets.get("global_default")
        if global_default:
            g_period = (
                global_default.get("period", "month")
                if isinstance(global_default, dict)
                else global_default.period
            )
            g_user_totals = period_user_totals.get(g_period, {})
            g_limit = (
                global_default["limit"]
                if isinstance(global_default, dict)
                else global_default.limit
            )
            g_type = (
                global_default["type"]
                if isinstance(global_default, dict)
                else global_default.type
            )
            g_alert_percentage = (
                global_default["alert_threshold_percentage"]
                if isinstance(global_default, dict)
                else global_default.alert_threshold_percentage
            )
            g_hard = (
                global_default.get("hard_limit_enabled", False)
                if isinstance(global_default, dict)
                else global_default.hard_limit_enabled
            )

            for user, totals in g_user_totals.items():
                if user in budgets:
                    continue  # has a custom rule

                actual_consumed = totals["cost"] if g_type == "money" else totals["tokens"]
                g_alert_value = g_limit * (g_alert_percentage / 100.0)

                if actual_consumed >= g_alert_value:
                    pct = round((actual_consumed / g_limit) * 100.0, 1) if g_limit > 0 else 0.0
                    active_alerts.append(
                        {
                            "identity": user,
                            "metric": g_type,
                            "limit": g_limit,
                            "consumed": round(actual_consumed, 4),
                            "percentage": pct,
                            "threshold_percentage": g_alert_percentage,
                            "period": g_period,
                            "hard_limit_enabled": g_hard,
                            "severity": "danger" if actual_consumed >= g_limit else "warning",
                            "is_global_default": True,
                        }
                    )

        return {"status": "success", "data": active_alerts}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error computing alerts")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.get("/budget-status")
def get_budget_status():
    """
    Period-aware budget consumption for the UI progress bars.

    Returns one entry per identity:
    - Every user seen in logs for their applicable period window.
    - Users with a custom rule use that rule; others fall back to global_default
      with is_global_default=True.
    - Configured identities (excluding global_default) are always included,
      with consumed=0 if no activity exists in the period.

    Response shape per identity:
      {consumed, limit, type, period, percentage, threshold_percentage,
       hard_limit_enabled, is_global_default}
    """
    try:
        budgets = load_budgets()
        period_user_totals = _fetch_period_user_totals(budgets)

        global_default = budgets.get("global_default")
        result: Dict[str, Any] = {}

        # Collect all users seen across all period windows.
        all_seen_users: set = set()
        for user_totals in period_user_totals.values():
            all_seen_users.update(user_totals.keys())

        for user in all_seen_users:
            if user == "global_default":
                continue

            if user in budgets:
                rule = budgets[user]
                is_global_default = False
            elif global_default is not None:
                rule = global_default
                is_global_default = True
            else:
                continue  # no applicable rule

            period = rule.get("period", "month") if isinstance(rule, dict) else rule.period
            limit = rule["limit"] if isinstance(rule, dict) else rule.limit
            b_type = rule["type"] if isinstance(rule, dict) else rule.type
            threshold_pct = (
                rule["alert_threshold_percentage"]
                if isinstance(rule, dict)
                else rule.alert_threshold_percentage
            )
            hard_limit = (
                rule.get("hard_limit_enabled", False)
                if isinstance(rule, dict)
                else rule.hard_limit_enabled
            )

            totals = period_user_totals.get(period, {}).get(user, {"tokens": 0.0, "cost": 0.0})
            consumed = totals["cost"] if b_type == "money" else totals["tokens"]
            percentage = round((consumed / limit) * 100.0, 1) if limit > 0 else 0.0

            result[user] = {
                "consumed": round(consumed, 4),
                "limit": limit,
                "type": b_type,
                "period": period,
                "percentage": percentage,
                "threshold_percentage": threshold_pct,
                "hard_limit_enabled": hard_limit,
                "is_global_default": is_global_default,
            }

        # Also include configured custom identities with zero usage.
        for identity, rule in budgets.items():
            if identity == "global_default" or identity in result:
                continue
            period = rule.get("period", "month") if isinstance(rule, dict) else rule.period
            limit = rule["limit"] if isinstance(rule, dict) else rule.limit
            b_type = rule["type"] if isinstance(rule, dict) else rule.type
            threshold_pct = (
                rule["alert_threshold_percentage"]
                if isinstance(rule, dict)
                else rule.alert_threshold_percentage
            )
            hard_limit = (
                rule.get("hard_limit_enabled", False)
                if isinstance(rule, dict)
                else rule.hard_limit_enabled
            )
            result[identity] = {
                "consumed": 0.0,
                "limit": limit,
                "type": b_type,
                "period": period,
                "percentage": 0.0,
                "threshold_percentage": threshold_pct,
                "hard_limit_enabled": hard_limit,
                "is_global_default": False,
            }

        return {"status": "success", "data": result}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error computing budget status")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.get("/logging-config")
def get_logging_config():
    """
    Retrieves the payload request/response logging settings for Vertex AI models.
    """
    try:
        from backend.logging_client import load_logging_config

        config = load_logging_config()
        return {"status": "success", "data": config}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error loading logging config")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.post("/logging-config")
def update_logging_config(config: Dict[str, Any]):
    """
    Saves and applies payload request/response logging settings to Vertex AI models.

    Key validation: keys must match [A-Za-z0-9.-]{1,100}; values must be bool.
    Rejects an empty config dict (400) and raises 500 if the save fails.
    Returns HTTP 200 with status='partial_failure' if any model apply fails so
    the frontend can surface per-model results.
    """
    try:
        if not config:
            raise HTTPException(
                status_code=400,
                detail="empty logging configuration",
            )

        for key, value in config.items():
            if not _CONFIG_KEY_RE.match(key):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid config key {key!r}. Keys must contain only "
                        "letters, digits, dots, or hyphens and be at most 100 "
                        "characters."
                    ),
                )
            if not isinstance(value, bool):
                raise HTTPException(
                    status_code=400,
                    detail=f"Config value for key {key!r} must be a boolean.",
                )

        from backend.logging_client import save_logging_config, apply_vertex_logging

        saved = save_logging_config(config)
        if not saved:
            raise HTTPException(status_code=500, detail="Internal server error")

        result = apply_vertex_logging(config)

        if result.get("all_succeeded"):
            return {
                "status": "success",
                "message": "Logging configurations successfully saved and applied.",
                "results": result.get("results", {}),
            }
        # HTTP 200 with partial_failure so the frontend can parse; it handles
        # display of per-model errors.
        return {
            "status": "partial_failure",
            "message": "Logging configurations saved but some models failed to update.",
            "results": result.get("results", {}),
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error updating logging config")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.post("/chat")
def chat_copilot(request: ChatRequest):
    """
    Sends chat messages to the FinOps Assistant copilot.

    Validates: max 40 messages (422), max 8000 chars per message (422).
    Returns 429 when the per-process sliding-window rate limit is exceeded.
    Returns 503 when the Vertex AI SDK is unavailable or errors.
    """
    # Sliding-window rate limit check (must run before try so 429 is not swallowed)
    now = time.monotonic()
    while _chat_request_times and _chat_request_times[0] <= now - _CHAT_RATE_LIMIT_WINDOW:
        _chat_request_times.popleft()
    if len(_chat_request_times) >= _CHAT_RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: max 10 assistant requests per minute.",
        )
    _chat_request_times.append(now)

    try:
        messages_dict = [
            {"role": msg.role, "content": msg.content} for msg in request.messages
        ]
        response_text = query_finops_assistant(messages_dict)
        return {"status": "success", "reply": response_text}
    except HTTPException:
        raise
    except AssistantUnavailableError:
        raise HTTPException(
            status_code=503,
            detail=(
                "FinOps assistant is unavailable "
                "(Vertex AI SDK not configured or errored)."
            ),
        )
    except Exception:
        logger.exception("Error in FinOps assistant chat")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Pricing endpoints
# ---------------------------------------------------------------------------

@api_router.get("/pricing")
def get_pricing():
    """
    Returns the current model pricing configuration from the in-memory
    PRICING dict (populated at startup from pricing.json).
    """
    try:
        return {"status": "success", "data": dict(bq_client.PRICING)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error fetching pricing")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.post("/pricing")
def update_pricing(pricing: Dict[str, ModelPricing]):
    """
    Replaces the full set of model pricing and writes atomically to pricing.json,
    then reloads the in-memory PRICING dict and clears both TTL caches.

    Validation:
    - Rejects an empty dict (400).
    - Rejects keys that do not match [A-Za-z0-9.-]{1,100} (400).
    - ModelPricing Pydantic model enforces both rate fields >= 0 (422).
    """
    try:
        if not pricing:
            raise HTTPException(
                status_code=400,
                detail="Pricing dict must not be empty.",
            )

        for key in pricing:
            if not _CONFIG_KEY_RE.match(key):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid model key {key!r}. Keys must contain only "
                        "letters, digits, dots, or hyphens and be at most 100 "
                        "characters."
                    ),
                )

        serialized = {k: v.model_dump(exclude_none=True) for k, v in pricing.items()}

        # Serialize writers (per process), write to a UNIQUE temp file in the
        # same directory, then os.replace — concurrent POSTs can't tear the
        # temp file or interleave the final rename. allow_nan=False is a
        # belt-and-braces guard against non-finite values reaching disk.
        with _pricing_write_lock:
            fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(bq_client.PRICING_PATH), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(serialized, f, indent=2, allow_nan=False)
                os.replace(tmp_path, bq_client.PRICING_PATH)
            except BaseException:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            # Reload in-memory dict and clear stale caches
            bq_client.reload_pricing()

        return {"status": "success", "data": serialized}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error updating pricing")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Estimates endpoints
# ---------------------------------------------------------------------------

@api_router.get("/estimates")
def get_estimates():
    """
    Returns all saved financial planning estimates.
    """
    try:
        data = load_estimates()
        return {"status": "success", "data": data}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error loading estimates")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.post("/estimates")
def upsert_estimate(estimate: Estimate):
    """
    Creates or updates a single named estimate.

    The server stamps 'updated_at' (UTC ISO 8601) on the stored record.
    Validation via the Estimate model enforces field lengths, token counts,
    term literals, note length, and a non-blank name after stripping whitespace.
    """
    try:
        with _estimates_lock:
            data = load_estimates()
            stored = {
                "name": estimate.name,
                "items": [item.model_dump() for item in estimate.items],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            data[estimate.name] = stored
            success = save_estimates(data)
        if not success:
            raise HTTPException(status_code=500, detail="Internal server error")
        return {"status": "success", "data": stored}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error saving estimate")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.delete("/estimates/{name}")
def delete_estimate(name: str):
    """
    Removes a named estimate.

    Returns 404 if the name is not found. The name path parameter may contain
    spaces when URL-encoded (%20); FastAPI decodes it automatically.
    """
    try:
        with _estimates_lock:
            data = load_estimates()
            if name not in data:
                raise HTTPException(
                    status_code=404,
                    detail=f"Estimate {name!r} not found.",
                )
            del data[name]
            success = save_estimates(data)
        if not success:
            raise HTTPException(status_code=500, detail="Internal server error")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error deleting estimate")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Model catalog / auto-sync endpoints
# ---------------------------------------------------------------------------

@api_router.get("/available-models")
def get_available_models(force: bool = False):
    """
    Returns the list of available Gemini models from the Vertex AI API.

    Results are cached for 5 minutes per process.  Pass ?force=true to bypass
    the cache (e.g. the UI refresh button).

    Returns 503 on catalog/API failures so the frontend can surface a clear
    error without exposing internal details.
    """
    global _available_models_cache

    now = time.monotonic()
    if not force and _available_models_cache is not None:
        ts, cached_models = _available_models_cache
        if now - ts < _AVAILABLE_MODELS_TTL:
            return {"status": "success", "data": {"models": cached_models}}

    try:
        models = list_available_models()
        _available_models_cache = (now, models)
        return {"status": "success", "data": {"models": models}}
    except ModelCatalogError as exc:
        logger.warning("Model catalog unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Model catalog unavailable — check Vertex AI credentials/config",
        )
    except Exception:
        logger.exception("Unexpected error fetching available models")
        raise HTTPException(
            status_code=503,
            detail="Model catalog unavailable — check Vertex AI credentials/config",
        )


@api_router.get("/model-sync-config")
def get_model_sync_config():
    """Returns the persisted model-sync settings."""
    try:
        from backend.logging_client import load_model_sync_config

        cfg = load_model_sync_config()
        return {"status": "success", "data": cfg}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error loading model-sync config")
        raise HTTPException(status_code=500, detail="Internal server error")


@api_router.post("/model-sync-config")
def update_model_sync_config(body: ModelSyncConfig):
    """Saves the model-sync settings and returns the saved data."""
    try:
        from backend.logging_client import save_model_sync_config

        cfg = body.model_dump()
        saved = save_model_sync_config(cfg)
        if not saved:
            raise HTTPException(status_code=500, detail="Internal server error")
        return {"status": "success", "data": cfg}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error saving model-sync config")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Cost anomaly detection — pure helper + endpoint
# ---------------------------------------------------------------------------

def _compute_cost_anomalies(
    today_rows: list,
    baseline_rows: list,
    baseline_days: int,
    threshold: float = 3.0,
) -> dict:
    """
    Compute overall and per-user cost anomalies.

    Parameters
    ----------
    today_rows     : get_user_model_totals_cached(days=1)
    baseline_rows  : get_user_model_totals_cached(days=baseline_days+1)
                     (covers today + the baseline window so today is subtracted out)
    baseline_days  : number of days in the baseline window (excludes today)
    threshold      : spike ratio that triggers an anomaly flag (default 3.0×)

    A $0.01 cost floor is required to flag an anomaly so that trivially small
    amounts (e.g. a single free-tier call that happens to be 10× a zero baseline)
    do not generate noisy false-positive alerts.

    Returns the full response data dict (excludes generated_at_utc / threshold_ratio
    so callers can stamp those).
    """
    # Aggregate today cost
    today_cost = sum(r["estimated_cost_usd"] for r in today_rows)

    # Aggregate baseline: full window cost minus today cost = pure baseline days
    baseline_window_cost = sum(r["estimated_cost_usd"] for r in baseline_rows)
    baseline_total = max(baseline_window_cost - today_cost, 0.0)
    baseline_daily_avg = baseline_total / baseline_days if baseline_days > 0 else 0.0

    # Overall ratio / anomaly
    if baseline_daily_avg > 0:
        ratio = today_cost / baseline_daily_avg
    else:
        ratio = None

    _COST_FLOOR = 0.01  # Ignore sub-cent spikes to avoid trivial false positives
    # A zero baseline means there is NO history to divide by, so ratio is
    # undefined — but spend appearing where there was none is exactly the case
    # anomaly detection exists to catch (a brand-new principal or workload).
    # It needs a higher bar than the ratio path because there is no trend to
    # corroborate it, hence a separate, larger floor.
    _NEW_SPEND_FLOOR = 1.00
    is_anomaly = (
        (
            baseline_daily_avg > 0
            and ratio is not None
            and ratio >= threshold
            and today_cost >= _COST_FLOOR
        )
        or (baseline_daily_avg <= 0 and today_cost >= _NEW_SPEND_FLOOR)
    )
    # Distinguishes the two detections for callers/UI copy.
    anomaly_reason = (
        None if not is_anomaly
        else ("new_spend" if baseline_daily_avg <= 0 else "spike")
    )

    # Per-user breakdown (only users that appear in today's rows)
    today_by_user: dict = {}
    for r in today_rows:
        u = r["user_email"]
        today_by_user[u] = today_by_user.get(u, 0.0) + r["estimated_cost_usd"]

    baseline_by_user: dict = {}
    for r in baseline_rows:
        u = r["user_email"]
        baseline_by_user[u] = baseline_by_user.get(u, 0.0) + r["estimated_cost_usd"]

    per_user = []
    for user, u_today_cost in today_by_user.items():
        u_baseline_window = baseline_by_user.get(user, 0.0)
        u_baseline_total = max(u_baseline_window - u_today_cost, 0.0)
        u_baseline_daily = u_baseline_total / baseline_days if baseline_days > 0 else 0.0

        if u_baseline_daily > 0:
            u_ratio: Optional[float] = u_today_cost / u_baseline_daily
        else:
            u_ratio = None

        u_anomaly = (
            (
                u_baseline_daily > 0
                and u_ratio is not None
                and u_ratio >= threshold
                and u_today_cost >= _COST_FLOOR
            )
            # Same new-spend rule per principal: someone with no baseline who
            # starts spending materially today is flagged even though no ratio
            # can be computed.
            or (u_baseline_daily <= 0 and u_today_cost >= _NEW_SPEND_FLOOR)
        )
        u_reason = (
            None if not u_anomaly
            else ("new_spend" if u_baseline_daily <= 0 else "spike")
        )
        per_user.append({
            "user_email": user,
            "today_cost_usd": round(u_today_cost, 4),
            "baseline_daily_avg_usd": round(u_baseline_daily, 4),
            "ratio": round(u_ratio, 2) if u_ratio is not None else None,
            "is_anomaly": u_anomaly,
            "reason": u_reason,
        })

    per_user.sort(key=lambda x: x["today_cost_usd"], reverse=True)

    return {
        "baseline_days": baseline_days,
        "threshold_ratio": threshold,
        "today_cost_usd": round(today_cost, 4),
        "baseline_daily_avg_usd": round(baseline_daily_avg, 4),
        "ratio": round(ratio, 2) if ratio is not None else None,
        "is_anomaly": is_anomaly,
        "reason": anomaly_reason,
        "per_user": per_user,
    }


@api_router.get("/cost-anomalies")
def get_cost_anomalies(baseline_days: int = 7):
    """
    Detects whether today's spend is anomalously high compared to recent history.

    ?baseline_days (2..90, default 7): number of past days to average as the
    baseline.  Returns 400 if outside that range.

    The baseline daily average intentionally EXCLUDES today so a big day doesn't
    inflate the baseline and suppress its own alert.
    """
    try:
        if not 2 <= baseline_days <= 90:
            raise HTTPException(
                status_code=400,
                detail="baseline_days must be between 2 and 90.",
            )
        today_rows = get_user_model_totals_cached(days=1)
        baseline_rows = get_user_model_totals_cached(days=baseline_days + 1)
        data = _compute_cost_anomalies(today_rows, baseline_rows, baseline_days)
        data["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        return {"status": "success", "data": data}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error computing cost anomalies")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Config-health detection — pure helper + endpoint
# ---------------------------------------------------------------------------

def _compute_config_health(
    budgets: dict,
    logging_config: dict,
    usage_rows: list,
) -> dict:
    """
    Detect stale/misconfigured items across budgets, logging, and pricing.

    unused_budgets
        Budget rules (excluding "global_default") whose identity has ZERO usage
        rows in the supplied 30-day window.  Useful for identifying rules that
        were configured for users who have since stopped using the platform.

    logged_unused_models
        Models that have logging enabled (value True in logging_config) but
        appear in NO usage rows.  These may be consuming Vertex AI quota for
        logging that yields no data.

    used_unpriced_models
        Models that appear in usage rows whose pricing_match == "default"
        (i.e. unpriced — every call contributes $0 to cost budgets).

    unlogged_used_models
        Models appearing in usage rows that are NOT enabled (False or absent)
        in logging_config.  These are being called but payload logging is off,
        so future usage detail may go untracked.
    """
    # Build sets of identities/models from usage
    used_identities: set = {r["user_email"] for r in usage_rows}
    used_models: set = {r["model_name"] for r in usage_rows}

    # unused_budgets: configured (non-global_default) identities with no usage
    unused_budgets = []
    for identity, rule in budgets.items():
        if identity == "global_default":
            continue
        if identity not in used_identities:
            period = rule.get("period", "month") if isinstance(rule, dict) else rule.period
            b_type = rule.get("type", "token") if isinstance(rule, dict) else rule.type
            limit = rule.get("limit") if isinstance(rule, dict) else rule.limit
            unused_budgets.append({
                "identity": identity,
                "period": period,
                "type": b_type,
                "limit": limit,
            })
    unused_budgets.sort(key=lambda x: x["identity"])

    # logged_unused_models: logging=True but no usage
    logged_unused_models = sorted(
        model for model, enabled in logging_config.items()
        if enabled and model not in used_models
    )

    # used_unpriced_models: models in usage with pricing_match == "default"
    used_unpriced_models = sorted(
        {r["model_name"] for r in usage_rows if r.get("pricing_match") == "default"}
    )

    # unlogged_used_models: models in usage not enabled in logging_config
    unlogged_used_models = sorted(
        model for model in used_models
        if not logging_config.get(model, False)
    )

    return {
        "unused_budgets": unused_budgets,
        "logged_unused_models": logged_unused_models,
        "used_unpriced_models": used_unpriced_models,
        "unlogged_used_models": unlogged_used_models,
        "usage_window_days": 30,
    }


@api_router.get("/config-health")
def get_config_health():
    """
    Detects stale/misconfigured items across budgets, logging, and model pricing.

    Uses a 30-day usage window.  Returns four lists:
    - unused_budgets: configured non-global identities with no recent usage
    - logged_unused_models: logging-enabled models with no recent usage
    - used_unpriced_models: models in usage whose cost is $0 (unpriced)
    - unlogged_used_models: models in usage with payload logging disabled
    """
    try:
        from backend.logging_client import load_logging_config

        budgets = load_budgets()
        logging_config = load_logging_config()
        usage_rows = get_user_model_totals_cached(days=30)
        data = _compute_config_health(budgets, logging_config, usage_rows)
        data["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        return {"status": "success", "data": data}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error computing config health")
        raise HTTPException(status_code=500, detail="Internal server error")


# Register all /api routes on the app.
app.include_router(api_router)

# ---------------------------------------------------------------------------
# Static frontend mount (production)
# ---------------------------------------------------------------------------
static_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_path):
    app.mount("/", StaticFiles(directory=static_path, html=True), name="static")
else:

    @app.get("/")
    def read_root():
        return {
            "message": (
                "FastAPI is running in local development mode. "
                "Build the frontend to serve React statically."
            )
        }
