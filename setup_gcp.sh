#!/usr/bin/env bash
#
# setup_gcp.sh — guided, idempotent GCP setup for the Vertex AI consumption portal.
#
# Automates Steps 3-7 of setup_new_environment_guide.md (APIs, Vertex audit logs,
# BigQuery dataset, Cloud Logging sink + writer grant, GCS bucket, .env), and
# optionally Steps 8-9 (frontend build, payload logging, test call, BigQuery view).
#
# Wizard flow: all questions up front -> summary -> single confirmation -> run.
# Every step is idempotent: re-running converges rather than duplicating.
#
# Usage:
#   ./setup_gcp.sh              # interactive
#   ./setup_gcp.sh --dry-run    # show what would run (read-only checks still execute)
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $arg (try --help)" >&2; exit 1 ;;
    esac
done

# ── Output helpers ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
    C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'
else
    C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""
fi

info()  { printf '%s\n' "$*"; }
ok()    { printf '%s  ok%s  %s\n' "$C_GREEN" "$C_RESET" "$*"; }
skip()  { printf '%sskip%s  %s\n' "$C_DIM" "$C_RESET" "$*"; }
warn()  { printf '%swarn%s  %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
die()   { printf '\n%serror%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }
step()  { printf '\n%s%s%s\n' "$C_BOLD$C_BLUE" "$*" "$C_RESET"; }
rule()  { printf '%s%s%s\n' "$C_DIM" "────────────────────────────────────────────────────────────────" "$C_RESET"; }

# run <description> <command...> — honours --dry-run for mutating commands only.
run() {
    local desc="$1"; shift
    if $DRY_RUN; then
        printf '%s would run%s  %s\n' "$C_DIM" "$C_RESET" "$desc"
        printf '%s           $ %s%s\n' "$C_DIM" "$*" "$C_RESET"
        return 0
    fi
    "$@"
}

ask() { # ask <var> <prompt> <default>
    local __var="$1" __prompt="$2" __default="${3-}" __reply
    if [ -n "$__default" ]; then
        read -r -p "$__prompt [$__default]: " __reply || true
        __reply="${__reply:-$__default}"
    else
        read -r -p "$__prompt: " __reply || true
    fi
    printf -v "$__var" '%s' "$__reply"
}

ask_yn() { # ask_yn <var> <prompt> <default y|n>
    local __var="$1" __prompt="$2" __default="$3" __reply __hint
    [ "$__default" = "y" ] && __hint="Y/n" || __hint="y/N"
    while true; do
        read -r -p "$__prompt ($__hint): " __reply || true
        __reply="${__reply:-$__default}"
        case "$__reply" in
            [Yy]|[Yy][Ee][Ss]) printf -v "$__var" '%s' "true"; return 0 ;;
            [Nn]|[Nn][Oo])     printf -v "$__var" '%s' "false"; return 0 ;;
            *) echo "Please answer y or n." ;;
        esac
    done
}

# ── Preflight ────────────────────────────────────────────────────────────────
step "Preflight"

for cmd in gcloud bq python3; do
    command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' not found on PATH. Install the Google Cloud SDK (gcloud + bq) and Python 3."
done
ok "gcloud, bq, python3 present"

ACTIVE_ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
if [ -z "$ACTIVE_ACCOUNT" ] || [ "$ACTIVE_ACCOUNT" = "(unset)" ]; then
    die "No active gcloud account. Run: gcloud auth login"
fi
ok "gcloud account: $ACTIVE_ACCOUNT"

if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    warn "Application Default Credentials are not set up."
    warn "The backend and the Python setup scripts need them. Run: gcloud auth application-default login"
    ask_yn ADC_CONTINUE "Continue anyway?" "n"
    [ "$ADC_CONTINUE" = "true" ] || exit 1
else
    ok "Application Default Credentials present"
fi

DEFAULT_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
[ "$DEFAULT_PROJECT" = "(unset)" ] && DEFAULT_PROJECT=""

# ── Questions ────────────────────────────────────────────────────────────────
step "Configuration"
info "${C_DIM}Answer everything up front; nothing is applied until you confirm.${C_RESET}"
echo

