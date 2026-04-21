#!/usr/bin/env bash
set -euo pipefail

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$DOCKER_DIR")"

# Load and validate pinned versions
VERSIONS_FILE="$DOCKER_DIR/versions.env"
if [[ ! -f "$VERSIONS_FILE" ]]; then
    echo "ERROR: $VERSIONS_FILE not found."
    echo "See docker/versions.env for the template, populate all values, then rebuild."
    exit 1
fi
# shellcheck source=docker/versions.env
source "$VERSIONS_FILE"

# Assert all required variables are non-empty (catches partial fills)
_required_vars=(
    NODE_IMAGE ALPINE_IMAGE
    CLAUDE_CODE_VERSION
    BASH_VERSION COREUTILS_VERSION GIT_VERSION PY3_YAML_VERSION
    TINYPROXY_VERSION
    TYPESCRIPT_VERSION ESLINT_VERSION PRETTIER_VERSION PNPM_VERSION
    PYTHON3_VERSION PY3_PIP_VERSION PYTHON3_DEV_VERSION
    PYTHON3_SLIM_IMAGE
    RUFF_VERSION MYPY_VERSION PYTEST_VERSION
    PLAYWRIGHT_VERSION
    GO_VERSION
    RUST_VERSION CARGO_VERSION
)
_missing=false
for _var in "${_required_vars[@]}"; do
    if [[ -z "${!_var:-}" ]]; then
        echo "ERROR: $VERSIONS_FILE: variable $_var is empty or missing."
        _missing=true
    fi
done
[[ "$_missing" == "true" ]] && exit 1
unset _required_vars _missing _var

