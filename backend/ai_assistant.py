import json
import logging
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

    # 1. Fetch 30-day SQL-aggregated totals for the headline summary and
    #    per-model breakdown.  Rows are per (user, model, tier, region);
    #    the loops below SUM across them, so finer grain is transparent.
    rows_30 = get_user_model_totals_cached(days=30)
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
        days = _PERIOD_DAYS[period]
        rows = get_user_model_totals_cached(days=days)
        pu: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            u = row["user_email"]
            if u not in pu:
                pu[u] = {"tokens": 0, "cost": 0.0, "calls": 0}
            pu[u]["tokens"] += row["total_tokens"]
            pu[u]["cost"] += row["estimated_cost_usd"]
            pu[u]["calls"] += row["call_count"]
        period_user_totals[period] = pu

    # Format summarizations for the context
    context_data: Dict[str, Any] = {
        "summary": {
            "total_spend_usd": round(total_spend, 4),
            "total_tokens_consumed": total_tokens,
            "total_calls": total_calls,
            "unique_users_tracked": len(user_totals_30),
        },
        "per_user_consumption_and_budgets": {},
        "per_model_consumption": model_totals,
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
                context_data["per_model_consumption"].items(),
                key=lambda kv: kv[1].get("cost", 0),
                reverse=True,
            )[:50]
        )
        context_data["per_user_consumption_and_budgets"] = top_users
        context_data["per_model_consumption"] = top_models
        context_data["_truncated"] = True
        context_data["_truncation_note"] = (
            "Breakdowns truncated to top 50 entries by cost due to context size limits."
        )

    # Injected System Prompt guiding the LLM
    system_prompt = f"""You are the "Gemini FinOps Assistant" — an elite Google Cloud FinOps auditor specializing in tracking user-level token usage and budget configurations for direct Vertex AI SDK calls.

Below is the dynamic, real-time consumption and budget telemetry of the enterprise:
{json.dumps(context_data, indent=2)}

Guidelines:
1. Provide extremely concise, professional, and clear answers grounded directly in the provided telemetry.
2. Avoid generic answers. Quote precise numbers, dollars, token counts, and emails of users when asked.
3. If a user is near or exceeding their budget limit, call them out as an alert or risk and suggest optimizations (e.g. suggest switching expensive pro-class workloads to a flash-class model).
4. If asked to write a budget rule or adjust settings, explain that they can easily do so in the "Budget Manager" tab.
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

        # Request generation
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.2,
            max_output_tokens=800
        )

        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=config
        )
        return response.text

    except Exception as e:
        logger.exception("Vertex AI SDK generation error: %s", e)
        raise AssistantUnavailableError(
            "The FinOps assistant is temporarily unavailable. Please try again later."
        )
