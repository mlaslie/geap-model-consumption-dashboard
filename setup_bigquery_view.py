import os
import sys
from google.cloud import bigquery
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID") or os.getenv("PROJECT_ID")
if not PROJECT_ID:
    print(
        "ERROR: Neither BIGQUERY_PROJECT_ID nor PROJECT_ID is set. "
        "Export one of these variables before running this script.",
        file=sys.stderr
    )
    sys.exit(1)
DATASET_ID = os.getenv("BIGQUERY_DATASET", "vertex_ai_user_telemetry")
VIEW_ID = os.getenv("BIGQUERY_VIEW", "user_token_chargebacks")

# Fallback identity used when the audit-log join table is unavailable.
# Set FALLBACK_IDENTITY in the environment for new deployments; the default
# 'unattributed@unknown' avoids hardcoding any real account.
# Validated before f-string interpolation into the view DDL (quotes/backslashes
# in the env value would break or alter the SQL).
import re as _re
FALLBACK_IDENTITY = os.getenv("FALLBACK_IDENTITY", "unattributed@unknown")
if not _re.match(r"^[A-Za-z0-9@._\-]+$", FALLBACK_IDENTITY):
    print(f"ERROR: FALLBACK_IDENTITY {FALLBACK_IDENTITY!r} contains unsafe characters "
          "(allowed: letters, digits, @ . _ -).", file=sys.stderr)
    sys.exit(1)

client = bigquery.Client(project=PROJECT_ID)

