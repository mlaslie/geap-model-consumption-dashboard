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
