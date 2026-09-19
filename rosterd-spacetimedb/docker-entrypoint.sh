#!/bin/sh
# Waits for the `spacetimedb` compose service to come up, then publishes
# rosterd's module into it. Runs once per `docker compose up` and exits --
# see docker-compose.yml's `spacetimedb-publish` service, which kernel and
# coordinator both depend on with `condition: service_completed_successfully`
# so a fresh stack always has the module published before either service's
# first write.
set -eu

SERVER_URL="${ROSTERD_SPACETIMEDB_URL:-http://spacetimedb:3000}"
MODULE_NAME="${ROSTERD_SPACETIMEDB_MODULE:-rosterd}"

echo "waiting for spacetimedb at ${SERVER_URL} ..."
for i in $(seq 1 60); do
    if curl -sf "${SERVER_URL}/v1/ping" >/dev/null 2>&1; then
        echo "spacetimedb is up"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "spacetimedb never became reachable at ${SERVER_URL}, giving up" >&2
        exit 1
    fi
    sleep 1
done

cd /module
echo "publishing module '${MODULE_NAME}' to ${SERVER_URL} ..."
spacetime publish "${MODULE_NAME}" --server "${SERVER_URL}" --yes
echo "published."