# NOTE: The full view aggregates per (user_email, model_name, pricing_tier,
# region, project_id, DAY). Each row is a daily summary for a
# user+model+tier+region+project combination — NOT an individual API call and
# NOT an all-time aggregate. The daily grain means that WHERE call_timestamp >=
# <window> filters in bq_client correctly return per-period sums rather than
# all-time totals, which is required for period-aware budget alerts.
# call_count provides the true number of distinct calls (request_ids) per day.
#
# CONTEXT-TIER PRICING (pricing_tier column):
# pricing_tier is computed PER REQUEST from the input token count extracted from
# the response payload BEFORE daily aggregation. This ensures the correct per-
# request tier (le200k or gt200k) is preserved through the daily rollup so that
# bq_client can apply the right rate when computing estimated cost.
#
# REGION PRICING (region column):
# region is extracted from the audit-log resourceName path component
# 'locations/<location>/'. When the location is 'global' the value is 'global';
# any other location is 'regional'. Unmatched rows (no audit join) default to
# 'global' — the cheaper rate — giving conservative under-billing rather than
# over-billing. This is noted in a SQL comment on the COALESCE expression.
#
# ATTRIBUTION DESIGN (time-window correlation, not a key join):
# The Data Access audit log carries the caller identity (principalEmail) but its
# `metadata` field is EMPTY for GenerateContent calls and `operation.id` does NOT
# match request_response_logging.request_id — there is no shared key between the
# two streams. Instead, each payload row's call START time is reconstructed as
# logging_time - metadata.request_latency (milliseconds), which lands within
# ~100ms of the audit entry's timestamp for the same model. Each payload row is
# matched ONE-TO-ONE to audit entries for the same model within a ±10s window
# using mutual-nearest assignment (a payload row and an audit entry pair up only
# when each is the other's closest candidate). Rows with no exclusive match —
# including all rows whose request_latency is missing (their true start time is
# unknowable) — are attributed to FALLBACK_IDENTITY rather than guessed.
# Concurrent same-model calls from different users therefore cannot double-book
# one identity; the loser of a contested match lands in the fallback bucket.
#
# KNOWN LIMITS (documented, not bugs):
# - The view re-correlates full history on every query (the range join cannot be
#   pruned by the outer call_timestamp filter). Fine for demo/small datasets;
#   materialize to a scheduled table before scaling to large log volumes.
# - Day buckets are UTC calendar days and the API's period windows include the
#   whole day when any of its calls fall in the window (day-bucket semantics).
# - Model matching uses the trailing path segment; version-suffix skew between
#   audit resourceName and payload model falls back to unattributed.
#
# Re-run this script whenever the view definition changes. Re-running is REQUIRED
# for this version which adds the pricing_tier and region columns.
full_view_query = f"""
CREATE OR REPLACE VIEW `{PROJECT_ID}.{DATASET_ID}.{VIEW_ID}` AS
WITH audit_identities AS (
  -- One row per audited Vertex AI generate/predict call, deduplicated by
  -- insertId (the export can contain duplicate entries for streamed calls).
  SELECT
    protopayload_auditlog.authenticationInfo.principalEmail AS user_email,
    timestamp AS audit_call_start,
    insertId AS audit_id,
    REGEXP_EXTRACT(protopayload_auditlog.resourceName, r'models/([^/]+)$') AS model_short,
    -- Extract endpoint region from resourceName 'locations/<loc>/'.
    -- 'global' stays 'global'; any other location maps to 'regional'.
    -- A missing/unparseable location must default to 'global' (the cheaper
    -- rate): CASE handles the NULL explicitly — IF(NULL=..., a, b) would
    -- return b ('regional'), inverting the conservative-under-billing policy.
    CASE
      WHEN REGEXP_EXTRACT(protopayload_auditlog.resourceName, r'locations/([^/]+)/') IS NULL
        THEN 'global'
      WHEN REGEXP_EXTRACT(protopayload_auditlog.resourceName, r'locations/([^/]+)/') = 'global'
        THEN 'global'
      ELSE 'regional'
    END AS region
  FROM
    -- Wildcard tolerates BOTH sink export modes: a single partitioned table
    -- (sink created with --use-partitioned-tables) and date-sharded tables
    -- (cloudaudit_..._YYYYMMDD, Cloud Logging's default). insertId dedupe
    -- above also covers any overlap if a sink was switched between modes.
    `{PROJECT_ID}.{DATASET_ID}.cloudaudit_googleapis_com_data_access*`
  WHERE
    protopayload_auditlog.serviceName = 'aiplatform.googleapis.com'
    AND (
      protopayload_auditlog.methodName LIKE '%GenerateContent%'
      OR protopayload_auditlog.methodName LIKE '%Predict%'
    )
    AND protopayload_auditlog.authenticationInfo.principalEmail IS NOT NULL
    AND protopayload_auditlog.resourceName LIKE '%/models/%'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY insertId ORDER BY timestamp) = 1
),
payload_tokens AS (
  SELECT
    CAST(request_id AS STRING) AS request_id,
    model AS model_name,
    REGEXP_EXTRACT(model, r'([^/]+)$') AS model_short,
    COALESCE(
      SAFE_CAST(JSON_VALUE(full_response, '$.usageMetadata.promptTokenCount') AS INT64),
      SAFE_CAST(JSON_VALUE(full_response, '$.usage_metadata.prompt_token_count') AS INT64),
      0
    ) AS input_tokens,
    COALESCE(
      SAFE_CAST(JSON_VALUE(full_response, '$.usageMetadata.candidatesTokenCount') AS INT64),
      SAFE_CAST(JSON_VALUE(full_response, '$.usage_metadata.candidates_token_count') AS INT64),
      0
    ) AS output_tokens,
    COALESCE(
      SAFE_CAST(JSON_VALUE(full_response, '$.usageMetadata.totalTokenCount') AS INT64),
      SAFE_CAST(JSON_VALUE(full_response, '$.usage_metadata.total_token_count') AS INT64),
      0
    ) AS total_tokens,
    logging_time,
    -- Context-tier pricing bucket computed PER REQUEST before daily aggregation.
    -- Tiering is per-request: a single daily row must not mix le200k and gt200k
    -- calls or the wrong rate would be applied to the blended token count.
    IF(
      COALESCE(
        SAFE_CAST(JSON_VALUE(full_response, '$.usageMetadata.promptTokenCount') AS INT64),
        SAFE_CAST(JSON_VALUE(full_response, '$.usage_metadata.prompt_token_count') AS INT64),
        0
      ) <= 200000,
      'le200k',
      'gt200k'
    ) AS pricing_tier,
    -- Reconstruct the call START from the response time minus request latency.
    -- When request_latency is missing the true start is unknowable: leave the
    -- start NULL so the row cleanly falls to FALLBACK_IDENTITY instead of
    -- pretending start = end (which would mis-window long calls).
    CASE
      WHEN SAFE_CAST(JSON_VALUE(TO_JSON_STRING(metadata), '$.request_latency') AS FLOAT64) IS NULL
        THEN NULL
      ELSE TIMESTAMP_SUB(
        logging_time,
        INTERVAL CAST(ROUND(
          SAFE_CAST(JSON_VALUE(TO_JSON_STRING(metadata), '$.request_latency') AS FLOAT64)
        ) AS INT64) MILLISECOND
      )
    END AS payload_call_start
  FROM
    `{PROJECT_ID}.{DATASET_ID}.request_response_logging`
),
candidates AS (
  -- All plausible payload<->audit pairings: same model, starts within ±10s.
  SELECT
    P.request_id,
    A.audit_id,
    A.user_email,
    A.region,
    ABS(TIMESTAMP_DIFF(P.payload_call_start, A.audit_call_start, MILLISECOND)) AS dist_ms,
    A.audit_call_start
  FROM payload_tokens P
  JOIN audit_identities A
    ON A.model_short = P.model_short
   AND P.payload_call_start IS NOT NULL
   AND ABS(TIMESTAMP_DIFF(P.payload_call_start, A.audit_call_start, MILLISECOND)) <= 10000
),
assignments AS (
  -- Mutual-nearest ONE-TO-ONE assignment: keep a pair only when the audit entry
  -- is the payload's closest candidate AND the payload is the audit entry's
  -- closest candidate. Deterministic tie-breaks on timestamp/id/email.
  SELECT request_id, user_email, region
  FROM (
    SELECT
      request_id,
      user_email,
      region,
      ROW_NUMBER() OVER (
        PARTITION BY request_id
        ORDER BY dist_ms, audit_call_start, audit_id, user_email
      ) AS rn_payload,
      ROW_NUMBER() OVER (
        PARTITION BY audit_id
        ORDER BY dist_ms, request_id
      ) AS rn_audit
    FROM candidates
  )
  WHERE rn_payload = 1 AND rn_audit = 1
)
SELECT
  COALESCE(S.user_email, '{FALLBACK_IDENTITY}') AS user_email,
  P.model_name,
  SUM(P.input_tokens) AS input_tokens,
  SUM(P.output_tokens) AS output_tokens,
  SUM(P.total_tokens) AS total_tokens,
  MAX(P.logging_time) AS call_timestamp,
  '{PROJECT_ID}' AS project_id,
  COUNT(DISTINCT P.request_id) AS call_count,
  P.pricing_tier,
  -- Unmatched rows have no audit join so region is unknown; default to 'global'
  -- (the cheaper rate) for conservative under-billing rather than over-billing.
  COALESCE(S.region, 'global') AS region
FROM payload_tokens P
LEFT JOIN assignments S USING (request_id)
GROUP BY
  user_email, model_name, DATE(P.logging_time), pricing_tier, region
"""

