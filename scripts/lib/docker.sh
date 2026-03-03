#!/usr/bin/env bash
# scripts/lib/docker.sh — Docker isolation, container lifecycle, agent runners
#
# Provides:
#   Lifecycle: require_docker, setup_networks, setup_proxies, teardown_proxies, cleanup_containers
#   Safety:    check_symlink_safety
#   Builders:  _build_docker_base_args, _execute_container
#   Runners:   run_agent, invoke_agent, run_quality_gate
#
# Requires globals (set before first use):
#   LANGUAGE, SPEC_NAME, DEBUG_MODE, TIMEOUT_CMD, DRY_RUN (optional)
#   DARK_FACTORY_HOME, AGENTS_DIR, AGENT_MEMORY, AGENT_CPUS, AGENT_MAX_TURNS
#   AGENT_TIMEOUT, GATE_TIMEOUT, DOCKER_NETWORK, EGRESS_FILTERING_ENABLED
#   SOURCE_DIRS, SOURCE_DIR, TEST_CMD
#   DF_AUTH_TYPE (set during pre-flight)
#   All barrier globals from barriers.sh (IMPLEMENTER_DENY, etc.)
#   AUDIT_LOG (for audit_log calls — source audit.sh first)

[[ -n "${_DF_LIB_DOCKER_LOADED:-}" ]] && return 0
_DF_LIB_DOCKER_LOADED=1

# ==============================================================================
# Lifecycle
# ==============================================================================

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
    if [[ "${EGRESS_FILTERING_ENABLED:-true}" == "true" ]]; then
        if ! docker image inspect dark-factory-proxy &>/dev/null 2>&1; then
            echo "ERROR: Docker image 'dark-factory-proxy' not found."
            echo "Run: bash ${DARK_FACTORY_HOME:-dark-factory}/docker/build-images.sh"
            exit 1
        fi
    fi
}

setup_networks() {
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        echo "[dry-run] Would create Docker networks"
        return
    fi
    if [[ "${EGRESS_FILTERING_ENABLED:-true}" == "true" ]]; then
        docker network inspect df-analysis  &>/dev/null 2>&1 || docker network create --internal df-analysis
        docker network inspect df-execution &>/dev/null 2>&1 || docker network create --internal df-execution
        docker network inspect df-external  &>/dev/null 2>&1 || docker network create df-external
        docker network inspect df-evaluation &>/dev/null 2>&1 || docker network create df-evaluation
    else
        docker network inspect "${DOCKER_NETWORK:-df-pipeline}" &>/dev/null 2>&1 || \
            docker network create --driver bridge "${DOCKER_NETWORK:-df-pipeline}"
    fi
}

cleanup_containers() {
    docker ps -q --filter "name=df-${SPEC_NAME:-unknown}-" 2>/dev/null | \
        xargs -r docker rm -f 2>/dev/null || true
}

setup_proxies() {
    [[ "${EGRESS_FILTERING_ENABLED:-true}" != "true" ]] && return
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        echo "[dry-run] Would start proxy containers"
        return
    fi

    for tier in analysis execution; do
        local name="df-${tier}-proxy"
        if ! docker ps --filter "name=${name}" --filter status=running -q | grep -q .; then
            docker run -d --rm --name "$name" \
                --network "df-${tier}" \
                -e TINYPROXY_CONF="/etc/tinyproxy/tinyproxy-${tier}.conf" \
                dark-factory-proxy
            docker network connect df-external "$name"
        fi

        # Readiness probe — wait up to 10s for proxy to accept connections
        local ready=false
        for i in $(seq 1 10); do
            if docker exec "$name" nc -z localhost 8080 2>/dev/null; then
                ready=true; break
            fi
            sleep 1
        done
        if [[ "$ready" != "true" ]]; then
            echo "ERROR: Proxy container '$name' failed to become ready."
            exit 1
        fi
    done
}

teardown_proxies() {
    docker stop df-analysis-proxy  2>/dev/null || true
    docker stop df-execution-proxy 2>/dev/null || true
}

# ==============================================================================
# Safety
# ==============================================================================