ask PROJECT_ID "GCP project ID" "$DEFAULT_PROJECT"
[ -n "$PROJECT_ID" ] || die "Project ID is required."

ask BQ_LOCATION      "BigQuery dataset / GCS bucket location" "US"
ask DATASET          "BigQuery dataset name" "vertex_ai_user_telemetry"
ask VIEW_NAME        "BigQuery view name" "user_token_chargebacks"
ask SINK_NAME        "Cloud Logging sink name" "vertex-ai-telemetry-sink"

echo
info "Sink writer identity needs write access to the dataset."
info "  ${C_DIM}dataset  = dataset-level WRITER (least privilege, recommended)${C_RESET}"
info "  ${C_DIM}project  = project-wide roles/bigquery.dataEditor (broader, simpler)${C_RESET}"
while true; do
    ask GRANT_SCOPE "Grant scope (dataset/project)" "dataset"
    case "$GRANT_SCOPE" in dataset|project) break ;; *) echo "Enter 'dataset' or 'project'." ;; esac
done

echo
ask_yn CREATE_BUCKET "Create a GCS bucket for portal config persistence? (recommended)" "y"
if [ "$CREATE_BUCKET" = "true" ]; then
    ask BUCKET_NAME "GCS bucket name" "vertex-ai-finops-$PROJECT_ID"
else
    BUCKET_NAME=""
    warn "Without GCS, budgets/pricing/logging config are local files — ephemeral on Cloud Run."
fi

echo
ask GEMINI_MODEL  "Model for the FinOps Assistant + test call" "gemini-3.6-flash"
ask VERTEX_REGION "Vertex AI region ('global' is cheapest)" "global"
ask SAMPLING_RATE "Payload logging sampling rate (0.0-1.0)" "1.0"
ask PORT          "Local server port" "8000"

echo
info "PORTAL_AUTH_TOKEN protects every /api/* endpoint. Empty = unauthenticated dev mode."
ask_yn GEN_TOKEN "Generate a strong bearer token now?" "y"
if [ "$GEN_TOKEN" = "true" ]; then
    if command -v openssl >/dev/null 2>&1; then
        PORTAL_AUTH_TOKEN="$(openssl rand -base64 32)"
    else
        PORTAL_AUTH_TOKEN="$(python3 -c 'import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())')"
    fi
else
    ask PORTAL_AUTH_TOKEN "Bearer token (blank = unauthenticated dev mode)" ""
    [ -n "$PORTAL_AUTH_TOKEN" ] || warn "Running unauthenticated — never expose this port beyond localhost."
fi

echo
ask_yn RECONCILE "If existing resources differ from the documented config, update them?" "y"

echo
ask_yn DO_FRONTEND "Build the React frontend (npm install && npm run build)?" "y"
ask_yn DO_BOOTSTRAP "Bootstrap telemetry (enable payload logging, test call, create BigQuery view)?" "y"

DO_VERIFY=false
# 20, not 15: on a genuinely fresh project the first export was measured at ~17-18
# min (the audit config itself has to propagate before any call is even logged).
# A 15-min cap expired and emitted an alarming warning on a run that was healthy.
VERIFY_WAIT_MIN=20
if [ "$DO_BOOTSTRAP" = "true" ]; then
    echo
    info "The Cloud Logging sink takes 5-15 min to export the first audit entry"
    info "(longer on a brand-new project — the audit config must propagate first)."
    info "${C_DIM}Setup can wait for it, then run verify_telemetry.py to prove attribution works.${C_RESET}"
    ask_yn DO_VERIFY "Wait and verify automatically after setup?" "y"
    [ "$DO_VERIFY" = "true" ] && ask VERIFY_WAIT_MIN "Maximum minutes to wait" "20"
fi

ENV_ACTION="write"
if [ -f .env ]; then
    echo
    warn ".env already exists."
    ask_yn OVERWRITE_ENV "Rewrite it? (a timestamped backup is kept)" "y"
    [ "$OVERWRITE_ENV" = "true" ] || ENV_ACTION="keep"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