# NOTE: The fallback view also aggregates per (model, pricing_tier, DAY) — no
# per-user attribution when the audit log table is unavailable. pricing_tier is
# computed PER REQUEST (same as in the full view) so tiering is preserved before
# the daily rollup. region is always 'global' because there is no audit data
# from which to extract an endpoint location.
fallback_view_query = f"""
CREATE OR REPLACE VIEW `{PROJECT_ID}.{DATASET_ID}.{VIEW_ID}` AS
SELECT
  '{FALLBACK_IDENTITY}' AS user_email,
  model AS model_name,
  SUM(COALESCE(
    SAFE_CAST(JSON_VALUE(full_response, '$.usageMetadata.promptTokenCount') AS INT64),
    SAFE_CAST(JSON_VALUE(full_response, '$.usage_metadata.prompt_token_count') AS INT64),
    0
  )) AS input_tokens,
  SUM(COALESCE(
    SAFE_CAST(JSON_VALUE(full_response, '$.usageMetadata.candidatesTokenCount') AS INT64),
    SAFE_CAST(JSON_VALUE(full_response, '$.usage_metadata.candidates_token_count') AS INT64),
    0
  )) AS output_tokens,
  SUM(COALESCE(
    SAFE_CAST(JSON_VALUE(full_response, '$.usageMetadata.totalTokenCount') AS INT64),
    SAFE_CAST(JSON_VALUE(full_response, '$.usage_metadata.total_token_count') AS INT64),
    0
  )) AS total_tokens,
  MAX(logging_time) AS call_timestamp,
  '{PROJECT_ID}' AS project_id,
  COUNT(*) AS call_count,
  -- Context-tier pricing bucket computed PER REQUEST before daily aggregation.
  IF(
    COALESCE(
      SAFE_CAST(JSON_VALUE(full_response, '$.usageMetadata.promptTokenCount') AS INT64),
      SAFE_CAST(JSON_VALUE(full_response, '$.usage_metadata.prompt_token_count') AS INT64),
      0
    ) <= 200000,
    'le200k',
    'gt200k'
  ) AS pricing_tier,
  'global' AS region
FROM
  `{PROJECT_ID}.{DATASET_ID}.request_response_logging`
GROUP BY
  model, DATE(logging_time), pricing_tier
"""

