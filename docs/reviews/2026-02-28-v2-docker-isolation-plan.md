# Plan: Docker-Based Hard Isolation for Dark Factory — v2

**Date:** 2026-02-28
**Depends on:** docs/reviews/2026-02-28-v1-critical-evaluation.md
**Scope:** Replace soft information barriers with hard filesystem isolation using Docker containers; fix critical pipeline bugs

---

## Context

The critical evaluation (v1) identified that Dark Factory's information barriers are enforced only by `--disallowedTools` flags, which agents can trivially bypass via the Bash tool (`cat .dark-factory/scenarios/*`). This plan replaces soft prompt-level barriers with hard filesystem isolation using Docker containers, where restricted files are physically absent from the agent's environment.

Additionally, three critical pipeline bugs are fixed alongside the Docker work: the `set -e` crash on quality gate failure (C2), missing agent timeouts/turn limits (H1), and fragile Oracle output parsing (H3).

## Decisions

- Docker is **required** — no bare-metal fallback mode
- **All** pipeline stages containerized (LLM agents + quality gate)
- Agents install their own dependencies inside containers with persistent cache volumes
- Both local dev (Docker Desktop) and CI (GitHub Actions) supported
- `.git/` is masked in barrier-sensitive containers to prevent `git log`/`git show` leaks

---

## Architecture

### Core Insight: The Mount Is the Barrier

Instead of telling agents "don't read X", we never mount X into the container. The Implementer's container has no `.dark-factory/scenarios/` directory — not empty, not permission-denied, simply nonexistent. No tool, technique, or prompt injection can read files that don't exist in the filesystem.

### Mount Strategy: Bind Mount + tmpfs Masking

Mount the project root into every container, then **mask** restricted directories with empty read-only tmpfs mounts. Docker processes mounts by specificity — a tmpfs on `/workspace/.dark-factory/scenarios` overrides the parent bind mount for that path.

```
Implementer sees:
/workspace/              <- project root (bind mount, rw)
/workspace/src/          <- real source code (from bind mount)
/workspace/.dark-factory/scenarios/  <- EMPTY (tmpfs mask, ro)
/workspace/.git/         <- EMPTY (tmpfs mask, ro)

Scenario Architect sees:
/workspace/              <- project root (bind mount, ro)
/workspace/src/          <- EMPTY (tmpfs mask, ro)
/workspace/.dark-factory/scenarios/{spec}/  <- writable (bind mount override)
/workspace/.git/         <- EMPTY (tmpfs mask, ro)
```

### Agent -> Container Mount Matrix

| Agent | Project Mount | Masked (empty tmpfs, ro) | RW Overrides |
|-------|:---:|---|---|
| Spec Validator | `:ro` | — | `.dark-factory/reviews/` |
| Scenario Architect | `:ro` | `SOURCE_DIR/`, `.git/` | `.dark-factory/scenarios/{spec}/` |
| Implementer | `:rw` | `.dark-factory/scenarios/`, `.git/` | — |
| Quality Gate | `:ro` | — | `.dark-factory/reports/` |
| Reviewer | `:ro` | — | `.dark-factory/reviews/` |
| Oracle | `:ro` | — | `.dark-factory/reviews/` |
| Escalation | `:ro` | — | `.dark-factory/reviews/` |

### Network Isolation

All agents need `api.anthropic.com` for the Claude API. The Implementer and Quality Gate additionally need package registries (npm, PyPI, crates.io, proxy.golang.org).

- Create Docker network `df-pipeline` (bridge mode)
- Optional: restrict egress with a proxy sidecar or Docker Sandboxes network policies (documented as hardening guide, not enforced by default — the filesystem barrier is the primary control)

### Container Security Hardening

Every container runs with:
```
--read-only                          # Immutable root filesystem
--cap-drop=ALL                       # Drop all Linux capabilities
--security-opt=no-new-privileges     # Prevent privilege escalation
--pids-limit=512                     # Prevent fork bombs
--memory=4g                          # Memory limit
--cpus=4                             # CPU limit
--user $(id -u):$(id -g)            # Run as host user (correct file ownership)
--tmpfs /tmp:noexec,nosuid,size=512m # Writable scratch space
--tmpfs /home/agent:size=256m        # Claude Code state + config
```

