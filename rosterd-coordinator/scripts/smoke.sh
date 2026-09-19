#!/usr/bin/env bash
# End-to-end sanity check against a running coordinator.
set -euo pipefail
BASE="${1:-http://localhost:8300}"

echo "== GET /healthz =="
curl -sf "$BASE/healthz" | python3 -m json.tool

echo "== POST /events (a healthy run) =="
curl -sf -X POST "$BASE/events" \
  -H 'content-type: application/json' \
  -d '{"site_id":"site-a","agent_id":"fulfillment","status":"done","timestamp":"2026-01-01T00:00:00Z"}' \
  | python3 -m json.tool

echo "== GET /sites =="
curl -sf "$BASE/sites" | python3 -m json.tool

echo "== GET /events =="
curl -sf "$BASE/events" | python3 -m json.tool

echo "smoke check complete"
