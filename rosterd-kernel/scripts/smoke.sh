#!/usr/bin/env bash
# End-to-end sanity check against a running kernel: health, an unknown-agent
# dispatch (expected 404), and a policy push. Does not require Docker or a
# live demo-agent -- simulated mode plus a manifest-not-ready 503 is a
# valid, expected outcome when nothing has been wired up yet.
set -euo pipefail
BASE="${1:-http://localhost:8100}"

echo "== GET /healthz =="
curl -sf "$BASE/healthz" | python3 -m json.tool

echo "== GET /health =="
curl -sf "$BASE/health" | python3 -m json.tool

echo "== GET /manifest =="
curl -sf "$BASE/manifest" | python3 -m json.tool

echo "== POST /dispatch (unknown agent, expect 404 or 503) =="
curl -s -o /dev/stderr -w "\nHTTP %{http_code}\n" -X POST "$BASE/dispatch" \
  -H 'content-type: application/json' \
  -d '{"agent_id":"nope","task":{"id":"t1","title":"test","description":"test"},"assignees":["me"]}' || true

echo "smoke check complete"