Named cache volumes for package managers persist across runs:
```
df-npm-cache:/home/agent/.npm
df-pip-cache:/home/agent/.cache/pip
df-cargo-registry:/home/agent/.cargo/registry
df-go-mod:/home/agent/go/pkg/mod
```

---

## New Files

### 1. `docker/Dockerfile.base`

Multi-stage build. Alpine + Node.js + Claude Code CLI + bash + coreutils.

```dockerfile
FROM node:22-alpine AS base
RUN apk add --no-cache bash coreutils git
RUN npm install -g @anthropic-ai/claude-code
RUN mkdir -p /home/agent && chmod 777 /home/agent
WORKDIR /workspace
ENTRYPOINT ["claude", "-p", "--dangerously-skip-permissions"]
```

Notes:
- `git` is included because Claude Code may need it for internal operations, but `.git/` is masked via tmpfs in barrier containers so `git log`/`git show` return nothing useful
- `--dangerously-skip-permissions` is safe inside a container with restricted mounts — the agent has full permission within its sandbox, which is exactly the intended scope
- No USER directive — runtime `--user` flag handles uid mapping

### 2. `docker/Dockerfile.node`

```dockerfile
FROM dark-factory-base AS node
# Node.js is already in the base (node:22-alpine)
# Add common dev tools
RUN npm install -g typescript eslint prettier
```

### 3. `docker/Dockerfile.python`

```dockerfile
FROM dark-factory-base AS python
RUN apk add --no-cache python3 py3-pip python3-dev
RUN pip install --break-system-packages ruff mypy pytest
```

### 4. `docker/Dockerfile.go`

```dockerfile
FROM dark-factory-base AS go
RUN apk add --no-cache go
ENV GOPATH=/home/agent/go
ENV PATH="$GOPATH/bin:$PATH"
```

### 5. `docker/Dockerfile.rust`

```dockerfile
FROM dark-factory-base AS rust
RUN apk add --no-cache rust cargo
```

### 6. `docker/.dockerignore`

```
.git
.dark-factory/scenarios
.dark-factory/state
.dark-factory/audit
node_modules
.venv
target
```

### 7. `docker/docker-compose.yml`

For local dev convenience — builds all images.

```yaml
services:
  base:
    build:
      context: .
      dockerfile: Dockerfile.base
    image: dark-factory-base

  node:
    build:
      context: .
      dockerfile: Dockerfile.node
    image: dark-factory-node
    depends_on: [base]

  python:
    build:
      context: .
      dockerfile: Dockerfile.python
    image: dark-factory-python
    depends_on: [base]

  go:
    build:
      context: .
      dockerfile: Dockerfile.go
    image: dark-factory-go
    depends_on: [base]

  rust:
    build:
      context: .
      dockerfile: Dockerfile.rust
    image: dark-factory-rust
    depends_on: [base]

volumes:
  df-npm-cache:
  df-pip-cache:
  df-cargo-registry:
  df-go-mod:

networks:
  df-pipeline:
    driver: bridge
```

### 8. `docker/build-images.sh`

Script to build all images (called by `integrate.sh` and CI).

```bash
#!/usr/bin/env bash
set -euo pipefail
DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Build base first, then language variants in parallel
docker build -t dark-factory-base -f "$DOCKER_DIR/Dockerfile.base" "$DOCKER_DIR"

LANGUAGE="${1:-all}"
case "$LANGUAGE" in
    node)   docker build -t dark-factory-node   -f "$DOCKER_DIR/Dockerfile.node"   "$DOCKER_DIR" ;;
    python) docker build -t dark-factory-python -f "$DOCKER_DIR/Dockerfile.python" "$DOCKER_DIR" ;;
    go)     docker build -t dark-factory-go     -f "$DOCKER_DIR/Dockerfile.go"     "$DOCKER_DIR" ;;
    rust)   docker build -t dark-factory-rust   -f "$DOCKER_DIR/Dockerfile.rust"   "$DOCKER_DIR" ;;
    all)    # Build all in parallel
        docker build -t dark-factory-node   -f "$DOCKER_DIR/Dockerfile.node"   "$DOCKER_DIR" &
        docker build -t dark-factory-python -f "$DOCKER_DIR/Dockerfile.python" "$DOCKER_DIR" &
        docker build -t dark-factory-go     -f "$DOCKER_DIR/Dockerfile.go"     "$DOCKER_DIR" &
        docker build -t dark-factory-rust   -f "$DOCKER_DIR/Dockerfile.rust"   "$DOCKER_DIR" &
        wait
        ;;
esac
```