step "Review"
rule
printf '  %-28s %s\n' "Project"                "$PROJECT_ID"
printf '  %-28s %s\n' "Account"                "$ACTIVE_ACCOUNT"
printf '  %-28s %s\n' "Location"               "$BQ_LOCATION"
printf '  %-28s %s\n' "BigQuery dataset"       "$PROJECT_ID:$DATASET"
printf '  %-28s %s\n' "BigQuery view"          "$VIEW_NAME"
printf '  %-28s %s\n' "Logging sink"           "$SINK_NAME"
printf '  %-28s %s\n' "Sink writer grant"      "$GRANT_SCOPE"
printf '  %-28s %s\n' "GCS bucket"             "${BUCKET_NAME:-<none — local file fallback>}"
printf '  %-28s %s\n' "Assistant model"        "$GEMINI_MODEL"
printf '  %-28s %s\n' "Vertex region"          "$VERTEX_REGION"
printf '  %-28s %s\n' "Sampling rate"          "$SAMPLING_RATE"
printf '  %-28s %s\n' "Server port"            "$PORT"
if [ "$ENV_ACTION" = "keep" ]; then
    printf '  %-28s %s\n' "Auth token" "${C_DIM}unchanged — existing .env is kept${C_RESET}"
elif [ -n "$PORTAL_AUTH_TOKEN" ]; then
    printf '  %-28s %s\n' "Auth token" "${PORTAL_AUTH_TOKEN:0:6}… (written to .env)"
else
    printf '  %-28s %s\n' "Auth token" "${C_YELLOW}none — unauthenticated${C_RESET}"
fi
printf '  %-28s %s\n' ".env"                   "$([ "$ENV_ACTION" = write ] && echo "write (backup existing)" || echo "leave unchanged")"
printf '  %-28s %s\n' "Build frontend"         "$DO_FRONTEND"
printf '  %-28s %s\n' "Bootstrap telemetry"    "$DO_BOOTSTRAP"
printf '  %-28s %s\n' "Wait + verify"          "$([ "$DO_VERIFY" = true ] && echo "yes (up to ${VERIFY_WAIT_MIN} min)" || echo "no")"
printf '  %-28s %s\n' "Reconcile existing"     "$RECONCILE"
rule
echo
info "This will enable APIs, modify the project's Vertex AI audit log config, and create"
info "a dataset, a logging sink${BUCKET_NAME:+, a GCS bucket} and IAM grants in ${C_BOLD}$PROJECT_ID${C_RESET}."
$DRY_RUN && info "${C_YELLOW}--dry-run: nothing will actually be created.${C_RESET}"
echo
read -r -p "Type 'yes' to proceed: " CONFIRM || true
[ "$CONFIRM" = "yes" ] || { echo "Aborted."; exit 1; }

# ── Step 1: Billing ──────────────────────────────────────────────────────────
step "1/10 Billing"
BILLING="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null || echo "unknown")"
case "$BILLING" in
    True|true) ok "Billing is enabled" ;;
    unknown)   warn "Could not read billing status (missing permission or Billing API). Continuing." ;;
    *)         die "Billing is not enabled on $PROJECT_ID. Link a billing account first — Vertex AI and BigQuery will fail without it." ;;
esac

# ── Step 2: APIs ─────────────────────────────────────────────────────────────
step "2/10 Enable APIs"
APIS=(
    aiplatform.googleapis.com
    bigquery.googleapis.com
    storage.googleapis.com
    logging.googleapis.com
    run.googleapis.com
    cloudbuild.googleapis.com
    artifactregistry.googleapis.com
)
ENABLED="$(gcloud services list --enabled --project="$PROJECT_ID" --format='value(config.name)' 2>/dev/null || true)"
TO_ENABLE=()
for api in "${APIS[@]}"; do
    grep -qx "$api" <<<"$ENABLED" || TO_ENABLE+=("$api")
