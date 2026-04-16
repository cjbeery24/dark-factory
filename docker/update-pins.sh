#!/usr/bin/env bash
# Prints current latest versions for all pinned packages.
# Review the output and update docker/versions.env manually, then rebuild.
set -euo pipefail
DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DOCKER_DIR/versions.env"

echo "=== Base image digests ==="
docker pull -q node:22-alpine  >/dev/null; docker inspect node:22-alpine  --format='node:22-alpine  : {{index .RepoDigests 0}}'
docker pull -q alpine:3.19     >/dev/null; docker inspect alpine:3.19     --format='alpine:3.19    : {{index .RepoDigests 0}}'

echo ""
echo "=== npm packages ==="
for pkg in @anthropic-ai/claude-code typescript eslint prettier; do
    echo "$pkg : $(npm show "$pkg" version)"
done

echo ""
echo "=== pip packages ==="
for pkg in ruff mypy pytest pyyaml markdown-it-py mdurl tomli-w; do
    echo "$pkg : $(pip index versions "$pkg" 2>/dev/null | grep -oE '^[^ ]+' | head -1)"
done

echo ""
echo "=== apk packages (node base: ${NODE_IMAGE}) ==="
docker run --rm "$NODE_IMAGE" sh -c 'apk update -q 2>/dev/null; apk search -x bash coreutils git python3 py3-pip python3-dev go rust cargo 2>/dev/null' || true
echo ""
echo "=== apk packages (alpine: ${ALPINE_IMAGE}) ==="
docker run --rm "$ALPINE_IMAGE" sh -c 'apk update -q 2>/dev/null; apk search -x tinyproxy 2>/dev/null' || true

echo ""
echo "=== Next steps ==="
echo "1. Update docker/versions.env with new versions"
echo "2. Run: bash docker/regenerate-lockfiles.sh   (regenerate lockfiles inside containers)"
echo "3. Review: git diff docker/lockfiles/"
echo "4. Rebuild: bash docker/build-images.sh [language]"
