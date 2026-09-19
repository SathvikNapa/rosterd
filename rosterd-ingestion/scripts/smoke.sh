#!/usr/bin/env bash
# End-to-end check against a running service. Usage: scripts/smoke.sh [base_url]
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${1:-http://127.0.0.1:8000}"

echo "== health =="
curl -sS "$BASE/healthz"; echo

echo "== POST /ingest =="
PAYLOAD=$(.venv/bin/python -c '
import json, pathlib
print(json.dumps({
    "repo_url": "http://localhost/demo-agent",
    "constraints_yaml": pathlib.Path("demo-agent/constraints.yaml").read_text(),
}))')
RESPONSE=$(curl -sS -X POST "$BASE/ingest" -H 'content-type: application/json' -d "$PAYLOAD")
echo "$RESPONSE" | .venv/bin/python -m json.tool

MANIFEST_ID=$(echo "$RESPONSE" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["manifest_id"])')

echo "== GET /manifest/$MANIFEST_ID =="
curl -sS "$BASE/manifest/$MANIFEST_ID" | .venv/bin/python -m json.tool | head -20

echo "== GET /manifest/$MANIFEST_ID/provenance =="
curl -sS "$BASE/manifest/$MANIFEST_ID/provenance" | .venv/bin/python -m json.tool
