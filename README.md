# Agent Platform Model Telemetry

A Vertex AI per-user token consumption and FinOps portal built on GCP-native telemetry. It attributes token usage and estimated costs to individual principals by correlating Data Access audit logs (`cloudaudit_googleapis_com_data_access`) with Vertex AI payload logs (`request_response_logging`) in a BigQuery view — no agent-side instrumentation required. On top of that telemetry foundation the portal provides period-aware budget rules with configurable alerts, an editable per-model pricing table, a financial planner for estimating future workload costs, and a Gemini-powered FinOps assistant that answers spend questions with live consumption data injected into its context.

---

## Features

Features are organized by the six UI tabs:

| Tab | Hash | What it does |
|---|---|---|
| **Dashboard Overview** | `#dashboard` | Summary cards (total tokens, estimated cost, active alerts), time-series and per-model charts, per-user table with inline budget progress bars, principal/model dropdown filters. Shows a truncation warning when the result set hits the 1,000-row cap. |
| **Budget Constraints** | `#budgets` | Create, edit, and delete per-user or global-default budget rules. Each rule configures an identity (email or `global_default`), period (`day`/`week`/`month`/`year`), type (`token` or `money`), spending limit, alert threshold percentage, and optional hard-limit flag. |
| **Pricing & Planner** | `#planner` | Editable per-model pricing table (`pricing.json`) — input and output cost per million tokens. Named financial estimates let you project future costs across multiple models and terms before committing budget. |
| **Alerts Center** | `#alerts` | Real-time alert list computed on-demand by comparing period-scoped consumption against each budget rule. Alerts are classified as `warning` (threshold crossed) or `danger` (limit reached). |
| **Model Logging** | `#logging` | Toggle Vertex AI request-response payload logging per model. Changes are applied to GCP immediately via the Vertex AI SDK and persisted to `logging_config.json` (GCS or local). |
| **Gemini FinOps AI** | `#assistant` | Chat interface backed by `gemini-3.6-flash` on Vertex AI. The backend injects the current consumption table, active budgets, and pricing rules into the system prompt before forwarding messages. Returns HTTP 503 on any SDK failure rather than fabricating a response. |

**Cross-cutting capabilities:**
- **Dual themes**: Light, Dark, and Auto (follows `prefers-color-scheme`), persisted in `localStorage`. Toggle in the sidebar.
- **Sync-status log**: Header chip shows last-sync timestamp; clicking it opens a dropdown log of every sync (initial, manual, auto-refresh) with per-entry success/failure detail. Auto-refresh runs every 30 seconds.
- **Deep-linkable tabs**: Each tab maps to a URL hash (`#dashboard`, `#budgets`, `#planner`, `#alerts`, `#logging`, `#assistant`). Paste a hash to open the correct tab directly.
- **Bearer-token auth**: All `/api/*` routes require an `Authorization: Bearer <token>` header matching `PORTAL_AUTH_TOKEN`. When the variable is unset the server starts in unauthenticated dev mode and logs a startup warning. A modal prompts for the token on a 401 response.

---

## Architecture at a Glance

```
Browser (React 19 + Vite)
        │  REST /api/*
        ▼
FastAPI backend (backend/main.py)           ← single-port: serves React SPA + API
    ├── bq_client.py         → BigQuery view: user_token_chargebacks
    ├── gcs_client.py        → GCS bucket (or local file) for budgets.json / estimates.json
    ├── logging_client.py    → Vertex AI SDK — toggle per-model payload logging
    ├── ai_assistant.py      → Vertex AI: gemini-3.6-flash (FinOps assistant)
    ├── auth.py              → Bearer token dependency (require_auth)
    └── pricing.json         → Flat per-model pricing file (USD / million tokens)

BigQuery
    ├── request_response_logging          (Vertex AI payload telemetry)
    ├── cloudaudit_googleapis_com_data_access  (GCP Data Access audit logs)
    └── user_token_chargebacks  ← logical view: time-window correlation (±10 s, same model)
                                   attributes tokens to principalEmail; unmatched rows → FALLBACK_IDENTITY
```

The frontend is compiled by `npm run build` into `backend/static/`. FastAPI mounts that directory with `StaticFiles(html=True)` so React Router deep-links work without a separate server-side interceptor. The same port serves both the SPA and every `/api/*` endpoint.

For a detailed description of the BigQuery correlation algorithm, GCS fail-closed semantics, per-process write locks, rate limiting, and security design, see [comprehensive_design_document.md](comprehensive_design_document.md).

---

## Quick Start

See [setup_new_environment_guide.md](setup_new_environment_guide.md) for the full walkthrough including GCP API enablement, audit-log configuration, IAM setup, and Cloud Run deployment. For an interactive experience with your values substituted into every command, open [setup-guide.html](setup-guide.html) in your browser after cloning.

### Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- Google Cloud SDK (`gcloud`) authenticated with Application Default Credentials
- A GCP project with billing enabled, BigQuery and Vertex AI enabled

### 1. Configure environment

```bash
cp .env.template .env
# Edit .env — set at minimum:
#   BIGQUERY_PROJECT_ID, BIGQUERY_DATASET, BIGQUERY_VIEW
#   PORTAL_AUTH_TOKEN   (required before any shared/public deploy)
```

### 2. Install dependencies

```bash
# Python (create venv first — setup scripts need these packages)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Frontend
cd frontend && npm install && npm run build && cd ..
# Outputs to backend/static/
```