done
if [ ${#TO_ENABLE[@]} -eq 0 ]; then
    skip "all ${#APIS[@]} APIs already enabled"
else
    info "Enabling: ${TO_ENABLE[*]}"
    run "enable ${#TO_ENABLE[@]} API(s)" gcloud services enable "${TO_ENABLE[@]}" --project="$PROJECT_ID"
    ok "APIs enabled (propagation can take 1-2 minutes)"
    $DRY_RUN || sleep 10
fi

# ── Step 3: Vertex AI Data Access audit logs ─────────────────────────────────
# Merge-only: the aiplatform entry is unioned into the existing auditConfigs so
# other services' audit settings survive. set-iam-policy is skipped entirely when
# nothing needs to change — it is a full-policy atomic replace (last writer wins).
step "3/10 Vertex AI Data Access audit logs"
POLICY_IN="$(mktemp)"; POLICY_OUT="$(mktemp)"
trap 'rm -f "$POLICY_IN" "$POLICY_OUT"' EXIT

gcloud projects get-iam-policy "$PROJECT_ID" --format=json > "$POLICY_IN"

set +e
python3 - "$POLICY_IN" "$POLICY_OUT" <<'PY'
import json, sys

src, dst = sys.argv[1], sys.argv[2]
SERVICE = "aiplatform.googleapis.com"
WANT = {"DATA_READ", "DATA_WRITE", "ADMIN_READ"}

policy = json.load(open(src))
configs = policy.get("auditConfigs", [])

for entry in configs:
    if entry.get("service") == SERVICE:
        have = {c["logType"] for c in entry.get("auditLogConfigs", [])}
        if WANT <= have:
            sys.exit(3)  # already correct — do not rewrite the policy
        entry["auditLogConfigs"] = [{"logType": t} for t in sorted(have | WANT)]
        break
else:
    configs.append({"service": SERVICE,
                    "auditLogConfigs": [{"logType": t} for t in sorted(WANT)]})

policy["auditConfigs"] = configs
with open(dst, "w") as f:
    json.dump(policy, f, indent=2)
PY
POLICY_RC=$?
set -e

case "$POLICY_RC" in
    3) skip "DATA_READ, DATA_WRITE, ADMIN_READ already enabled for aiplatform.googleapis.com" ;;
    0)
        warn "set-iam-policy replaces the FULL project IAM policy atomically."
        warn "Do not run concurrent IAM changes against $PROJECT_ID right now."
        run "apply merged IAM policy" gcloud projects set-iam-policy "$PROJECT_ID" "$POLICY_OUT" --quiet >/dev/null
        ok "Vertex AI Data Access audit logs enabled"
        ;;
    *) die "Failed to compute the merged IAM policy (python exit $POLICY_RC)." ;;
esac

# ── Step 4: BigQuery dataset ─────────────────────────────────────────────────
step "4/10 BigQuery dataset"
if bq --project_id="$PROJECT_ID" show --format=none "$PROJECT_ID:$DATASET" >/dev/null 2>&1; then
    skip "dataset $DATASET already exists"
else
    run "create dataset $DATASET" bq --project_id="$PROJECT_ID" --location="$BQ_LOCATION" mk --dataset \
        --description "Vertex AI Consumption Telemetry" "$PROJECT_ID:$DATASET"
    ok "dataset $PROJECT_ID:$DATASET created in $BQ_LOCATION"
fi

# ── Step 5: Cloud Logging sink ───────────────────────────────────────────────
# Cloud Logging has no LIKE operator — ':' means "contains". A LIKE filter compiles
# silently and matches nothing, so the audit table never appears.
step "5/10 Cloud Logging sink"
SINK_FILTER='log_id("cloudaudit.googleapis.com/data_access") AND protoPayload.serviceName="aiplatform.googleapis.com" AND (protoPayload.methodName:"GenerateContent" OR protoPayload.methodName:"Predict")'
SINK_DEST="bigquery.googleapis.com/projects/$PROJECT_ID/datasets/$DATASET"

