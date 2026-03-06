#!/usr/bin/env bash
# scripts/lib/docker.sh — Docker isolation, container lifecycle, agent runners
#
# Provides:
#   Lifecycle: require_docker, setup_networks, setup_proxies, teardown_proxies, cleanup_containers, cleanup_networks
#   Namespacing: _df_net, _df_proxy
#   Safety:    check_symlink_safety
#   Builders:  _build_docker_base_args, _execute_container
#   Runners:   run_agent, invoke_agent, run_quality_gate
#
# Requires globals (set before first use):
#   RUN_ID (required for network/proxy namespacing)
#   LANGUAGE, SPEC_NAME, DEBUG_MODE, TIMEOUT_CMD, DRY_RUN (optional)
#   DARK_FACTORY_HOME, AGENTS_DIR, AGENT_MEMORY, AGENT_CPUS, AGENT_MAX_TURNS
#   AGENT_TIMEOUT, GATE_TIMEOUT, DOCKER_NETWORK, EGRESS_FILTERING_ENABLED
#   _DF_SOURCE_DIRS (array, set by _df_parse_source_dirs), SOURCE_DIR, TEST_CMD
#   DF_AUTH_TYPE (set during pre-flight)
#   All barrier globals from barriers.sh (IMPLEMENTER_DENY, etc.)
#   AUDIT_LOG (for audit_log calls — source audit.sh first)

[[ -n "${_DF_LIB_DOCKER_LOADED:-}" ]] && return 0
_DF_LIB_DOCKER_LOADED=1

# ==============================================================================
# Namespacing helpers — isolate networks/proxies per run
# ==============================================================================

_df_net()   { echo "df-${RUN_ID:?RUN_ID not set}-${1}"; }
_df_proxy() { echo "df-${RUN_ID:?RUN_ID not set}-${1}-proxy"; }

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
        docker network inspect "$(_df_net analysis)"  &>/dev/null 2>&1 || docker network create --internal "$(_df_net analysis)"
        docker network inspect "$(_df_net execution)" &>/dev/null 2>&1 || docker network create --internal "$(_df_net execution)"
        docker network inspect "$(_df_net external)"  &>/dev/null 2>&1 || docker network create "$(_df_net external)"
        docker network inspect "$(_df_net evaluation)" &>/dev/null 2>&1 || docker network create --internal "$(_df_net evaluation)"
    else
        docker network inspect "${DOCKER_NETWORK:-df-pipeline}" &>/dev/null 2>&1 || \
            docker network create --driver bridge "${DOCKER_NETWORK:-df-pipeline}"
    fi
}

cleanup_networks() {
    for _net in analysis execution external evaluation; do
        docker network rm "$(_df_net "$_net")" 2>/dev/null || true
    done
}

cleanup_containers() {
    local _escaped_name="${SPEC_NAME//./\\.}"
    local _containers
    _containers=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E "^df-${_escaped_name:-unknown}-" || true)
    if [[ -n "$_containers" ]]; then
        echo "$_containers" | xargs -r docker rm -f 2>/dev/null || true
    fi
}