### 3. Run locally

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
# Open http://127.0.0.1:8000
```

### 4. Enable payload logging and make a test call

In the portal's **Model Logging** tab, tick the model matching `GEMINI_MODEL` in your `.env` and click **Apply Logging Settings**. Then:

```bash
python trigger_call.py
# Triggers request_response_logging (appears in seconds) and
# a Data Access audit log entry (exported to BigQuery in 5–15 min)
```

### 5. Create the BigQuery view (run twice)

```bash
# First run: request_response_logging now exists; audit table may not yet.
# Script automatically falls back to a no-attribution view — this is expected.
python setup_bigquery_view.py

# Wait 5–15 min for cloudaudit_googleapis_com_data_access to appear, then:
python setup_bigquery_view.py   # upgrades to the full attribution view
```

### 6. Docker / Cloud Run

```bash
# Build the image first, then deploy
gcloud builds submit --tag gcr.io/$PROJECT_ID/vertex-ai-consumption-portal
gcloud run deploy vertex-consumption-portal \
    --image gcr.io/$PROJECT_ID/vertex-ai-consumption-portal \
    --region us-central1 --no-allow-unauthenticated
# See setup guide (Step 10) for full flags, runtime SA roles, and how to reach
# a --no-allow-unauthenticated service.
```

### 7. Run tests

```bash
python -m pytest tests/ -q
```

---

## Security Notes

- **Set `PORTAL_AUTH_TOKEN`** before exposing the portal on any non-local network. Without it all API endpoints are open. Consider Cloud IAP or authenticated Cloud Run invokers for production.
- **`LOGGING_SAMPLING_RATE`** (0.0–1.0 in `.env`) controls what fraction of Vertex AI request/response payloads are written to BigQuery. Prompts and completions contain user content — set an appropriate rate for your privacy requirements.
- **CORS** is restricted to explicit origins via `CORS_ALLOW_ORIGINS` in `.env`. The default allows only `localhost:5173` and `127.0.0.1:5173` (Vite dev server). For single-port production the middleware is a no-op.
- See the Security Considerations section in [comprehensive_design_document.md](comprehensive_design_document.md) for a full threat model.

---

## API Reference

All `/api/*` endpoints require `Authorization: Bearer <PORTAL_AUTH_TOKEN>`. `/healthz` is unauthenticated.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness check — returns `{"status":"ok"}` with no downstream calls |
| `GET` | `/api/usage` | Last-30-day token usage logs from BigQuery (up to 1,000 rows; `truncated` flag) |
| `GET` | `/api/budgets` | Load all budget rules from GCS / local file |
| `POST` | `/api/budgets` | Replace full budget ruleset (requires `global_default` key; key must match `identity`) |
| `GET` | `/api/alerts` | Compute active alerts by comparing period-scoped spend against budget rules |
| `GET` | `/api/budget-status` | Period-aware consumption vs. limit per identity (drives progress bars) |
| `GET` | `/api/logging-config` | Read per-model payload logging enable/disable state |
| `POST` | `/api/logging-config` | Save and apply logging config to Vertex AI SDK; returns `partial_failure` on model errors |
| `GET` | `/api/pricing` | Current per-model pricing (USD / million input and output tokens) |
| `POST` | `/api/pricing` | Replace pricing table; atomically writes `pricing.json` and clears BQ caches |
| `GET` | `/api/estimates` | All saved financial planning estimates |
| `POST` | `/api/estimates` | Create or update a named estimate (stamped with `updated_at`) |
| `DELETE` | `/api/estimates/{name}` | Delete a named estimate (404 if not found) |
| `POST` | `/api/chat` | Send a message to the Gemini FinOps assistant (rate-limited: 10 req/min/process) |

---

## Repository Layout

```
.
├── backend/                    Python FastAPI application
│   ├── main.py                 API endpoints, CORS, static mount, alert engine
│   ├── auth.py                 Bearer token dependency (require_auth)
│   ├── config.py               Settings loaded from environment via python-dotenv
│   ├── bq_client.py            BigQuery client — queries user_token_chargebacks
│   ├── gcs_client.py           GCS reader/writer for budgets.json (local fallback)
│   ├── estimates_client.py     Reader/writer for estimates.json (local fallback)
│   ├── logging_client.py       Vertex AI SDK payload logging manager
│   ├── ai_assistant.py         FinOps copilot — gemini-3.6-flash with spend context
│   ├── constants.py            Shared PERIOD_DAYS mapping
│   ├── pricing.json            Per-model token pricing (USD / million tokens)
│   └── static/                 Compiled React SPA (created by `npm run build`)
├── frontend/                   Vite + React 19 SPA
│   ├── src/
│   │   ├── App.jsx             Root component; tabs, theme, sync state, auth modal
│   │   ├── components/         One component per tab + shared UI pieces
│   │   └── utils/              apiFetch helper, formatters
│   ├── vite.config.js          Dev proxy → 127.0.0.1:8000; build → ../backend/static
│   └── package.json
├── tests/                      pytest suite (16 test modules)
├── setup_bigquery_view.py      Creates the user_token_chargebacks BQ view
├── enable_audit_logs.py        Merges Vertex AI Data Access audit config into IAM policy
├── Dockerfile                  Multi-stage build: node:20-alpine → python:3.11-slim
├── requirements.txt            Python dependencies
├── .env.template               Environment variable reference
├── budgets.json                Local fallback budget storage
├── logging_config.json         Local fallback logging-config storage
├── estimates.json              Local fallback estimates storage
├── comprehensive_design_document.md  Architecture, BQ schema, lessons learned
├── setup_new_environment_guide.md    Full GCP setup walkthrough
```

---

## Further Reading

- [comprehensive_design_document.md](comprehensive_design_document.md) — architecture deep-dive, BigQuery view SQL, correlation algorithm, GCS semantics, security considerations, and development lessons.
- [setup_new_environment_guide.md](setup_new_environment_guide.md) — step-by-step GCP environment setup, from API enablement through Cloud Run deployment.