check_symlink_safety() {
    local dir="$1" forbidden="$2" max_depth="${3:-}"
    local -a find_args=("$dir")
    [[ -n "$max_depth" ]] && find_args+=(-maxdepth "$max_depth")
    find_args+=(-type l)
    local link target
    while IFS= read -r link; do
        target=$(readlink -f "$link" 2>/dev/null) || continue
        if [[ "$target" == *"$forbidden"* ]]; then
            echo "ERROR: Symlink '$link' points into restricted area '$forbidden'"
            echo "This would bypass the information barrier. Remove or fix the symlink."
            return 1
        fi
    done < <(find "${find_args[@]}" 2>/dev/null)
    return 0
}

# ==============================================================================
# Builders
# ==============================================================================

# Global array set by _build_docker_base_args, consumed by callers and tests.
_DOCKER_ARGS=()

# _build_docker_base_args <container_name> <project_mode>
#   Sets _DOCKER_ARGS with the common Docker plumbing shared by run_agent and run_quality_gate:
#   security opts, resource limits, user mapping, home/tmp tmpfs, debug/rm.
_build_docker_base_args() {
    local container_name="$1"
    local project_mode="$2"

    _DOCKER_ARGS=(
        docker run
        --name "$container_name"
        --cap-drop=ALL
        --security-opt "no-new-privileges:true"
        --pids-limit=512
        --memory="${AGENT_MEMORY:-4g}"
        --cpus="${AGENT_CPUS:-4}"
        --user "$(id -u):$(id -g)"
        -e HOME=/home/agent
        --tmpfs /tmp:size=512m
        --tmpfs /home/agent:size=512m
    )

    # Conditional --rm based on debug mode
    if [[ "${DEBUG_MODE:-false}" == "true" ]]; then
        echo "DEBUG: Container '$container_name' will be kept for inspection."
    else
        _DOCKER_ARGS+=(--rm)
    fi

    # Project root mount
    _DOCKER_ARGS+=(-v "${PWD}:/workspace:${project_mode}")
}

# _execute_container <timeout_seconds> <role_label>
#   Runs the docker command in _DOCKER_ARGS with timeout and audit logging.
#   Supports DRY_RUN mode: prints command instead of executing.
_execute_container() {
    local timeout_seconds="$1"
    local role_label="$2"

    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        echo "[dry-run] ${role_label}:"
        printf '  %s\n' "${_DOCKER_ARGS[@]}"
        return 0
    fi

    local exit_code=0
    if [[ -n "${TIMEOUT_CMD:-}" ]]; then
        $TIMEOUT_CMD "$timeout_seconds" "${_DOCKER_ARGS[@]}" || exit_code=$?
    else
        "${_DOCKER_ARGS[@]}" || exit_code=$?
    fi

    if [[ $exit_code -eq 124 ]]; then
        audit_log "$role_label" "TIMEOUT" "exceeded ${timeout_seconds}s"
        echo "WARNING: Agent '$role_label' timed out after ${timeout_seconds}s."
    fi

    audit_log "$role_label" "CONTAINER_END" "exit_code=$exit_code"
    return $exit_code
}

# ==============================================================================
# Runners
# ==============================================================================

