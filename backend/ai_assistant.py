import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any
from backend.config import settings
from backend.bq_client import get_user_model_totals_cached
from backend.gcs_client import load_budgets

logger = logging.getLogger(__name__)

# Robust import of google-genai SDK
try:
    from google import genai
    from google.genai import types
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False

from backend.constants import PERIOD_DAYS as _PERIOD_DAYS


class AssistantUnavailableError(Exception):
    """Raised when the FinOps assistant cannot process a request."""
    pass


# Time windows always injected into the assistant's context, so questions like
# "who used the most tokens in the last 24 hours" resolve against real
# window-scoped data instead of the model guessing from a single 30-day blob.
STANDARD_WINDOWS: Dict[str, int] = {
    "last_24_hours": 1,
    "last_7_days": 7,
    "last_30_days": 30,
}


def _aggregate_by(rows: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    """Sums per-(user, model, tier, region) rows by any single dimension."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        k = row.get(key) or "unknown"
        if k not in out:
            out[k] = {"tokens": 0, "cost": 0.0, "calls": 0}
        out[k]["tokens"] += row["total_tokens"]
        out[k]["cost"] += row["estimated_cost_usd"]
        out[k]["calls"] += row["call_count"]
    return out


def _aggregate_users(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Sums per-(user, model, tier, region) rows into per-user totals."""
    return _aggregate_by(rows, "user_email")


def _fmt_breakdown(agg: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Token-sorted, rounded view of an aggregation for prompt injection."""
    return {
        k: {"tokens": m["tokens"], "cost_usd": round(m["cost"], 4), "calls": m["calls"]}
        for k, m in sorted(agg.items(), key=lambda kv: kv[1]["tokens"], reverse=True)
    }


def query_finops_assistant(messages: List[Dict[str, str]]) -> str:
    """
    Answers user questions about FinOps, budgets, and token usage.
    Injects current metrics and budgets into the LLM context.

    Raises:
        AssistantUnavailableError: if the SDK is not available, project is not
            configured, or the generation call fails.
    """
    if not _SDK_AVAILABLE or not settings.BIGQUERY_PROJECT_ID:
        raise AssistantUnavailableError(
            "google-genai SDK not installed or BIGQUERY_PROJECT_ID unset"
        )

    # Memoize per-days fetches so a window shared by STANDARD_WINDOWS and a
    # budget period is only queried once per turn (the client also has a 30s
    # TTL cache behind this).
    _rows_by_days: Dict[int, List[Dict[str, Any]]] = {}

    def rows_for(days: int) -> List[Dict[str, Any]]:
        if days not in _rows_by_days:
            _rows_by_days[days] = get_user_model_totals_cached(days=days)
        return _rows_by_days[days]

    # 1. Fetch 30-day SQL-aggregated totals for the headline summary and
    #    per-model breakdown.  Rows are per (user, model, tier, region);
    #    the loops below SUM across them, so finer grain is transparent.
    rows_30 = rows_for(30)
    budgets = load_budgets()

    # Build 30-day user and model summaries
    user_totals_30: Dict[str, Any] = {}
    model_totals: Dict[str, Any] = {}
    total_spend = 0.0
    total_tokens = 0
    total_calls = 0

    for row in rows_30:
        user = row["user_email"]
        model = row["model_name"]
        tokens = row["total_tokens"]
        cost = row["estimated_cost_usd"]
        calls = row["call_count"]

        total_spend += cost
        total_tokens += tokens
        total_calls += calls

        if user not in user_totals_30:
            user_totals_30[user] = {"tokens": 0, "cost": 0.0, "calls": 0}
        user_totals_30[user]["tokens"] += tokens
        user_totals_30[user]["cost"] += cost
        user_totals_30[user]["calls"] += calls

        if model not in model_totals:
            model_totals[model] = {"tokens": 0, "cost": 0.0, "calls": 0}
        model_totals[model]["tokens"] += tokens
        model_totals[model]["cost"] += cost
        model_totals[model]["calls"] += calls

    # 2. Fetch period-specific totals for accurate budget % calculation.
    #    Only fetch for the distinct periods that actually appear in budgets.
    distinct_periods: set = set()
    for user in user_totals_30:
        budget_info = budgets.get(user) or budgets.get("global_default")
        if budget_info is not None:
            p = budget_info.get("period", "month") if isinstance(budget_info, dict) else budget_info.period
            if p in _PERIOD_DAYS:
                distinct_periods.add(p)

    period_user_totals: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for period in distinct_periods:
        period_user_totals[period] = _aggregate_users(rows_for(_PERIOD_DAYS[period]))

    # 3. Window-scoped usage so relative-time questions ("last 24 hours",
    #    "this week") are answered from real window data. Windows with no
    #    activity are still emitted with explicit zeros so the model states
    #    "no usage" instead of borrowing another window's numbers.
    usage_by_window: Dict[str, Any] = {}
    for label, days in STANDARD_WINDOWS.items():
        window_rows = rows_for(days)
        per_user = _aggregate_users(window_rows)
        usage_by_window[label] = {
            "window_days": days,
            "total_tokens": sum(u["tokens"] for u in per_user.values()),
            "total_cost_usd": round(sum(u["cost"] for u in per_user.values()), 4),
            "total_calls": sum(u["calls"] for u in per_user.values()),
            "active_users": len(per_user),
            "per_user": _fmt_breakdown(per_user),
            # Per-model and per-region within the SAME window, so questions like
            # "which models/regions were used in the last 24 hours" are answered
            # from window data rather than the 30-day model table.
            "per_model": _fmt_breakdown(_aggregate_by(window_rows, "model_name")),
            "per_region": _fmt_breakdown(_aggregate_by(window_rows, "region")),
        }

    now_utc = datetime.now(timezone.utc)

    # Format summarizations for the context
    context_data: Dict[str, Any] = {
        "report_generated_at_utc": now_utc.isoformat(timespec="seconds"),
        "usage_by_window": usage_by_window,
        "summary_last_30_days": {
            "total_spend_usd": round(total_spend, 4),
            "total_tokens_consumed": total_tokens,
            "total_calls": total_calls,
            "unique_users_tracked": len(user_totals_30),
        },
        "per_user_consumption_and_budgets": {},
        "per_model_consumption_last_30_days": model_totals,
    }

    # Match consumption with actual set budgets; guard against missing global_default
    for user, metrics_30 in user_totals_30.items():
        budget_info = budgets.get(user) or budgets.get("global_default")

        if budget_info is None:
            context_data["per_user_consumption_and_budgets"][user] = {
                "total_calls": metrics_30["calls"],
                "tokens_consumed": metrics_30["tokens"],
                "cost_consumed_usd": round(metrics_30["cost"], 4),
                "budget_configured": "no budget configured",
                "actual_consumption_percentage_of_budget": "N/A",
            }
            continue

        limit = budget_info["limit"] if isinstance(budget_info, dict) else budget_info.limit
        b_type = budget_info["type"] if isinstance(budget_info, dict) else budget_info.type
        period = budget_info["period"] if isinstance(budget_info, dict) else budget_info.period
        alert_pct = (
            budget_info["alert_threshold_percentage"]
            if isinstance(budget_info, dict)
            else budget_info.alert_threshold_percentage
        )

        # Use period-specific consumption for budget % accuracy
        period_metrics = period_user_totals.get(period, {}).get(
            user, {"tokens": 0, "cost": 0.0, "calls": 0}
        )
        actual = period_metrics["cost"] if b_type == "money" else period_metrics["tokens"]
        pct_consumed = round((actual / limit) * 100, 2) if limit and limit > 0 else 0

        context_data["per_user_consumption_and_budgets"][user] = {
            "total_calls": period_metrics["calls"],
            "tokens_consumed": period_metrics["tokens"],
            "cost_consumed_usd": round(period_metrics["cost"], 4),
            "budget_configured": {
                "limit": limit,
                "type": b_type,
                "period": period,
                "alert_at_percentage": alert_pct,
            },
            "actual_consumption_percentage_of_budget": f"{pct_consumed}%",
        }

    # Cap injected context to ~30000 chars: truncate to top-50 entries by cost if needed
    if len(json.dumps(context_data)) > 30000:
        top_users = dict(
            sorted(
                context_data["per_user_consumption_and_budgets"].items(),
                key=lambda kv: kv[1].get("cost_consumed_usd", 0) if isinstance(kv[1], dict) else 0,
                reverse=True,
            )[:50]
        )
        top_models = dict(
            sorted(
                context_data["per_model_consumption_last_30_days"].items(),
                key=lambda kv: kv[1].get("cost", 0),
                reverse=True,
            )[:50]
        )
        context_data["per_user_consumption_and_budgets"] = top_users
        context_data["per_model_consumption_last_30_days"] = top_models
        # Window per_user maps can also be large — keep the top 50 by tokens.
        for label, w in context_data["usage_by_window"].items():
            if len(w.get("per_user", {})) > 50:
                w["per_user"] = dict(list(w["per_user"].items())[:50])  # already token-sorted
        context_data["_truncated"] = True
        context_data["_truncation_note"] = (
            "Breakdowns truncated to top 50 entries by cost due to context size limits."
        )

    # Injected System Prompt guiding the LLM
    system_prompt = f"""You are the "Gemini FinOps Assistant" — an elite Google Cloud FinOps auditor specializing in tracking user-level token usage and budget configurations for direct Vertex AI SDK calls.

The current date and time is {now_utc.strftime('%Y-%m-%d %H:%M')} UTC. Resolve every
relative time reference ("today", "last 24 hours", "this week", "recently") against that clock.

Below is the dynamic, real-time consumption and budget telemetry of the enterprise:
{json.dumps(context_data, indent=2)}

TIME WINDOWS — read carefully:
- `usage_by_window` holds SEPARATE totals for each available window: {", ".join(STANDARD_WINDOWS)}.
  Answer a time-scoped question ONLY from the matching window.
- NEVER substitute one window's numbers for another. If a window's totals are zero,
  say plainly that there was no recorded usage in that window — do not fall back to a
  wider window's figures and do not imply the usage happened recently.
- If the question targets a window that is not available (e.g. "the last hour",
  "yesterday only", "the last 90 days"), say so and answer from the nearest available
  window, stating explicitly which window your numbers cover.
- Windows are computed from UTC day buckets, so a window may include part of the
  preceding UTC day; describe short windows as approximate.
- Telemetry lags reality: token rows land ~1-2 minutes after a call and user attribution
  can take 5-15 minutes. Very recent calls may not appear yet — mention this when a
  short window looks empty.
- `per_user_consumption_and_budgets` is scoped to each user's own BUDGET PERIOD (not to
  the windows above); use it for budget/percentage questions only.
- `per_model_consumption_last_30_days` and `summary_last_30_days` are 30-day figures;
  always label them as such.

Guidelines:
1. Provide extremely concise, professional, and clear answers grounded directly in the provided telemetry.
2. Avoid generic answers. Quote precise numbers, dollars, token counts, and emails of users when asked — and state the time window each figure covers.
3. If a user is near or exceeding their budget limit, call them out as an alert or risk and suggest optimizations (e.g. suggest switching expensive pro-class workloads to a flash-class model).
4. If asked to write a budget rule or adjust settings, explain that they can easily do so in the "Budget Manager" tab.
5. `unattributed@unknown` is not a real principal — it is usage the pipeline could not
   match to a caller identity (calls made before audit-log export was configured, or
   rows missing latency metadata). Explain that rather than treating it as a person.
6. Models priced at $0 with no rate configured are UNPRICED, not free; their tokens are
   real but cost cannot be computed until rates are added in the "Pricing & Planner" tab.
"""

    try:
        # Initialize Vertex AI client using the new google-genai library
        client = genai.Client(
            vertexai=True,
            project=settings.BIGQUERY_PROJECT_ID,
            location=settings.VERTEX_REGION
        )

        # Format chat history/messages for GenAI SDK
        # Since the messages list format is [{"role": "user"/"model", "content": "..."}]
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        # No max_output_tokens: an explicit cap previously truncated multi-part
        # answers mid-sentence, and output is billed only for what is actually
        # generated, so limiting it buys nothing. The model's own ceiling
        # applies; the MAX_TOKENS check below still flags that case.
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.2,
        )

        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=config
        )
        text = response.text or ""

        # If the model still hit the ceiling, say so rather than presenting a
        # sentence that simply stops. Defensive: SDK shapes vary, so any
        # failure to read the finish reason just skips the notice.
        try:
            finish = str(getattr(response.candidates[0], "finish_reason", "") or "")
            if "MAX_TOKENS" in finish.upper():
                logger.warning("Assistant reply hit max_output_tokens; answer truncated.")
                text += (
                    "\n\n_⚠️ This answer was cut off at the response length limit. "
                    "Ask for a specific part (e.g. just the regions) for the rest._"
                )
        except Exception:  # noqa: BLE001 — never fail a good answer over metadata
            pass

        return text

    except Exception as e:
        logger.exception("Vertex AI SDK generation error: %s", e)
        raise AssistantUnavailableError(
            "The FinOps assistant is temporarily unavailable. Please try again later."
        )