# Lockfile freshness check: warn if versions.env diverges from lockfile package.json
_lockfile_cc="$DOCKER_DIR/lockfiles/claude-code/package.json"
if [[ -f "$_lockfile_cc" ]]; then
    _lockfile_ver=$(grep -oE '"@anthropic-ai/claude-code":\s*"[^"]+"' "$_lockfile_cc" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
    if [[ -n "$_lockfile_ver" && "$_lockfile_ver" != "$CLAUDE_CODE_VERSION" ]]; then
        echo "WARNING: Lockfile claude-code version (${_lockfile_ver}) differs from versions.env (${CLAUDE_CODE_VERSION})."
        echo "         Run: bash docker/regenerate-lockfiles.sh"
    fi
else
    echo "WARNING: Lockfile not found at ${_lockfile_cc}."
    echo "         Run: bash docker/regenerate-lockfiles.sh"
fi
_lockfile_nt="$DOCKER_DIR/lockfiles/node-tools/package.json"
if [[ -f "$_lockfile_nt" ]]; then
    _lockfile_ts=$(grep -oE '"typescript":\s*"[^"]+"' "$_lockfile_nt" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
    if [[ -n "$_lockfile_ts" && "$_lockfile_ts" != "$TYPESCRIPT_VERSION" ]]; then
        echo "WARNING: Lockfile typescript version (${_lockfile_ts}) differs from versions.env (${TYPESCRIPT_VERSION})."
        echo "         Run: bash docker/regenerate-lockfiles.sh"
    fi
    _lockfile_pnpm=$(grep -oE '"pnpm":\s*"[^"]+"' "$_lockfile_nt" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
    if [[ -n "$_lockfile_pnpm" && "$_lockfile_pnpm" != "$PNPM_VERSION" ]]; then
        echo "WARNING: Lockfile pnpm version (${_lockfile_pnpm}) differs from versions.env (${PNPM_VERSION})."
        echo "         Run: bash docker/regenerate-lockfiles.sh"
    fi
fi
_lockfile_pw_node="$DOCKER_DIR/lockfiles/playwright-node-tools/package.json"
if [[ -f "$_lockfile_pw_node" ]]; then
    _lockfile_pw_node_ver=$(grep -oE '"@playwright/test":\s*"[^"]+"' "$_lockfile_pw_node" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
    if [[ -n "$_lockfile_pw_node_ver" && "$_lockfile_pw_node_ver" != "$PLAYWRIGHT_VERSION" ]]; then
        echo "WARNING: Lockfile playwright-node version (${_lockfile_pw_node_ver}) differs from versions.env (${PLAYWRIGHT_VERSION})."
        echo "         Run: bash docker/regenerate-lockfiles.sh"
    fi
else
    echo "WARNING: Lockfile not found at ${_lockfile_pw_node}."
    echo "         Run: bash docker/regenerate-lockfiles.sh"
fi
unset _lockfile_cc _lockfile_ver _lockfile_nt _lockfile_ts _lockfile_pw_node _lockfile_pw_node_ver

echo "Building dark-factory-proxy (${ALPINE_IMAGE})..."
docker build -t dark-factory-proxy \
    --build-arg ALPINE_IMAGE="$ALPINE_IMAGE" \
    --build-arg TINYPROXY_VERSION="$TINYPROXY_VERSION" \
    -f "$DOCKER_DIR/Dockerfile.proxy" "$REPO_DIR"

echo "Building dark-factory-base (${NODE_IMAGE}, claude-code@${CLAUDE_CODE_VERSION}, pnpm@${PNPM_VERSION})..."
docker build -t dark-factory-base \
    --build-arg NODE_IMAGE="$NODE_IMAGE" \
    --build-arg BASH_VERSION="$BASH_VERSION" \
    --build-arg COREUTILS_VERSION="$COREUTILS_VERSION" \
    --build-arg GIT_VERSION="$GIT_VERSION" \
    --build-arg PNPM_VERSION="$PNPM_VERSION" \
    --build-arg PYTHON3_VERSION="$PYTHON3_VERSION" \
    --build-arg PY3_YAML_VERSION="$PY3_YAML_VERSION" \
    -f "$DOCKER_DIR/Dockerfile.base" "$REPO_DIR"

# Post-build verification: check local install at /opt/claude-code
echo "Verifying claude-code@${CLAUDE_CODE_VERSION}..."
INSTALLED=$(docker run --rm --entrypoint npm dark-factory-base \
    list --depth=0 --prefix /opt/claude-code @anthropic-ai/claude-code 2>/dev/null \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if [[ "$INSTALLED" != "$CLAUDE_CODE_VERSION" ]]; then
    echo "ERROR: claude-code version mismatch."
    echo "  Expected: $CLAUDE_CODE_VERSION"
    echo "  Installed: ${INSTALLED:-<not found — npm list failed>}"
    echo "Run: bash docker/regenerate-lockfiles.sh && bash docker/build-images.sh"
    exit 1
fi
# Verify claude binary is on PATH via /opt/claude-code
CLAUDE_PATH=$(docker run --rm --entrypoint which dark-factory-base claude 2>/dev/null || true)
if [[ "$CLAUDE_PATH" != "/opt/claude-code/node_modules/.bin/claude" ]]; then
    echo "WARNING: claude binary at unexpected path: ${CLAUDE_PATH:-<not found>}"
    echo "  Expected: /opt/claude-code/node_modules/.bin/claude"
fi
echo "Verified: claude-code@${INSTALLED}"

LANGUAGE="${1:-all}"

_build_python() {
    echo "Building dark-factory-python (python@${PYTHON3_VERSION})..."
    docker build -t dark-factory-python \
        --build-arg PYTHON3_VERSION="$PYTHON3_VERSION" \
        --build-arg PY3_PIP_VERSION="$PY3_PIP_VERSION" \
        --build-arg PYTHON3_DEV_VERSION="$PYTHON3_DEV_VERSION" \
        -f "$DOCKER_DIR/Dockerfile.python" "$REPO_DIR"
}

_build_playwright() {
    echo "Building dark-factory-playwright (playwright@${PLAYWRIGHT_VERSION}, ${PYTHON3_SLIM_IMAGE})..."
    docker build -t dark-factory-playwright \
        --build-arg PYTHON3_SLIM_IMAGE="$PYTHON3_SLIM_IMAGE" \
        --build-arg PLAYWRIGHT_VERSION="$PLAYWRIGHT_VERSION" \
        -f "$DOCKER_DIR/Dockerfile.playwright" "$REPO_DIR"
}

_build_playwright_node() {
    local _lockfile="$DOCKER_DIR/lockfiles/playwright-node-tools/package-lock.json"
    if [[ ! -f "$_lockfile" ]]; then
        echo "Generating playwright-node-tools lockfile (@playwright/test@${PLAYWRIGHT_VERSION})..."
        cat > "$DOCKER_DIR/lockfiles/playwright-node-tools/package.json" <<EOF
{
  "name": "dark-factory-playwright-node-tools",
  "private": true,
  "dependencies": {
    "@playwright/test": "${PLAYWRIGHT_VERSION}"
  }
}
EOF
        docker run --rm \
            -v "$DOCKER_DIR/lockfiles/playwright-node-tools:/work" \
            -w /work \
            "$NODE_IMAGE" \
            npm install --package-lock-only --ignore-scripts
        echo "  Generated: lockfiles/playwright-node-tools/package-lock.json"
    fi
    echo "Building dark-factory-playwright-node (playwright@${PLAYWRIGHT_VERSION})..."
    docker build -t dark-factory-playwright-node \
        --build-arg PLAYWRIGHT_VERSION="$PLAYWRIGHT_VERSION" \
        -f "$DOCKER_DIR/Dockerfile.playwright-node" "$REPO_DIR"
}

case "$LANGUAGE" in
    node)
        docker build -t dark-factory-node \
            -f "$DOCKER_DIR/Dockerfile.node" "$REPO_DIR"
        _build_python
        _build_playwright
        _build_playwright_node
        ;;
    python)
        _build_python
        _build_playwright
        ;;
    go)
        docker build -t dark-factory-go \
            --build-arg GO_VERSION="$GO_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.go" "$REPO_DIR"
        _build_python
        _build_playwright
        ;;
    rust)
        docker build -t dark-factory-rust \
            --build-arg RUST_VERSION="$RUST_VERSION" \
            --build-arg CARGO_VERSION="$CARGO_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.rust" "$REPO_DIR"
        _build_python
        _build_playwright
        ;;
    all)
        _pids=()
        docker build -t dark-factory-node \
            -f "$DOCKER_DIR/Dockerfile.node" "$REPO_DIR" & _pids+=($!)
        _build_python & _pids+=($!)
        docker build -t dark-factory-go \
            --build-arg GO_VERSION="$GO_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.go" "$REPO_DIR" & _pids+=($!)
        docker build -t dark-factory-rust \
            --build-arg RUST_VERSION="$RUST_VERSION" \
            --build-arg CARGO_VERSION="$CARGO_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.rust" "$REPO_DIR" & _pids+=($!)
        # PID-based wait — propagates failures on macOS bash 3.2 (plain 'wait' returns 0 always)
        for _pid in "${_pids[@]}"; do wait "$_pid" || exit 1; done
        _build_playwright
        _build_playwright_node
        ;;
    *)
        echo "ERROR: Unknown language: $LANGUAGE"
        echo "Usage: $0 [node|python|go|rust|all]"
        exit 1
        ;;
esac

echo "Docker images built successfully."