run_agent() {
    local role="$1"
    local prompt="$2"
    local image="dark-factory-${LANGUAGE:-node}"
    local -a masks=()
    local -a rw_overrides=()
    local project_mode="ro"
    local cache_prefix="df-${role}"
    local use_cache=false
    local container_name="df-${SPEC_NAME}-${role}-$$"

    # Role-specific mount configuration
    case "$role" in
        spec-validator)
            rw_overrides+=(".dark-factory/reviews")
            ;;
        scenario-architect)
            for dir in ${SOURCE_DIRS:-${SOURCE_DIR:-src}}; do
                [[ -d "$dir" ]] && masks+=("$dir")
            done
            masks+=(".git")
            rw_overrides+=(".dark-factory/scenarios/${SPEC_NAME}")
            ;;
        implementer)
            project_mode="rw"
            masks+=(".dark-factory/scenarios" ".dark-factory/reports" ".dark-factory/audit" ".dark-factory/state" ".git")
            use_cache=true
            ;;
        quality-gate)
            project_mode="rw"
            use_cache=true
            ;;
        reviewer)
            masks+=(".dark-factory/scenarios" ".git")
            rw_overrides+=(".dark-factory/reviews")
            ;;
        evaluator)
            for dir in ${SOURCE_DIRS:-${SOURCE_DIR:-src}}; do
                [[ -d "$dir" ]] && masks+=("$dir")
            done
            masks+=(".git")
            rw_overrides+=(".dark-factory/reports")
            ;;
        oracle|escalation)
            rw_overrides+=(".dark-factory/reviews")
            ;;
    esac

    # Pre-flight symlink safety check for barrier agents
    if [[ ${#masks[@]} -gt 0 ]]; then
        for mask in "${masks[@]}"; do
            [[ "$mask" == ".git" ]] && continue
            if ! check_symlink_safety "." "$mask" 1; then exit 1; fi
            if [[ -d ".dark-factory" ]]; then
                if ! check_symlink_safety ".dark-factory" "$mask"; then exit 1; fi
            fi
            for sdir in ${SOURCE_DIRS:-${SOURCE_DIR:-src}}; do
                if [[ -d "$sdir" ]]; then
                    if ! check_symlink_safety "$sdir" "$mask"; then exit 1; fi
                fi
            done
        done
    fi

    # Build base Docker args
    _build_docker_base_args "$container_name" "$project_mode"

    # Network selection (egress filtering or legacy flat network)
    if [[ "${EGRESS_FILTERING_ENABLED:-true}" == "true" ]]; then
        case "$role" in
            implementer)
                _DOCKER_ARGS+=(--network df-execution)
                _DOCKER_ARGS+=(-e HTTPS_PROXY=http://df-execution-proxy:8080)
                _DOCKER_ARGS+=(-e HTTP_PROXY=http://df-execution-proxy:8080)
                ;;
            evaluator)
                # Evaluator uses a bridge (not --internal) network to reach the deployed app
                _DOCKER_ARGS+=(--network df-evaluation)
                ;;
            *)
                _DOCKER_ARGS+=(--network df-analysis)
                _DOCKER_ARGS+=(-e HTTPS_PROXY=http://df-analysis-proxy:8080)
                _DOCKER_ARGS+=(-e HTTP_PROXY=http://df-analysis-proxy:8080)
                ;;
        esac
        _DOCKER_ARGS+=(-e NO_PROXY=localhost,127.0.0.1,df-analysis-proxy,df-execution-proxy)
    else
        _DOCKER_ARGS+=(--network "${DOCKER_NETWORK:-df-pipeline}")
    fi

    # Inject auth credential
    if [[ "${DF_AUTH_TYPE:-api_key}" == "oauth" ]]; then
        _DOCKER_ARGS+=(-e CLAUDE_CODE_OAUTH_TOKEN)
    else
        _DOCKER_ARGS+=(-e ANTHROPIC_API_KEY)
    fi

    # Apply tmpfs masks (HARD barrier enforcement)
    if [[ ${#masks[@]} -gt 0 ]]; then
        for mask in "${masks[@]}"; do
            _DOCKER_ARGS+=(--tmpfs "/workspace/${mask}:ro,size=1k")
        done
    fi

    # Apply RW overrides (output directories, explicitly :rw)
    if [[ ${#rw_overrides[@]} -gt 0 ]]; then
        for override in "${rw_overrides[@]}"; do
            mkdir -p "${PWD}/${override}"
            _DOCKER_ARGS+=(-v "${PWD}/${override}:/workspace/${override}:rw")
        done
    fi

    # Apply per-role cache volumes (prevents cache poisoning)
    if [[ "$use_cache" == "true" ]]; then
        case "${LANGUAGE:-node}" in
            node)   _DOCKER_ARGS+=(-v "${cache_prefix}-npm:/home/agent/.npm") ;;
            python) _DOCKER_ARGS+=(-v "${cache_prefix}-pip:/home/agent/.cache/pip") ;;
            go)     _DOCKER_ARGS+=(-v "${cache_prefix}-go:/home/agent/go/pkg/mod") ;;
            rust)   _DOCKER_ARGS+=(-v "${cache_prefix}-cargo:/home/agent/.cargo/registry") ;;
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
        evaluator)
            claude_args+=(--disallowedTools "$EVALUATOR_DENY")
            claude_args+=(--append-system-prompt "$EVALUATOR_BARRIER")
            ;;
        reviewer)
            claude_args+=(--disallowedTools "$REVIEWER_DENY")
            claude_args+=(--append-system-prompt "$REVIEWER_BARRIER")
            ;;
        spec-validator)
            claude_args+=(--disallowedTools "$SPEC_VALIDATOR_DENY")
            claude_args+=(--append-system-prompt "$SPEC_VALIDATOR_BARRIER")
            ;;
        oracle)
            claude_args+=(--disallowedTools "$ORACLE_DENY")
            ;;
        escalation)
            claude_args+=(--disallowedTools "$ESCALATION_DENY")
            ;;
    esac

    # Audit: log the container invocation
    local masks_str=""
    [[ ${#masks[@]} -gt 0 ]] && masks_str="${masks[*]}"
    audit_log "$role" "CONTAINER_START" "container=$container_name image=$image masks=[${masks_str}]"

    # Image + claude args + prompt
    _DOCKER_ARGS+=("$image" "${claude_args[@]}" "$prompt")

    # Execute with timeout
    _execute_container "${AGENT_TIMEOUT:-600}" "$role"
}

# invoke_agent <role> <prompt> [min_output]
#   Captures exit code + output from run_agent into globals.
#   Performs minimum output length validation and audit logging.
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
    if [[ ${#AGENT_OUTPUT} -lt $min_output ]]; then
        audit_log "$role" "AGENT_LOW_OUTPUT" "length=${#AGENT_OUTPUT} threshold=$min_output"
        if [[ $AGENT_EXIT -eq 0 ]]; then
            AGENT_EXIT=99
            audit_log "$role" "AGENT_FAILURE" "exit_code=99(synthetic) reason=output_below_threshold"
        fi
    fi
}

# run_quality_gate <spec_name>
run_quality_gate() {
    local spec_name="$1"
    local container_name="df-${spec_name}-quality-gate-$$"
    local qg_script_path="${DARK_FACTORY_HOME:-dark-factory}/scripts/quality-gate.sh"

    # Build base Docker args (rw for quality gate)
    _build_docker_base_args "$container_name" "rw"

    # Network selection for quality gate (execution tier — needs package registries)
    if [[ "${EGRESS_FILTERING_ENABLED:-true}" == "true" ]]; then
        _DOCKER_ARGS+=(--network df-execution)
        _DOCKER_ARGS+=(-e HTTPS_PROXY=http://df-execution-proxy:8080)
        _DOCKER_ARGS+=(-e HTTP_PROXY=http://df-execution-proxy:8080)
        _DOCKER_ARGS+=(-e NO_PROXY=localhost,127.0.0.1,df-analysis-proxy,df-execution-proxy)
    else
        _DOCKER_ARGS+=(--network "${DOCKER_NETWORK:-df-pipeline}")
    fi

    # Per-role cache volume (separate from Implementer's cache)
    case "${LANGUAGE:-node}" in
        node)   _DOCKER_ARGS+=(-v "df-qg-npm:/home/agent/.npm") ;;
        python) _DOCKER_ARGS+=(-v "df-qg-pip:/home/agent/.cache/pip") ;;
        go)     _DOCKER_ARGS+=(-v "df-qg-go:/home/agent/go/pkg/mod") ;;
        rust)   _DOCKER_ARGS+=(-v "df-qg-cargo:/home/agent/.cargo/registry") ;;
    esac

    # Override entrypoint to run bash, not claude
    _DOCKER_ARGS+=(--entrypoint bash --workdir /workspace)
    _DOCKER_ARGS+=("dark-factory-${LANGUAGE:-node}")
    _DOCKER_ARGS+=("/workspace/${qg_script_path}" "$spec_name" ${TEST_CMD:+--test-cmd "$TEST_CMD"})

    audit_log "quality-gate" "CONTAINER_START" "container=$container_name"

    # Execute with timeout
    _execute_container "${GATE_TIMEOUT:-300}" "quality-gate"
}
