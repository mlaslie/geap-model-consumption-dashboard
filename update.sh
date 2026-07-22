#!/usr/bin/env bash
# update.sh — graceful application update for the Vertex AI token-consumption portal
#
# What this script PRESERVES (nothing below is ever touched by this script):
#   .env                    — GCP credentials and portal configuration
#   backend/pricing.json    — user-edited pricing table (untracked; seeded from
#                             pricing.defaults.json on first run if absent)
#   budgets.json            — local budget-rules fallback
#   logging_config.json     — local payload-logging-config fallback
#   estimates.json          — local financial-planning estimates fallback
#   model_sync.json         — model catalogue sync state
#   GCS-stored config       — budgets / logging / estimates stored in your
#                             GCS_BUCKET_NAME bucket are never touched locally
#
# What it does:
#   a) Preflight checks (git repo, clean tracked working tree)
#   b) git pull --ff-only  (fast-forward only; aborts if history diverges)
#   c) Python venv + pip install -r requirements.txt
#   d) Frontend: npm install + npm run build
#   e) BigQuery view migration (idempotent CREATE OR REPLACE; warns on failure)
#   f) Summary: preserved state, new version, restart reminder

set -euo pipefail

###############################################################################
# Helpers
###############################################################################

info()  { echo "[update] $*"; }
warn()  { echo "[update] WARNING: $*" >&2; }
abort() { echo "[update] ERROR: $*" >&2; exit 1; }

###############################################################################
# a) Preflight
###############################################################################

info "Checking environment..."

# Must be inside a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    abort "Not inside a git repository. Run this script from the repo root."
fi

# Repo root — all subsequent paths are relative to here
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Only tracked-file modifications should block the pull.  Untracked state files
# (.env, pricing.json, budgets.json, etc.) are gitignored and are fine.
DIRTY_TRACKED="$(git status --porcelain | grep -v '^??' || true)"
if [ -n "$DIRTY_TRACKED" ]; then
    abort "You have uncommitted changes to tracked files:
$DIRTY_TRACKED

Stash or commit these local code changes before running ./update.sh:
  git stash        # saves changes; restore with: git stash pop
  git commit -am \"my changes\"
Your .env, pricing.json, budgets.json, and other state files are not affected."
fi

info "Preflight OK — working tree is clean."

###############################################################################
# b) git pull --ff-only
###############################################################################

info "Pulling latest changes (fast-forward only)..."
if ! git pull --ff-only; then
    abort "git pull --ff-only failed.  This usually means your local branch has
commits that are not in the upstream branch (histories have diverged).
Options:
  git fetch && git log --oneline HEAD..@{u}   # see what's upstream
  git rebase @{u}                              # replay your commits on top
  git reset --hard @{u}                        # DISCARD local commits (destructive)"
fi
info "Pull complete. $(git log -1 --oneline)"

###############################################################################
# c) Python virtualenv + dependencies
###############################################################################

info "Setting up Python virtualenv..."
if [ ! -d ".venv" ]; then
    info ".venv not found — creating with python3..."
    python3 -m venv .venv
fi

info "Installing Python dependencies..."
.venv/bin/pip install -r requirements.txt -q
info "Python dependencies up to date."

###############################################################################
# d) Frontend build
###############################################################################

info "Building frontend..."
(
    cd frontend
    npm install --no-audit --no-fund
    npm run build
)
info "Frontend build complete → backend/static/"

###############################################################################
# e) BigQuery view migration (idempotent — warns on failure, does not abort)
###############################################################################

info "Running BigQuery view migration (idempotent CREATE OR REPLACE)..."
if .venv/bin/python setup_bigquery_view.py; then
    info "BigQuery view migration succeeded."
else
    warn "BigQuery view migration failed.  The application may still start on
the previous view schema.  If /api/usage returns HTTP 500 after restart,
re-run:  .venv/bin/python setup_bigquery_view.py"
fi

###############################################################################
# f) Summary
###############################################################################

NEW_VERSION="$(git log -1 --oneline)"

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  Update complete                                                 ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  New version : $NEW_VERSION"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Preserved state                                                 ║"
echo "║    .env                  (GCP credentials + portal config)       ║"
echo "║    backend/pricing.json  (user pricing table — untracked)        ║"
echo "║    budgets.json          (local budget-rules fallback)           ║"
echo "║    logging_config.json   (local payload-logging-config fallback) ║"
echo "║    estimates.json        (local estimates fallback)              ║"
echo "║    model_sync.json       (model catalogue sync state)            ║"
echo "║    GCS bucket config     (budgets / logging / estimates in GCS)  ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Next steps                                                      ║"
echo "║                                                                   ║"
echo "║  Local / Docker:                                                  ║"
echo "║    uvicorn backend.main:app --host 127.0.0.1 --port 8000         ║"
echo "║    # or: docker build -t vertex-ai-consumption-portal . &&       ║"
echo "║    #       docker run -p 8000:8000 --env-file .env ...           ║"
echo "║                                                                   ║"
echo "║  Cloud Run (see setup guide Step 10):                            ║"
echo "║    gcloud builds submit --tag gcr.io/\$PROJECT_ID/vertex-ai-consumption-portal"
echo "║    gcloud run deploy vertex-consumption-portal \\                 ║"
echo "║        --image gcr.io/\$PROJECT_ID/vertex-ai-consumption-portal \\"
echo "║        --region us-central1 --no-allow-unauthenticated           ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