### 9. `.github/workflows/dark-factory.yml`

GitHub Actions workflow for CI.

```yaml
name: Dark Factory Pipeline
on:
  workflow_dispatch:
    inputs:
      spec_name:
        description: 'Spec name to process'
        required: true

jobs:
  pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build Dark Factory images
        run: bash docker/build-images.sh "${{ env.LANGUAGE }}"
      - name: Run pipeline
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: bash scripts/dark-factory.sh "${{ inputs.spec_name }}" --ci
```

---

## Modified Files

### 10. `scripts/dark-factory.sh` — Major Changes

This is the largest change. The file needs:

#### a. New `run_agent()` function (replaces all direct `claude` calls)

Core function that maps agent roles to Docker containers with appropriate mounts:

```bash
run_agent() {
    local role="$1"
    local prompt="$2"
    local image="dark-factory-${LANGUAGE}"
    local -a mounts=()
    local -a masks=()
    local -a rw_overrides=()
    local -a cache_vols=()
    local project_mode="ro"  # Default: read-only project mount

    # Role-specific mount configuration
    case "$role" in
        spec-validator)
            rw_overrides+=(".dark-factory/reviews")
            ;;
        scenario-architect)
            masks+=("${SOURCE_DIR}" ".git")
            rw_overrides+=(".dark-factory/scenarios/${SPEC_NAME}")
            ;;
        implementer)
            project_mode="rw"
            masks+=(".dark-factory/scenarios" ".git")
            cache_vols+=("df-${LANGUAGE}-cache")  # Package manager cache
            ;;
        quality-gate)
            rw_overrides+=(".dark-factory/reports")
            ;;
        reviewer|oracle|escalation)
            rw_overrides+=(".dark-factory/reviews")
            ;;
    esac

    # Build docker run command
    local -a docker_args=(
        docker run --rm
        --network df-pipeline
        --read-only
        --cap-drop=ALL
        --security-opt=no-new-privileges:true
        --pids-limit=512
        --memory="${AGENT_MEMORY:-4g}"
        --cpus="${AGENT_CPUS:-4}"
        --user "$(id -u):$(id -g)"
        -e HOME=/home/agent
        -e ANTHROPIC_API_KEY
        --tmpfs /tmp:noexec,nosuid,size=512m
        --tmpfs /home/agent:size=256m
    )

    # Project root mount
    docker_args+=(-v "${PWD}:/workspace:${project_mode}")

    # Apply tmpfs masks (barrier enforcement)
    for mask in "${masks[@]}"; do
        docker_args+=(--tmpfs "/workspace/${mask}:ro,size=1k")
    done

    # Apply RW overrides (output directories)
    for override in "${rw_overrides[@]}"; do
        docker_args+=(-v "${PWD}/${override}:/workspace/${override}")
    done

    # Apply cache volumes
    for cache in "${cache_vols[@]}"; do
        # Map language to cache path
        case "$LANGUAGE" in
            node)   docker_args+=(-v "${cache}:/home/agent/.npm") ;;
            python) docker_args+=(-v "${cache}:/home/agent/.cache/pip") ;;
            go)     docker_args+=(-v "${cache}:/home/agent/go/pkg/mod") ;;
            rust)   docker_args+=(-v "${cache}:/home/agent/.cargo/registry") ;;
        esac
    done

    # Image and command
    docker_args+=("$image")
    docker_args+=(--max-turns "${AGENT_MAX_TURNS:-200}")

    # Execute with timeout
    local timeout_seconds="${AGENT_TIMEOUT:-600}"
    timeout "$timeout_seconds" "${docker_args[@]}" "$prompt"
}
```

#### b. Replace all 7 `claude --print` calls

