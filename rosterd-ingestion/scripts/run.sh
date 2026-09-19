#!/usr/bin/env bash
# Start the ingestion service on :8000 with the demo repo ingestable locally.
set -euo pipefail
cd "$(dirname "$0")/.."

# The service ingests demo-agent by CLONING it, so it needs to be a git repo —
# but demo-agent/ itself must stay a plain directory, because a .git inside the
# working tree makes the outer repo record it as a bare gitlink and skip its
# source files entirely (teammates would clone and find it empty).
#
# So: stage a throwaway git copy under .demo-repo/ (gitignored) and point the
# service at that. demo-agent/ stays ordinary files that commit normally.
STAGE=".demo-repo"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R demo-agent "$STAGE/demo-agent"
git init --quiet "$STAGE/demo-agent"
git -C "$STAGE/demo-agent" -c user.name=rosterd -c user.email=rosterd@local add -A
git -C "$STAGE/demo-agent" -c user.name=rosterd -c user.email=rosterd@local \
    commit --quiet -m "demo-agent: triage/refund/escalation support graph fixture"

export ROSTERD_LOCAL_REPO_ROOT="${ROSTERD_LOCAL_REPO_ROOT:-$(pwd)/$STAGE}"
exec .venv/bin/python -m uvicorn app:app --reload --port "${PORT:-8000}"