if gcloud logging sinks describe "$SINK_NAME" --project="$PROJECT_ID" --format=none >/dev/null 2>&1; then
    EXISTING_FILTER="$(gcloud logging sinks describe "$SINK_NAME" --project="$PROJECT_ID" --format='value(filter)')"
    EXISTING_DEST="$(gcloud logging sinks describe "$SINK_NAME" --project="$PROJECT_ID" --format='value(destination)')"
    if [ "$EXISTING_FILTER" = "$SINK_FILTER" ] && [ "$EXISTING_DEST" = "$SINK_DEST" ]; then
        skip "sink $SINK_NAME already matches the documented config"
    elif [ "$RECONCILE" = "true" ]; then
        warn "sink $SINK_NAME exists with a different config — updating"
        [ "$EXISTING_DEST" != "$SINK_DEST" ] && info "  destination: $EXISTING_DEST -> $SINK_DEST"
        [ "$EXISTING_FILTER" != "$SINK_FILTER" ] && info "  filter changed"
        run "update sink $SINK_NAME" gcloud logging sinks update "$SINK_NAME" "$SINK_DEST" \
            --project="$PROJECT_ID" --log-filter="$SINK_FILTER" --quiet >/dev/null
        ok "sink updated"
    else
        warn "sink $SINK_NAME exists with a different config and reconcile is off — leaving as is"
        warn "Attribution may not work until the filter/destination match the guide."
    fi
else
    run "create sink $SINK_NAME" gcloud logging sinks create "$SINK_NAME" "$SINK_DEST" \
        --project="$PROJECT_ID" --use-partitioned-tables --log-filter="$SINK_FILTER" >/dev/null
    ok "sink $SINK_NAME created (partitioned tables)"
fi

# ── Step 6: Sink writer grant ────────────────────────────────────────────────
# The single most-missed step in the guide. Without it, exports fail SILENTLY:
# the sink reports success and no table ever appears in BigQuery.
step "6/10 Sink writer access"
if $DRY_RUN && ! gcloud logging sinks describe "$SINK_NAME" --project="$PROJECT_ID" --format=none >/dev/null 2>&1; then
    skip "sink does not exist yet (dry run) — writer grant would follow sink creation"
else
    SINK_SA="$(gcloud logging sinks describe "$SINK_NAME" --project="$PROJECT_ID" --format='value(writerIdentity)')"
    [ -n "$SINK_SA" ] || die "Sink $SINK_NAME has no writerIdentity."
    SINK_SA_EMAIL="${SINK_SA#serviceAccount:}"
    info "writer identity: $SINK_SA_EMAIL"

    if [ "$GRANT_SCOPE" = "project" ]; then
        run "grant roles/bigquery.dataEditor" gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="$SINK_SA" --role="roles/bigquery.dataEditor" --condition=None --quiet >/dev/null
        ok "$SINK_SA_EMAIL granted roles/bigquery.dataEditor (project-wide)"
    else
        DS_IN="$(mktemp)"; DS_OUT="$(mktemp)"
        bq --project_id="$PROJECT_ID" show --format=prettyjson "$PROJECT_ID:$DATASET" > "$DS_IN"

        set +e
        SINK_SA_EMAIL="$SINK_SA_EMAIL" python3 - "$DS_IN" "$DS_OUT" <<'PY'
import json, os, sys

src, dst = sys.argv[1], sys.argv[2]
sa = os.environ["SINK_SA_EMAIL"]

info = json.load(open(src))
access = info.get("access", [])
entry = {"role": "WRITER", "userByEmail": sa}

for a in access:
    if a.get("userByEmail") == sa and a.get("role") in ("WRITER", "OWNER", "roles/bigquery.dataEditor", "roles/bigquery.dataOwner"):
        sys.exit(3)  # already granted

access.append(entry)
with open(dst, "w") as f:
    json.dump({"access": access}, f)
PY
        DS_RC=$?
        set -e

        case "$DS_RC" in
            3) skip "$SINK_SA_EMAIL already has write access on $DATASET" ;;
            0)
                run "grant dataset WRITER" bq --project_id="$PROJECT_ID" update --source "$DS_OUT" "$PROJECT_ID:$DATASET" >/dev/null
                ok "$SINK_SA_EMAIL granted WRITER on $DATASET"
                ;;
            *) rm -f "$DS_IN" "$DS_OUT"; die "Failed to compute the dataset access list (python exit $DS_RC)." ;;
        esac
        rm -f "$DS_IN" "$DS_OUT"
    fi
