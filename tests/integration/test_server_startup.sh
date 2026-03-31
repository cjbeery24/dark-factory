#!/usr/bin/env bash
# tests/integration/test_server_startup.sh
#
# Integration test: verify the server container starts successfully with
# service sidecars, tmpfs overlays, pnpm-store, and inject_env.
#
# Usage:  bash tests/integration/test_server_startup.sh /path/to/project
#
# Requires:
#   - Docker running
#   - dark-factory-node image built (bash docker/build-images.sh node)
#   - Project must have been built (pnpm install && pnpm run build)
#
# This mirrors the exact Docker lifecycle run_quality_gate uses:
#   1. Create execution network
#   2. Start postgres sidecar with health check
#   3. Start server container with identical flags to _start_server_container
#   4. Poll health endpoint
#   5. Print diagnostics and clean up
#
# NOTE: The df-pnpm-store named volume persists between runs (cache reuse).
# To test from a clean state: docker volume rm df-pnpm-store
#
# NOTE: Workspace member tmpfs overlays are not auto-detected here.
# For monorepo projects, add --tmpfs lines for workspace member node_modules
# if pnpm install fails to link workspace packages.

set -euo pipefail

PROJECT_DIR="${1:?Usage: $0 /path/to/project}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# Config — mirrors budget-brain-v2 config.toml
SERVER_CMD="cd apps/api && node dist/index.js"
BASE_URL="http://localhost:3000"
HEALTH_PATH="/health"
STARTUP_TIMEOUT=60  # 30s config + 30s install padding
POSTGRES_IMAGE="postgres:16-alpine"
DF_IMAGE="dark-factory-node"

RUN_ID="inttest-$$"
NETWORK="df-${RUN_ID}-execution"
PG_CONTAINER="df-${RUN_ID}-svc-postgres"
SERVER_CONTAINER="df-${RUN_ID}-server"

cleanup() {
    echo ""
    echo "=== Cleanup ==="
    docker rm -f "$SERVER_CONTAINER" 2>/dev/null || true
    docker rm -f "$PG_CONTAINER" 2>/dev/null || true
    sleep 1
    docker network rm "$NETWORK" 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT

echo "=== Integration Test: Server Startup ==="
echo "Project: $PROJECT_DIR"
echo "Run ID:  $RUN_ID"
echo ""

# --- Step 1: Create network ---
echo "--- Creating network: $NETWORK ---"
docker network create "$NETWORK"

# --- Step 2: Start postgres ---
echo "--- Starting postgres sidecar ---"
docker run -d \
    --name "$PG_CONTAINER" \
    --network "$NETWORK" \
    --network-alias postgres \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=testdb \
    "$POSTGRES_IMAGE"

echo "  Waiting for postgres to be ready..."
PG_DEADLINE=$((SECONDS + 30))
while [[ $SECONDS -lt $PG_DEADLINE ]]; do
    if docker exec "$PG_CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then
        echo "  Postgres ready."
        break
    fi
    sleep 1
done
if [[ $SECONDS -ge $PG_DEADLINE ]]; then
    echo "  FAIL: Postgres did not become ready within 30s"
    echo "  Postgres logs:"
    docker logs --tail 30 "$PG_CONTAINER"
    exit 1
fi

# --- Step 3: Start server container ---
# Flags mirror build_base_args + _start_server_container in quality_gate.py
echo "--- Starting server container ---"
EFFECTIVE_CMD="pnpm install --frozen-lockfile 2>&1 && ${SERVER_CMD}"
echo "  Command: $EFFECTIVE_CMD"

docker run -d \
    --name "$SERVER_CONTAINER" \
    --cap-drop=ALL \
    --security-opt=no-new-privileges \
    --pids-limit=512 \
    --user=1000:1000 \
    --tmpfs /tmp:rw,exec,nosuid,size=512m \
    --tmpfs /home/agent:rw,exec,nosuid,size=256m \
    -e npm_config_store_dir=/pnpm-store \
    -e CI=true \
    -v "${PROJECT_DIR}:/project:ro" \
    --tmpfs /project/node_modules:rw,exec,size=1g,uid=1000,gid=1000 \
    --tmpfs /project/apps/api/node_modules:rw,exec,size=512m,uid=1000,gid=1000 \
    --tmpfs /project/apps/web/node_modules:rw,exec,size=512m,uid=1000,gid=1000 \
    --tmpfs /project/packages/shared/node_modules:rw,exec,size=512m,uid=1000,gid=1000 \
    -v df-pnpm-store:/pnpm-store \
    --network "$NETWORK" \
    --network-alias server \
    -p "127.0.0.1:3000:3000" \
    -e "DATABASE_URL=postgres://postgres:postgres@postgres:5432/testdb" \
    -e "JWT_SECRET=df-pipeline-test-secret-do-not-use-in-prod" \
    --workdir /project \
    --entrypoint bash \
    "$DF_IMAGE" \
    -c "$EFFECTIVE_CMD"

# --- Step 4: Poll health endpoint ---
echo "--- Polling ${BASE_URL}${HEALTH_PATH} (timeout: ${STARTUP_TIMEOUT}s) ---"
HEALTH_URL="${BASE_URL}${HEALTH_PATH}"
DEADLINE=$((SECONDS + STARTUP_TIMEOUT))
LAST_ERROR=""

while [[ $SECONDS -lt $DEADLINE ]]; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
        echo ""
        echo "=== SUCCESS: Server healthy (HTTP 200) ==="
        echo ""
        echo "Server container logs:"
        docker logs --tail 30 "$SERVER_CONTAINER"
        exit 0
    fi
    LAST_ERROR="HTTP $HTTP_CODE"
    printf "."
    sleep 1
done

# --- Timeout ---
echo ""
echo "=== FAIL: Server did not become healthy within ${STARTUP_TIMEOUT}s ==="
echo "Last health check: $LAST_ERROR"
echo ""
echo "Server container logs (last 80 lines):"
docker logs --tail 80 "$SERVER_CONTAINER"
echo ""
echo "Server container status:"
docker inspect --format='{{.State.Status}} (exit {{.State.ExitCode}})' "$SERVER_CONTAINER" 2>/dev/null || true
exit 1
