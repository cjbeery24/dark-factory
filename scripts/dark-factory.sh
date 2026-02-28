#!/usr/bin/env bash
set -euo pipefail

# Dark Factory Pipeline Orchestrator
# Usage: ./scripts/dark-factory.sh [spec-name] [options]
#
# Options:
#   --max-iterations N    Maximum iteration cycles (default: 3)
#   --test-cmd "command"  Override test command
#   --resume              Resume a previous interrupted run
#   --fresh               Start fresh, ignoring previous state
#   --ci                  CI mode: auto-resume, no interactive prompts
#   --skip-dep-check      Skip dependency checking for multi-spec plans
#   --debug               Keep containers alive on failure for inspection

MAX_ITERATIONS=3
TEST_CMD=""
RESUME_MODE=""
CI_MODE=false
SKIP_DEP_CHECK=false
DEBUG_MODE=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ==============================================================================
# Config validation and sourcing
# ==============================================================================

validate_config() {
    local config="$1"
    [[ ! -f "$config" ]] && return 0
    # Allow: comments, blank lines, variable assignments (with optional export)
    if grep -vE '^\s*#|^\s*$|^\s*(export\s+)?[a-zA-Z_][a-zA-Z0-9_]*=' "$config" | grep -qE '.'; then
        echo "ERROR: Config contains non-assignment lines. Only variable assignments allowed."
        echo "Review: $config"
        exit 1
    fi
}