setup_proxies() {
    [[ "${EGRESS_FILTERING_ENABLED:-true}" != "true" ]] && return
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        echo "[dry-run] Would start proxy containers (analysis, execution)"
        if [[ -n "${DEPLOY_CMD:-}" ]] && [[ -n "${DEPLOY_BASE_URL:-}" ]]; then
            echo "[dry-run] Would start evaluator proxy for ${DEPLOY_BASE_URL}"
        fi
        return
    fi

    for tier in analysis execution; do
        local name="$(_df_proxy "$tier")"
        if ! docker ps --filter "name=${name}" --filter status=running -q | grep -q .; then
            docker run -d --rm --name "$name" \
                --network "$(_df_net "$tier")" \
                -e TINYPROXY_CONF="/etc/tinyproxy/tinyproxy-${tier}.conf" \
                dark-factory-proxy
            docker network connect "$(_df_net external)" "$name"
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

    # Evaluator proxy — only when both DEPLOY_CMD and DEPLOY_BASE_URL are configured
    if [[ -n "${DEPLOY_CMD:-}" ]] && [[ -n "${DEPLOY_BASE_URL:-}" ]]; then
        local deploy_host deploy_port deploy_scheme
        deploy_scheme="${DEPLOY_BASE_URL%%://*}"
        local _url_remainder="${DEPLOY_BASE_URL#*://}"
        deploy_host="${_url_remainder%%[:/]*}"
        deploy_port=""
        if [[ "$_url_remainder" == *":"* ]]; then
            deploy_port="${_url_remainder#*:}"
            deploy_port="${deploy_port%%[/]*}"
        fi
        # Default ports by scheme
        if [[ -z "$deploy_port" ]]; then
            if [[ "$deploy_scheme" == "https" ]]; then
                deploy_port=443
            else
                deploy_port=80
            fi
        fi

        # Map localhost → host.docker.internal for Docker access
        local docker_deploy_host="$deploy_host"
        if [[ "$deploy_host" == "localhost" ]] || [[ "$deploy_host" == "127.0.0.1" ]]; then
            docker_deploy_host="host.docker.internal"
        fi

        # Escape hostname for regex filter
        local escaped_host="${docker_deploy_host//./\\.}"

        # Generate dynamic filter file
        _DF_EVAL_FILTER_TMP="$(mktemp)"
        cat > "$_DF_EVAL_FILTER_TMP" <<FILTEREOF
^api\.anthropic\.com$
^${escaped_host}$
FILTEREOF

        # Generate dynamic tinyproxy conf
        _DF_EVAL_CONF_TMP="$(mktemp)"
        local connect_ports="ConnectPort 443"
        if [[ "$deploy_port" -ne 443 ]] && [[ "$deploy_port" -ne 80 ]]; then
            connect_ports="${connect_ports}
ConnectPort ${deploy_port}"
        fi
        cat > "$_DF_EVAL_CONF_TMP" <<CONFEOF
Port 8080
Timeout 60
MaxClients 20
Allow 0.0.0.0/0
FilterDefaultDeny Yes
FilterExtended On
Filter "/etc/tinyproxy/eval-filter"
${connect_ports}
CONFEOF

        local name="$(_df_proxy evaluation)"
        if ! docker ps --filter "name=${name}" --filter status=running -q | grep -q .; then
            docker run -d --rm --name "$name" \
                --network "$(_df_net evaluation)" \
                -v "${_DF_EVAL_FILTER_TMP}:/etc/tinyproxy/eval-filter:ro" \
                -v "${_DF_EVAL_CONF_TMP}:/etc/tinyproxy/tinyproxy.conf:ro" \
                --entrypoint tinyproxy \
                dark-factory-proxy -d -c /etc/tinyproxy/tinyproxy.conf
            docker network connect "$(_df_net external)" "$name"
        fi

        # Readiness probe
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
    fi
}