Each existing invocation pattern:
```bash
# BEFORE
OUTPUT=$(claude --print --dangerously-skip-permissions \
    --disallowedTools "$DENY" \
    --append-system-prompt "$BARRIER" \
    "$PROMPT" 2>&1) || true

# AFTER
OUTPUT=$(run_agent "$ROLE" "$PROMPT" 2>&1) || true
```

The `--disallowedTools` and `--append-system-prompt` flags are **removed** — they are no longer needed because the filesystem barrier is enforced by Docker mounts. The prompt barriers can optionally be kept as defense-in-depth by appending them to the prompt string.

#### c. Docker prerequisite check (new, near top of script)

```bash
require_docker() {
    if ! command -v docker &>/dev/null; then
        echo "ERROR: Docker is required. Install Docker Desktop or Docker Engine."
        exit 1
    fi
    if ! docker info &>/dev/null 2>&1; then
        echo "ERROR: Docker daemon is not running."
        exit 1
    fi
    # Verify images exist
    if ! docker image inspect "dark-factory-${LANGUAGE}" &>/dev/null 2>&1; then
        echo "ERROR: Docker image 'dark-factory-${LANGUAGE}' not found."
        echo "Run: bash docker/build-images.sh ${LANGUAGE}"
        exit 1
    fi
}
```

#### d. Docker network setup (new, called once at pipeline start)

```bash
setup_network() {
    docker network inspect df-pipeline &>/dev/null 2>&1 || \
        docker network create --driver bridge df-pipeline
}
```

#### e. Create required directories before container runs

Containers mount specific host directories. Those dirs must exist before `docker run` or Docker will create them as root-owned:

```bash
mkdir -p ".dark-factory/reviews" ".dark-factory/reports" \
         ".dark-factory/scenarios/${SPEC_NAME}" \
         ".dark-factory/state" ".dark-factory/audit"
```

### 11. Fix C2: `set -e` crashes pipeline on quality gate failure

**File:** `scripts/dark-factory.sh` (around current line 621)

```bash
# BEFORE (pipeline crashes on QG failure)
bash "$SCRIPT_DIR/quality-gate.sh" "$SPEC_NAME" ${TEST_CMD:+--test-cmd "$TEST_CMD"}

# AFTER (capture exit code, pass to reviewer/oracle)
QG_EXIT=0
run_quality_gate "$SPEC_NAME" || QG_EXIT=$?

if [[ "$QG_EXIT" -ne 0 ]]; then
    audit_log "quality-gate" "FAIL" "exit_code=$QG_EXIT"
    echo "[Step 4.${ITERATION}] Quality gate FAILED (exit $QG_EXIT) — proceeding to review."
fi
```

The review/oracle/iterate loop continues regardless of quality gate outcome. The Reviewer and Oracle see the quality gate report (which details what failed) and make their decision based on it.

### 12. Fix H1: Add timeouts and turn limits to agent invocations

Already handled in the `run_agent()` function above:
- `timeout "$timeout_seconds"` wraps the docker run command
- `--max-turns "${AGENT_MAX_TURNS:-200}"` is passed to the claude CLI

New timeout handling after `run_agent`:
```bash
OUTPUT=$(run_agent "implementer" "$PROMPT" 2>&1)
EXIT_CODE=$?
if [[ $EXIT_CODE -eq 124 ]]; then
    audit_log "implementer" "TIMEOUT" "exceeded ${AGENT_TIMEOUT}s"
    echo "WARNING: Implementer timed out after ${AGENT_TIMEOUT}s"
fi
```

**File:** `templates/config.sh.template` — add/update variables:
```bash
AGENT_MAX_TURNS="200"        # Maximum tool-call turns per agent
AGENT_TIMEOUT="600"          # Maximum seconds per agent invocation
AGENT_MEMORY="4g"            # Container memory limit
AGENT_CPUS="4"               # Container CPU limit
```

### 13. Fix H3: Robust Oracle output parsing

**File:** `scripts/dark-factory.sh` (around current line 650)

