import json
import os
import subprocess
import sys
import tempfile
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID = os.getenv("BIGQUERY_PROJECT_ID") or os.getenv("PROJECT_ID")
if not PROJECT_ID:
    print(
        "ERROR: PROJECT_ID is not set. "
        "Export BIGQUERY_PROJECT_ID or PROJECT_ID before running this script.",
        file=sys.stderr,
    )
    sys.exit(1)

print("Fetching current IAM policy...")
result = subprocess.run(
    ["gcloud", "projects", "get-iam-policy", PROJECT_ID, "--format=json"],
    capture_output=True,
    text=True,
    check=True,
)

policy = json.loads(result.stdout)

# Merge aiplatform.googleapis.com audit config into the existing auditConfigs
# instead of replacing them, so audit settings for other services are preserved.
TARGET_SERVICE = "aiplatform.googleapis.com"
DESIRED_LOG_TYPES = {"DATA_READ", "DATA_WRITE", "ADMIN_READ"}

existing_configs = policy.get("auditConfigs", [])
merged = False
for entry in existing_configs:
    if entry.get("service") == TARGET_SERVICE:
        # Union of existing log types with desired log types
        existing_types = {c["logType"] for c in entry.get("auditLogConfigs", [])}
        all_types = existing_types | DESIRED_LOG_TYPES
        entry["auditLogConfigs"] = [{"logType": t} for t in sorted(all_types)]
        merged = True
        break

if not merged:
    existing_configs.append(
        {
            "service": TARGET_SERVICE,
            "auditLogConfigs": [{"logType": t} for t in sorted(DESIRED_LOG_TYPES)],
        }
    )

policy["auditConfigs"] = existing_configs

# Write updated policy to a temporary file; deleted automatically after apply.
fd, policy_file = tempfile.mkstemp(suffix=".json", prefix="iam_policy_tmp_")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(policy, f, indent=2)

    print(
        "\nWARNING: `gcloud projects set-iam-policy` replaces the FULL project IAM "
        "policy in one atomic write. Do NOT run this concurrently with other IAM "
        "changes — the last writer wins and intermediate changes will be lost.\n"
    )
    print(f"Applying updated policy from temporary file {policy_file} ...")

    apply_result = subprocess.run(
        ["gcloud", "projects", "set-iam-policy", PROJECT_ID, policy_file],
        capture_output=True,
        text=True,
    )
finally:
    try:
        os.unlink(policy_file)
    except OSError:
        pass

if apply_result.returncode == 0:
    print("SUCCESS: Data Access Audit Logs have been successfully enabled for Vertex AI API!")
else:
    print("ERROR applying policy:")
    print(apply_result.stderr)
    sys.exit(apply_result.returncode)