fi

# ── Step 7: GCS bucket ───────────────────────────────────────────────────────
step "7/10 GCS bucket"
if [ "$CREATE_BUCKET" != "true" ]; then
    skip "bucket creation declined — portal will use local file fallback"
elif gcloud storage buckets describe "gs://$BUCKET_NAME" --format=none >/dev/null 2>&1; then
    skip "bucket gs://$BUCKET_NAME already exists"
else
    run "create bucket gs://$BUCKET_NAME" gcloud storage buckets create "gs://$BUCKET_NAME" \
        --project="$PROJECT_ID" --location="$BQ_LOCATION" >/dev/null
    ok "bucket gs://$BUCKET_NAME created"
fi

# ── Step 8: .env ─────────────────────────────────────────────────────────────
step "8/10 Application environment (.env)"
if [ "$ENV_ACTION" = "keep" ]; then
    skip ".env left unchanged"
elif $DRY_RUN; then
    skip "would write .env (BIGQUERY_PROJECT_ID=$PROJECT_ID, dataset=$DATASET, bucket=${BUCKET_NAME:-<none>})"
else
    if [ -f .env ]; then
        BACKUP=".env.backup.$(date +%Y%m%d-%H%M%S)"
        cp .env "$BACKUP"
        ok "existing .env backed up to $BACKUP"
    fi
    umask 077
    cat > .env <<EOF
# Generated by setup_gcp.sh — see setup_new_environment_guide.md Step 7 for the
# annotated reference. This file is gitignored; it holds a secret.

# ── Server ───────────────────────────────────────────────────────────────────
PORT=$PORT

# ── BigQuery ─────────────────────────────────────────────────────────────────
BIGQUERY_PROJECT_ID=$PROJECT_ID
BIGQUERY_DATASET=$DATASET
BIGQUERY_VIEW=$VIEW_NAME

# ── Cloud Storage ────────────────────────────────────────────────────────────
# Empty = local file fallback (ephemeral on Cloud Run).
GCS_BUCKET_NAME=$BUCKET_NAME

# ── Vertex AI ────────────────────────────────────────────────────────────────
VERTEX_REGION=$VERTEX_REGION
GEMINI_MODEL=$GEMINI_MODEL

# ── Authentication ───────────────────────────────────────────────────────────
# Bearer token for every /api/* endpoint. Empty = unauthenticated dev mode.
PORTAL_AUTH_TOKEN=$PORTAL_AUTH_TOKEN

# ── Logging behaviour ────────────────────────────────────────────────────────
APPLY_LOGGING_ON_STARTUP=false
LOGGING_SAMPLING_RATE=$SAMPLING_RATE

# ── CORS ─────────────────────────────────────────────────────────────────────
CORS_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# ── Fallback identity (read by setup_bigquery_view.py, not the server) ───────
# FALLBACK_IDENTITY=unattributed@unknown
EOF
    chmod 600 .env
    ok ".env written (mode 600)"
fi

# ── Step 9: Application bootstrap ────────────────────────────────────────────
step "9/10 Application bootstrap"

PY_BIN="python3"
[ -x "$REPO_DIR/.venv/bin/python" ] && PY_BIN="$REPO_DIR/.venv/bin/python"

if [ "$DO_FRONTEND" = "true" ]; then
    if ! command -v npm >/dev/null 2>&1; then
        warn "npm not found — skipping frontend build. Install Node, then: npm --prefix frontend install && npm --prefix frontend run build"
    else
        info "Building frontend (output: backend/static/)…"
        run "npm install"    npm --prefix frontend install
        run "npm run build"  npm --prefix frontend run build
        ok "frontend built"
    fi
else
    skip "frontend build declined"
fi

if [ "$DO_BOOTSTRAP" != "true" ]; then
    skip "telemetry bootstrap declined"