```bash
# BEFORE (fragile, silently fails on malformed output)
if echo "$ORACLE_OUTPUT" | head -1 | grep -qE '^SHIP\b'; then
    SHIP=true
fi

# AFTER (explicit validation with fallback)
ORACLE_FIRST_LINE=$(echo "$ORACLE_OUTPUT" | head -1 | tr -d '[:space:]')
ORACLE_DECISION=$(echo "$ORACLE_FIRST_LINE" | tr '[:lower:]' '[:upper:]')

if [[ "$ORACLE_DECISION" == "SHIP" ]]; then
    SHIP=true
    audit_log "oracle" "DECISION" "SHIP"
elif [[ "$ORACLE_DECISION" == "NO-SHIP" ]] || [[ "$ORACLE_DECISION" == "NOSHIP" ]]; then
    audit_log "oracle" "DECISION" "NO-SHIP"
else
    audit_log "oracle" "MALFORMED_OUTPUT" "first_line='$(echo "$ORACLE_OUTPUT" | head -1)'"
    echo "WARNING: Oracle output did not start with SHIP or NO-SHIP."
    echo "First line was: $(echo "$ORACLE_OUTPUT" | head -1)"
    echo "Treating as NO-SHIP for safety."
fi
```

### 14. `scripts/quality-gate.sh` — Containerized execution

The quality gate runs build/lint/test commands. A new `run_quality_gate()` function in `dark-factory.sh` runs the existing `quality-gate.sh` script inside a container:

```bash
run_quality_gate() {
    local spec_name="$1"
    local -a docker_args=(
        docker run --rm
        --network df-pipeline
        --read-only
        --cap-drop=ALL
        --security-opt=no-new-privileges:true
        --user "$(id -u):$(id -g)"
        -e HOME=/home/agent
        --tmpfs /tmp:noexec,nosuid,size=512m
        --tmpfs /home/agent:size=256m
        -v "${PWD}:/workspace:ro"
        -v "${PWD}/.dark-factory/reports:/workspace/.dark-factory/reports"
    )

    # Add language-specific cache volume
    case "$LANGUAGE" in
        node)   docker_args+=(-v "df-node-cache:/home/agent/.npm") ;;
        python) docker_args+=(-v "df-pip-cache:/home/agent/.cache/pip") ;;
        go)     docker_args+=(-v "df-go-cache:/home/agent/go/pkg/mod") ;;
        rust)   docker_args+=(-v "df-cargo-cache:/home/agent/.cargo/registry") ;;
    esac

    # Override entrypoint to run bash script instead of claude
    docker_args+=(--entrypoint bash)
    docker_args+=("dark-factory-${LANGUAGE}")
    docker_args+=(/workspace/dark-factory/scripts/quality-gate.sh "$spec_name")

    timeout "${GATE_TIMEOUT:-300}" "${docker_args[@]}"
}
```

Note: The quality gate container mounts the project `:ro` with a `:rw` override for `reports/`. It has access to both `src/` and `scenarios/` (no barrier needed). The `quality-gate.sh` script itself runs inside the container unchanged.

### 15. `templates/config.sh.template` — New variables

Add to the existing template:

```bash
# --- Docker Isolation Settings ---
AGENT_MAX_TURNS="200"          # Max tool-call turns per agent (prevents runaway agents)
AGENT_TIMEOUT="600"            # Max wall-clock seconds per agent invocation
AGENT_MEMORY="4g"              # Container memory limit
AGENT_CPUS="4"                 # Container CPU limit
DOCKER_NETWORK="df-pipeline"   # Docker network name
```

### 16. `scripts/integrate.sh` — Docker setup during integration

Add a step after config generation:

```bash
# Step N: Build Docker images
echo "Building Dark Factory Docker images for ${LANGUAGE}..."
bash "${DARK_FACTORY_ROOT}/docker/build-images.sh" "${LANGUAGE}"

# Create Docker network
docker network inspect df-pipeline &>/dev/null 2>&1 || \
    docker network create --driver bridge df-pipeline

# Create cache volumes
docker volume create "df-${LANGUAGE}-cache" 2>/dev/null || true
```

### 17. `scripts/validate-setup.sh` — Docker validation

Add Docker checks to the existing validation script:

```bash
check_docker() {
    local status=0
    command -v docker &>/dev/null || { echo "FAIL: Docker not installed"; status=1; }
    docker info &>/dev/null 2>&1 || { echo "FAIL: Docker daemon not running"; status=1; }
    docker image inspect "dark-factory-${LANGUAGE}" &>/dev/null 2>&1 || \
        { echo "FAIL: Image dark-factory-${LANGUAGE} not built"; status=1; }
    docker network inspect df-pipeline &>/dev/null 2>&1 || \
        { echo "WARN: Docker network df-pipeline not created (will be created at runtime)"; }
    return $status
}
```

