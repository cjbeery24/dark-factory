#!/usr/bin/env bash
set -euo pipefail

# Regenerates lockfiles for all pinned dependencies inside Docker containers.
# This ensures lockfiles match the target platform (Alpine Linux).
#
# Usage: bash docker/regenerate-lockfiles.sh
#
# Prerequisites:
#   - Docker running
#   - docker/versions.env populated

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCKFILES_DIR="$DOCKER_DIR/lockfiles"

# Load versions
source "$DOCKER_DIR/versions.env"

echo "=== Regenerating lockfiles ==="
echo ""

# --- Claude Code lockfile ---
echo "--- claude-code (npm) ---"

# Generate package.json from versions.env
cat > "$LOCKFILES_DIR/claude-code/package.json" <<EOF
{
  "name": "dark-factory-claude-code",
  "private": true,
  "dependencies": {
    "@anthropic-ai/claude-code": "${CLAUDE_CODE_VERSION}"
  }
}
EOF

# Generate package-lock.json inside Alpine container
docker run --rm \
    -v "$LOCKFILES_DIR/claude-code:/work" \
    -w /work \
    "$NODE_IMAGE" \
    npm install --package-lock-only --ignore-scripts 2>&1

echo "  Generated: lockfiles/claude-code/package-lock.json"

# --- Node tools lockfile ---
echo ""
echo "--- node-tools (npm) ---"

cat > "$LOCKFILES_DIR/node-tools/package.json" <<EOF
{
  "name": "dark-factory-node-tools",
  "private": true,
  "dependencies": {
    "typescript": "${TYPESCRIPT_VERSION}",
    "eslint": "${ESLINT_VERSION}",
    "prettier": "${PRETTIER_VERSION}"
  }
}
EOF

docker run --rm \
    -v "$LOCKFILES_DIR/node-tools:/work" \
    -w /work \
    "$NODE_IMAGE" \
    npm install --package-lock-only --ignore-scripts 2>&1

echo "  Generated: lockfiles/node-tools/package-lock.json"

# --- Python tools lockfile ---
echo ""
echo "--- python-tools (pip) ---"

cat > "$LOCKFILES_DIR/python-tools/requirements.in" <<EOF
ruff==${RUFF_VERSION}
mypy==${MYPY_VERSION}
pytest==${PYTEST_VERSION}
pyyaml==${PYYAML_VERSION}
markdown-it-py==${MARKDOWN_IT_PY_VERSION}
mdurl==${MDURL_VERSION}
tomli-w==${TOMLI_W_VERSION}
EOF

# Generate frozen requirements.txt with hashes inside Alpine container
# We install into a temp venv, freeze, then copy out
docker run --rm \
    -v "$LOCKFILES_DIR/python-tools:/work" \
    -w /work \
    "$NODE_IMAGE" \
    sh -c '
        apk add --no-cache python3 py3-pip >/dev/null 2>&1
        python3 -m venv /tmp/venv
        /tmp/venv/bin/pip install --quiet -r requirements.in
        /tmp/venv/bin/pip freeze > requirements.txt
    ' 2>&1

echo "  Generated: lockfiles/python-tools/requirements.txt"

# --- Playwright Node tools lockfile ---
echo ""
echo "--- playwright-node-tools (npm) ---"

cat > "$LOCKFILES_DIR/playwright-node-tools/package.json" <<EOF
{
  "name": "dark-factory-playwright-node-tools",
  "private": true,
  "dependencies": {
    "@playwright/test": "${PLAYWRIGHT_VERSION}"
  }
}
EOF

docker run --rm \
    -v "$LOCKFILES_DIR/playwright-node-tools:/work" \
    -w /work \
    "$NODE_IMAGE" \
    npm install --package-lock-only --ignore-scripts 2>&1

echo "  Generated: lockfiles/playwright-node-tools/package-lock.json"

echo ""
echo "=== Lockfile regeneration complete ==="
echo ""
echo "Review the changes:"
echo "  git diff docker/lockfiles/"
echo ""
echo "Then commit and rebuild:"
echo "  bash docker/build-images.sh [language]"
