#!/usr/bin/env bash
set -euo pipefail

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
    BASH_VERSION COREUTILS_VERSION GIT_VERSION
    TINYPROXY_VERSION
    TYPESCRIPT_VERSION ESLINT_VERSION PRETTIER_VERSION
    PYTHON3_VERSION PY3_PIP_VERSION PYTHON3_DEV_VERSION
    RUFF_VERSION MYPY_VERSION PYTEST_VERSION
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

echo "Building dark-factory-proxy (${ALPINE_IMAGE})..."
docker build -t dark-factory-proxy \
    --build-arg ALPINE_IMAGE="$ALPINE_IMAGE" \
    --build-arg TINYPROXY_VERSION="$TINYPROXY_VERSION" \
    -f "$DOCKER_DIR/Dockerfile.proxy" "$DOCKER_DIR"

echo "Building dark-factory-base (${NODE_IMAGE}, claude-code@${CLAUDE_CODE_VERSION})..."
docker build -t dark-factory-base \
    --build-arg NODE_IMAGE="$NODE_IMAGE" \
    --build-arg BASH_VERSION="$BASH_VERSION" \
    --build-arg COREUTILS_VERSION="$COREUTILS_VERSION" \
    --build-arg GIT_VERSION="$GIT_VERSION" \
    --build-arg CLAUDE_CODE_VERSION="$CLAUDE_CODE_VERSION" \
    -f "$DOCKER_DIR/Dockerfile.base" "$DOCKER_DIR"

# Post-build verification: npm list -g is deterministic (claude --version output is not)
echo "Verifying claude-code@${CLAUDE_CODE_VERSION}..."
INSTALLED=$(docker run --rm --entrypoint npm dark-factory-base \
    list -g --depth=0 @anthropic-ai/claude-code 2>/dev/null \
    | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if [[ "$INSTALLED" != "$CLAUDE_CODE_VERSION" ]]; then
    echo "ERROR: claude-code version mismatch."
    echo "  Expected: $CLAUDE_CODE_VERSION"
    echo "  Installed: ${INSTALLED:-<not found — npm list failed>}"
    echo "Update CLAUDE_CODE_VERSION in docker/versions.env and rebuild."
    exit 1
fi
echo "Verified: claude-code@${INSTALLED}"

LANGUAGE="${1:-all}"
case "$LANGUAGE" in
    node)
        docker build -t dark-factory-node \
            --build-arg TYPESCRIPT_VERSION="$TYPESCRIPT_VERSION" \
            --build-arg ESLINT_VERSION="$ESLINT_VERSION" \
            --build-arg PRETTIER_VERSION="$PRETTIER_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.node" "$DOCKER_DIR"
        ;;
    python)
        docker build -t dark-factory-python \
            --build-arg PYTHON3_VERSION="$PYTHON3_VERSION" \
            --build-arg PY3_PIP_VERSION="$PY3_PIP_VERSION" \
            --build-arg PYTHON3_DEV_VERSION="$PYTHON3_DEV_VERSION" \
            --build-arg RUFF_VERSION="$RUFF_VERSION" \
            --build-arg MYPY_VERSION="$MYPY_VERSION" \
            --build-arg PYTEST_VERSION="$PYTEST_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.python" "$DOCKER_DIR"
        ;;
    go)
        docker build -t dark-factory-go \
            --build-arg GO_VERSION="$GO_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.go" "$DOCKER_DIR"
        ;;
    rust)
        docker build -t dark-factory-rust \
            --build-arg RUST_VERSION="$RUST_VERSION" \
            --build-arg CARGO_VERSION="$CARGO_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.rust" "$DOCKER_DIR"
        ;;
    all)
        _pids=()
        docker build -t dark-factory-node \
            --build-arg TYPESCRIPT_VERSION="$TYPESCRIPT_VERSION" \
            --build-arg ESLINT_VERSION="$ESLINT_VERSION" \
            --build-arg PRETTIER_VERSION="$PRETTIER_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.node" "$DOCKER_DIR" & _pids+=($!)
        docker build -t dark-factory-python \
            --build-arg PYTHON3_VERSION="$PYTHON3_VERSION" \
            --build-arg PY3_PIP_VERSION="$PY3_PIP_VERSION" \
            --build-arg PYTHON3_DEV_VERSION="$PYTHON3_DEV_VERSION" \
            --build-arg RUFF_VERSION="$RUFF_VERSION" \
            --build-arg MYPY_VERSION="$MYPY_VERSION" \
            --build-arg PYTEST_VERSION="$PYTEST_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.python" "$DOCKER_DIR" & _pids+=($!)
        docker build -t dark-factory-go \
            --build-arg GO_VERSION="$GO_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.go" "$DOCKER_DIR" & _pids+=($!)
        docker build -t dark-factory-rust \
            --build-arg RUST_VERSION="$RUST_VERSION" \
            --build-arg CARGO_VERSION="$CARGO_VERSION" \
            -f "$DOCKER_DIR/Dockerfile.rust" "$DOCKER_DIR" & _pids+=($!)
        # PID-based wait — propagates failures on macOS bash 3.2 (plain 'wait' returns 0 always)
        for _pid in "${_pids[@]}"; do wait "$_pid" || exit 1; done
        ;;
    *)
        echo "ERROR: Unknown language: $LANGUAGE"
        echo "Usage: $0 [node|python|go|rust|all]"
        exit 1
        ;;
esac

echo "Docker images built successfully."