# Source per-project config if it exists
CONFIG_FILE=".dark-factory/config.sh"
if [[ -f "$CONFIG_FILE" ]]; then
    validate_config "$CONFIG_FILE"
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"

    # Post-source: validate plain-value variables don't contain shell metacharacters
    for var in LANGUAGE SOURCE_DIR TEST_DIR DARK_FACTORY_HOME; do
        val="${!var:-}"
        if [[ "$val" =~ [\$\`\(] ]]; then
            echo "ERROR: Config variable $var contains shell metacharacters: $val"
            exit 1
        fi
    done
fi

# Resolve agent path: use DARK_FACTORY_HOME from config, or detect from script location
AGENTS_DIR="${DARK_FACTORY_HOME:-dark-factory}/agents"
if [[ ! -d "$AGENTS_DIR" ]]; then
    # Fallback: try relative to the script directory (running from within the repo)
    AGENTS_DIR="${SCRIPT_DIR}/../agents"
fi

# ==============================================================================
# Timeout detection (for host-side operations)
# ==============================================================================

if command -v timeout &>/dev/null; then
    TIMEOUT_CMD="timeout"
elif command -v gtimeout &>/dev/null; then
    TIMEOUT_CMD="gtimeout"
else
    TIMEOUT_CMD=""
    echo "WARN: timeout command not found. No execution time limits." >&2
fi

# ==============================================================================
# Shared functions
# ==============================================================================

# --- State management (P1-5) ---

write_state() {
    local key="$1" value="$2"
    mkdir -p "$(dirname "$STATE_FILE")"
    local tmp="${STATE_FILE}.tmp"
    (
        flock -x 200 2>/dev/null || true  # Best-effort lock
        if [[ -f "$STATE_FILE" ]]; then
            grep -v "^${key}=" "$STATE_FILE" > "$tmp" 2>/dev/null || true
        else
            : > "$tmp"
        fi
        echo "${key}=${value}" >> "$tmp"
        mv "$tmp" "$STATE_FILE"
    ) 200>"${STATE_FILE}.lock"
}

read_state() {
    local key="$1" default="${2:-}"
    grep "^${key}=" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || echo "$default"
}

# --- Audit logging (P0-4) ---

audit_log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [$1] [$2] ${3:-}" >> "$AUDIT_LOG"
}

audit_check_output() {
    local agent="$1" output="$2" pattern="$3"
    local hits
    hits=$(echo "$output" | grep -c "$pattern" 2>/dev/null) || hits=0
    if [[ "$hits" -gt 0 ]]; then
        audit_log "$agent" "VIOLATION_SUSPECTED" \
            "Output mentions restricted pattern '$pattern' ($hits times). Manual review recommended."
        echo "WARNING: $agent output references '$pattern' ($hits times) — check audit log"
    fi
}

audit_summary() {
    local v
    v=$(grep -c "VIOLATION_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || v=0
    echo ""
    echo "--- Audit: ${v} suspected violation(s) ---"
    if [[ "$v" -gt 0 ]]; then
        grep "VIOLATION_SUSPECTED" "$AUDIT_LOG"
    fi
    echo "Full log: $AUDIT_LOG"
}

# --- Spec validation (P0-3) ---

validate_spec_result() {
    local status_file=".dark-factory/reviews/${SPEC_NAME}-validation.status"
    local report_file=".dark-factory/reviews/${SPEC_NAME}-validation.md"
    local verdict="" score=""

    # Primary: parse .status file (key=value)
    if [[ -f "$status_file" ]]; then
        verdict=$(grep '^verdict=' "$status_file" | cut -d= -f2-)
        score=$(grep '^score=' "$status_file" | cut -d= -f2-)
    fi

    # Fallback: parse markdown report for "Score: XX/100" pattern
    if [[ -z "$score" ]] && [[ -f "$report_file" ]]; then
        score=$(grep -oE '[0-9]+/100' "$report_file" | head -1 | cut -d/ -f1) || score=""
        if grep -q 'REJECT\|REVISE' "$report_file"; then
            verdict="REJECT"
        else
            verdict="PASS"
        fi
        echo "WARNING: No .status file found. Parsed score from markdown: ${score:-unknown}"
    fi

    # Enforce hard gate
    if [[ -z "$score" ]]; then
        echo "ERROR: Could not determine validation score. Re-run spec validation."
        return 1
    fi
    if [[ "$verdict" != "PASS" ]]; then
        echo "SPEC VALIDATION FAILED: Verdict is $verdict."
        return 1
    fi
    if [[ "$score" -lt 100 ]]; then
        echo "SPEC VALIDATION FAILED: Score is ${score}/100 (must be 100)."
        return 1
    fi
    echo "Spec validated: PASS (${score}/100)"
}

# --- Dependency checking (P2-4) ---

check_dependencies() {
    local spec_file="$1"
    local dep_line deps dep state_file dep_status

    # Extract depends-on from HTML metadata comment
    dep_line=$(grep -A5 'dark-factory-metadata' "$spec_file" 2>/dev/null | grep 'depends-on:' | head -1) || dep_line=""
    if [[ -z "$dep_line" ]]; then
        return 0  # No dependencies declared
    fi

    # Parse: depends-on: [F-1, F-2] or depends-on: []
    deps=$(echo "$dep_line" | sed 's/.*depends-on:\s*\[//;s/\].*//' | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    if [[ -z "$deps" ]]; then
        return 0  # Empty dependency list
    fi

    local failed=false
    while IFS= read -r dep; do
        [[ -z "$dep" ]] && continue
        dep=$(echo "$dep" | tr -d '[:space:]')

        # Find spec files matching this feature ID
        local found=false
        for sf in .dark-factory/specs/*.md; do
            [[ ! -f "$sf" ]] && continue
            if grep -q "feature-id:.*${dep}" "$sf" 2>/dev/null; then
                local dep_spec_name
                dep_spec_name="$(basename "$sf" .md)"
                state_file=".dark-factory/state/${dep_spec_name}.state"
                if [[ -f "$state_file" ]]; then
                    dep_status=$(grep '^status=' "$state_file" 2>/dev/null | tail -1 | cut -d= -f2-) || dep_status=""
                    if [[ "$dep_status" == "shipped" ]]; then
                        found=true
                        break
                    fi
                fi
                echo "ERROR: Dependency ${dep} (spec: ${dep_spec_name}) has not shipped (status: ${dep_status:-unknown})."
                failed=true
                found=true
                break
            fi
        done

        if [[ "$found" == "false" ]]; then
            echo "ERROR: Dependency ${dep} not found in any spec file."
            failed=true
        fi
    done <<< "$deps"

    if [[ "$failed" == "true" ]]; then
        echo "Unmet dependencies. Run dependent specs first, or use --skip-dep-check to override."
        return 1
    fi
}

# ==============================================================================
# Docker isolation functions
# ==============================================================================

# --- Docker prerequisite check ---

require_docker() {
    if ! command -v docker &>/dev/null; then
        echo "ERROR: Docker is required. Install Docker Desktop or Docker Engine."
        echo "See: https://docs.docker.com/get-docker/"
        exit 1
    fi
    if ! docker info &>/dev/null 2>&1; then
        echo "ERROR: Docker daemon is not running. Start Docker Desktop or the Docker service."
        exit 1
    fi
    if ! docker image inspect "dark-factory-${LANGUAGE:-node}" &>/dev/null 2>&1; then
        echo "ERROR: Docker image 'dark-factory-${LANGUAGE:-node}' not found."
        echo "Run: bash docker/build-images.sh ${LANGUAGE:-node}"
        exit 1
    fi
}

# --- Docker network setup ---

setup_network() {
    docker network inspect "${DOCKER_NETWORK:-df-pipeline}" &>/dev/null 2>&1 || \
        docker network create --driver bridge "${DOCKER_NETWORK:-df-pipeline}"
}

# --- Container cleanup trap ---

cleanup_containers() {
    # Kill any orphaned containers from this pipeline run
    docker ps -q --filter "name=df-${SPEC_NAME:-unknown}-" 2>/dev/null | \
        xargs -r docker rm -f 2>/dev/null || true
}

# --- Symlink safety check (barrier protection) ---

check_symlink_safety() {
    local dir="$1" forbidden="$2"
    # Find symlinks in $dir that resolve to paths inside $forbidden
    while IFS= read -r link; do
        local target
        target=$(readlink -f "$link" 2>/dev/null) || continue
        if [[ "$target" == *"$forbidden"* ]]; then
            echo "ERROR: Symlink '$link' points into restricted area '$forbidden'"
            echo "This would bypass the information barrier. Remove or fix the symlink."
            exit 1
        fi
    done < <(find "$dir" -type l 2>/dev/null)
}

# --- Core agent runner (Docker-isolated) ---

run_agent() {
    local role="$1"
    local prompt="$2"
    local image="dark-factory-${LANGUAGE:-node}"
    local -a masks=()
    local -a rw_overrides=()
    local project_mode="ro"
    local cache_prefix="df-${role}"  # Per-role cache to prevent poisoning
    local use_cache=false
    local container_name="df-${SPEC_NAME}-${role}-$$"

    # Role-specific mount configuration
    case "$role" in
        spec-validator)
            rw_overrides+=(".dark-factory/reviews")
            ;;
        scenario-architect)
            # Mask ALL configured source directories (fixes H4)
            for dir in ${SOURCE_DIRS:-${SOURCE_DIR:-src}}; do
                [[ -d "$dir" ]] && masks+=("$dir")
            done
            masks+=(".git")
            rw_overrides+=(".dark-factory/scenarios/${SPEC_NAME}")
            ;;
        implementer)
            project_mode="rw"
            masks+=(".dark-factory/scenarios" ".git")
            use_cache=true
            ;;
        quality-gate)
            project_mode="rw"
            use_cache=true
            ;;
        reviewer|oracle|escalation)
            rw_overrides+=(".dark-factory/reviews")
            ;;
    esac

    # Pre-flight symlink safety check for barrier agents
    if [[ ${#masks[@]} -gt 0 ]]; then
        for mask in "${masks[@]}"; do
            [[ "$mask" == ".git" ]] && continue
            check_symlink_safety "." "$mask"
        done
    fi

    # Build docker run command
    local -a docker_args=(
        docker run
        --name "$container_name"
        --network "${DOCKER_NETWORK:-df-pipeline}"
        --cap-drop=ALL
        --security-opt "no-new-privileges:true"
        --pids-limit=512
        --memory="${AGENT_MEMORY:-4g}"
        --cpus="${AGENT_CPUS:-4}"
        --user "$(id -u):$(id -g)"
        -e HOME=/home/agent
        -e ANTHROPIC_API_KEY
        --tmpfs /tmp:size=512m
        --tmpfs /home/agent:size=512m
    )

    # Conditional --rm based on debug mode
    if [[ "${DEBUG_MODE}" == "true" ]]; then
        echo "DEBUG: Container '$container_name' will be kept for inspection."
    else
        docker_args+=(--rm)
    fi

    # Project root mount
    docker_args+=(-v "${PWD}:/workspace:${project_mode}")

    # Apply tmpfs masks (HARD barrier enforcement)
    for mask in "${masks[@]}"; do
        docker_args+=(--tmpfs "/workspace/${mask}:ro,size=1k")
    done

    # Apply RW overrides (output directories, explicitly :rw)
    for override in "${rw_overrides[@]}"; do
        # Ensure the host directory exists before mounting
        mkdir -p "${PWD}/${override}"
        docker_args+=(-v "${PWD}/${override}:/workspace/${override}:rw")
    done

    # Apply per-role cache volumes (prevents cache poisoning)
    if [[ "$use_cache" == "true" ]]; then
        case "${LANGUAGE:-node}" in
            node)   docker_args+=(-v "${cache_prefix}-npm:/home/agent/.npm") ;;
            python) docker_args+=(-v "${cache_prefix}-pip:/home/agent/.cache/pip") ;;
            go)     docker_args+=(-v "${cache_prefix}-go:/home/agent/go/pkg/mod") ;;
            rust)   docker_args+=(-v "${cache_prefix}-cargo:/home/agent/.cargo/registry") ;;
        esac
    fi

    # Defense-in-depth: keep prompt barriers alongside Docker barriers
    local -a claude_args=(--max-turns "${AGENT_MAX_TURNS:-200}")
    case "$role" in
        implementer)
            claude_args+=(--disallowedTools "$IMPLEMENTER_DENY")
            claude_args+=(--append-system-prompt "$IMPLEMENTER_BARRIER")
            ;;
        scenario-architect)
            claude_args+=(--disallowedTools "$SCENARIO_ARCH_DENY")
            claude_args+=(--append-system-prompt "$SCENARIO_ARCH_BARRIER")
            ;;
    esac

    # Audit: log the container invocation
    audit_log "$role" "CONTAINER_START" "container=$container_name image=$image masks=[${masks[*]:-}]"

    # Image + claude args + prompt
    docker_args+=("$image" "${claude_args[@]}" "$prompt")

    # Execute with timeout
    local timeout_seconds="${AGENT_TIMEOUT:-600}"
    local exit_code=0
    if [[ -n "$TIMEOUT_CMD" ]]; then
        $TIMEOUT_CMD "$timeout_seconds" "${docker_args[@]}" || exit_code=$?
    else
        "${docker_args[@]}" || exit_code=$?
    fi

    if [[ $exit_code -eq 124 ]]; then
        audit_log "$role" "TIMEOUT" "exceeded ${timeout_seconds}s"
        echo "WARNING: Agent '$role' timed out after ${timeout_seconds}s."
    fi

    audit_log "$role" "CONTAINER_END" "exit_code=$exit_code"
    return $exit_code
}

# --- Agent invocation wrapper (H5 fix) ---
# Captures exit code + output from run_agent into globals.
# Performs minimum output length validation and audit logging.
# Globals are safe here: pipeline execution is strictly sequential (no concurrent agents).

AGENT_OUTPUT=""
AGENT_EXIT=0

invoke_agent() {
    local role="$1"
    local prompt="$2"
    local min_output="${3:-20}"

    AGENT_EXIT=0
    AGENT_OUTPUT=$(run_agent "$role" "$prompt" 2>&1) || AGENT_EXIT=$?

    if [[ $AGENT_EXIT -ne 0 ]]; then
        audit_log "$role" "AGENT_FAILURE" "exit_code=$AGENT_EXIT output_length=${#AGENT_OUTPUT}"
    fi

    # Detect silent failures: agent exits 0 but produces negligible output
    # (API key error, container misconfiguration, etc.)
    if [[ ${#AGENT_OUTPUT} -lt $min_output ]]; then
        audit_log "$role" "AGENT_LOW_OUTPUT" "length=${#AGENT_OUTPUT} threshold=$min_output"
        if [[ $AGENT_EXIT -eq 0 ]]; then
            AGENT_EXIT=99
            audit_log "$role" "AGENT_FAILURE" "exit_code=99(synthetic) reason=output_below_threshold"
        fi
    fi
}

# --- Quality gate runner (Docker-isolated) ---

run_quality_gate() {
    local spec_name="$1"
    local container_name="df-${spec_name}-quality-gate-$$"
    local qg_script_path="${DARK_FACTORY_HOME:-dark-factory}/scripts/quality-gate.sh"

    local -a docker_args=(
        docker run
        --name "$container_name"
        --network "${DOCKER_NETWORK:-df-pipeline}"
        --cap-drop=ALL
        --security-opt "no-new-privileges:true"
        --user "$(id -u):$(id -g)"
        -e HOME=/home/agent
        --tmpfs /tmp:size=512m
        --tmpfs /home/agent:size=512m
        # Quality Gate gets FULL rw access — it runs build/lint/test commands
        # that write to node_modules, dist, target, __pycache__, etc.
        -v "${PWD}:/workspace:rw"
    )

    # Conditional --rm based on debug mode
    if [[ "${DEBUG_MODE}" == "true" ]]; then
        echo "DEBUG: QG container '$container_name' will be kept for inspection."
    else
        docker_args+=(--rm)
    fi

    # Per-role cache volume (separate from Implementer's cache)
    case "${LANGUAGE:-node}" in
        node)   docker_args+=(-v "df-qg-npm:/home/agent/.npm") ;;
        python) docker_args+=(-v "df-qg-pip:/home/agent/.cache/pip") ;;
        go)     docker_args+=(-v "df-qg-go:/home/agent/go/pkg/mod") ;;
        rust)   docker_args+=(-v "df-qg-cargo:/home/agent/.cargo/registry") ;;
    esac

    # Override entrypoint to run bash, not claude
    docker_args+=(--entrypoint bash --workdir /workspace)
    docker_args+=("dark-factory-${LANGUAGE:-node}")
    docker_args+=("/workspace/${qg_script_path}" "$spec_name" ${TEST_CMD:+--test-cmd "$TEST_CMD"})

    audit_log "quality-gate" "CONTAINER_START" "container=$container_name"

    local exit_code=0
    local gate_timeout="${GATE_TIMEOUT:-300}"
    if [[ -n "$TIMEOUT_CMD" ]]; then
        $TIMEOUT_CMD "$gate_timeout" "${docker_args[@]}" || exit_code=$?
    else
        "${docker_args[@]}" || exit_code=$?
    fi

    if [[ $exit_code -eq 124 ]]; then
        audit_log "quality-gate" "TIMEOUT" "exceeded ${gate_timeout}s"
        echo "WARNING: Quality gate timed out after ${gate_timeout}s."
    fi

    audit_log "quality-gate" "CONTAINER_END" "exit_code=$exit_code"
    return $exit_code
}

# ==============================================================================
# Interactive spec selector
# ==============================================================================

select_spec() {
    local specs_dir=".dark-factory/specs"
    local -a names=()
    local -a dates=()
    local file base

    # Collect specs newest-first
    while IFS= read -r file; do
        base="$(basename "$file" .md)"
        names+=("$base")
        if [[ "$(uname)" == "Darwin" ]]; then
            dates+=("$(stat -f '%Sm' -t '%Y-%m-%d' "$file")")
        else
            dates+=("$(date -r "$file" '+%Y-%m-%d')")
        fi
    done < <(ls -t "$specs_dir"/*.md 2>/dev/null)

    local count=${#names[@]}

    if (( count == 0 )); then
        echo "ERROR: No specs found in $specs_dir/"
        echo "Create a spec first using a template from templates/"
        exit 1
    fi

    if (( count == 1 )); then
        echo "Auto-selecting only spec: ${names[0]}"
        SPEC_NAME="${names[0]}"
        return
    fi

    # CI mode: cannot interactively select
    if [[ "$CI_MODE" == "true" ]]; then
        echo "ERROR: Multiple specs found and --ci mode cannot interactively select."
        echo "Provide spec name as first argument."
        exit 1
    fi

    local sel=0  # 0-indexed

    _df_cleanup() {
        printf '\033[?25h'     # show cursor
        printf '\033[?1049l'   # exit alternate screen
    }
    trap '_df_cleanup; exit 130' INT

    printf '\033[?1049h'       # enter alternate screen
    printf '\033[?25l'         # hide cursor

    _df_draw() {
        printf '\033[H\033[J'  # move to top-left + clear screen
        local i line maxw=$(( ${COLUMNS:-80} - 10 ))
        echo "Dark Factory — select a spec:"
        echo ""
        for (( i=0; i<count; i++ )); do
            line="${names[$i]}  (${dates[$i]})"
            if (( ${#line} > maxw )); then
                line="${line:0:$((maxw-1))}…"
            fi
            if (( i == sel )); then
                printf '  \033[36m>\033[0m \033[7m %d) %s \033[0m\n' $((i+1)) "$line"
            else
                printf '    %d) %s\n' $((i+1)) "$line"
            fi
        done
        printf '\n  \033[2m↑/↓ navigate • # jump • enter select • q quit\033[0m'
    }

    _df_select() {
        _df_cleanup
        trap - INT
        SPEC_NAME="${names[$sel]}"
    }

    _df_draw

    local key key2 key3
    while true; do
        read -rsn1 key
        case "$key" in
            $'\x1b')
                key2='' key3=''
                read -rsn1 -t 0.1 key2
                read -rsn1 -t 0.1 key3
                case "${key2}${key3}" in
                    '[A'|'OA') (( sel > 0 )) && (( sel-- )) || true ;;
                    '[B'|'OB') (( sel < count - 1 )) && (( sel++ )) || true ;;
                esac
                ;;
            [1-9])
                if (( key >= 1 && key <= count )); then
                    sel=$(( key - 1 ))
                    _df_select
                    return
                fi
                ;;
            ''|$'\n')  # Enter
                _df_select
                return
                ;;
            q|Q)
                _df_cleanup
                trap - INT
                exit 0
                ;;
        esac
        _df_draw
    done
}

# ==============================================================================
# Argument parsing
# ==============================================================================

# Determine spec name: from argument or interactive picker
if [[ -n "${1:-}" ]] && [[ "${1:0:2}" != "--" ]]; then
    SPEC_NAME="$1"
    shift
else
    SPEC_NAME=""
fi

# Parse optional arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --max-iterations)
            MAX_ITERATIONS="$2"
            shift 2
            ;;
        --test-cmd)
            TEST_CMD="$2"
            shift 2
            ;;
        --resume)
            RESUME_MODE="resume"
            shift
            ;;
        --fresh)
            RESUME_MODE="fresh"
            shift
            ;;
        --ci)
            CI_MODE=true
            shift
            ;;
        --skip-dep-check)
            SKIP_DEP_CHECK=true
            shift
            ;;
        --debug)
            DEBUG_MODE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# If no spec name from args, use interactive picker
if [[ -z "$SPEC_NAME" ]]; then
    select_spec
fi

# --- P0-2: Input validation ---
if [[ ! "$SPEC_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]; then
    echo "ERROR: Invalid spec name: '$SPEC_NAME'"
    echo "Spec names must start with alphanumeric and contain only alphanumerics, dots, hyphens, underscores."
    exit 1
fi

# ==============================================================================
# Path setup
# ==============================================================================

SPEC_FILE=".dark-factory/specs/${SPEC_NAME}.md"
SCENARIOS_DIR=".dark-factory/scenarios/${SPEC_NAME}"
REVIEW_FILE=".dark-factory/reviews/${SPEC_NAME}-review.md"
REPORT_FILE=".dark-factory/reports/${SPEC_NAME}-quality-gate.md"

# Audit setup (P0-4)
AUDIT_DIR=".dark-factory/audit"
mkdir -p "$AUDIT_DIR"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
AUDIT_LOG="${AUDIT_DIR}/${SPEC_NAME}-${RUN_ID}.log"

# State file setup (P1-5)
STATE_FILE=".dark-factory/state/${SPEC_NAME}.state"
mkdir -p ".dark-factory/state"

# ==============================================================================
# Information barrier configuration (P0-1)
# Defense-in-depth: kept alongside Docker filesystem barriers
# ==============================================================================

SA_SOURCE_DIR="${SOURCE_DIR:-src}"

# File-access tool restrictions (reliable — path-based patterns)
IMPLEMENTER_DENY="Read(.dark-factory/scenarios/**),Edit(.dark-factory/scenarios/**),Write(.dark-factory/scenarios/**),Grep(.dark-factory/scenarios/**),Glob(.dark-factory/scenarios/**)"

SCENARIO_ARCH_DENY="Read(${SA_SOURCE_DIR}/**),Grep(${SA_SOURCE_DIR}/**),Glob(${SA_SOURCE_DIR}/**)"

IMPLEMENTER_BARRIER="CRITICAL SECURITY RULE: You MUST NOT read, access, or examine any file in .dark-factory/scenarios/. This is an information barrier. If you see scenario content in error messages, do not use it to guide implementation."

SCENARIO_ARCH_BARRIER="CRITICAL SECURITY RULE: You MUST NOT read, access, or examine any implementation source code in ${SA_SOURCE_DIR}/. Derive all scenarios purely from the specification. If you encounter implementation details, ignore them."

# ==============================================================================
# Docker setup
# ==============================================================================

require_docker

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ERROR: ANTHROPIC_API_KEY is not set."
    echo "Docker containers cannot use config-file auth — an explicit API key is required."
    echo "Set it with: export ANTHROPIC_API_KEY=your-key"
    exit 1
fi

setup_network

# ==============================================================================
# Resume detection (P1-5)
# ==============================================================================

RESUME=false
if [[ -f "$STATE_FILE" ]] && [[ "$RESUME_MODE" != "fresh" ]]; then
    if [[ "$RESUME_MODE" == "resume" ]] || [[ "$CI_MODE" == "true" ]]; then
        RESUME=true  # Auto-resume in CI or with --resume flag
    elif [[ -t 0 ]]; then
        # Interactive: prompt user
        read -rp "Previous run detected for '${SPEC_NAME}'. [r]esume / [s]tart fresh / [q]uit: " choice
        case "$choice" in
            r|R) RESUME=true ;;
            s|S) RESUME=false ;;
            *) exit 0 ;;
        esac
    else
        echo "Previous run detected for '${SPEC_NAME}'. Use --resume or --fresh. Exiting."
        exit 1
    fi
fi

# Crash recovery trap — includes container cleanup
trap 'ec=$?; cleanup_containers; if [[ $ec -ne 0 ]]; then write_state "status" "interrupted" 2>/dev/null || true; fi' EXIT

# ==============================================================================
# Pipeline start
# ==============================================================================

echo "============================================"
echo "  Dark Factory Pipeline"
echo "  Spec: ${SPEC_NAME}"
echo "  Max Iterations: ${MAX_ITERATIONS}"
echo "  Run ID: ${RUN_ID}"
echo "  Isolation: Docker containers"
if [[ "$RESUME" == "true" ]]; then
    echo "  Mode: RESUME"
fi
if [[ "$DEBUG_MODE" == "true" ]]; then
    echo "  Debug: ON (containers preserved on failure)"
fi
echo "============================================"
echo ""

audit_log "pipeline" "START" "spec=${SPEC_NAME} max_iterations=${MAX_ITERATIONS} resume=${RESUME} run_id=${RUN_ID} isolation=docker"

# Step 0: Verify spec exists
if [[ ! -f "$SPEC_FILE" ]]; then
    echo "ERROR: Spec file not found: $SPEC_FILE"
    echo "Create a spec first using a template from templates/"
    exit 1
fi

write_state "spec" "$SPEC_NAME"
write_state "status" "running"
write_state "run_id" "$RUN_ID"

# --- P2-4: Dependency checking ---
if [[ "$SKIP_DEP_CHECK" == "false" ]]; then
    if ! check_dependencies "$SPEC_FILE"; then
        exit 1
    fi
fi

# ==============================================================================
# Step 1: Validate specification
# ==============================================================================

COMPLETED_STEP=$(read_state "completed_step" "0")

if [[ "$RESUME" == "true" ]] && [[ "$COMPLETED_STEP" -ge 1 ]]; then
    echo "[Step 1] Spec validation already completed (resuming). Verifying..."
    validate_spec_result
else
    echo "[Step 1] Validating specification..."
    echo "---"

    audit_log "spec-validator" "START" ""
    invoke_agent "spec-validator" \
        "You are the Spec Validator. Read the agent definition at ${AGENTS_DIR}/spec-validator.md, then validate the specification at ${SPEC_FILE}. Produce the validation report. If the score is below 100%, output REVISE or REJECT with specific issues. Write the report to .dark-factory/reviews/${SPEC_NAME}-validation.md. Also write a machine-readable status file to .dark-factory/reviews/${SPEC_NAME}-validation.status with this exact format (one key=value per line, no quotes): spec=${SPEC_NAME} on the first line, date=<ISO 8601> on the second, score=<0-100> on the third, verdict=<PASS|REVISE|REJECT> on the fourth."
    echo "$AGENT_OUTPUT"
    audit_log "spec-validator" "END" ""

    if [[ $AGENT_EXIT -ne 0 ]]; then
        echo "ERROR: Spec validator agent failed (exit $AGENT_EXIT). Cannot proceed."
        write_state "status" "failed"
        write_state "failure_reason" "spec-validator agent failed with exit code $AGENT_EXIT"
        exit 1
    fi

    echo ""

    # Check validation result (P0-3: hard gate)
    if ! validate_spec_result; then
        echo "Fix the issues in your spec and re-run."
        exit 1
    fi

    write_state "completed_step" "1"
fi

# ==============================================================================
# Step 2: Generate blind test scenarios
# ==============================================================================

if [[ "$RESUME" == "true" ]] && [[ "$COMPLETED_STEP" -ge 2 ]]; then
    echo "[Step 2] Scenario generation already completed (resuming)."
else
    if [[ -d "$SCENARIOS_DIR" ]] && [[ -n "$(ls -A "$SCENARIOS_DIR" 2>/dev/null)" ]]; then
        # P2-3: Stale scenario warning
        if [[ -f "$SPEC_FILE" ]]; then
            spec_mtime=$(stat -f '%m' "$SPEC_FILE" 2>/dev/null || stat -c '%Y' "$SPEC_FILE" 2>/dev/null || echo "0")
            scenario_mtime=$(stat -f '%m' "$SCENARIOS_DIR" 2>/dev/null || stat -c '%Y' "$SCENARIOS_DIR" 2>/dev/null || echo "0")
            if [[ "$spec_mtime" -gt "$scenario_mtime" ]]; then
                echo "WARNING: Scenarios may be stale (spec modified after scenario generation). Consider regenerating."
            fi
        fi
        echo "[Step 2] Scenarios already exist at ${SCENARIOS_DIR}, skipping regeneration."
    else
        echo "[Step 2] Generating blind test scenarios..."
        echo "---"
        echo "IMPORTANT: Running scenario generation in Docker isolation."

        mkdir -p "$SCENARIOS_DIR"
        audit_log "scenario-architect" "START" ""
        invoke_agent "scenario-architect" \
            "You are the Scenario Architect. Read the agent definition at ${AGENTS_DIR}/scenario-architect.md, then generate blind test scenarios for the spec at ${SPEC_FILE}. Write executable test files to ${SCENARIOS_DIR}/. Do NOT read any implementation source code. Produce the scenario generation report."
        echo "$AGENT_OUTPUT"
        audit_check_output "scenario-architect" "$AGENT_OUTPUT" "${SA_SOURCE_DIR}/"
        audit_log "scenario-architect" "END" ""

        if [[ $AGENT_EXIT -ne 0 ]]; then
            echo "ERROR: Scenario architect agent failed (exit $AGENT_EXIT). Cannot proceed without scenarios."
            write_state "status" "failed"
            write_state "failure_reason" "scenario-architect agent failed with exit code $AGENT_EXIT"
            exit 1
        fi
    fi

    write_state "completed_step" "2"
fi
echo ""

# ==============================================================================
# Step 3: Implementation loop
# ==============================================================================

echo "[Step 3] Starting implementation..."
echo "---"

# Resume iteration if available
if [[ "$RESUME" == "true" ]]; then
    ITERATION=$(read_state "iteration" "1")
else
    ITERATION=1
fi
SHIP=false

while [[ $ITERATION -le $MAX_ITERATIONS ]] && [[ "$SHIP" == "false" ]]; do
    echo ""
    echo "========== Iteration ${ITERATION} of ${MAX_ITERATIONS} =========="
    echo ""

    write_state "iteration" "$ITERATION"
    write_state "phase" "implementing"

    # --- Implementation ---
    if [[ $ITERATION -eq 1 ]]; then
        echo "[Step 3.${ITERATION}] Implementing from spec..."
        audit_log "implementer" "START" "iteration=$ITERATION"
        invoke_agent "implementer" \
            "You are the Implementer. Read the agent definition at ${AGENTS_DIR}/implementer.md, then implement the specification at ${SPEC_FILE}. Follow existing codebase conventions. Write self-tests. Do NOT read anything in .dark-factory/scenarios/."
        echo "$AGENT_OUTPUT"
        audit_check_output "implementer" "$AGENT_OUTPUT" ".dark-factory/scenarios/"
        audit_log "implementer" "END" ""

        if [[ $AGENT_EXIT -ne 0 ]]; then
            echo "WARNING: Implementer agent failed (exit $AGENT_EXIT). Proceeding to review/oracle."
            mkdir -p ".dark-factory/reviews"
            echo "IMPLEMENTER FAILURE: Agent exited with code $AGENT_EXIT on iteration ${ITERATION}. No implementation changes were made." \
                > ".dark-factory/reviews/${SPEC_NAME}-impl-failure-iter${ITERATION}.md"
        fi
    else
        echo "[Step 3.${ITERATION}] Iterating on implementation with feedback..."
        FEEDBACK_FILE=".dark-factory/reviews/${SPEC_NAME}-feedback-iter$((ITERATION-1)).md"
        audit_log "implementer" "START" "iteration=$ITERATION"

        IMPL_PROMPT="You are the Implementer. Read the agent definition at ${AGENTS_DIR}/implementer.md. This is iteration ${ITERATION}."
        if [[ -f "$FEEDBACK_FILE" ]]; then
            IMPL_PROMPT="${IMPL_PROMPT} Read the structured feedback at ${FEEDBACK_FILE}."
        fi
        IMPL_PROMPT="${IMPL_PROMPT} Also read ${REPORT_FILE} and ${REVIEW_FILE} for additional context. Fix the issues in your implementation. Do NOT read anything in .dark-factory/scenarios/."

        invoke_agent "implementer" "$IMPL_PROMPT"
        echo "$AGENT_OUTPUT"
        audit_check_output "implementer" "$AGENT_OUTPUT" ".dark-factory/scenarios/"
        audit_log "implementer" "END" ""

        if [[ $AGENT_EXIT -ne 0 ]]; then
            echo "WARNING: Implementer agent failed (exit $AGENT_EXIT). Proceeding to review/oracle."
            mkdir -p ".dark-factory/reviews"
            echo "IMPLEMENTER FAILURE: Agent exited with code $AGENT_EXIT on iteration ${ITERATION}. Implementation may be incomplete." \
                > ".dark-factory/reviews/${SPEC_NAME}-impl-failure-iter${ITERATION}.md"
        fi
    fi
    echo ""

    write_state "phase" "quality-gate"

    # --- Quality Gates (Fix C2: capture exit code without crashing) ---
    echo "[Step 4.${ITERATION}] Running quality gates..."
    echo "---"
    audit_log "quality-gate" "START" "iteration=$ITERATION"

    QG_EXIT=0
    run_quality_gate "$SPEC_NAME" || QG_EXIT=$?

    if [[ "$QG_EXIT" -ne 0 ]]; then
        audit_log "quality-gate" "FAIL" "exit_code=$QG_EXIT"
        echo "[Step 4.${ITERATION}] Quality gate FAILED (exit $QG_EXIT) — proceeding to review."
    fi
    echo ""

    write_state "phase" "review"

    # --- Review ---
    echo "[Step 5.${ITERATION}] Running review..."
    echo "---"
    audit_log "reviewer" "START" "iteration=$ITERATION"
    invoke_agent "reviewer" \
        "You are the Reviewer. Read the agent definition at ${AGENTS_DIR}/reviewer.md. Review the implementation for spec ${SPEC_FILE}. Quality gate report is at ${REPORT_FILE}. Write your review to ${REVIEW_FILE}."
    echo "$AGENT_OUTPUT"
    audit_log "reviewer" "END" ""

    if [[ $AGENT_EXIT -ne 0 ]]; then
        echo "WARNING: Reviewer agent failed (exit $AGENT_EXIT). Proceeding to oracle without review."
    fi
    echo ""

    write_state "phase" "oracle"

    # --- Oracle Decision ---
    echo "[Step 6.${ITERATION}] Oracle decision..."
    echo "---"
    audit_log "oracle" "START" "iteration=$ITERATION"
    invoke_agent "oracle" \
        "You are the Oracle. Read the agent definition at ${AGENTS_DIR}/oracle.md. Make a SHIP/NO-SHIP decision for spec ${SPEC_FILE}. Review is at ${REVIEW_FILE}. Quality gate report is at ${REPORT_FILE}. This is iteration ${ITERATION} of ${MAX_ITERATIONS}. Your output MUST start with SHIP or NO-SHIP on the very first line (plain text, no markdown formatting), followed by your rationale. If NO-SHIP, also write structured iteration feedback to .dark-factory/reviews/${SPEC_NAME}-feedback-iter${ITERATION}.md using the Iteration Feedback format from your agent definition."
    ORACLE_OUTPUT="$AGENT_OUTPUT"

    echo "$ORACLE_OUTPUT"
    audit_log "oracle" "END" ""

    # Parse Oracle decision (Fix H3: robust parsing)
    ORACLE_FIRST_LINE=$(echo "$ORACLE_OUTPUT" | head -1 | tr -d '[:space:]')
    ORACLE_DECISION=$(echo "$ORACLE_FIRST_LINE" | tr '[:lower:]' '[:upper:]')

    if [[ "$ORACLE_DECISION" == "SHIP" ]]; then
        SHIP=true
        audit_log "oracle" "DECISION" "SHIP"
        echo ""
        echo "============================================"
        echo "  SHIP — Pipeline complete!"
        echo "  Iterations: ${ITERATION}"
        echo "============================================"
    elif [[ "$ORACLE_DECISION" =~ ^NO-?SHIP$ ]]; then
        SHIP=false
        audit_log "oracle" "DECISION" "NO-SHIP"
    else
        audit_log "oracle" "MALFORMED_OUTPUT" "first_line='$(echo "$ORACLE_OUTPUT" | head -1)'"
        echo "WARNING: Oracle output did not start with SHIP or NO-SHIP."
        echo "First line was: $(echo "$ORACLE_OUTPUT" | head -1)"
        echo "Treating as NO-SHIP for safety."
        SHIP=false
    fi

    write_state "last_decision" "$(echo "$ORACLE_OUTPUT" | head -1)"
    ITERATION=$((ITERATION + 1))
done

# ==============================================================================
# Pipeline result
# ==============================================================================

if [[ "$SHIP" == "true" ]]; then
    write_state "status" "shipped"
    write_state "completed_step" "99"
    trap - EXIT  # Disarm crash recovery trap
    cleanup_containers  # Clean up any remaining containers
    audit_log "pipeline" "END" "result=SHIP iterations=$((ITERATION - 1))"
    audit_summary
else
    echo ""
    echo "============================================"
    echo "  ESCALATION — Max iterations reached"
    echo "  Human review required."
    echo "  See: ${REVIEW_FILE}"
    echo "  See: ${REPORT_FILE}"

    # P2-3: Generate escalation report
    ESCALATION_FILE=".dark-factory/reviews/${SPEC_NAME}-escalation.md"
    audit_log "pipeline" "ESCALATION" "generating escalation report"

    invoke_agent "escalation" \
        "Read the escalation report template at templates/escalation-report.md. Generate a completed escalation report for spec '${SPEC_NAME}'. Review all iteration feedback files at .dark-factory/reviews/${SPEC_NAME}-feedback-iter*.md, the quality gate report at ${REPORT_FILE}, and the review at ${REVIEW_FILE}. The pipeline run ID is ${RUN_ID}. Write the completed report to ${ESCALATION_FILE}." 0

    if [[ $AGENT_EXIT -ne 0 ]]; then
        echo "  WARNING: Escalation report generation failed (exit $AGENT_EXIT). Continuing."
    fi

    echo "  Escalation report: ${ESCALATION_FILE}"
    echo "  See: docs/ESCALATION.md for next steps"
    echo "============================================"

    write_state "status" "escalated"
    trap - EXIT  # Disarm crash recovery trap
    cleanup_containers  # Clean up any remaining containers
    audit_log "pipeline" "END" "result=ESCALATION iterations=$((ITERATION - 1))"
    audit_summary
    exit 2
fi
