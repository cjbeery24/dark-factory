#!/usr/bin/env bash
set -euo pipefail

# Dark Factory Docker Image Builder
# Usage: bash docker/build-images.sh [language|all]
#
# Builds the base image first, then the specified language variant (or all).

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Building dark-factory-base..."
docker build -t dark-factory-base -f "$DOCKER_DIR/Dockerfile.base" "$DOCKER_DIR"

LANGUAGE="${1:-all}"
case "$LANGUAGE" in
    node)   docker build -t dark-factory-node   -f "$DOCKER_DIR/Dockerfile.node"   "$DOCKER_DIR" ;;
    python) docker build -t dark-factory-python -f "$DOCKER_DIR/Dockerfile.python" "$DOCKER_DIR" ;;
    go)     docker build -t dark-factory-go     -f "$DOCKER_DIR/Dockerfile.go"     "$DOCKER_DIR" ;;
    rust)   docker build -t dark-factory-rust   -f "$DOCKER_DIR/Dockerfile.rust"   "$DOCKER_DIR" ;;
    all)
        docker build -t dark-factory-node   -f "$DOCKER_DIR/Dockerfile.node"   "$DOCKER_DIR" &
        docker build -t dark-factory-python -f "$DOCKER_DIR/Dockerfile.python" "$DOCKER_DIR" &
        docker build -t dark-factory-go     -f "$DOCKER_DIR/Dockerfile.go"     "$DOCKER_DIR" &
        docker build -t dark-factory-rust   -f "$DOCKER_DIR/Dockerfile.rust"   "$DOCKER_DIR" &
        wait
        ;;
    *)
        echo "ERROR: Unknown language: $LANGUAGE"
        echo "Usage: $0 [node|python|go|rust|all]"
        exit 1
        ;;
esac

echo "Docker images built successfully."
