#!/usr/bin/env bash
# Serves the kernel on :8100 (or $PORT), fake docker mode by default so it
# runs with no Docker daemon and no manifest wired up yet.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/uvicorn app:app --host 0.0.0.0 --port "${PORT:-8100}" --reload