teardown_proxies() {
    docker stop "$(_df_proxy analysis)"   2>/dev/null || true
    docker stop "$(_df_proxy execution)"  2>/dev/null || true
    docker stop "$(_df_proxy evaluation)" 2>/dev/null || true
    # Clean up evaluator proxy temp files
    [[ -n "${_DF_EVAL_FILTER_TMP:-}" ]] && rm -f "$_DF_EVAL_FILTER_TMP"
    [[ -n "${_DF_EVAL_CONF_TMP:-}" ]]   && rm -f "$_DF_EVAL_CONF_TMP"
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

# check_hardlink_safety <scan_dir> <forbidden_dir>
#   Detects hardlinks that cross information barriers.
#   Scans scan_dir for multi-linked files, checks if any share inodes with forbidden_dir.
check_hardlink_safety() {
    local scan_dir="$1" forbidden_dir="$2"
    [[ ! -d "$scan_dir" ]] && return 0
    [[ ! -d "$forbidden_dir" ]] && return 0

    # Collect inodes from forbidden_dir
    local -A forbidden_inodes=()
    local _inode
    while IFS= read -r _inode; do
        forbidden_inodes["$_inode"]=1
    done < <(_df_stat_inode_dir "$forbidden_dir")

    [[ ${#forbidden_inodes[@]} -eq 0 ]] && return 0

    # Scan for multi-linked files in scan_dir
    local _file _scan_inode
    while IFS= read -r _file; do
        _scan_inode=$(_df_stat_inode "$_file") || continue
        if [[ -n "${forbidden_inodes[$_scan_inode]+x}" ]]; then
            echo "ERROR: Hardlink '$_file' shares inode $_scan_inode with file in restricted area '$forbidden_dir'"
            echo "This would bypass the information barrier. Remove or replace the hardlink."
            return 1
        fi
    done < <(find "$scan_dir" -type f -links +1 2>/dev/null)

    return 0
}

# _df_stat_inode <file> — print inode number (Linux first, macOS fallback)
_df_stat_inode() {
    stat -c '%i' "$1" 2>/dev/null || stat -f '%i' "$1" 2>/dev/null
}

# _df_stat_inode_dir <dir> — print inode numbers for all regular files in dir
_df_stat_inode_dir() {
    local _f
    while IFS= read -r _f; do
        _df_stat_inode "$_f"
    done < <(find "$1" -type f 2>/dev/null)
}

# ==============================================================================
# Builders
# ==============================================================================

# Global array set by _build_docker_base_args, consumed by callers and tests.
# NOTE: _DOCKER_ARGS is not concurrency-safe. Each dark-factory.sh process
# runs in its own shell, so concurrent plan specs don't share this array.
_DOCKER_ARGS=()

# _build_docker_base_args <container_name> <project_mode>
#   Sets _DOCKER_ARGS with the common Docker plumbing shared by run_agent and run_quality_gate:
#   security opts, resource limits, user mapping, home/tmp tmpfs, debug/rm.
_build_docker_base_args() {
    local container_name="$1"
    local project_mode="$2"
    local docker_project="${_DF_DOCKER_PROJECT_DIR:-$PWD}"

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

    # Disk I/O limits (opt-in, Linux-only — silently ignored on macOS Docker Desktop)
    if [[ -n "${AGENT_IO_DEVICE:-}" ]]; then
        [[ -n "${AGENT_IO_READ_BPS:-}" ]] && \
            _DOCKER_ARGS+=(--device-read-bps "${AGENT_IO_DEVICE}:${AGENT_IO_READ_BPS}")
        [[ -n "${AGENT_IO_WRITE_BPS:-}" ]] && \
            _DOCKER_ARGS+=(--device-write-bps "${AGENT_IO_DEVICE}:${AGENT_IO_WRITE_BPS}")
    fi

    # Project root mount (uses worktree path when running in worktree mode)
    _DOCKER_ARGS+=(-v "${docker_project}:/workspace:${project_mode}")
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
            for dir in "${_DF_SOURCE_DIRS[@]}"; do
                [[ -d "$dir" ]] && masks+=("$dir")
            done
            masks+=(".git")
            rw_overrides+=(".dark-factory/scenarios/${SPEC_NAME}")
            ;;
        implementer)
            masks+=(".dark-factory/scenarios" ".dark-factory/reports" ".dark-factory/audit" ".dark-factory/state" ".git")
            # Implementer write scope — configurable, defaults to full project (backward-compatible)
            local _impl_write="${IMPLEMENTER_WRITE_DIRS:-}"
            if [[ "$_impl_write" == "." ]] || [[ -z "$_impl_write" ]]; then
                # Full project write access (default — backward compatible)
                project_mode="rw"
            else
                # Scoped write: read-only base + explicit rw overrides
                project_mode="ro"
                IFS=':' read -ra _impl_dirs <<< "$_impl_write"
                for dir in "${_impl_dirs[@]}"; do
                    rw_overrides+=("$dir")
                done
                rw_overrides+=(".dark-factory/reviews")
            fi
            use_cache=true
            ;;
        quality-gate)
            project_mode="rw"
            use_cache=true
            ;;
        reviewer)
            masks+=(".dark-factory/scenarios" ".dark-factory/reports" ".git")
            rw_overrides+=(".dark-factory/reviews")
            ;;
        evaluator)
            for dir in "${_DF_SOURCE_DIRS[@]}"; do
                [[ -d "$dir" ]] && masks+=("$dir")
            done
            masks+=(".git")
            rw_overrides+=(".dark-factory/reports")
            ;;
        oracle|escalation)
            rw_overrides+=(".dark-factory/reviews")
            ;;
    esac

    # Pre-flight symlink + hardlink safety check for barrier agents
    if [[ ${#masks[@]} -gt 0 ]]; then
        for mask in "${masks[@]}"; do
            [[ "$mask" == ".git" ]] && continue
            if ! check_symlink_safety "." "$mask" 1; then exit 1; fi
            if [[ -d ".dark-factory" ]]; then
                if ! check_symlink_safety ".dark-factory" "$mask"; then exit 1; fi
            fi
            for sdir in "${_DF_SOURCE_DIRS[@]}"; do
                if [[ -d "$sdir" ]]; then
                    if ! check_symlink_safety "$sdir" "$mask"; then exit 1; fi
                fi
            done
        done

        # Hardlink safety: check that multi-linked files don't cross barriers
        case "$role" in
            implementer)
                for sdir in "${_DF_SOURCE_DIRS[@]}"; do
                    if [[ -d "$sdir" ]]; then
                        if ! check_hardlink_safety "$sdir" ".dark-factory/scenarios"; then exit 1; fi
                        if ! check_hardlink_safety "$sdir" ".dark-factory/reports"; then exit 1; fi
                    fi
                done
                ;;
            scenario-architect|evaluator)
                for sdir in "${_DF_SOURCE_DIRS[@]}"; do
                    if [[ -d ".dark-factory/scenarios" ]]; then
                        if ! check_hardlink_safety ".dark-factory/scenarios" "$sdir"; then exit 1; fi
                    fi
                done
                ;;
        esac
    fi

    # Build base Docker args
    _build_docker_base_args "$container_name" "$project_mode"

    # Network selection (egress filtering or legacy flat network)
    if [[ "${EGRESS_FILTERING_ENABLED:-true}" == "true" ]]; then
        local _analysis_proxy _execution_proxy _evaluation_proxy
        _analysis_proxy="$(_df_proxy analysis)"
        _execution_proxy="$(_df_proxy execution)"
        _evaluation_proxy="$(_df_proxy evaluation)"

        case "$role" in
            implementer)
                _DOCKER_ARGS+=(--network "$(_df_net execution)")
                _DOCKER_ARGS+=(-e "HTTPS_PROXY=http://${_execution_proxy}:8080")
                _DOCKER_ARGS+=(-e "HTTP_PROXY=http://${_execution_proxy}:8080")
                ;;
            evaluator)
                _DOCKER_ARGS+=(--network "$(_df_net evaluation)")
                if [[ -n "${DEPLOY_CMD:-}" ]] && [[ -n "${DEPLOY_BASE_URL:-}" ]]; then
                    _DOCKER_ARGS+=(-e "HTTPS_PROXY=http://${_evaluation_proxy}:8080")
                    _DOCKER_ARGS+=(-e "HTTP_PROXY=http://${_evaluation_proxy}:8080")
                    # Linux Docker Engine needs explicit host.docker.internal mapping
                    local _eval_host="${DEPLOY_BASE_URL#*://}"
                    _eval_host="${_eval_host%%[:/]*}"
                    if [[ "$_eval_host" == "localhost" ]] || [[ "$_eval_host" == "127.0.0.1" ]]; then
                        _DOCKER_ARGS+=(--add-host=host.docker.internal:host-gateway)
                    fi
                fi
                ;;
            *)
                _DOCKER_ARGS+=(--network "$(_df_net analysis)")
                _DOCKER_ARGS+=(-e "HTTPS_PROXY=http://${_analysis_proxy}:8080")
                _DOCKER_ARGS+=(-e "HTTP_PROXY=http://${_analysis_proxy}:8080")
                ;;
        esac
        _DOCKER_ARGS+=(-e "NO_PROXY=localhost,127.0.0.1,${_analysis_proxy},${_execution_proxy},${_evaluation_proxy}")
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
        local docker_project="${_DF_DOCKER_PROJECT_DIR:-$PWD}"
        for override in "${rw_overrides[@]}"; do
            mkdir -p "${docker_project}/${override}"
            _DOCKER_ARGS+=(-v "${docker_project}/${override}:/workspace/${override}:rw")
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

