#!/usr/bin/env bash
# Generates the SpacetimeDB TypeScript client into src/module_bindings/.
#
# Until this runs, the UI reads SpacetimeDB over HTTP SQL (see
# src/lib/live/sql.ts). After it runs, src/lib/live/bindings.ts picks the
# generated client up automatically and upgrades to a real websocket
# subscription — no code change needed.
#
# Needs the spacetime CLI:  curl -sSf https://install.spacetimedb.com | sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
module="$here/../rosterd-spacetimedb"

if ! command -v spacetime >/dev/null 2>&1; then
  echo "spacetime CLI not found. Install it with:" >&2
  echo "  curl -sSf https://install.spacetimedb.com | sh -s -- -y" >&2
  exit 1
fi

if [ ! -d "$module/spacetimedb" ]; then
  echo "module source not found at $module/spacetimedb" >&2
  exit 1
fi

echo "==> generating TypeScript bindings from $module/spacetimedb"
(cd "$module" && spacetime generate \
  --lang typescript \
  --out-dir "$here/src/module_bindings" \
  --module-path ./spacetimedb)

# The generated code imports from "spacetimedb" directly (verified by
# grepping src/module_bindings/index.ts's own `from` clause after a real
# generate run) -- @clockworklabs/spacetimedb-sdk isn't the package it
# needs and doesn't resolve (`npm install` fails on a peer dep, spacetimedb@next,
# that package pulls in). Pinned to 2.10.* to match rosterd-spacetimedb's
# server package.json and the locally installed CLI (spacetime --version).
echo "==> installing the client SDK the generated code imports"
(cd "$here" && npm install spacetimedb@2.10.1)

echo "done. Restart the dev server; the nav pill should read 'Live'."
