#!/usr/bin/env bash
# Dark Factory — K8s/Argo Rollouts canary traffic shift template.
#
# Required env vars:
#   DF_SPEC_NAME       — spec being deployed
#   DF_RUN_ID          — pipeline run ID
#   CANARY_PERCENT     — target traffic percentage (1-100)
#   ROLLOUT_NAME       — Argo Rollout resource name
#   NAMESPACE          — Kubernetes namespace (default: default)
#
# Usage: configured as canary_shift_cmd in config.toml
set -euo pipefail

: "${DF_SPEC_NAME:?DF_SPEC_NAME required}"
: "${CANARY_PERCENT:?CANARY_PERCENT required}"
: "${ROLLOUT_NAME:?ROLLOUT_NAME required}"
NAMESPACE="${NAMESPACE:-default}"

echo "Shifting ${ROLLOUT_NAME} to ${CANARY_PERCENT}% canary (spec: ${DF_SPEC_NAME})"

kubectl argo rollouts set weight "${ROLLOUT_NAME}" \
  --weight "${CANARY_PERCENT}" \
  --namespace "${NAMESPACE}"

echo "Waiting for rollout to reach ${CANARY_PERCENT}%..."
kubectl argo rollouts status "${ROLLOUT_NAME}" \
  --namespace "${NAMESPACE}" \
  --timeout 120s

echo "Canary shift to ${CANARY_PERCENT}% complete."
