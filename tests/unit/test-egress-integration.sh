#!/usr/bin/env bash
set -euo pipefail

# Integration test for egress proxy filtering.
# Requires Docker. Verifies that tinyproxy allows CONNECT to whitelisted
# domains and blocks everything else.
#
# Usage: bash tests/test-egress-integration.sh [analysis|execution]
# Default: analysis

CONF="${1:-analysis}"
if [[ "$CONF" != "analysis" && "$CONF" != "execution" ]]; then
    echo "Usage: $0 [analysis|execution]"
    exit 1
fi

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../docker" && pwd)"

# --- Pre-flight checks ---
if ! command -v docker &>/dev/null; then
    echo "SKIP: docker not found"
    exit 0
fi
if ! docker info &>/dev/null 2>&1; then
    echo "SKIP: Docker daemon not running"
    exit 0
fi
if ! docker image inspect dark-factory-proxy &>/dev/null 2>&1; then
    echo "SKIP: dark-factory-proxy image not built (run docker/build-images.sh first)"
    exit 0
fi

# --- Test resources ---
NET_INTERNAL="df-test-internal-$$"
NET_EXTERNAL="df-test-external-$$"
PROXY_NAME="df-test-proxy-$$"

cleanup() {
    docker rm -f "$PROXY_NAME" 2>/dev/null || true
    docker network rm "$NET_INTERNAL" "$NET_EXTERNAL" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Egress integration test (config: ${CONF}) ==="
echo ""

# Create networks: internal (no outbound) and external (bridge, has outbound)
docker network create --internal "$NET_INTERNAL" >/dev/null
docker network create "$NET_EXTERNAL" >/dev/null

# Start proxy on external network, then also connect to internal
docker run -d --name "$PROXY_NAME" \
    --network "$NET_EXTERNAL" \
    -e "TINYPROXY_CONF=/etc/tinyproxy/tinyproxy-${CONF}.conf" \
    dark-factory-proxy >/dev/null

docker network connect "$NET_INTERNAL" "$PROXY_NAME"

# Wait for proxy readiness (check that tinyproxy is listening)
for i in $(seq 1 20); do
    if docker exec "$PROXY_NAME" sh -c 'wget -q -O /dev/null http://127.0.0.1:8080 2>&1 || true' 2>/dev/null; then
        break
    fi
    sleep 0.5
done
# Give tinyproxy a moment to stabilize
sleep 1

PASS=0
FAIL=0
TOTAL=0

# Helper: run a command in a client container on the internal network.
# Uses --entrypoint sh to override the proxy image's tinyproxy entrypoint.
run_client() {
    docker run --rm \
        --entrypoint sh \
        --network "$NET_INTERNAL" \
        "$@"
}

echo "--- Proxy filtering tests ---"

# Test 1: Allowed domain — proxy should allow CONNECT to api.anthropic.com:443
# We send a raw CONNECT request via printf+nc. We only check the proxy's response
# line (200 = tunnel established), not the upstream response.
TOTAL=$((TOTAL + 1))
PROXY_RESPONSE=$(run_client dark-factory-proxy -c \
    "printf 'CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com:443\r\n\r\n' | nc -w 3 ${PROXY_NAME} 8080 2>/dev/null | head -1" 2>/dev/null) || true
if echo "$PROXY_RESPONSE" | grep -q "200"; then
    echo "  PASS  CONNECT api.anthropic.com:443 allowed"
    PASS=$((PASS + 1))
else
    echo "  FAIL  CONNECT api.anthropic.com:443 — expected 200, got: '${PROXY_RESPONSE}'"
    FAIL=$((FAIL + 1))
fi

# Test 2: Blocked domain — proxy should reject CONNECT to google.com:443
TOTAL=$((TOTAL + 1))
PROXY_RESPONSE=$(run_client dark-factory-proxy -c \
    "printf 'CONNECT google.com:443 HTTP/1.1\r\nHost: google.com:443\r\n\r\n' | nc -w 3 ${PROXY_NAME} 8080 2>/dev/null | head -1" 2>/dev/null) || true
if echo "$PROXY_RESPONSE" | grep -qE "403|Connection refused"; then
    echo "  PASS  CONNECT google.com:443 rejected (403)"
    PASS=$((PASS + 1))
elif [[ -z "$PROXY_RESPONSE" ]]; then
    echo "  PASS  CONNECT google.com:443 rejected (connection closed)"
    PASS=$((PASS + 1))
else
    echo "  FAIL  CONNECT google.com:443 — expected 403/closed, got: '${PROXY_RESPONSE}'"
    FAIL=$((FAIL + 1))
fi

# Test 3: Non-443 port — should be rejected by ConnectPort directive
TOTAL=$((TOTAL + 1))
PROXY_RESPONSE=$(run_client dark-factory-proxy -c \
    "printf 'CONNECT api.anthropic.com:8080 HTTP/1.1\r\nHost: api.anthropic.com:8080\r\n\r\n' | nc -w 3 ${PROXY_NAME} 8080 2>/dev/null | head -1" 2>/dev/null) || true
if echo "$PROXY_RESPONSE" | grep -qE "403|Connection refused"; then
    echo "  PASS  CONNECT api.anthropic.com:8080 rejected (non-443 port)"
    PASS=$((PASS + 1))
elif [[ -z "$PROXY_RESPONSE" ]]; then
    echo "  PASS  CONNECT api.anthropic.com:8080 rejected (connection closed)"
    PASS=$((PASS + 1))
else
    echo "  FAIL  CONNECT api.anthropic.com:8080 — expected rejection, got: '${PROXY_RESPONSE}'"
    FAIL=$((FAIL + 1))
fi

# Test 4: Plain HTTP to blocked domain — should be rejected
TOTAL=$((TOTAL + 1))
PROXY_RESPONSE=$(run_client dark-factory-proxy -c \
    "printf 'GET http://google.com/ HTTP/1.1\r\nHost: google.com\r\n\r\n' | nc -w 3 ${PROXY_NAME} 8080 2>/dev/null | head -1" 2>/dev/null) || true
if echo "$PROXY_RESPONSE" | grep -qE "403|Connection refused"; then
    echo "  PASS  HTTP GET google.com rejected"
    PASS=$((PASS + 1))
elif [[ -z "$PROXY_RESPONSE" ]]; then
    echo "  PASS  HTTP GET google.com rejected (connection closed)"
    PASS=$((PASS + 1))
else
    echo "  FAIL  HTTP GET google.com — expected 403/closed, got: '${PROXY_RESPONSE}'"
    FAIL=$((FAIL + 1))
fi

# Test 5: Direct access without proxy on internal network — should fail
TOTAL=$((TOTAL + 1))
DIRECT_EXIT=0
run_client dark-factory-proxy -c \
    "wget -q -O /dev/null --timeout=3 http://google.com/ 2>/dev/null" 2>/dev/null || DIRECT_EXIT=$?
if [[ $DIRECT_EXIT -ne 0 ]]; then
    echo "  PASS  Direct access (no proxy) on internal network fails"
    PASS=$((PASS + 1))
else
    echo "  FAIL  Direct access (no proxy) should fail on --internal network"
    FAIL=$((FAIL + 1))
fi

# Test execution-only: additional allowed domains
if [[ "$CONF" == "execution" ]]; then
    for domain in registry.npmjs.org pypi.org; do
        TOTAL=$((TOTAL + 1))
        PROXY_RESPONSE=$(run_client dark-factory-proxy -c \
            "printf 'CONNECT ${domain}:443 HTTP/1.1\r\nHost: ${domain}:443\r\n\r\n' | nc -w 3 ${PROXY_NAME} 8080 2>/dev/null | head -1" 2>/dev/null) || true
        if echo "$PROXY_RESPONSE" | grep -q "200"; then
            echo "  PASS  CONNECT ${domain}:443 allowed"
            PASS=$((PASS + 1))
        else
            echo "  FAIL  CONNECT ${domain}:443 — expected 200, got: '${PROXY_RESPONSE}'"
            FAIL=$((FAIL + 1))
        fi
    done
fi

echo ""
echo "=== Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