# Count files changed by implementer relative to spec baseline commit.
# Called from host context after container exits. Scoped to SOURCE_DIRS.
# The count is a cumulative delta from spec start (not per-invocation),
# because git diff always compares against the fixed baseline commit.
# Overwrites _DF_SPEC_FILES_CHANGED each call — this is intentional.
_df_count_implementer_files() {
    local role="$1"
    [[ "$role" == "implementer" && -n "${_DF_SPEC_BASELINE_COMMIT:-}" ]] || return 0
    # No source dirs configured — nothing meaningful to count
    [[ -z "${SOURCE_DIRS:-}" ]] && return 0
    local _files_changed=0
    # Tracked files changed since baseline (scoped to SOURCE_DIRS)
    _files_changed=$(git diff --name-only "$_DF_SPEC_BASELINE_COMMIT" -- ${SOURCE_DIRS:-} 2>/dev/null | wc -l | tr -d ' ') || _files_changed=0
    # Untracked files in SOURCE_DIRS only (avoid walking entire tree)
    for _src_dir in ${SOURCE_DIRS:-}; do
        _files_changed=$(( _files_changed + $(git ls-files --others --exclude-standard "$_src_dir" 2>/dev/null | wc -l | tr -d ' ') )) || true
    done
    _DF_SPEC_FILES_CHANGED=$_files_changed
    export _DF_SPEC_FILES_CHANGED
    if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
        emit_files_changed "agent_role=implementer" "files_changed=$_files_changed" 2>/dev/null || true
    fi
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
    local _max_retries="${AGENT_RETRIES:-1}"
    local _attempt=0
    local _cumulative_wall_s=0

    while [[ $_attempt -le $_max_retries ]]; do
        AGENT_EXIT=0
        local _attempt_start=$SECONDS
        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_agent_start "agent_role=$role" "max_turns=${AGENT_MAX_TURNS:-200}" || true
        fi
        AGENT_OUTPUT=$(run_agent "$role" "$prompt" 2>&1) || AGENT_EXIT=$?
        local _attempt_wall_s=$(( SECONDS - _attempt_start ))
        _cumulative_wall_s=$(( _cumulative_wall_s + _attempt_wall_s ))

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

        if [[ $AGENT_EXIT -eq 0 ]]; then
            if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
                emit_agent_completed "agent_role=$role" "exit_code=$AGENT_EXIT" \
                    "wall_s=$_attempt_wall_s" "turns_allocated=${AGENT_MAX_TURNS:-200}" \
                    "model_id=${DF_MODEL_ID:-claude-sonnet-4-20250514}" \
                    "est_input_tokens=$(( ${AGENT_MAX_TURNS:-200} * ${COST_EST_INPUT_TOKENS_PER_TURN:-4000} ))" \
                    "est_output_tokens=$(( ${AGENT_MAX_TURNS:-200} * ${COST_EST_OUTPUT_TOKENS_PER_TURN:-2000} ))" \
                    || true
                # Per-agent anomaly check (runaway agent detection)
                anomaly_check_agent "$role" "${AGENT_MAX_TURNS:-200}" "$_cumulative_wall_s" 2>/dev/null || {
                    audit_log "$role" "ANOMALY_AGENT_BLOCKED" "turns=${AGENT_MAX_TURNS:-200} wall_s=$_cumulative_wall_s"
                }
            fi
            _df_count_implementer_files "$role"
            _DF_SPEC_TOTAL_TURNS=$(( ${_DF_SPEC_TOTAL_TURNS:-0} + ${AGENT_MAX_TURNS:-200} ))
            _DF_ITER_TURNS=$(( ${_DF_ITER_TURNS:-0} + ${AGENT_MAX_TURNS:-200} ))
            return 0
        fi

        if [[ -n "${_DF_EVENTS_FILE:-}" ]]; then
            emit_agent_failed "agent_role=$role" "exit_code=$AGENT_EXIT" \
                "wall_s=$_attempt_wall_s" "error=agent_exit_$AGENT_EXIT" \
                "model_id=${DF_MODEL_ID:-claude-sonnet-4-20250514}" \
                "est_input_tokens=$(( ${AGENT_MAX_TURNS:-200} * ${COST_EST_INPUT_TOKENS_PER_TURN:-4000} ))" \
                "est_output_tokens=$(( ${AGENT_MAX_TURNS:-200} * ${COST_EST_OUTPUT_TOKENS_PER_TURN:-2000} ))" \
                || true
            # Per-agent anomaly check on failure path too
            anomaly_check_agent "$role" "${AGENT_MAX_TURNS:-200}" "$_cumulative_wall_s" 2>/dev/null || {
                audit_log "$role" "ANOMALY_AGENT_BLOCKED" "turns=${AGENT_MAX_TURNS:-200} wall_s=$_cumulative_wall_s"
            }
        fi
        _df_count_implementer_files "$role"
        _DF_SPEC_TOTAL_TURNS=$(( ${_DF_SPEC_TOTAL_TURNS:-0} + ${AGENT_MAX_TURNS:-200} ))
        _DF_ITER_TURNS=$(( ${_DF_ITER_TURNS:-0} + ${AGENT_MAX_TURNS:-200} ))

        audit_log "$role" "AGENT_FAILURE" "exit=$AGENT_EXIT attempt=$((_attempt+1))"
        if [[ $_attempt -lt $_max_retries ]]; then
            audit_log "$role" "AGENT_RETRY" "attempt=$((_attempt+2))_of_$((_max_retries+1))"
            echo "WARNING: Agent '$role' failed (exit $AGENT_EXIT). Retrying ($((_attempt+2))/$((_max_retries+1)))..."
        fi
        _attempt=$((_attempt + 1))
    done
    # All retries exhausted — AGENT_EXIT and AGENT_OUTPUT reflect last attempt
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
        local _execution_proxy _analysis_proxy _evaluation_proxy
        _execution_proxy="$(_df_proxy execution)"
        _analysis_proxy="$(_df_proxy analysis)"
        _evaluation_proxy="$(_df_proxy evaluation)"
        _DOCKER_ARGS+=(--network "$(_df_net execution)")
        _DOCKER_ARGS+=(-e "HTTPS_PROXY=http://${_execution_proxy}:8080")
        _DOCKER_ARGS+=(-e "HTTP_PROXY=http://${_execution_proxy}:8080")
        _DOCKER_ARGS+=(-e "NO_PROXY=localhost,127.0.0.1,${_analysis_proxy},${_execution_proxy},${_evaluation_proxy}")
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
    _DOCKER_ARGS+=("/workspace/${qg_script_path}" "$spec_name")
    if [[ -n "${TEST_CMD:-}" ]]; then
        _DOCKER_ARGS+=(--test-cmd "$TEST_CMD")
    fi
    if [[ -n "${ITERATION:-}" ]]; then
        _DOCKER_ARGS+=(--iteration "$ITERATION")
    fi

    audit_log "quality-gate" "CONTAINER_START" "container=$container_name"

    # Execute with timeout
    _execute_container "${GATE_TIMEOUT:-300}" "quality-gate"
}
