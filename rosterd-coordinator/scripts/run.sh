#!/usr/bin/env bash
# Serves the coordinator on :8300 (or $PORT). No SpacetimeDB or kernel
# needs to be running -- writes fall back to logging, and there's simply
# nothing to broadcast to until ROSTERD_COORDINATOR_SITE_KERNELS is set.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/uvicorn app:app --host 0.0.0.0 --port "${PORT:-8300}" --reload
