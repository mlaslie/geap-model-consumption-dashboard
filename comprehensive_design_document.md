# Vertex AI User-Level Consumption Portal: Design Document

This document serves as the master technical design reference for the **Vertex AI User-Level Consumption and FinOps Portal**. It details lessons learned, architecture, database schemas, API interfaces, and development guidelines to support future extensions, fixes, and audits.

---

## 🏗️ System Architecture & Data Flow

The portal leverages direct GCP native telemetry and logging pathways to compute precise user-level usage and cost allocations. It functions entirely on a production-only data flow; no mock or simulated data is returned. If the Vertex AI SDK is unavailable or errors, the `/api/chat` endpoint raises an `AssistantUnavailableError` and returns HTTP 503 rather than fabricating fallback responses.

### High-Level Data Flow Diagram
```mermaid
graph TD
    %% Consumption Inputs
    subgraph Google Cloud Platform (Telemetry)
        A[Vertex AI SDK Client Calls] -->|Automatic Telemetry| B[Vertex AI Service Logging]
        B -->|Writes payload request-response logs| C[(BigQuery Table: request_response_logging)]
        D[Cloud Logging Data Access Logs] -->|Filters aiplatform.googleapis.com| E[(BigQuery Table: cloudaudit_googleapis_com_data_access)]
    end

    %% Database Tier
    subgraph BigQuery Logical Tier
        C -->|time-window correlation| F[[BigQuery View: user_token_chargebacks]]
        E -->|time-window correlation| F
    end

    %% Application Tier
    subgraph FastAPI Python Backend
        G[FastAPI Server: backend/main.py]
        H[BigQuery Client: bq_client.py] -->|Queries Aggregated Usage| F
        I[GCS Client: gcs_client.py] <-->|Read/Write Budgets| J[(GCS Bucket: budgets.json)]
        K[FinOps Copilot: ai_assistant.py] <-->|Queries Spend Context & Generates Plan| L[Vertex AI: gemini-3.6-flash]
        M[Logging Client: logging_client.py] -->|Applies Configs| A
    end

    %% Presentation Tier
    subgraph React Single Port Frontend (Zinc Glassmorphism)
        N[Vite React Client] <-->|HTTP REST APIs| G
    end

    %% Styling and Binding
    classDef bq fill:#3367d6,stroke:#fff,stroke-width:2px,color:#fff;
    classDef backend fill:#34a853,stroke:#fff,stroke-width:2px,color:#fff;
    classDef ui fill:#ea4335,stroke:#fff,stroke-width:2px,color:#fff;
    class C,E,F bq;
    class G,H,I,K,M backend;
    class N ui;
```

### 1. Presentation Tier (React Frontend)
- **Framework**: Vite + React (v19) with standard ES6 modules.
- **Styling**: Single-file Vanilla CSS custom-themed design (`frontend/src/index.css`) utilizing modern CSS custom properties, zinc glassmorphism (`backdrop-filter: blur`), dark-mode palette, flexbox grids, and subtle keyframe micro-animations (pulsing warnings, smooth tab transitions).
- **Distribution**: Single-port distribution. Running `npm run build` compiles frontend assets and pipes them directly into the backend static mounting folder (`../backend/static/`).

### 2. Application Tier (FastAPI Backend)
- **Framework**: FastAPI (Python 3.11+).
- **Core Modules**:
  - `main.py`: Houses API endpoints, static assets mounting (`StaticFiles(html=True)` — React Router deep links are handled by the browser once the SPA is loaded, not by a separate server-side interceptor), and the **Dynamic Alert Evaluator** which runs in-memory comparison loops.
  - `auth.py`: FastAPI dependency (`require_auth`) that enforces Bearer token authentication via the `PORTAL_AUTH_TOKEN` environment variable. When `PORTAL_AUTH_TOKEN` is unset the app runs in unauthenticated dev mode and logs a startup warning.
  - `bq_client.py`: Queries the unified logical view in BigQuery and transforms raw logs into standard JSON records.
  - `gcs_client.py`: Stores and reads budget configurations (`budgets.json`) to/from Google Cloud Storage. When GCS is configured, read failures are fail-closed (raise `RuntimeError` → 500) rather than silently serving stale local state.
  - `logging_client.py`: Integrates with the Vertex AI preview SDK to enable or disable request-response telemetry payload pipelines directly on GCP. Returns a per-model results dict so partial failures are surfaced to callers.
  - `ai_assistant.py`: FinOps Chat Copilot. Uses `google-genai` and injects localized, token-capped system prompts containing the latest consumption table, active budget constraints, and pricing rules to prevent hallucinated recommendations. Raises `AssistantUnavailableError` on any SDK failure.

