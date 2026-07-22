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

## Updating

```bash
./update.sh
```

That is the entire command. The script handles pulling, dependency updates, the frontend build, and the BigQuery view migration automatically. All user state is preserved.

### Installed from a ZIP download?

A ZIP install has no `.git` directory, so `update.sh` cannot pull. Convert it into a git checkout **in place** — your state files are untracked, so they are not touched:

```bash
cd <your-app-directory>
git init -b main
git remote add origin https://github.com/mlaslie/geap-model-consumption-dashboard.git
git fetch origin
git reset --hard origin/main        # overwrites CODE with upstream; state files (.env,
                                    # budgets.json, pricing.json, etc.) are untracked and survive
git branch --set-upstream-to=origin/main main
./update.sh
```

> Any local **code** edits in the ZIP copy are replaced by upstream. If you prefer, the alternative is a fresh `git clone` next to the old directory, copying over `.env` (plus `budgets.json`, `logging_config.json`, `estimates.json`, `model_sync.json`, and `backend/pricing.json` if you run without GCS). After either path, all future updates are just `./update.sh`.

### What survives an update

| State | Where it lives | Survives `git pull`? |
|---|---|---|
| GCP credentials & config | `.env` | Yes — gitignored, never touched |
| User pricing table | `backend/pricing.json` | Yes — gitignored, user-owned |
| Budget rules (local) | `budgets.json` | Yes — gitignored |
| Payload logging config (local) | `logging_config.json` | Yes — gitignored |
| Financial estimates (local) | `estimates.json` | Yes — gitignored |
| Model catalogue sync state | `model_sync.json` | Yes — gitignored |
| Budget rules / estimates / logging config (Cloud Run) | GCS bucket (`GCS_BUCKET_NAME`) | Yes — stored in GCS, never on disk |

### Pricing file split (`pricing.defaults.json` vs `pricing.json`)

Starting from this version, pricing is managed as two files:

- **`backend/pricing.defaults.json`** — shipped default rates; tracked by git; updated when new models are added in a release. Never edited at runtime.
- **`backend/pricing.json`** — runtime, user-owned config; **not tracked by git**. Edited via the **Pricing & Planner** UI or directly. On first run (or after a fresh clone) the app seeds `pricing.json` by copying `pricing.defaults.json` to it, then uses `pricing.json` exclusively from that point forward. A `git pull` never touches `pricing.json`.

### Cloud Run update path

State on Cloud Run lives in your GCS bucket (`budgets.json`, `logging_config.json`, `estimates.json`) and is never affected by a redeploy. After `./update.sh` completes locally, rebuild and redeploy the container:

```bash
gcloud builds submit --tag gcr.io/$PROJECT_ID/vertex-ai-consumption-portal
gcloud run deploy vertex-consumption-portal \
    --image gcr.io/$PROJECT_ID/vertex-ai-consumption-portal \
    --region us-central1 --no-allow-unauthenticated
```

See [setup_new_environment_guide.md](setup_new_environment_guide.md) Step 10 for the full flags and runtime service account configuration.

### BigQuery view migrations

