#!/usr/bin/env bash
# Dark Factory — docker-compose canary shift via nginx upstream weighting.
#
# Required env vars:
#   DF_SPEC_NAME       — spec being deployed
#   CANARY_PERCENT     — target traffic percentage (1-100)
#   COMPOSE_PROJECT    — compose project name
#   NGINX_CONF         — path to nginx upstream config (default: ./nginx/upstream.conf)
set -euo pipefail

: "${CANARY_PERCENT:?CANARY_PERCENT required}"
: "${COMPOSE_PROJECT:?COMPOSE_PROJECT required}"
NGINX_CONF="${NGINX_CONF:-./nginx/upstream.conf}"

STABLE_WEIGHT=$((100 - CANARY_PERCENT))

echo "Updating nginx weights: stable=${STABLE_WEIGHT}%, canary=${CANARY_PERCENT}%"

cat > "${NGINX_CONF}" <<EOF
upstream app {
    server app-stable:8080 weight=${STABLE_WEIGHT};
    server app-canary:8080 weight=${CANARY_PERCENT};
}
EOF

docker compose -p "${COMPOSE_PROJECT}" exec -T nginx nginx -s reload
echo "Canary shift to ${CANARY_PERCENT}% complete."
