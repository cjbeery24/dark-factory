#!/usr/bin/env bash
# Dark Factory — K8s/Argo Rollouts rollback template.
#
# Required env vars:
#   ROLLOUT_NAME       — Argo Rollout resource name
#   NAMESPACE          — Kubernetes namespace (default: default)
set -euo pipefail

: "${ROLLOUT_NAME:?ROLLOUT_NAME required}"
NAMESPACE="${NAMESPACE:-default}"

echo "Aborting rollout ${ROLLOUT_NAME} in ${NAMESPACE}..."
kubectl argo rollouts abort "${ROLLOUT_NAME}" --namespace "${NAMESPACE}"

echo "Waiting for rollback..."
kubectl argo rollouts status "${ROLLOUT_NAME}" \
  --namespace "${NAMESPACE}" \
  --timeout 120s || true

echo "Rollback complete."