### 3. Database Tier (BigQuery Views & Filesystem Storage)
- **`request_response_logging`**: BigQuery table where the native Vertex AI SDK routes request and response payloads. Contains token metadata, model names, unique `request_id` values, and `metadata.request_latency` (milliseconds).
- **`cloudaudit_googleapis_com_data_access`**: GCP Data Access Audit log table. Captures the caller identity (`principalEmail`) and request timestamp. **Note:** its `metadata` field is empty for GenerateContent calls and `operation.id` does not match `request_id` — there is **no shared key** between the two streams.
- **`user_token_chargebacks`**: Unified BQ logical view. Attributes tokens to users via **time-window correlation**: each payload row's call start time is reconstructed as `logging_time − request_latency`, then matched one-to-one (mutual-nearest, ±10s, same model) to an audit entry. Rows with no exclusive match — including rows missing `request_latency` — are attributed to `FALLBACK_IDENTITY` (`unattributed@unknown` by default) rather than guessed. The view re-correlates full history on every query; materialize it to a scheduled table before scaling to large log volumes.
- **`pricing.json` / `budgets.json`**:
  - `pricing.json`: Flat metadata file storing pricing per million input/output tokens per model (editable via the Pricing & Planner tab). Rates are Standard-tier, global-endpoint prices with two optional per-model fields: `input_cost_per_million_gt_200k` (the >200K-context input rate, applied per request when its input exceeds 200K tokens) and `non_global_multiplier` (the regional-endpoint premium — 1.1 per Google's July 1, 2026 pricing — applied to both input and output rates). The BigQuery view classifies every request's `pricing_tier` (le200k/gt200k, from per-request input size) and `region` (from the audit resourceName's `locations/<loc>`; unattributed rows default to global, the cheaper rate) before daily aggregation, and `bq_client` applies the matching rates. Enables pricing updates without writing Python or SQL.
  - `budgets.json`: Stores customized user budget limits, alert percentages, periods, and hard-limit toggles.

---

## 📂 Codebase File Map

```
/Vertex AI Consumption
│
├── .env                         # Local environment configuration file (BIGQUERY_PROJECT_ID, region, etc.)
├── .env.template                # Clean template file for setting up new environments
├── .gitignore                   # Prevents committing secrets, runtime state, and build artifacts
├── .dockerignore                # Excludes .env, iam_policy.json, etc. from Docker build context
├── requirements.txt             # Python backend dependencies (fastapi, google-cloud-bigquery, google-genai, google-cloud-aiplatform)
├── Dockerfile                   # Multi-stage production container build file (React compilation + FastAPI packaging)
├── budgets.json                 # Backend budget rule storage (Local filesystem fallback)
├── logging_config.json          # Model logging enablement states (Local filesystem fallback)
├── setup_bigquery_view.py       # BigQuery view generator (highly-resilient double-join creator)
├── enable_audit_logs.py         # Helper script: merges Vertex AI audit config into existing IAM policy
│
├── backend/                     # Python Backend Directory
│   ├── main.py                  # API endpoints, CORS policies, static mounting, alert engine
│   ├── auth.py                  # Bearer token authentication dependency (require_auth)
│   ├── config.py                # Settings loaded from environment via python-dotenv
│   ├── bq_client.py             # BigQuery execution client (queries token usage logs)
│   ├── gcs_client.py            # GCS reader/writer for budgets.json with local fallbacks
│   ├── logging_client.py        # Vertex AI SDK payload logging manager (per-model results)
│   ├── ai_assistant.py          # FinOps Copilot assistant utilizing gemini-3.6-flash
│   ├── pricing.json             # Decoupled model token pricing rules (USD per Million tokens)
│   └── static/                  # Production-compiled React frontend directory (created by vite build)
│
├── tests/                       # Python test suite (pytest)
│   ├── test_auth.py
│   ├── test_pricing.py
│   ├── test_budget_validation.py
│   ├── test_usage_endpoint.py
│   └── ...                      # Additional endpoint and integration tests
│
└── frontend/                    # Vite React Frontend Directory
    ├── package.json             # Frontend dependency manifest
    ├── vite.config.js           # Vite server configuration (routing static builds to ../backend/static)
    ├── src/                     # Source Directory
    │   ├── main.jsx             # Entrypoint
    │   ├── App.jsx              # Core coordinator, tab manager, filters, background sync loops
    │   ├── utils/
    │   │   └── api.js           # apiFetch helper: injects Bearer token from sessionStorage, dispatches portal-auth-required on 401
    │   └── components/          # Component Subdirectory
    │       ├── SummaryCards.jsx # High-level metric dashboards
    │       ├── UsageCharts.jsx  # SVG timeseries cost charts & dynamic model share meters
    │       ├── UserTable.jsx    # Sortable usage allocations & budget consumption progress bars
    │       ├── BudgetManager.jsx# Budget limits manager (click-to-load inputs, sliders, deletion)
    │       ├── AlertCenter.jsx  # Glassmorphic active budget threshold warning list
    │       ├── ModelLogging.jsx # Model Logging tab: checkbox panel, Apply Logging Settings button
    │       └── FinOpsAssistant.jsx # Assistant panel (scrollable context-injected chat interface)
```

---

## 📜 Key API Endpoints & Interfaces

All `/api/*` endpoints require an `Authorization: Bearer <token>` header when `PORTAL_AUTH_TOKEN` is set. The `/healthz` endpoint is unauthenticated.

### 0. Health Check
- **Endpoint**: `GET /healthz`
- **Auth**: None (always accessible)
- **Output**: `{"status": "ok"}`

### 1. Usage Telemetry API
- **Endpoint**: `GET /api/usage`
- **Output Schema**:
  ```json
  {
    "status": "success",
    "project_id": "your-gcp-project-id",
    "truncated": false,
    "data": [
      {
        "user_email": "user@example.com",
        "model_name": "gemini-2.5-pro",
        "input_tokens": 125000,
        "output_tokens": 34000,
        "total_tokens": 159000,
        "call_timestamp": "2026-07-21T01:00:00Z",
        "call_count": 42,
        "estimated_cost_usd": 0.2225,
        "pricing_match": "exact"
      }
    ]
  }
  ```
  - `model_name` is the normalized model identifier (e.g. `gemini-2.5-pro`), not `model_id`.
  - `pricing_match` is `"exact"`, `"prefix"`, or `"default"` — indicates how the cost rate was resolved.
  - `pricing_tier` is `"le200k"` or `"gt200k"` — the per-request input-context tier used for rate selection (rows are aggregated per tier).
  - `region` is `"global"` or `"regional"` — the endpoint region used for rate selection (regional applies the model's `non_global_multiplier`).
  - `call_count` is the true API call count within the row's day bucket (requires the daily-grain view).
  - `truncated` is `true` when the result set hit the LIMIT 1000 cap; the UI should surface a notice. Budget alerts and `/api/budget-status` are NOT subject to this cap — they aggregate in SQL with no row limit.
  - Returns logs for the trailing 30 calendar days.

### 2. Budget CRUD API
- **Endpoints**:
  - `GET /api/budgets`: Retrieves budget records.
  - `POST /api/budgets`: Replaces the full set of budget rules.
- **Input/Output Schema**:
  ```json
  {
    "user@example.com": {
      "identity": "user@example.com",
      "period": "month",
      "type": "money",
      "limit": 100.0,
      "alert_threshold_percentage": 80.0,
      "hard_limit_enabled": true
    },
    "global_default": {
      "identity": "global_default",
      "period": "month",
      "type": "token",
      "limit": 5000000.0,
      "alert_threshold_percentage": 90.0,
      "hard_limit_enabled": false
    }
  }
  ```
  **POST validation rules** (400 is returned on any violation):
  - Dict must not be empty (prevents accidental wipe of all rules).
  - A `"global_default"` key is required.
  - Each dict key must equal the rule's `identity` field.
  - `period` must be one of `"day"`, `"week"`, `"month"`, `"year"`.
  - `type` must be `"token"` or `"money"`.
  - `limit` must be > 0.
  - `alert_threshold_percentage` must be 1–100.
  - **Semantics**: This is a whole-set replace. Submit the complete desired ruleset; omitting a key deletes that user's rule.

### 3. Budget Status API
- **Endpoint**: `GET /api/budget-status`
- **Purpose**: Period-aware budget consumption for UI progress bars. Each identity's consumed value is computed against the trailing window defined by that rule's `period` field.
- **Output Schema** (per identity):
  ```json
  {
    "user@example.com": {
      "consumed": 42.50,
      "limit": 100.0,
      "type": "money",
      "period": "month",
      "percentage": 42.5,
      "threshold_percentage": 80.0,
      "hard_limit_enabled": false,
      "is_global_default": false
    }
  }
  ```

### 4. Model Logging Settings API
- **Endpoints**:
  - `GET /api/logging-config`: Retrieves states from storage.
  - `POST /api/logging-config`: Persists states and calls the Vertex AI API.
- **Schema**:
  ```json
  {
    "gemini-2.5-pro": true,
    "gemini-2.5-flash": false,
    "gemini-3.1-flash-lite": true,
    "gemini-3.5-flash": true
  }
  ```
  **POST response — partial failure shape**: When some models fail to apply, the server returns HTTP 200 with `status: "partial_failure"` (not a 5xx) so the frontend can parse and display per-model error details:
  ```json
  {
    "status": "partial_failure",
    "message": "Logging configurations saved but some models failed to update.",
    "results": {
      "gemini-2.5-pro": {"success": true, "error": null},
      "gemini-2.5-flash": {"success": false, "error": "SomeError: configuration apply failed"}
    }
  }
  ```

### 5. FinOps Chat API
- **Endpoint**: `POST /api/chat`
- **Rate limit**: 10 requests per 60-second sliding window per process (returns 429 when exceeded). Note: this limit is per-process — it does not aggregate across multiple Cloud Run instances.
- **Returns 503** when the Vertex AI SDK is unavailable or the generation call fails.
- **Request**: `{"messages": [{"role": "user", "content": "..."}]}` — max 40 messages, max 8000 chars each.

---

## 🔐 Authentication & CORS

### Authentication
All `/api/*` endpoints are protected by `backend/auth.py` via a FastAPI `Depends(require_auth)` dependency on the API router.

- **Mechanism**: Bearer token in the `Authorization` header, compared with constant-time `secrets.compare_digest`.
- **Configuration**: Set `PORTAL_AUTH_TOKEN` in the environment. When the variable is empty the app starts in **dev mode** — all endpoints are unauthenticated and a `SECURITY WARNING` is logged at startup.
- **Frontend**: `frontend/src/utils/api.js` reads the token from `sessionStorage` and injects it as `Authorization: Bearer <token>` on every API call. On a 401 response it dispatches a `portal-auth-required` custom event that triggers a token-entry modal in `App.jsx`.
- **Production recommendation**: Use IAP (Identity-Aware Proxy) or authenticated Cloud Run invokers (OIDC) as the primary access control layer. If the service is publicly reachable (e.g. `--allow-unauthenticated`), `PORTAL_AUTH_TOKEN` must be set.

### CORS
Origins are restricted to an explicit list — no wildcard. Configure via the `CORS_ALLOW_ORIGINS` environment variable (comma-separated). The default is `http://localhost:5173,http://127.0.0.1:5173` (Vite dev server). Credentials are not passed with CORS requests.

---

## 🧠 Lessons Learned & Engineering Insights

This section outlines major engineering discoveries made during the development of the portal, which must be respected during future changes:

### 1. The Vertex AI SDK Sequential Logging Block (Critical Bug Fix)
> [!IMPORTANT]
> **Issue**: In initial versions, toggling payload logging for models other than `gemini-2.5-pro` (like `gemini-3.5-flash`) failed silently.
> 
> **Root Cause**: The SDK method `GenerativeModel.set_request_response_logging_config(...)` throws a `409 Conflict: The same PublisherModelConfig already exists` error if you try to re-enable an already active config. The backend loop was sequential; when `gemini-2.5-pro` (the first model in the dict) threw this 409 conflict, the exception crashed the entire function. Consequently, subsequent models were never processed.
> 
> **Resolution**: Implement individual, isolated try-catch blocks for each model in the loop. A 409 conflict is now treated as **success** — the config is already active, so there is nothing to do. The error field in the per-model result is set to `"already active (409 conflict)"` for transparency, and the loop continues to configure all remaining models. The `POST /api/logging-config` response includes the full per-model results dict so the frontend can report any genuine failures.

### 2. Double-Resilient BigQuery View (Schema Buffering)
> [!TIP]
> **Issue**: If you deploy the application in a fresh environment before any model requests have run, the BigQuery table `cloudaudit_googleapis_com_data_access` does not yet exist, causing full SQL view joins to throw compile-time database errors.
> 
> **Resolution**: We engineered a dual-stage setup script (`setup_bigquery_view.py`). If the full audit logs are not yet buffered, the script automatically builds a **fallback view** directly querying `request_response_logging` (which aggregates usage under a configurable `FALLBACK_IDENTITY`). Once the first audit log routes, re-running the script elevates the view to the **full time-correlation** schema (see Lesson 6 — the join is by time window, not by key).
>
> **Important — daily grain**: Both the full-join and fallback views aggregate to a **per user × model × day** grain (not per-call, not all-time). This daily grain is required for period-aware budget alerts to compute correctly — the `WHERE call_timestamp >= <window>` filter in `bq_client.py` returns per-period sums only when each row represents a single day. Re-run `setup_bigquery_view.py` when upgrading an existing deployment.

### 3. Decoupled Model Pricing Design
> [!NOTE]
> Pricing is maintained completely client-side in the backend inside `backend/pricing.json`. This is critical:
> - **No Hardcoding**: We avoid embedding cost equations inside complex BigQuery SQL code.
> - **Agility**: If Google drops token prices or releases a new model, administrators can update the values in `pricing.json` without modifying code or refactoring tables.
> - **Efficiency**: The backend queries raw token integers from BigQuery, and computes costs dynamically in python memory on the fly.

### 4. Single-Port SPA Deployment Pattern
> [!NOTE]
> Rather than hosting the API and static React UI on separate ports (which introduces CORS preflight latency, network complications, and additional target configurations in Cloud Run), the application compiles into standard static outputs which FastAPI mounts.
> - **Static Mounting**: `app.mount("/", StaticFiles(directory="backend/static", html=True), name="static")`
> - The `html=True` flag on `StaticFiles` causes Starlette to serve `index.html` for paths that do not match a static file, enabling React Router deep links to resolve in the browser. There is no separate server-side route interceptor.

### 5. GCS Fail-Closed Reads
> [!IMPORTANT]
> When `GCS_BUCKET_NAME` is configured, read failures for `budgets.json` and `logging_config.json` raise a `RuntimeError` that propagates as HTTP 500 rather than silently falling back to local defaults. This is intentional: on multi-instance Cloud Run deployments the container filesystem is ephemeral, so local state may be stale or missing. A missing GCS blob (first-time deployment) is not treated as an error — it initializes defaults and writes them to GCS.

### 6. There Is No Join Key Between Audit Logs and Payload Logs (Critical Redesign)
> [!IMPORTANT]
> **Issue**: The original design attributed tokens to users by joining the Data Access audit log to `request_response_logging` on `requestId`. In production this join can never match: the audit entry's `protoPayload.metadata` field is **empty** for GenerateContent calls, and its `operation.id` does **not** equal the payload table's `request_id`. The "full join" view would silently return zero rows.
>
> **Resolution**: Attribution now uses **time-window correlation**. The payload table stores `metadata.request_latency` (ms), so each row's call *start* time is `logging_time − request_latency` — measured within ~100ms of the audit entry's timestamp for the same call. The view pairs payload rows and audit entries one-to-one (mutual-nearest within ±10s, same model, deterministic tie-breaks). Anything without an exclusive match — including rows with missing latency — is attributed to `FALLBACK_IDENTITY` rather than guessed. Trade-off: heavy concurrent same-model traffic pushes contested calls into the unattributed bucket instead of mis-billing a user.

### 7. A Broken Log Sink Fails Silently (Twice)
> [!WARNING]
> The project's audit-log sink exported **nothing for its entire life** for two independent reasons, neither of which produces a visible error in normal operation:
> 1. **Invalid filter syntax**: the filter used SQL's `LIKE`, which is not a Cloud Logging operator — it matched zero entries. Use `:` (contains) or `=~` (regex); validate any sink filter by running it through `gcloud logging read '<filter>' --limit 1` before trusting it.
> 2. **Missing destination permission**: the sink's writer service account (`service-…@gcp-sa-logging.iam.gserviceaccount.com`) had no WRITER access on the target BigQuery dataset, so even matching entries would have been dropped.
>
> Both must be verified when the `cloudaudit_googleapis_com_data_access` table fails to appear. Also note sinks are forward-only: entries logged before the fix are never backfilled, so attribution begins at the moment the sink works.

### 8. Cross-Model Review Loop
> [!NOTE]
> The 2026-07 remediation used a two-model review process: every change batch was tested (pytest suite + frontend build), then independently reviewed by a second model (Grok 4.5 via headless CLI), with disagreements adjudicated case-by-case. This repeatedly caught real issues the primary implementation missed (frontend not sending auth headers, period filters incompatible with the aggregated view grain, a LIMIT cap silently undercounting budgets, non-finite pricing values poisoning cost math, and the many-to-one attribution race in the correlation view). The full finding-by-finding history is preserved in the project's development log.

---

## 📈 Database Schema Specifications

### 1. Raw Telemetry Schema (`request_response_logging`)
*Automatically generated by the Vertex AI Logging Pipeline:*
- `request_id` (STRING): Unique execution identifier.
- `model` (STRING): Fully qualified model path (e.g. `publishers/google/models/gemini-2.5-pro`). Note: the column is named `model`, not `model_name`.
- `logging_time` (TIMESTAMP): Time the payload was logged. Note: the column is named `logging_time`, not `create_time`.
- `full_response` (JSON): Raw response containing `usageMetadata.promptTokenCount`, `usageMetadata.candidatesTokenCount`, and `usageMetadata.totalTokenCount`. Note: the column is named `full_response`, not `response_payload`.

### 2. Audit Trail Schema (`cloudaudit_googleapis_com_data_access`)
*Automatically generated by Cloud Logging BigQuery Sink:*
- `timestamp` (TIMESTAMP): Event time.
- `protopayload_auditlog.authenticationInfo.principalEmail` (STRING): User email address.
- `protopayload_auditlog.resourceName` (STRING): Resource string.
- `protopayload_auditlog.metadata` (JSON): **Empty in practice** for GenerateContent calls — it does NOT contain a usable `requestId`. Correlation with the payload table is done by time window (see the view description above), not by key.
- `insertId` (STRING): Export deduplication key (streamed calls can produce duplicate audit rows).

### 3. Aggregated Logical View (`user_token_chargebacks`)
*Synthesized View combining both tables — aggregated to daily grain (user × model × project × day):*
- `user_email` (STRING): Caller email address.
- `model_name` (STRING): Normalized model identifier (e.g. `gemini-2.5-pro`). Note: the column is named `model_name` in the view (and in API responses), not `model_id`.
- `input_tokens` (INTEGER): Sum of input tokens for the day.
- `output_tokens` (INTEGER): Sum of output tokens for the day.
- `total_tokens` (INTEGER): Sum of combined input/output for the day.
- `call_timestamp` (TIMESTAMP): Maximum (most recent) call timestamp within the day bucket.
- `project_id` (STRING): Active project ID.
- `call_count` (INTEGER): Count of distinct `request_id` values within the day bucket — true API call count.

---

## 🚀 Guidelines for Future Enhancements

### Adding a New Gemini Model to the Portal
To support a new model (e.g. a future `gemini-4.0-flash`):
1. **Pricing**: Open `backend/pricing.json` and append the cost mapping:
   ```json
   "gemini-4.0-flash": {
     "input_cost_per_million": 0.075,
     "output_cost_per_million": 0.30
   }
   ```
2. **Logging Config default**: Open `backend/logging_client.py` and add `"gemini-4.0-flash": false` to `DEFAULT_LOGGING_CONFIG` (the dict near the top of the file).
3. **Frontend list**: Update the initial `modelStates` object in `frontend/src/components/ModelLogging.jsx` so the model appears as a toggleable checkbox in the **Model Logging** tab.
4. **Deploy**: Build and push. The platform's dynamic filters, charts, and budget limits will automatically adapt to support the new model.

---

## 🛡️ Security Considerations

The following behaviors have privacy or security implications that operators should understand before deploying to a shared or production environment.

### Payload Logging and Privacy
When model logging is enabled via the **Model Logging** tab, the Vertex AI SDK routes raw input prompts and generated responses into the `request_response_logging` BigQuery table. The fraction of requests logged is controlled by `LOGGING_SAMPLING_RATE` (0.0–1.0; default 1.0 = 100%). This means **full user prompts and AI responses are stored in BigQuery** for every logged call. Operators should consider:
- Reducing `LOGGING_SAMPLING_RATE` to limit exposure.
- Applying BigQuery column-level security or table expiration policies.
- Informing users that their prompts are captured.

### Budget API — Whole-Set Replace Semantics
`POST /api/budgets` replaces the entire budget ruleset atomically. Submitting a partial payload will silently delete rules for omitted identities. The API rejects empty payloads (400) and requires a `global_default` key as a safeguard, but callers must still submit the full intended set.

### Config Writes — Single-Instance Scope
Pricing edits (`POST /api/pricing`) write `backend/pricing.json` on the local container filesystem and hot-reload only the current process; budgets/estimates read-modify-write cycles are serialized with per-process locks and atomic file replaces. On a **multi-replica** deployment (Cloud Run with >1 instance), pricing edits will not propagate to other replicas until restart, and concurrent writes from different replicas can still race. Run a single instance for config-editing workflows, or move pricing to the GCS-backed pattern used by budgets/estimates before scaling out.

### Rate Limiting Scope
The `/api/chat` rate limit (10 requests per 60 seconds) is enforced **per process**. On a horizontally scaled Cloud Run deployment with multiple instances, each instance enforces its own limit independently — the aggregate request capacity is `limit × instance_count`. Use a shared store (Redis, Cloud Memorystore) if stricter cross-instance enforcement is needed.

### Authentication in Dev Mode
When `PORTAL_AUTH_TOKEN` is not set, all API endpoints are unauthenticated and the server logs a `SECURITY WARNING` at startup. Never expose the server on a public or shared network in this mode.