print(f"Attempting to create/update BigQuery view '{VIEW_ID}' with FULL audit log join...")
try:
    query_job = client.query(full_view_query)
    query_job.result()
    print(f"SUCCESS: Created view '{VIEW_ID}' successfully using full Data Access Audit Log join!")
    print(
        "\nNOTE: The view now includes pricing_tier and region columns "
        "(per user+model+tier+region+project+day grain). "
        "Existing deployments MUST re-run this script so that bq_client can "
        "apply context-tier and endpoint-region pricing correctly."
    )
except Exception as e:
    err_str = str(e)
    if "cloudaudit_googleapis_com_data_access" in err_str or "Not found" in err_str:
        print("\n[WARNING] cloudaudit_googleapis_com_data_access table not found in BigQuery yet.")
        print("This is expected if the first Data Access log is still being routed/buffered by GCP Logging.")
        print("Creating the view with highly-resilient FALLBACK schema (using request_response_logging directly)...")
        try:
            query_job = client.query(fallback_view_query)
            query_job.result()
            print(f"SUCCESS: Fallback view '{VIEW_ID}' created successfully!")
            print(
                "\nNOTE: The fallback view now includes pricing_tier and region columns "
                "(per model+tier+day grain; region is always 'global'). "
                "Existing deployments MUST re-run this script so that bq_client can "
                "apply context-tier pricing correctly."
            )
        except Exception as fallback_err:
            fallback_err_str = str(fallback_err)
            if "request_response_logging" in fallback_err_str:
                print(
                    "\nThe request_response_logging table does not exist yet. "
                    "Enable payload logging in the Model Logging tab (or run enable-logging.py), "
                    "make one test call (trigger_call.py), wait ~2 minutes, then re-run this script.",
                    file=sys.stderr
                )
            else:
                print(f"FAILED to create fallback view: {fallback_err}")
            sys.exit(1)
    else:
        print(f"FAILED to create full view: {e}")
        sys.exit(1)