---

## Implementation Order

### Phase 1: Docker Infrastructure (no pipeline changes yet)
1. Create `docker/` directory
2. Write `Dockerfile.base`
3. Write language variant Dockerfiles (node, python, go, rust)
4. Write `docker/.dockerignore`
5. Write `docker/build-images.sh`
6. Write `docker/docker-compose.yml`
7. Test: build all images, verify claude CLI works inside containers

### Phase 2: Core Pipeline Refactor
8. Add `run_agent()` function to `dark-factory.sh`
9. Add `run_quality_gate()` function to `dark-factory.sh`
10. Add `require_docker()` and `setup_network()` functions
11. Replace all 7 `claude --print` invocations with `run_agent()` calls
12. Replace quality gate invocation with `run_quality_gate()`
13. Remove `--disallowedTools` and `--append-system-prompt` barrier variables (replaced by mount barriers)
14. Update audit system to log container IDs and mount configurations

### Phase 3: Critical Bug Fixes
15. Fix C2: Wrap quality gate in explicit error handling (capture exit code, don't crash)
16. Fix H1: Wire `AGENT_MAX_TURNS` and `AGENT_TIMEOUT` into `run_agent()`
17. Fix H3: Robust Oracle output parsing with malformed-output detection

### Phase 4: Integration & Config
18. Update `config.sh.template` with Docker variables
19. Update `integrate.sh` to build images and create network/volumes
20. Update `validate-setup.sh` with Docker checks

### Phase 5: CI/CD
21. Write `.github/workflows/dark-factory.yml`

### Phase 6: Documentation
22. Update `docs/WORKFLOW.md` — Docker requirements, new architecture
23. Update `docs/TOOLS.md` — Docker setup and commands
24. Update `docs/INTEGRATION.md` — Docker integration steps
25. Update `docs/TEAM-SETUP.md` — Docker for multi-user environments
26. Update `CLAUDE.md` — reflect Docker as the barrier mechanism

---

## Verification Plan

### Unit Verification
1. **Build all images:** `bash docker/build-images.sh all` — verify clean builds
2. **Test Claude in container:** `docker run --rm -e ANTHROPIC_API_KEY dark-factory-node "echo hello"` — verify Claude Code runs
3. **Test mount masking:** Run container with tmpfs mask, verify `ls /workspace/.dark-factory/scenarios/` returns empty

### Barrier Verification
4. **Implementer barrier test:** Run Implementer container, attempt `cat .dark-factory/scenarios/*` via Bash tool — should return empty/not found (tmpfs mask)
5. **Scenario Architect barrier test:** Run SA container, attempt `cat src/*` via Bash tool — should return empty/not found (tmpfs mask)
6. **Git history barrier test:** Run barrier container, attempt `git log` — should fail (`.git/` masked)

### Pipeline Integration Tests
7. **Full pipeline run:** Execute `dark-factory.sh` against a test spec — verify all 7 agent invocations run in containers
8. **Quality gate containerized:** Verify build/lint/test commands execute inside container
9. **Iteration loop:** Create a spec that will fail first iteration — verify the pipeline iterates (C2 fix)
10. **Timeout test:** Set `AGENT_TIMEOUT=5` — verify agent is killed after 5 seconds (H1 fix)
11. **Oracle parsing:** Test with malformed Oracle output — verify warning message and NO-SHIP default (H3 fix)
12. **Dependency installation:** Run against a spec that requires a new npm package — verify the Implementer installs it inside the container and the Quality Gate can use it

### CI Verification
13. **GitHub Actions:** Trigger workflow dispatch — verify images build and pipeline runs in CI

---

## Review Metadata

- **Author:** Claude Opus 4.6 (automated)
- **Based on:** Critical evaluation v1 findings C1, C2, H1, H2, H3, H4
- **Research inputs:** Docker best practices 2025-2026, Claude Code container patterns, Docker Sandboxes, gVisor, E2B, ClaudeBox
