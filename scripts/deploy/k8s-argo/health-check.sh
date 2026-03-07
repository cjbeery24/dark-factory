#!/usr/bin/env bash
# Dark Factory — K8s readiness probe template.
#
# Required env vars:
#   DEPLOY_BASE_URL    — base URL of the deployed service
#   HEALTH_ENDPOINT    — health check path (default: /health)
#   HEALTH_TIMEOUT     — timeout in seconds (default: 60)
set -euo pipefail

: "${DEPLOY_BASE_URL:?DEPLOY_BASE_URL required}"
HEALTH_ENDPOINT="${HEALTH_ENDPOINT:-/health}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"

URL="${DEPLOY_BASE_URL}${HEALTH_ENDPOINT}"
echo "Health checking ${URL} (timeout: ${HEALTH_TIMEOUT}s)..."

deadline=$((SECONDS + HEALTH_TIMEOUT))
while [ $SECONDS -lt $deadline ]; do
  if curl -sf --max-time 5 "${URL}" > /dev/null 2>&1; then
    echo "Health check passed."
    exit 0
  fi
  sleep 2
done

echo "Health check failed after ${HEALTH_TIMEOUT}s."
exit 1