elif $DRY_RUN; then
    skip "would run: enable-logging.py, trigger_call.py, setup_bigquery_view.py"
elif ! "$PY_BIN" -c "import vertexai, dotenv, google.cloud.bigquery" >/dev/null 2>&1; then
    warn "Python deps missing for $PY_BIN — skipping telemetry bootstrap."
    warn "Install them, then re-run: pip install -r requirements.txt"
    DO_BOOTSTRAP=false
else
    # Braces are required: bash 3.2 folds the following multibyte character into
    # the variable name, producing an "unbound variable" abort under `set -u`.
    info "Enabling Vertex AI payload logging for ${GEMINI_MODEL}…"
    if run "enable payload logging" "$PY_BIN" enable-logging.py; then
        ok "payload logging enabled"
    else
        warn "enable-logging.py failed — you can enable it later in the portal's Model Logging tab."
    fi

    info "Making a test call to trigger both telemetry streams…"
    "$PY_BIN" trigger_call.py || warn "trigger_call.py failed — check model availability and ADC."

    info "Waiting for request_response_logging to appear (up to 5 min)…"
    FOUND=false
    for _ in $(seq 1 30); do
        if bq --project_id="$PROJECT_ID" show --format=none "$PROJECT_ID:$DATASET.request_response_logging" >/dev/null 2>&1; then
            FOUND=true; break
        fi
        sleep 10
    done
    if $FOUND; then
        ok "request_response_logging exists"
    else
        warn "request_response_logging has not appeared yet. This is recoverable —"
        warn "re-run '$PY_BIN setup_bigquery_view.py' once it does."
    fi

    info "Creating the BigQuery view…"
    "$PY_BIN" setup_bigquery_view.py || warn "setup_bigquery_view.py failed — re-run it manually."
fi

# ── What was set up ──────────────────────────────────────────────────────────
step "Setup complete"
rule
info "${C_BOLD}Provisioned in $PROJECT_ID:${C_RESET}"
info "  • APIs enabled and Vertex AI Data Access audit logging on"
info "  • BigQuery dataset ${C_BOLD}$DATASET${C_RESET} ($BQ_LOCATION)"
info "  • Logging sink ${C_BOLD}$SINK_NAME${C_RESET} -> the dataset, writer granted ($GRANT_SCOPE scope)"
[ -n "$BUCKET_NAME" ] && info "  • GCS bucket ${C_BOLD}gs://$BUCKET_NAME${C_RESET} for portal config"
[ "$ENV_ACTION" = "write" ] && info "  • .env written (mode 600)"
[ "$DO_FRONTEND" = "true" ] && info "  • Frontend built into backend/static/"
if [ "$DO_BOOTSTRAP" = "true" ]; then
    info "  • Payload logging on for ${C_BOLD}$GEMINI_MODEL${C_RESET}, test call made, view ${C_BOLD}$VIEW_NAME${C_RESET} created"
fi
echo
info "Start the portal:"
info "  ${C_BOLD}$PY_BIN -m uvicorn backend.main:app --host 127.0.0.1 --port $PORT${C_RESET}"
info "  then open http://127.0.0.1:$PORT"
[ -n "$PORTAL_AUTH_TOKEN" ] && info "  the token modal expects PORTAL_AUTH_TOKEN from .env"
rule

if [ "$DO_BOOTSTRAP" != "true" ]; then
    echo
    info "${C_BOLD}Remaining manual steps:${C_RESET}"
    info "  1. Portal -> Model Logging tab -> tick ${C_BOLD}$GEMINI_MODEL${C_RESET} -> Apply Logging Settings."
    info "     (The repo default pre-checks gemini-2.5-pro only.)"
    info "  2. $PY_BIN trigger_call.py"
    info "  3. $PY_BIN setup_bigquery_view.py"
fi

# ── Step 10: Wait for the audit export, then verify ──────────────────────────
# The sink is the slow link (5-15 min) and is forward-only. Rather than sleeping
# blindly, poll for the audit table and short-circuit the moment it lands.
VERIFY_CMD="$PY_BIN verify_telemetry.py --fix"

