#!/usr/bin/env bash
# Dark Factory — Unleash API flag creation template.
#
# Required env vars:
#   UNLEASH_URL        — Unleash server URL
#   UNLEASH_API_TOKEN  — Unleash admin API token
#   DF_SPEC_NAME       — spec name (used as flag name prefix)
set -euo pipefail

: "${UNLEASH_URL:?UNLEASH_URL required}"
: "${UNLEASH_API_TOKEN:?UNLEASH_API_TOKEN required}"
: "${DF_SPEC_NAME:?DF_SPEC_NAME required}"

FLAG_NAME="df-${DF_SPEC_NAME}"

echo "Creating feature flag: ${FLAG_NAME}"

curl -sf -X POST "${UNLEASH_URL}/api/admin/projects/default/features" \
  -H "Authorization: ${UNLEASH_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"${FLAG_NAME}\",
    \"description\": \"Dark Factory deployment flag for ${DF_SPEC_NAME}\",
    \"type\": \"release\",
    \"impressionData\": false
  }"

echo "Flag ${FLAG_NAME} created."