`./update.sh` runs `setup_bigquery_view.py` automatically as its last step. The script is idempotent (`CREATE OR REPLACE VIEW`) and reads your `.env` for project/dataset/view names. If it fails (e.g. the BigQuery credentials aren't available in the shell running the script), the update continues with a warning and you can re-run it manually:

```bash
.venv/bin/python setup_bigquery_view.py
```

---

## Troubleshooting

Quick checks and fixes for the most common problems, in the order they typically bite. Set these once:

```bash
export PROJECT_ID=your-gcp-project-id
export DATASET=vertex_ai_user_telemetry
export SINK=vertex-ai-telemetry-sink
```

### 1. Every call shows as `unattributed@unknown`

The #1 cause: the Cloud Logging sink's service account was never granted write access on the dataset, so audit-log exports fail silently and the identity data never reaches BigQuery.

```bash
# Check — the sink's writer SA must appear with role WRITER:
SINK_SA=$(gcloud logging sinks describe $SINK --project=$PROJECT_ID --format="value(writerIdentity)" | sed 's/serviceAccount://')
bq show --format=prettyjson $PROJECT_ID:$DATASET | grep -A2 "$SINK_SA" || echo "MISSING GRANT — this is your problem"
```

```bash
# Fix — grant dataset-level WRITER to the sink SA:
bq show --format=prettyjson $PROJECT_ID:$DATASET > /tmp/ds.json
python3 -c "
import json; d=json.load(open('/tmp/ds.json'))
d['access'].append({'role':'WRITER','userByEmail':'$SINK_SA'})
json.dump({'access':d['access']}, open('/tmp/ds-access.json','w'))"
bq update --source /tmp/ds-access.json $PROJECT_ID:$DATASET
```

Then make one new call (`python trigger_call.py`), wait 5–15 minutes, and re-run `python setup_bigquery_view.py`. **Calls made before the grant stay unattributed forever** — sinks never backfill.

### 2. The `cloudaudit_...` table never appears in BigQuery

Either the grant above is missing, or the sink filter matches nothing (e.g. hand-edited with invalid syntax — Cloud Logging has no `LIKE` operator).

```bash
# Check — does the filter actually match entries? (make a Vertex call first)
gcloud logging read "$(gcloud logging sinks describe $SINK --project=$PROJECT_ID --format='value(filter)')" \
  --project=$PROJECT_ID --limit=1 --freshness=1d --format="value(timestamp)"
# Empty output after a fresh call = broken filter
```

```bash
# Fix — restore the known-good filter:
gcloud logging sinks update $SINK --project=$PROJECT_ID \
  --log-filter='log_id("cloudaudit.googleapis.com/data_access") AND protoPayload.serviceName="aiplatform.googleapis.com" AND (protoPayload.methodName:"GenerateContent" OR protoPayload.methodName:"Predict")'
```

### 3. Audit tables exist but are named `cloudaudit_..._YYYYMMDD` (date-sharded)

Sinks created without `--use-partitioned-tables` shard by date. Current versions of the view handle both modes — if you see shards and still get `unattributed@unknown`, your view predates the fix.

```bash
# Fix — redeploy the view (queries a wildcard covering both modes):
python setup_bigquery_view.py
```

### 4. No usage data at all (empty dashboard, `request_response_logging` missing)

Payload logging isn't enabled, or no call has been made since enabling it. The table is created automatically on the first logged call.

```bash
# Check:
bq ls --project_id=$PROJECT_ID $DATASET | grep request_response_logging || echo "no payload table yet"
# Fix: enable the model in the portal's Model Logging tab (enable the SAME
# model id as GEMINI_MODEL in .env), Apply, then:
python trigger_call.py
```

### 5. Data Access audit logs aren't being generated at all

```bash
# Check — aiplatform must appear with DATA_READ/DATA_WRITE:
gcloud projects get-iam-policy $PROJECT_ID --format="json(auditConfigs)" | grep -A3 aiplatform || echo "audit logs NOT enabled"
# Fix (merges safely into the existing policy):
python enable_audit_logs.py
```

### 6. `/api/usage` returns 500 after an upgrade

The deployed view is older than the code (e.g. missing `pricing_tier`/`region`/`call_count` columns).

```bash
# Fix:
python setup_bigquery_view.py
```

### 7. Browser shows the "API token required" modal or curl gets 401

`PORTAL_AUTH_TOKEN` is set in `.env` — paste the same value into the modal. For curl, the token must be in your shell first:

```bash
export PORTAL_AUTH_TOKEN=<value-from-your-.env>
curl -H "Authorization: Bearer $PORTAL_AUTH_TOKEN" http://127.0.0.1:8000/api/usage
```

### 8. FinOps assistant returns 503

The backend can't reach Vertex AI — usually missing Application Default Credentials or a wrong project id.

```bash
# Fix:
gcloud auth application-default login
grep BIGQUERY_PROJECT_ID .env   # must be your real project id
```

For the full symptom table, see Step 11 of [setup_new_environment_guide.md](setup_new_environment_guide.md).

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
│   ├── pricing.defaults.json   Shipped default rates (tracked; never edited at runtime)
│   ├── pricing.json            Runtime user-owned pricing (gitignored; seeded from
│   │                           pricing.defaults.json on first run; edited via UI)
│   └── static/                 Compiled React SPA (created by `npm run build`)
├── frontend/                   Vite + React 19 SPA
│   ├── src/
│   │   ├── App.jsx             Root component; tabs, theme, sync state, auth modal
│   │   ├── components/         One component per tab + shared UI pieces
│   │   └── utils/              apiFetch helper, formatters
│   ├── vite.config.js          Dev proxy → 127.0.0.1:8000; build → ../backend/static
│   └── package.json
├── tests/                      pytest suite (16 test modules)
├── update.sh                   One-command update: pull → deps → build → BQ migration
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