if $DRY_RUN; then
    # verify_telemetry.py makes a real, billable Vertex AI call and writes telemetry.
    # It must never run under --dry-run, regardless of the DO_VERIFY answer.
    step "10/10 Verification"
    if [ "$DO_VERIFY" = "true" ]; then
        skip "would wait up to ${VERIFY_WAIT_MIN} min for the audit export, then run: $VERIFY_CMD"
    else
        skip "automatic verification declined"
    fi
    rule
    info "${C_YELLOW}Dry run — no changes were made.${C_RESET}"
    exit 0
fi

if [ "$DO_VERIFY" != "true" ]; then
    step "10/10 Verification"
    skip "automatic verification declined"
    info "  Wait ~15 min for the sink's first export, then run:"
    info "    ${C_BOLD}$VERIFY_CMD${C_RESET}"
    info "  Until the audit table lands the view stays in fallback mode and every"
    info "  row shows unattributed@unknown. Sinks never backfill."
    rule
    exit 0
fi

step "10/10 Waiting for the audit export"

if [ ! -f verify_telemetry.py ]; then
    warn "verify_telemetry.py not found — skipping verification."
    info "  Re-run manually once the audit table lands: ${C_BOLD}$VERIFY_CMD${C_RESET}"
    exit 0
fi

AUDIT_TABLE_PREFIX="cloudaudit_googleapis_com_data_access"
audit_landed() {
    bq --project_id="$PROJECT_ID" ls --max_results=1000 "$PROJECT_ID:$DATASET" 2>/dev/null \
        | grep -q "$AUDIT_TABLE_PREFIX"
}

# Ctrl-C during the wait must not look like setup failed — everything above is done.
trap 'echo; echo; warn "Wait interrupted — setup itself is complete and intact.";
      info "  Run this once the audit table lands: ${C_BOLD}$VERIFY_CMD${C_RESET}"; exit 0' INT

info "The sink exports the first Data Access entry 5-15 min after the test call."
info "${C_DIM}Polling every 30s, up to ${VERIFY_WAIT_MIN} min. Ctrl-C to stop waiting — setup is already done.${C_RESET}"
echo

DEADLINE=$(( $(date +%s) + VERIFY_WAIT_MIN * 60 ))
LANDED=false
while true; do
    if audit_landed; then LANDED=true; break; fi
    NOW=$(date +%s)
    [ "$NOW" -ge "$DEADLINE" ] && break
    REMAIN=$(( DEADLINE - NOW ))
    if [ -t 1 ]; then
        printf '\r  %s waiting for audit table — %02d:%02d remaining %s' \
            "$C_DIM" $((REMAIN / 60)) $((REMAIN % 60)) "$C_RESET"
    else
        printf '  waiting for audit table — %d min remaining\n' $(( (REMAIN + 59) / 60 ))
    fi
    sleep 30
done
[ -t 1 ] && printf '\r%*s\r' 60 ""
trap - INT

if $LANDED; then
    ok "audit table has landed"
else
    warn "audit table still absent after ${VERIFY_WAIT_MIN} min — running verification anyway"
    warn "so it can pinpoint which link is broken."
fi

step "Verification"
set +e
$VERIFY_CMD
VERIFY_RC=$?
set -e

echo
rule
if [ "$VERIFY_RC" -eq 0 ]; then
    ok "${C_BOLD}End-to-end verified.${C_RESET} Telemetry is flowing and calls attribute to real principals."
    info "  Portal: ${C_BOLD}$PY_BIN -m uvicorn backend.main:app --host 127.0.0.1 --port $PORT${C_RESET}"
else
    warn "${C_BOLD}Verification did not fully pass.${C_RESET} Setup itself completed — see the FAIL lines above."
    info "  Most common cause on a fresh project: the sink's first export has not"
    info "  arrived yet. Sinks take 5-15 min and never backfill."
    info "  Re-run when ready: ${C_BOLD}$VERIFY_CMD${C_RESET}"
fi
rule
exit "$VERIFY_RC"
