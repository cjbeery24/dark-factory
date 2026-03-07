#!/usr/bin/env bash
# Dark Factory — Unleash API flag kill switch template.
#
# Required env vars:
#   UNLEASH_URL        — Unleash server URL
#   UNLEASH_API_TOKEN  — Unleash admin API token
#   DF_SPEC_NAME       — spec name
#   UNLEASH_ENV        — Unleash environment (default: production)
set -euo pipefail

: "${UNLEASH_URL:?UNLEASH_URL required}"
: "${UNLEASH_API_TOKEN:?UNLEASH_API_TOKEN required}"
: "${DF_SPEC_NAME:?DF_SPEC_NAME required}"
UNLEASH_ENV="${UNLEASH_ENV:-production}"

FLAG_NAME="df-${DF_SPEC_NAME}"

echo "Disabling flag ${FLAG_NAME} in ${UNLEASH_ENV} (kill switch)"

curl -sf -X POST \
  "${UNLEASH_URL}/api/admin/projects/default/features/${FLAG_NAME}/environments/${UNLEASH_ENV}/off" \
  -H "Authorization: ${UNLEASH_API_TOKEN}" \
  -H "Content-Type: application/json"

echo "Flag ${FLAG_NAME} disabled in ${UNLEASH_ENV}."
