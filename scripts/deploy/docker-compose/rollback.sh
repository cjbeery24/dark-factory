#!/usr/bin/env bash
# Dark Factory — docker-compose rollback template.
#
# Required env vars:
#   COMPOSE_PROJECT    — compose project name
set -euo pipefail

: "${COMPOSE_PROJECT:?COMPOSE_PROJECT required}"

echo "Rolling back ${COMPOSE_PROJECT}..."

# Stop canary, route all traffic to stable.
docker compose -p "${COMPOSE_PROJECT}" stop app-canary 2>/dev/null || true
docker compose -p "${COMPOSE_PROJECT}" rm -f app-canary 2>/dev/null || true

# Reset nginx to stable-only.
NGINX_CONF="${NGINX_CONF:-./nginx/upstream.conf}"
cat > "${NGINX_CONF}" <<EOF
upstream app {
    server app-stable:8080;
}
EOF

docker compose -p "${COMPOSE_PROJECT}" exec -T nginx nginx -s reload 2>/dev/null || true
echo "Rollback complete."
