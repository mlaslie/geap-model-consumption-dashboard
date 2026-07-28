#!/usr/bin/env python3
"""
verify_telemetry.py — end-to-end proof that the telemetry pipeline works.

Makes a real Vertex AI call, then follows it all the way through to an
*attributed* row in the chargeback view, reporting exactly which stage fails.

The two streams land on very different schedules: the payload row appears in
seconds, the Data Access audit entry takes 5-15 minutes to be exported by the
Cloud Logging sink. This script waits for both.

Usage:
  python verify_telemetry.py                  # full check, makes a test call
  python verify_telemetry.py --skip-call      # verify existing data only
  python verify_telemetry.py --fix            # re-run the view setup if it is
                                              #   still the fallback variant
  python verify_telemetry.py --timeout 30     # minutes to wait for the sink

Exit code 0 = fully attributed. 1 = something is wrong or incomplete.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.cloud import bigquery

load_dotenv()

PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID") or os.getenv("PROJECT_ID")
DATASET_ID = os.getenv("BIGQUERY_DATASET", "vertex_ai_user_telemetry")
VIEW_ID = os.getenv("BIGQUERY_VIEW", "user_token_chargebacks")
MODEL_ID = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
LOCATION = os.getenv("VERTEX_REGION", "global")
FALLBACK_IDENTITY = os.getenv("FALLBACK_IDENTITY", "unattributed@unknown")

AUDIT_PREFIX = "cloudaudit_googleapis_com_data_access"
PAYLOAD_TABLE = "request_response_logging"

_IDENT = re.compile(r"^[A-Za-z0-9_\-]+$")

# ── Output ───────────────────────────────────────────────────────────────────
_TTY = sys.stdout.isatty()
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s

RESULTS = []

def check(name, passed, detail="", fatal=False, fail_detail=""):
    """Record and print one check. Returns `passed` so callers can branch.

    `detail` always prints; `fail_detail` is remediation advice shown only when
    the check fails (printing a fix-it hint next to a PASS reads as a problem).
    """
    shown = detail if passed else (fail_detail or detail)
    RESULTS.append((name, passed, shown))
    mark = _c("32", " PASS ") if passed else _c("31", " FAIL ")
    print(f"[{mark}] {name}" + (f"\n         {shown}" if shown else ""))
    if fatal and not passed:
        summary()
        sys.exit(1)
    return passed

def warn(name, detail=""):
    RESULTS.append((name, None, detail))
    print(f"[{_c('33', ' WARN ')}] {name}" + (f"\n         {detail}" if detail else ""))

def section(title):
    print(f"\n{_c('1;34', title)}")

def summary():
    print("\n" + "─" * 64)
    failed = [n for n, p, _ in RESULTS if p is False]
    warned = [n for n, p, _ in RESULTS if p is None]
    passed = [n for n, p, _ in RESULTS if p is True]
    print(f"{len(passed)} passed, {len(failed)} failed, {len(warned)} warnings")
    for n in failed:
        print(f"  {_c('31', 'FAIL')}  {n}")
    for n in warned:
        print(f"  {_c('33', 'WARN')}  {n}")
    print("─" * 64)
    return len(failed) == 0


# ── Helpers ──────────────────────────────────────────────────────────────────
def q(client, sql, params=None):
    job_config = bigquery.QueryJobConfig(query_parameters=params or [])
    return list(client.query(sql, job_config=job_config).result())


def list_tables(client):
    return {t.table_id: t.table_type for t in client.list_tables(f"{PROJECT_ID}.{DATASET_ID}")}


def view_is_attributing(client):
    """True when the deployed view joins the audit table (not the fallback)."""
    view = client.get_table(f"{PROJECT_ID}.{DATASET_ID}.{VIEW_ID}")
    return AUDIT_PREFIX in (view.view_query or "")


def upgrade_view(client):
    """Re-run setup_bigquery_view.py and report whether the view now attributes.

    Called at two points: once up front, and again after the audit export lands —
    on a fresh environment the table usually appears *during* the audit wait, so
    an up-front-only attempt is always too early.
    """
    print("         re-running setup_bigquery_view.py…")
    rc = subprocess.run([sys.executable, "setup_bigquery_view.py"],
                        capture_output=True, text=True).returncode
    return rc == 0 and view_is_attributing(client)


def wait_for(label, predicate, timeout_s, interval_s=15):
    """Poll predicate() until truthy or timeout. Returns the truthy value or None."""
    deadline = time.time() + timeout_s
    first = True
    while True:
        try:
            value = predicate()
        except Exception as e:  # transient BQ / permission hiccups shouldn't abort the wait
            value = None
            if first:
                print(f"         ({label}: {type(e).__name__}, retrying)")
        if value:
            return value
        if time.time() >= deadline:
            return None
        if first:
            mins = int(timeout_s / 60)
            print(f"         waiting for {label} (up to {mins} min, checking every {interval_s}s)…")
            first = False
        else:
            print("         still waiting…", flush=True)
        time.sleep(interval_s)


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-call", action="store_true",
                    help="do not make a new Vertex AI call; verify existing data only")
    ap.add_argument("--fix", action="store_true",
                    help="re-run setup_bigquery_view.py when the audit table exists "
                         "but the deployed view is still the fallback variant")
    ap.add_argument("--timeout", type=int, default=20,
                    help="minutes to wait for the audit export (default 20)")
    args = ap.parse_args()

    if not PROJECT_ID:
        print("ERROR: BIGQUERY_PROJECT_ID / PROJECT_ID is not set (check .env).", file=sys.stderr)
        return 1
    for ident, label in ((DATASET_ID, "BIGQUERY_DATASET"), (VIEW_ID, "BIGQUERY_VIEW")):
        if not _IDENT.match(ident):
            print(f"ERROR: {label}={ident!r} contains unsafe characters.", file=sys.stderr)
            return 1

    print(f"Project : {PROJECT_ID}")
    print(f"Dataset : {DATASET_ID}")
    print(f"View    : {VIEW_ID}")
    print(f"Model   : {MODEL_ID} ({LOCATION})")

    client = bigquery.Client(project=PROJECT_ID)
    start = datetime.now(timezone.utc)

    # ── 1. Structure ─────────────────────────────────────────────────────────
    section("1. Structure")
    try:
        tables = list_tables(client)
    except Exception as e:
        check("dataset is readable", False, f"{type(e).__name__}: {e}", fatal=True)
        return 1
    check("dataset is readable", True, f"{len(tables)} objects in {DATASET_ID}")

    check(f"{PAYLOAD_TABLE} exists", PAYLOAD_TABLE in tables,
          fail_detail="Vertex AI creates this on the first logged call. Payload logging "
                      "is off for this model — enable it in the Model Logging tab.")

    audit_tables = [t for t in tables if t.startswith(AUDIT_PREFIX)]
    has_audit = bool(audit_tables)
    if has_audit:
        check("audit table exists", True, ", ".join(sorted(audit_tables)))
    else:
        warn("audit table exists",
             "Not exported yet. Sinks take 5-15 min and are forward-only. "
             "Until it lands, every row attributes to the fallback identity.")

    check(f"view {VIEW_ID} exists", tables.get(VIEW_ID) == "VIEW")

    attributing = view_is_attributing(client)
    if attributing:
        check("view is the attributing variant", True, "joins the audit table")
    elif has_audit and args.fix:
        # Transitional state — printed, not recorded, so a successful upgrade does
        # not leave a stale WARN sitting next to its own PASS in the summary.
        print("         view is still the fallback variant — upgrading now")
        attributing = upgrade_view(client)
        check("view upgraded to attributing variant", attributing)
    elif has_audit:
        check("view is the attributing variant", False,
              "The audit table exists but the deployed view is still the fallback. "
              "Re-run: python setup_bigquery_view.py   (or pass --fix)")
    else:
        warn("view is the attributing variant",
             "Fallback variant — expected while the audit table is still pending.")

    # ── 2. Test call ─────────────────────────────────────────────────────────
    section("2. Test call")
    if args.skip_call:
        print("         skipped (--skip-call)")
    else:
        try:
            import vertexai
            from vertexai.preview.generative_models import GenerativeModel
            vertexai.init(project=PROJECT_ID, location=LOCATION)
            model = GenerativeModel(MODEL_ID)
            resp = model.generate_content(
                f"Telemetry verification probe at {start.isoformat()}. Reply with one short sentence."
            )
            preview = (resp.text or "").strip().replace("\n", " ")[:70]
            check("Vertex AI call succeeded", True, f"{MODEL_ID}: {preview}…")
        except Exception as e:
            check("Vertex AI call succeeded", False, f"{type(e).__name__}: {e}", fatal=True)

    # ── 3. Payload stream ────────────────────────────────────────────────────
    section("3. Payload stream (seconds)")
    payload_sql = f"""
        SELECT COUNT(*) AS n
        FROM `{PROJECT_ID}.{DATASET_ID}.{PAYLOAD_TABLE}`
        WHERE logging_time >= @since
    """
    params = [bigquery.ScalarQueryParameter("since", "TIMESTAMP", start)]
    if args.skip_call:
        payload_sql = payload_sql.replace("WHERE logging_time >= @since", "")
        params = []

    got = wait_for("payload row", lambda: (q(client, payload_sql, params)[0].n or 0) > 0,
                   timeout_s=300, interval_s=10)
    check("payload row written", bool(got),
          fail_detail="request_response_logging received nothing. Payload logging "
                      "is probably disabled for this model.")

    # ── 4. Audit stream ──────────────────────────────────────────────────────
    section(f"4. Audit stream (5-15 min, waiting up to {args.timeout} min)")

    def audit_principal():
        rows = q(client, f"""
            SELECT protopayload_auditlog.authenticationInfo.principalEmail AS principal,
                   COUNT(*) AS n
            FROM `{PROJECT_ID}.{DATASET_ID}.{AUDIT_PREFIX}*`
            WHERE protopayload_auditlog.serviceName = 'aiplatform.googleapis.com'
              AND protopayload_auditlog.authenticationInfo.principalEmail IS NOT NULL
              AND timestamp >= @since
            GROUP BY principal
            ORDER BY n DESC
            LIMIT 1
        """, [bigquery.ScalarQueryParameter(
            "since", "TIMESTAMP", start if not args.skip_call else datetime(1970, 1, 1, tzinfo=timezone.utc))])
        return rows[0].principal if rows else None

    principal = wait_for("audit export", audit_principal, timeout_s=args.timeout * 60, interval_s=30)
    if principal:
        check("audit entry exported to BigQuery", True, f"principal: {principal}")
    else:
        check("audit entry exported to BigQuery", False,
              fail_detail="No audit rows yet. If it has been well past 15 minutes, check: "
                          "the sink writer SA has dataset WRITER (fails silently without it), "
                          "the sink filter uses ':' not LIKE, and Data Access logs are enabled "
                          "for aiplatform.googleapis.com.")

    # ── 5. Attribution ───────────────────────────────────────────────────────
    section("5. Attribution in the view")
    if not principal:
        warn("call attributed to a real principal", "skipped — no audit data to correlate against")
    else:
        # The audit table commonly lands *during* the stage-4 wait, so the view may
        # only have become upgradable just now. Retry the upgrade before judging.
        if not view_is_attributing(client) and args.fix:
            warn("view was still fallback after the audit export", "upgrading now")
            if upgrade_view(client):
                check("view upgraded to attributing variant", True)

        if not view_is_attributing(client):
            check("call attributed to a real principal", False,
                  fail_detail="The audit table has landed but the view is still the fallback "
                              "variant. Re-run: python setup_bigquery_view.py   (or pass --fix)")
        else:
            rows = q(client, f"""
                SELECT SUM(call_count) AS calls, SUM(total_tokens) AS tokens
                FROM `{PROJECT_ID}.{DATASET_ID}.{VIEW_ID}`
                WHERE user_email = @principal
                  AND DATE(call_timestamp) >= DATE(@since)
            """, [bigquery.ScalarQueryParameter("principal", "STRING", principal),
                  bigquery.ScalarQueryParameter("since", "TIMESTAMP", start)])
            calls = (rows[0].calls or 0) if rows else 0
            check("call attributed to a real principal", calls > 0,
                  f"{principal}: {calls} call(s), {rows[0].tokens or 0} tokens"
                  if calls else
                  f"No rows for {principal}. Time-window correlation (±10s, mutual-nearest) "
                  "found no pair — rows missing request_latency metadata always fall back.")

        unattr = q(client, f"""
            SELECT SUM(call_count) AS calls
            FROM `{PROJECT_ID}.{DATASET_ID}.{VIEW_ID}`
            WHERE user_email = @fallback
        """, [bigquery.ScalarQueryParameter("fallback", "STRING", FALLBACK_IDENTITY)])
        n_unattr = (unattr[0].calls or 0) if unattr else 0
        if n_unattr:
            warn("unattributed calls present",
                 f"{n_unattr} call(s) as {FALLBACK_IDENTITY}. Expected for anything made "
                 "before the sink worked — sinks are forward-only and never backfill.")

    # ── 6. Token canary ──────────────────────────────────────────────────────
    section("6. Token reconciliation canary")
    rows = q(client, f"""
        SELECT SUM(input_tokens) + SUM(output_tokens) - SUM(total_tokens) AS gap,
               SUM(total_tokens) AS total
        FROM `{PROJECT_ID}.{DATASET_ID}.{VIEW_ID}`
    """)
    gap, total = (rows[0].gap, rows[0].total) if rows else (None, 0)
    if total:
        check("input + output == total_tokens", gap == 0,
              f"gap={gap}, total={total}" +
              ("" if gap == 0 else "  <- tokens are being dropped; reasoning "
                                   "(thoughtsTokenCount) is billed at output rates"))
    else:
        warn("input + output == total_tokens", "no token rows to reconcile yet")

    return 0 if summary() else 1


if __name__ == "__main__":
    sys.exit(main())
