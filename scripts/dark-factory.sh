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
#   --regen-scenarios     Clear and regenerate scenarios before implementation
#   --dry-run             Print Docker commands without executing
#   --regression          Run cross-spec regression testing before finalizing SHIP
#   --auto-pr             Create PR after SHIP (overrides config AUTO_PR)
#   --no-pr               Skip PR creation (overrides config AUTO_PR)
#   --base-branch NAME    Override GIT_BASE_BRANCH for PR target

MAX_ITERATIONS=3
TEST_CMD=""
RESUME_MODE=""
CI_MODE=false
SKIP_DEP_CHECK=false
DEBUG_MODE=false
REGEN_SCENARIOS=false
DRY_RUN=false
REGRESSION_FLAG=false
_CLI_AUTO_PR=""
_CLI_NO_PR=false
_CLI_BASE_BRANCH=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ==============================================================================
# Library bootstrap
# ==============================================================================

source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/state.sh"
source "${SCRIPT_DIR}/lib/audit.sh"
source "${SCRIPT_DIR}/lib/barriers.sh"
source "${SCRIPT_DIR}/lib/tui.sh"
source "${SCRIPT_DIR}/lib/docker.sh"
source "${SCRIPT_DIR}/lib/metrics.sh"
source "${SCRIPT_DIR}/lib/git-ops.sh"
source "${SCRIPT_DIR}/lib/notify.sh"

# ==============================================================================
# Unified cleanup (proxy + container + deploy teardown)
# ==============================================================================

cleanup_all() {
    teardown_proxies
    cleanup_containers
    cleanup_networks
    if [[ -n "${DEPLOY_TEARDOWN_CMD:-}" ]]; then
        bash -c "$DEPLOY_TEARDOWN_CMD" 2>/dev/null || true
    fi
}

# ==============================================================================
# Config validation and sourcing
# ==============================================================================

load_config ".dark-factory/config.sh" \
    "LANGUAGE SOURCE_DIR TEST_DIR DARK_FACTORY_HOME REGRESSION_POLICY HARD_GATE_BUILD AGENT_IO_DEVICE AGENT_IO_READ_BPS AGENT_IO_WRITE_BPS" \
    "AGENT_TIMEOUT GATE_TIMEOUT AGENT_MAX_TURNS"

# Validate non-negative integer configs (0 is valid — disables the feature)
validate_non_negative_integers AGENT_RETRIES MIN_SHIP_PASS_RATE MAX_TOTAL_AGENT_TURNS || exit 1

# Resolve agent path: use DARK_FACTORY_HOME from config, or detect from script location
AGENTS_DIR="${DARK_FACTORY_HOME:-dark-factory}/agents"
if [[ ! -d "$AGENTS_DIR" ]]; then
    AGENTS_DIR="${SCRIPT_DIR}/../agents"
fi

# Timeout detection (for host-side operations)
detect_timeout_cmd

# ==============================================================================
# Dependency checking (orchestrator-specific, used once)
# ==============================================================================

check_dependencies() {
    local spec_file="$1"
    local dep_line deps dep state_file dep_status

    local current_id
    current_id=$(grep -A5 'dark-factory-metadata' "$spec_file" 2>/dev/null | grep 'feature-id:' | head -1 | sed 's/.*feature-id:[[:space:]]*//' | tr -d '[:space:]') || current_id=""

    dep_line=$(grep -A5 'dark-factory-metadata' "$spec_file" 2>/dev/null | grep 'depends-on:' | head -1) || dep_line=""
    if [[ -z "$dep_line" ]]; then
        return 0
    fi

    deps=$(echo "$dep_line" | sed 's/.*depends-on:\s*\[//;s/\].*//' | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    if [[ -z "$deps" ]]; then
        return 0
    fi

    local failed=false
    while IFS= read -r dep; do
        [[ -z "$dep" ]] && continue
        dep=$(echo "$dep" | tr -d '[:space:]')

        local found=false
        for sf in .dark-factory/specs/*.md; do
            [[ ! -f "$sf" ]] && continue
            if grep -q "feature-id:.*${dep}" "$sf" 2>/dev/null; then
                local dep_spec_name
                dep_spec_name="$(basename "$sf" .md)"

                if [[ -n "$current_id" ]] && \
                   grep -A5 'dark-factory-metadata' "$sf" 2>/dev/null | grep 'depends-on:' | grep -q "${current_id}"; then
                    echo "ERROR: Circular dependency detected: ${current_id} and ${dep} depend on each other."
                    failed=true
                    found=true
                    break
                fi

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
            [[ $# -lt 2 ]] && { echo "ERROR: --max-iterations requires a value"; exit 1; }
            MAX_ITERATIONS="$2"
            shift 2
            ;;
        --test-cmd)
            [[ $# -lt 2 ]] && { echo "ERROR: --test-cmd requires a value"; exit 1; }
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
        --regen-scenarios)
            REGEN_SCENARIOS=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --regression)
            REGRESSION_FLAG=true
            shift
            ;;
        --auto-pr)
            _CLI_AUTO_PR=true
            shift
            ;;
        --no-pr)
            _CLI_NO_PR=true
            shift
            ;;
        --base-branch)
            [[ $# -lt 2 ]] && { echo "ERROR: --base-branch requires a value"; exit 1; }
            _CLI_BASE_BRANCH="$2"
            shift 2
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

# Input validation
validate_positive_integers MAX_ITERATIONS || exit 1

validate_spec_name "$SPEC_NAME" || exit 1

# ==============================================================================
# Path setup
# ==============================================================================

SPEC_FILE=".dark-factory/specs/${SPEC_NAME}.md"
SCENARIOS_DIR=".dark-factory/scenarios/${SPEC_NAME}"
REVIEW_FILE=".dark-factory/reviews/${SPEC_NAME}-review.md"
REPORT_FILE=".dark-factory/reports/${SPEC_NAME}-quality-gate.md"

# Audit setup
AUDIT_DIR=".dark-factory/audit"
mkdir -p "$AUDIT_DIR"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
AUDIT_LOG="${AUDIT_DIR}/${SPEC_NAME}-${RUN_ID}.log"

# State file setup
STATE_FILE=".dark-factory/state/${SPEC_NAME}.state"
mkdir -p ".dark-factory/state"

# ==============================================================================
# Information barrier configuration
# ==============================================================================

_df_parse_source_dirs || exit 1
configure_barriers

# ==============================================================================
# Docker setup
# ==============================================================================

require_docker

if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    DF_AUTH_TYPE="oauth"
    if [[ -n "${DF_TOKEN_EXPIRES:-}" ]]; then
        _today=$(date +%Y-%m-%d)
        _warn=$(date -j -v+30d +%Y-%m-%d 2>/dev/null || date -d "+30 days" +%Y-%m-%d 2>/dev/null)
        if [[ "${DF_TOKEN_EXPIRES}" < "${_today}" ]] || [[ "${DF_TOKEN_EXPIRES}" == "${_today}" ]]; then
            echo "ERROR: CLAUDE_CODE_OAUTH_TOKEN expired on ${DF_TOKEN_EXPIRES}."
            echo "Run 'claude setup-token' to generate a new token, then update DF_TOKEN_EXPIRES in .dark-factory/config.sh."
            exit 1
        elif [[ "${DF_TOKEN_EXPIRES}" < "${_warn}" ]] || [[ "${DF_TOKEN_EXPIRES}" == "${_warn}" ]]; then
            _days=$(( ( $(date -j -f "%Y-%m-%d" "${DF_TOKEN_EXPIRES}" +%s 2>/dev/null || date -d "${DF_TOKEN_EXPIRES}" +%s) - $(date +%s) ) / 86400 ))
            echo "WARNING: CLAUDE_CODE_OAUTH_TOKEN expires in ~${_days} days (${DF_TOKEN_EXPIRES})."
            echo "         Run 'claude setup-token' soon and update DF_TOKEN_EXPIRES in .dark-factory/config.sh."
        fi
        unset _today _warn _days
    fi
elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    DF_AUTH_TYPE="api_key"
else
    echo "ERROR: No authentication credentials found."
    echo "Set one of:"
    echo "  export CLAUDE_CODE_OAUTH_TOKEN=<token>  (Claude Pro/Max subscription — recommended)"
    echo "  export ANTHROPIC_API_KEY=<key>           (Anthropic API key)"
    echo ""
    echo "For OAuth tokens: run 'claude setup-token' to generate a long-lived token."
    exit 1
fi

if [[ "${EGRESS_FILTERING_ENABLED:-true}" != "true" ]]; then
    echo "WARNING: Egress filtering is disabled (EGRESS_FILTERING_ENABLED=false)."
    echo "         Agent containers have unrestricted internet access."
fi

# Resolve auto-PR setting: CLI flags override config
_auto_pr="${AUTO_PR:-false}"
[[ "$_CLI_AUTO_PR" == "true" ]] && _auto_pr=true
[[ "$_CLI_NO_PR" == "true" ]] && _auto_pr=false  # --no-pr always wins

# Git operations preflight (only if auto-PR is enabled)
if [[ "$_auto_pr" == "true" ]] && [[ "$DRY_RUN" != "true" ]]; then
    gitops_preflight || {
        echo "ERROR: Git operations preflight failed. Fix issues or use --no-pr."
        exit 1
    }
fi

setup_networks

# Crash recovery trap — installed BEFORE proxy setup so partial proxy failures
# are cleaned up, and BEFORE resume detection which may exit.
_DF_FINAL_STATUS=""
trap 'ec=$?;
  if [[ $ec -ne 0 ]] && [[ -n "${SPEC_NAME:-}" ]]; then
    case "${_DF_FINAL_STATUS:-}" in shipped|escalated|regression-failed|human-declined) ;;
      *) notify_failed "${SPEC_NAME}" "${RUN_ID:-unknown}" "pipeline crashed (exit $ec)" 2>/dev/null || true ;;
    esac;
    if [[ -z "${_DF_FINAL_STATUS:-}" ]]; then
      write_state "status" "interrupted" 2>/dev/null || true;
    fi;
  fi;
  cleanup_all || true' EXIT

setup_proxies

# ==============================================================================
# Resume detection
# ==============================================================================

RESUME=false
if [[ -f "$STATE_FILE" ]] && [[ "$RESUME_MODE" != "fresh" ]]; then
    if [[ "$RESUME_MODE" == "resume" ]] || [[ "$CI_MODE" == "true" ]]; then
        RESUME=true
    elif [[ -t 0 ]]; then
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

# Spec hash check: detect if the spec changed since the last run
if [[ "$RESUME" == "true" ]] && [[ -f "$SPEC_FILE" ]]; then
    STORED_HASH=$(read_state "spec_hash" "")
    if [[ -n "$STORED_HASH" ]]; then
        CURRENT_HASH=$(portable_sha256 "$SPEC_FILE") || CURRENT_HASH=""
        if [[ -n "$CURRENT_HASH" ]] && [[ "$STORED_HASH" != "$CURRENT_HASH" ]]; then
            echo "ERROR: Spec file has changed since the previous run."
            echo "  Stored hash: ${STORED_HASH:0:16}..."
            echo "  Current hash: ${CURRENT_HASH:0:16}..."
            echo "Use --fresh to start a new run with the updated spec."
            exit 1
        fi
    fi
fi

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
if [[ "$DRY_RUN" == "true" ]]; then
    echo "  Dry Run: ON (Docker commands printed, not executed)"
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

write_state "spec" "$SPEC_NAME" || exit 1
write_state "status" "running" || exit 1
write_state_or_warn "run_id" "$RUN_ID"
write_state_or_warn "spec_hash" "$(portable_sha256 "$SPEC_FILE" 2>/dev/null || echo "")"

# Metrics initialization
metrics_record_start "$SPEC_NAME" "$RUN_ID" "$MAX_ITERATIONS" "${AUTONOMY_LEVEL:-3}"
_DF_AGENT_FAILURES=0
_DF_PASS_RATES=""
_DF_SATISFACTION_SCORES=""

# Validate Phase 3 satisfaction config
_df_validate_satisfaction_config || exit 1

# Dependency checking
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
    _df_check_turn_budget "spec-validator"
    invoke_agent "spec-validator" \
        "You are the Spec Validator. Read the agent definition at ${AGENTS_DIR}/spec-validator.md, then validate the specification at ${SPEC_FILE}. Produce the validation report including the Precision Analysis (Implementation Divergence Test). If the score is below 100% or divergence is HIGH, output REVISE or REJECT with specific issues. Write the report to .dark-factory/reviews/${SPEC_NAME}-validation.md. Also write a machine-readable status file to .dark-factory/reviews/${SPEC_NAME}-validation.status with this exact format (one key=value per line, no quotes): spec=${SPEC_NAME} on the first line, date=<ISO 8601> on the second, score=<0-100> on the third, verdict=<PASS|REVISE|REJECT> on the fourth, divergence=<LOW|MEDIUM|HIGH> on the fifth. The divergence key is the result of the Precision Analysis — you MUST perform it and include the rating."
    echo "$AGENT_OUTPUT"
    audit_log "spec-validator" "END" ""

    if [[ $AGENT_EXIT -ne 0 ]]; then
        _DF_AGENT_FAILURES=$((_DF_AGENT_FAILURES + 1))
        echo "ERROR: Spec validator agent failed (exit $AGENT_EXIT). Cannot proceed."
        write_state "status" "failed" || exit 1
        write_state_or_warn "failure_reason" "spec-validator agent failed with exit code $AGENT_EXIT"
        metrics_record_end "$SPEC_NAME" "$RUN_ID" "failed" "0" "$_DF_AGENT_FAILURES" "$_DF_PASS_RATES" "$_DF_SATISFACTION_SCORES"
        exit 1
    fi

    echo ""

    if ! validate_spec_result; then
        echo "Fix the issues in your spec and re-run."
        exit 1
    fi

    write_state_or_warn "completed_step" "1"
fi

# ==============================================================================
# Step 2: Generate blind test scenarios
# ==============================================================================

if [[ "$RESUME" == "true" ]] && [[ "$COMPLETED_STEP" -ge 2 ]]; then
    echo "[Step 2] Scenario generation already completed (resuming)."
else
    # --regen-scenarios: clear existing scenarios to force regeneration
    if [[ "$REGEN_SCENARIOS" == "true" ]] && [[ -d "$SCENARIOS_DIR" ]]; then
        echo "[Step 2] --regen-scenarios: clearing existing scenarios at ${SCENARIOS_DIR}..."
        rm -rf "$SCENARIOS_DIR"
        write_state_or_warn "completed_step" "1"
        audit_log "scenario-architect" "REGEN" "scenarios cleared by --regen-scenarios flag"
    fi

    if [[ -d "$SCENARIOS_DIR" ]] && has_scenario_files "$SCENARIOS_DIR"; then
        if [[ -f "$SPEC_FILE" ]]; then
            spec_mtime=$(stat -f '%m' "$SPEC_FILE" 2>/dev/null || stat -c '%Y' "$SPEC_FILE" 2>/dev/null || echo "0")
            scenario_mtime=$(stat -f '%m' "$SCENARIOS_DIR" 2>/dev/null || stat -c '%Y' "$SCENARIOS_DIR" 2>/dev/null || echo "0")
            if [[ "$spec_mtime" -gt "$scenario_mtime" ]]; then
                echo "WARNING: Scenarios may be stale (spec modified after scenario generation). Re-run with --regen-scenarios to regenerate."
            fi
        fi
        echo "[Step 2] Scenarios already exist at ${SCENARIOS_DIR}, skipping regeneration."
    else
        echo "[Step 2] Generating blind test scenarios..."
        echo "---"
        echo "IMPORTANT: Running scenario generation in Docker isolation."

        mkdir -p "$SCENARIOS_DIR"
        # Build AGENTS.md reference if present
        _sa_agents_ref=""
        [[ -f "AGENTS.md" ]] && _sa_agents_ref=" Read AGENTS.md for project conventions and architecture."
        audit_log "scenario-architect" "START" ""
        _df_check_turn_budget "scenario-architect"
        invoke_agent "scenario-architect" \
            "You are the Scenario Architect. Read the agent definition at ${AGENTS_DIR}/scenario-architect.md, then generate blind test scenarios for the spec at ${SPEC_FILE}. Write executable test files to ${SCENARIOS_DIR}/. Do NOT read any implementation source code. Write the scenario generation report to .dark-factory/reports/${SPEC_NAME}-scenario-generation.md.${_sa_agents_ref}"
        echo "$AGENT_OUTPUT"
        for _dir in "${_DF_SOURCE_DIRS[@]}"; do
            audit_check_output "scenario-architect" "$AGENT_OUTPUT" "${_dir}/"
        done
        unset _dir
        audit_log "scenario-architect" "END" ""

        if [[ $AGENT_EXIT -ne 0 ]]; then
            _DF_AGENT_FAILURES=$((_DF_AGENT_FAILURES + 1))
            echo "ERROR: Scenario architect agent failed (exit $AGENT_EXIT). Cannot proceed without scenarios."
            write_state "status" "failed" || exit 1
            write_state_or_warn "failure_reason" "scenario-architect agent failed with exit code $AGENT_EXIT"
            metrics_record_end "$SPEC_NAME" "$RUN_ID" "failed" "0" "$_DF_AGENT_FAILURES" "$_DF_PASS_RATES" "$_DF_SATISFACTION_SCORES"
            exit 1
        fi
    fi

    # Step 2.5: Scenario quality validation
    SV_MODE="${SCENARIO_VALIDATION:-strict}"
    if [[ "$SV_MODE" != "off" ]]; then
        echo ""
        echo "[Step 2.5] Validating scenario quality..."
        echo "---"

        SV_REPORT=".dark-factory/reports/${SPEC_NAME}-scenario-validation.md"
        SCENARIO_MTIME=$(stat -f '%m' "$SCENARIOS_DIR" 2>/dev/null || stat -c '%Y' "$SCENARIOS_DIR" 2>/dev/null || echo "0")
        REPORT_MTIME=0
        if [[ -f "$SV_REPORT" ]]; then
            REPORT_MTIME=$(stat -f '%m' "$SV_REPORT" 2>/dev/null || stat -c '%Y' "$SV_REPORT" 2>/dev/null || echo "0")
        fi

        if [[ ! -f "$SV_REPORT" ]] || [[ "$SCENARIO_MTIME" -gt "$REPORT_MTIME" ]]; then
            sv_exit=0
            bash "${DARK_FACTORY_HOME:-dark-factory}/scripts/validate-scenarios.sh" "$SPEC_NAME" || sv_exit=$?
            audit_log "scenario-validation" "END" "exit_code=$sv_exit mode=$SV_MODE"

            if [[ "$SV_MODE" == "strict" ]]; then
                if [[ $sv_exit -eq 1 ]]; then
                    echo ""
                    echo "ERROR: Scenario quality validation failed."
                    echo "See report: ${SV_REPORT}"
                    echo "Fix the scenarios and re-run the pipeline."
                    write_state "status" "failed" || exit 1
                    write_state_or_warn "failure_reason" "scenario quality validation failed"
                    exit 1
                elif [[ $sv_exit -eq 2 ]]; then
                    echo ""
                    echo "ERROR: Scenarios directory missing or empty — generation may have failed silently."
                    write_state "status" "failed" || exit 1
                    write_state_or_warn "failure_reason" "scenarios directory missing after generation"
                    exit 1
                fi
            elif [[ $sv_exit -ne 0 ]]; then
                echo "WARNING: Scenario validation issues detected (mode: ${SV_MODE}). Continuing."
            fi
        else
            echo "  Scenario validation report is current, skipping re-validation."
            audit_log "scenario-validation" "SKIP" "report is current"
        fi
    else
        echo ""
        echo "[Step 2.5] Scenario quality validation: OFF (skipped)"
        audit_log "scenario-validation" "SKIP" "SCENARIO_VALIDATION=off"
    fi

    write_state_or_warn "completed_step" "2"
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
    # Reconstruct metrics chains on resume
    for (( _ri=1; _ri<ITERATION; _ri++ )); do
        _rs=$(read_state "satisfaction_iter${_ri}" "")
        _rp=$(read_state "pass_rate_iter${_ri}" "")
        [[ -n "$_rp" ]] && _DF_PASS_RATES="${_DF_PASS_RATES:+${_DF_PASS_RATES}|}${_rp}"
        [[ -n "$_rs" ]] && _DF_SATISFACTION_SCORES="${_DF_SATISFACTION_SCORES:+${_DF_SATISFACTION_SCORES}|}${_rs}"
    done
else
    ITERATION=1
fi
SHIP=false

while [[ $ITERATION -le $MAX_ITERATIONS ]] && [[ "$SHIP" == "false" ]]; do
    echo ""
    echo "========== Iteration ${ITERATION} of ${MAX_ITERATIONS} =========="
    echo ""

    write_state_or_warn "iteration" "$ITERATION"
    write_state_or_warn "phase" "implementing"

    # Pre-implementation rollback snapshot
    if git rev-parse --is-inside-work-tree &>/dev/null 2>&1; then
        git tag -f "dark-factory/pre-impl-${SPEC_NAME}-iter${ITERATION}" HEAD 2>/dev/null || true
    fi

    # --- Implementation ---
    # Build AGENTS.md reference if present
    _agents_md_ref=""
    [[ -f "AGENTS.md" ]] && _agents_md_ref=" Read AGENTS.md for project conventions and architecture."

    if [[ $ITERATION -eq 1 ]]; then
        echo "[Step 3.${ITERATION}] Implementing from spec..."
        audit_log "implementer" "START" "iteration=$ITERATION"
        _df_check_turn_budget "implementer"
        invoke_agent "implementer" \
            "You are the Implementer. Read the agent definition at ${AGENTS_DIR}/implementer.md, then implement the specification at ${SPEC_FILE}. Follow existing codebase conventions. Write self-tests. Do NOT read anything in .dark-factory/scenarios/.${_agents_md_ref}"
        echo "$AGENT_OUTPUT"
        audit_check_output "implementer" "$AGENT_OUTPUT" ".dark-factory/scenarios/"
        audit_log "implementer" "END" ""

        if [[ $AGENT_EXIT -ne 0 ]]; then
            _DF_AGENT_FAILURES=$((_DF_AGENT_FAILURES + 1))
            echo "WARNING: Implementer agent failed (exit $AGENT_EXIT). Proceeding to review/oracle."
            mkdir -p ".dark-factory/reviews"
            echo "IMPLEMENTER FAILURE: Agent exited with code $AGENT_EXIT on iteration ${ITERATION}. No implementation changes were made." \
                > ".dark-factory/reviews/${SPEC_NAME}-impl-failure-iter${ITERATION}.md"
        fi
    else
        echo "[Step 3.${ITERATION}] Iterating on implementation with feedback..."
        FEEDBACK_FILE=".dark-factory/reviews/${SPEC_NAME}-feedback-iter$((ITERATION-1)).md"
        audit_log "implementer" "START" "iteration=$ITERATION"
        _df_check_turn_budget "implementer"

        IMPL_PROMPT="You are the Implementer. Read the agent definition at ${AGENTS_DIR}/implementer.md. This is iteration ${ITERATION}."
        if [[ -f "$FEEDBACK_FILE" ]]; then
            IMPL_PROMPT="${IMPL_PROMPT} Read the structured feedback at ${FEEDBACK_FILE}."
        fi
        IMPL_PROMPT="${IMPL_PROMPT} Re-read the spec at ${SPEC_FILE} for any spec sections referenced in the feedback. Fix the issues in your implementation. Do NOT read anything in .dark-factory/scenarios/ or .dark-factory/reports/."

        invoke_agent "implementer" "$IMPL_PROMPT"
        echo "$AGENT_OUTPUT"
        audit_check_output "implementer" "$AGENT_OUTPUT" ".dark-factory/scenarios/"
        audit_log "implementer" "END" ""

        if [[ $AGENT_EXIT -ne 0 ]]; then
            _DF_AGENT_FAILURES=$((_DF_AGENT_FAILURES + 1))
            echo "WARNING: Implementer agent failed (exit $AGENT_EXIT). Proceeding to review/oracle."
            mkdir -p ".dark-factory/reviews"
            echo "IMPLEMENTER FAILURE: Agent exited with code $AGENT_EXIT on iteration ${ITERATION}. Implementation may be incomplete." \
                > ".dark-factory/reviews/${SPEC_NAME}-impl-failure-iter${ITERATION}.md"
        fi
    fi
    echo ""

    write_state_or_warn "phase" "quality-gate"

    # --- Quality Gates ---
    echo "[Step 4.${ITERATION}] Running quality gates..."
    echo "---"
    audit_log "quality-gate" "START" "iteration=$ITERATION"

    QG_EXIT=0
    run_quality_gate "$SPEC_NAME" || QG_EXIT=$?

    # --- Satisfaction score extraction, floor enforcement, trajectory analysis ---
    _SCENARIO_COUNTS_FILE=".dark-factory/reports/${SPEC_NAME}-scenario-counts.env"
    SCENARIO_PASS_RATE=""
    SCENARIO_PASSED=""
    SCENARIO_TOTAL=""
    SATISFACTION_SCORE=""
    _PASS_RATE_NOTE=""
    _OSCILLATION_WARNING=""

    if [[ -f "$_SCENARIO_COUNTS_FILE" ]]; then
        # shellcheck source=/dev/null
        source "$_SCENARIO_COUNTS_FILE"
        SCENARIO_PASS_RATE="${SCENARIO_RATE:-}"
        # Satisfaction score defaults to simple rate when not computed
        SATISFACTION_SCORE="${SATISFACTION_SCORE:-${SCENARIO_RATE:-0}}"

        write_state_or_warn "pass_rate_iter${ITERATION}" "${SCENARIO_PASS_RATE}"
        write_state_or_warn "satisfaction_iter${ITERATION}" "${SATISFACTION_SCORE}"

        # Build metrics chains (use ${var:+${var}|} idiom consistently)
        _DF_PASS_RATES="${_DF_PASS_RATES:+${_DF_PASS_RATES}|}${SCENARIO_PASS_RATE}"
        _DF_SATISFACTION_SCORES="${_DF_SATISFACTION_SCORES:+${_DF_SATISFACTION_SCORES}|}${SATISFACTION_SCORE}"

        # --- Floor enforcement ---
        # MIN_SATISFACTION_SCORE takes priority; fall back to MIN_SHIP_PASS_RATE
        _sat_floor="${MIN_SATISFACTION_SCORE:-0}"
        _rate_floor="${MIN_SHIP_PASS_RATE:-0}"

        if [[ "$_sat_floor" -gt 0 ]] && [[ "$SATISFACTION_SCORE" -lt "$_sat_floor" ]]; then
            audit_log "quality-gate" "SATISFACTION_BELOW_FLOOR" "score=${SATISFACTION_SCORE} floor=${_sat_floor}"
            _PASS_RATE_NOTE="Satisfaction score ${SATISFACTION_SCORE}% is below floor ${_sat_floor}%."
            echo "  Satisfaction floor: ${SATISFACTION_SCORE}% < ${_sat_floor}% — enforcing NO-SHIP."
            QG_EXIT=1
        elif [[ "$_rate_floor" -gt 0 ]] && [[ -n "$SCENARIO_PASS_RATE" ]] && [[ "$SCENARIO_PASS_RATE" -lt "$_rate_floor" ]]; then
            audit_log "quality-gate" "PASS_RATE_BELOW_FLOOR" "rate=${SCENARIO_PASS_RATE} floor=${_rate_floor}"
            _PASS_RATE_NOTE="Pass rate ${SCENARIO_PASS_RATE}% is below the minimum floor of ${_rate_floor}%."
            echo "  Pass-rate floor: ${SCENARIO_PASS_RATE}% < ${_rate_floor}% — enforcing NO-SHIP."
            QG_EXIT=1
        fi

        # --- Trajectory analysis (iteration >= 2) ---
        if [[ "$ITERATION" -ge 2 ]] && [[ -n "$_DF_SATISFACTION_SCORES" ]]; then
            _df_classify_trajectory "$_DF_SATISFACTION_SCORES"
            audit_log "quality-gate" "TRAJECTORY" "type=${_DF_TRAJECTORY_TYPE} scores=${_DF_SATISFACTION_SCORES}"
            write_state_or_warn "trajectory_iter${ITERATION}" "${_DF_TRAJECTORY_TYPE}"

            # Per-layer trajectory (if category data available)
            _df_layer_trajectory "$SPEC_NAME" "$ITERATION"

            # Strategy recommendation
            _df_strategy_recommendation "${_DF_TRAJECTORY_TYPE}"

            # Backward-compat: emit OSCILLATION_DETECTED for non-improving trajectories
            case "$_DF_TRAJECTORY_TYPE" in
                OSCILLATING|DEGRADING|PLATEAU|STUCK)
                    _OSCILLATION_WARNING="WARNING: ${_DF_TRAJECTORY_TYPE} trajectory. ${_DF_TRAJECTORY_DESC} ${_DF_TRAJECTORY_RECOMMENDATION}"
                    audit_log "quality-gate" "OSCILLATION_DETECTED" "trajectory=${_DF_TRAJECTORY_TYPE}"
                    echo "  Trajectory: ${_DF_TRAJECTORY_TYPE} — ${_DF_TRAJECTORY_DESC}"
                    ;;
                *)
                    echo "  Trajectory: ${_DF_TRAJECTORY_TYPE}"
                    ;;
            esac

            # Early escalation at iter >= 3 for non-improving trajectories
            if [[ "$ITERATION" -ge 3 ]]; then
                case "$_DF_TRAJECTORY_TYPE" in
                    DEGRADING|OSCILLATING)
                        echo "  ${_DF_TRAJECTORY_TYPE} trajectory — escalating early."
                        audit_log "quality-gate" "OSCILLATION_ESCALATION" "trajectory=${_DF_TRAJECTORY_TYPE} rates=${_DF_SATISFACTION_SCORES}"
                        ITERATION=$((ITERATION + 1))
                        break
                        ;;
                    PLATEAU)
                        echo "  Plateau (3 iterations, no improvement) — escalating early."
                        audit_log "quality-gate" "OSCILLATION_ESCALATION" "trajectory=PLATEAU rates=${_DF_SATISFACTION_SCORES}"
                        ITERATION=$((ITERATION + 1))
                        break
                        ;;
                esac
            fi
        fi
    fi

    if [[ "$QG_EXIT" -ne 0 ]]; then
        audit_log "quality-gate" "FAIL" "exit_code=$QG_EXIT"
        echo "[Step 4.${ITERATION}] Quality gate FAILED (exit $QG_EXIT)."

        if [[ "$QG_EXIT" -eq 2 ]] && [[ "${HARD_GATE_BUILD:-true}" == "true" ]]; then
            echo "[Step 4.${ITERATION}] BUILD FAILURE — skipping review/oracle."
            audit_log "quality-gate" "BUILD_HARD_GATE" "iteration=$ITERATION"
            mkdir -p ".dark-factory/reviews"
            # Write synthetic feedback so next iteration has guidance
            cat > ".dark-factory/reviews/${SPEC_NAME}-feedback-iter${ITERATION}.md" <<BFEOF
# Iteration ${ITERATION} Feedback — Build Failure
**Decision:** NO-SHIP (automatic — build failure hard gate)
## Build Failure
The code does not compile/build. Fix build errors. See: ${REPORT_FILE}
BFEOF
            write_state_or_warn "last_decision" "NO-SHIP (build hard gate)"
            ITERATION=$((ITERATION + 1))
            continue
        fi

        echo "[Step 4.${ITERATION}] Proceeding to review."
    fi
    echo ""

    # --- Step 4.5: Ephemeral Deployment ---
    DEPLOY_EXIT=0
    if [[ -n "${DEPLOY_CMD:-}" ]]; then
        write_state_or_warn "phase" "deployment"

        if [[ "$DRY_RUN" == "true" ]]; then
            echo "[Step 4.5.${ITERATION}] [dry-run] Would deploy: ${DEPLOY_CMD}"
            echo "[Step 4.5.${ITERATION}] [dry-run] Would health-check: ${DEPLOY_BASE_URL:-}${DEPLOY_HEALTH_ENDPOINT:-/health}"
            echo "[Step 4.5.${ITERATION}] [dry-run] Would teardown: ${DEPLOY_TEARDOWN_CMD:-}"
        else
            echo "[Step 4.5a.${ITERATION}] Deploying application..."
            audit_log "deploy" "START" "cmd=${DEPLOY_CMD}"

            bash -c "$DEPLOY_CMD" 2>&1 || { DEPLOY_EXIT=$?; echo "WARNING: Deploy command failed (exit $DEPLOY_EXIT)."; }

            if [[ $DEPLOY_EXIT -eq 0 ]] && [[ -n "${DEPLOY_BASE_URL:-}" ]]; then
                echo "[Step 4.5a.${ITERATION}] Waiting for health check..."
                _health_url="${DEPLOY_BASE_URL}${DEPLOY_HEALTH_ENDPOINT:-/health}"
                _health_timeout="${DEPLOY_HEALTH_TIMEOUT:-60}"
                _health_ok=false

                for _h in $(seq 1 "$_health_timeout"); do
                    if curl --connect-timeout 5 --max-time 10 -sf -o /dev/null "$_health_url" 2>/dev/null; then
                        _health_ok=true
                        echo "  Health check passed after ${_h}s"
                        break
                    fi
                    sleep 1
                done

                if [[ "$_health_ok" != "true" ]]; then
                    echo "  WARNING: Health check failed after ${_health_timeout}s"
                    audit_log "deploy" "HEALTH_FAIL" "url=${_health_url} timeout=${_health_timeout}"
                    DEPLOY_EXIT=1
                else
                    audit_log "deploy" "HEALTH_OK" "url=${_health_url}"
                fi
            fi

            # Step 4.5b: Evaluator (if evaluator agent exists and deploy succeeded)
            if [[ $DEPLOY_EXIT -eq 0 ]] && [[ -f "${AGENTS_DIR}/evaluator.md" ]]; then
                echo "[Step 4.5b.${ITERATION}] Running evaluator agent..."
                audit_log "evaluator" "START" "iteration=$ITERATION"
                _df_check_turn_budget "evaluator"
                invoke_agent "evaluator" \
                    "You are the Evaluator. Read the agent definition at ${AGENTS_DIR}/evaluator.md. Evaluate the deployed application at ${DEPLOY_BASE_URL:-} against the behavioral scenarios for spec ${SPEC_FILE}. Check .dark-factory/scenarios/${SPEC_NAME}/ for .eval.md files. Write evaluation results to .dark-factory/reports/${SPEC_NAME}-evaluation.md."
                echo "$AGENT_OUTPUT"
                audit_log "evaluator" "END" "exit_code=$AGENT_EXIT"
            fi

            # Step 4.5c: Teardown
            if [[ -n "${DEPLOY_TEARDOWN_CMD:-}" ]]; then
                echo "[Step 4.5c.${ITERATION}] Tearing down deployment..."
                bash -c "$DEPLOY_TEARDOWN_CMD" 2>/dev/null || true
                audit_log "deploy" "TEARDOWN" ""
            fi
        fi
    fi

    write_state_or_warn "phase" "review"

    # --- Review ---
    echo "[Step 5.${ITERATION}] Running review..."
    echo "---"
    audit_log "reviewer" "START" "iteration=$ITERATION"
    SUMMARY_FILE=".dark-factory/reports/${SPEC_NAME}-quality-gate-summary.md"
    EVAL_REPORT=".dark-factory/reports/${SPEC_NAME}-evaluation-iter${ITERATION}.md"
    _review_prompt="You are the Reviewer. Read the agent definition at ${AGENTS_DIR}/reviewer.md. Review the implementation for spec ${SPEC_FILE}."
    if [[ -f "$SUMMARY_FILE" ]]; then
        _review_prompt="${_review_prompt}

Quality gate summary:
$(cat "$SUMMARY_FILE")"
    fi
    if [[ -f "$EVAL_REPORT" ]]; then
        _review_prompt="${_review_prompt}

Evaluator report:
$(cat "$EVAL_REPORT")"
    fi
    _review_prompt="${_review_prompt} Write your review to ${REVIEW_FILE}."
    _df_check_turn_budget "reviewer"
    invoke_agent "reviewer" "$_review_prompt"
    echo "$AGENT_OUTPUT"
    audit_log "reviewer" "END" ""

    if [[ $AGENT_EXIT -ne 0 ]]; then
        _DF_AGENT_FAILURES=$((_DF_AGENT_FAILURES + 1))
        echo "WARNING: Reviewer agent failed (exit $AGENT_EXIT). Writing synthetic review — oracle will NO-SHIP."
        cat > "$REVIEW_FILE" <<EOF
# Review: FAILED

**VERDICT: REVIEW_FAILED**

The reviewer agent exited with code ${AGENT_EXIT} and did not produce a review.
This is an automatic NO-SHIP condition. A completed review is required before any SHIP decision.
EOF
        if [[ $? -ne 0 ]]; then
            echo "ERROR: Could not write synthetic review to ${REVIEW_FILE}. Pipeline cannot continue safely."
            exit 1
        fi
    fi
    echo ""

    write_state_or_warn "phase" "oracle"

    # --- Oracle Decision ---
    echo "[Step 6.${ITERATION}] Oracle decision..."
    echo "---"
    audit_log "oracle" "START" "iteration=$ITERATION"

    # Build Oracle prompt with satisfaction analysis context
    _oracle_prompt="You are the Oracle. Read the agent definition at ${AGENTS_DIR}/oracle.md. Make a SHIP/NO-SHIP decision for spec ${SPEC_FILE}. Review is at ${REVIEW_FILE}. Quality gate report is at ${REPORT_FILE}. This is iteration ${ITERATION} of ${MAX_ITERATIONS}."

    if [[ -n "${SATISFACTION_SCORE:-}" ]] && [[ -n "${SCENARIO_TOTAL:-}" ]]; then
        # Build full iteration intelligence analysis
        _df_build_oracle_analysis "$ITERATION" "${SCENARIO_PASS_RATE:-0}" "${SCENARIO_PASSED:-0}" "${SCENARIO_TOTAL:-0}" "${MIN_SATISFACTION_SCORE:-${MIN_SHIP_PASS_RATE:-0}}"
        _oracle_prompt="${_oracle_prompt}

${_DF_ORACLE_ANALYSIS}"
    fi

    if [[ -n "${_PASS_RATE_NOTE:-}" ]]; then
        _oracle_prompt="${_oracle_prompt} ${_PASS_RATE_NOTE} You MUST NOT SHIP."
    fi
    if [[ -n "${_OSCILLATION_WARNING:-}" ]]; then
        _oracle_prompt="${_oracle_prompt} ${_OSCILLATION_WARNING}"
    fi
    _oracle_prompt="${_oracle_prompt} Your output MUST start with SHIP or NO-SHIP on the very first line (plain text, no markdown formatting), followed by your rationale. If NO-SHIP, also write structured iteration feedback to .dark-factory/reviews/${SPEC_NAME}-feedback-iter${ITERATION}.md using the Iteration Feedback format from your agent definition."

    _df_check_turn_budget "oracle"
    invoke_agent "oracle" "$_oracle_prompt"
    ORACLE_OUTPUT="$AGENT_OUTPUT"

    echo "$ORACLE_OUTPUT"
    audit_log "oracle" "END" ""

    # Parse Oracle decision
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
        # Audit Oracle feedback file for barrier violations
        ORACLE_FEEDBACK=".dark-factory/reviews/${SPEC_NAME}-feedback-iter${ITERATION}.md"
        if [[ -f "$ORACLE_FEEDBACK" ]]; then
            FEEDBACK_CONTENT=$(cat "$ORACLE_FEEDBACK" 2>/dev/null) || FEEDBACK_CONTENT=""
            audit_check_output "oracle-feedback" "$FEEDBACK_CONTENT" ".dark-factory/scenarios/"
            validate_oracle_feedback "$FEEDBACK_CONTENT"
        else
            echo "WARNING: Oracle produced NO-SHIP but feedback file is missing: ${ORACLE_FEEDBACK}"
            echo "         The next iteration will run without structured feedback."
            audit_log "oracle" "FEEDBACK_MISSING" "expected=${ORACLE_FEEDBACK}"
        fi
    else
        audit_log "oracle" "MALFORMED_OUTPUT" "first_line='$(echo "$ORACLE_OUTPUT" | head -1)'"
        echo "WARNING: Oracle output did not start with SHIP or NO-SHIP."
        echo "First line was: $(echo "$ORACLE_OUTPUT" | head -1)"
        echo "Treating as NO-SHIP for safety."
        SHIP=false
    fi

    write_state_or_warn "last_decision" "$(echo "$ORACLE_OUTPUT" | head -1)"
    ITERATION=$((ITERATION + 1))
done

# ==============================================================================
# Pipeline result
# ==============================================================================

if [[ "$SHIP" == "true" ]]; then
    # --- Cross-spec regression testing ---
    _run_regression=false
    if [[ "$REGRESSION_FLAG" == "true" ]] || [[ "${REGRESSION_ON_SHIP:-false}" == "true" ]]; then
        _run_regression=true
    fi

    if [[ "$_run_regression" == "true" ]]; then
        echo ""
        echo "[Regression] Running cross-spec regression tests..."
        _regression_failed=()
        _regression_run=0
        _regression_timeout="${REGRESSION_TIMEOUT:-300}"
        _run_scenarios_script="${DARK_FACTORY_HOME:-dark-factory}/scripts/run-scenarios.sh"

        for _scen_dir in .dark-factory/scenarios/*/; do
            [[ ! -d "$_scen_dir" ]] && continue
            _other_spec="$(basename "$_scen_dir")"
            [[ "$_other_spec" == "$SPEC_NAME" ]] && continue

            # Only run regression for previously shipped specs
            _other_state=".dark-factory/state/${_other_spec}.state"
            if [[ -f "$_other_state" ]]; then
                _other_status=$(grep '^status=' "$_other_state" 2>/dev/null | tail -1 | cut -d= -f2-) || _other_status=""
            else
                echo "  SKIP  ${_other_spec} (no state file — never completed pipeline)"
                audit_log "regression" "SKIP_NO_STATE" "spec=${_other_spec}"
                continue
            fi
            if [[ "$_other_status" != "shipped" ]]; then
                echo "  SKIP  ${_other_spec} (status=${_other_status}, not shipped)"
                audit_log "regression" "SKIP_NOT_SHIPPED" "spec=${_other_spec} status=${_other_status}"
                continue
            fi

            _regression_run=$((_regression_run + 1))
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [dry-run] Would run regression: bash ${_run_scenarios_script} ${_other_spec}"
                continue
            fi

            _reg_exit=0
            if [[ -n "${TIMEOUT_CMD:-}" ]]; then
                $TIMEOUT_CMD "$_regression_timeout" bash "$_run_scenarios_script" "$_other_spec" >/dev/null 2>&1 || _reg_exit=$?
            else
                bash "$_run_scenarios_script" "$_other_spec" >/dev/null 2>&1 || _reg_exit=$?
            fi

            if [[ $_reg_exit -eq 0 ]]; then
                echo "  PASS  ${_other_spec}"
                audit_log "regression" "PASS" "spec=${_other_spec}"
            else
                echo "  FAIL  ${_other_spec} (exit ${_reg_exit})"
                _regression_failed+=("$_other_spec")
                audit_log "regression" "FAIL" "spec=${_other_spec} exit_code=${_reg_exit}"
            fi
        done

        if [[ ${#_regression_failed[@]} -gt 0 ]]; then
            _reg_policy="${REGRESSION_POLICY:-block}"
            # Validate policy value
            if [[ "$_reg_policy" != "block" && "$_reg_policy" != "warn" ]]; then
                echo "ERROR: Invalid REGRESSION_POLICY: '$_reg_policy'. Must be 'block' or 'warn'."
                exit 1
            fi

            echo ""
            echo "  ${#_regression_failed[@]} regression failure(s): ${_regression_failed[*]}"
            audit_log "regression" "SUMMARY" "total=${_regression_run} failed=${#_regression_failed[@]} specs=${_regression_failed[*]} policy=${_reg_policy}"

            if [[ "$_reg_policy" == "block" ]]; then
                echo ""
                echo "  REGRESSION BLOCKED: Cannot ship with regression failures."
                echo "  Failed specs: ${_regression_failed[*]}"
                echo "  To ship anyway, set REGRESSION_POLICY=warn in .dark-factory/config.sh"
                notify_regression_blocked "$SPEC_NAME" "${_regression_failed[*]}"
                _DF_FINAL_STATUS="regression-failed"
                write_state "status" "regression-failed" || true
                cleanup_all || true
                metrics_record_end "$SPEC_NAME" "$RUN_ID" "regression_blocked" "$((ITERATION - 1))" "$_DF_AGENT_FAILURES" "$_DF_PASS_RATES" "$_DF_SATISFACTION_SCORES"
                audit_log "pipeline" "END" "result=REGRESSION_BLOCKED iterations=$((ITERATION - 1))"
                audit_summary
                exit 2
            else
                echo "  WARNING: Proceeding with SHIP despite regression failures (REGRESSION_POLICY=warn)."
            fi
        elif [[ $_regression_run -gt 0 ]]; then
            echo "  All ${_regression_run} regression specs passed."
            audit_log "regression" "SUMMARY" "total=${_regression_run} failed=0"
        else
            echo "  No previously-shipped specs found for regression."
        fi
    fi

    # --- Autonomy-level gate ---
    _autonomy="${AUTONOMY_LEVEL:-3}"
    if [[ "$_autonomy" == "1" ]]; then
        if [[ -t 0 ]] && [[ "$CI_MODE" != "true" ]]; then
            echo ""
            echo "  AUTONOMY LEVEL 1 (Assisted): Human confirmation required."
            echo "  Review report: ${REPORT_FILE}"
            echo "  Review:        ${REVIEW_FILE}"
            read -rp "  Accept SHIP? [y/n]: " _confirm
            if [[ "$_confirm" != "y" && "$_confirm" != "Y" ]]; then
                echo "  SHIP declined by human."
                _DF_FINAL_STATUS="human-declined"
                write_state "status" "human-declined" || true
                audit_log "pipeline" "HUMAN_DECLINED" "autonomy_level=1"
                cleanup_all || true
                metrics_record_end "$SPEC_NAME" "$RUN_ID" "human_declined" "$((ITERATION - 1))" "$_DF_AGENT_FAILURES" "$_DF_PASS_RATES" "$_DF_SATISFACTION_SCORES"
                exit 2
            fi
            audit_log "pipeline" "HUMAN_CONFIRMED" "autonomy_level=1"
        else
            echo "  AUTONOMY LEVEL 1 (Assisted): CI mode — auto-accepting (no TTY)."
            audit_log "pipeline" "AUTO_ACCEPT_CI" "autonomy_level=1 ci_mode=${CI_MODE}"
        fi
    elif [[ "$_autonomy" == "2" ]]; then
        echo "  AUTONOMY LEVEL 2 (Supervised): Logging SHIP decision."
        audit_log "pipeline" "SUPERVISED_SHIP" "autonomy_level=2"
    fi
    # Level 3 (Autonomous): no gate, proceed as before.

    # --- Post-SHIP: Git operations ---
    # _auto_pr resolved once during preflight (line ~304); reuse here.
    _base_branch="${_CLI_BASE_BRANCH:-${GIT_BASE_BRANCH:-main}}"

    _pr_url=""
    if [[ "$_auto_pr" == "true" ]] && [[ "$DRY_RUN" != "true" ]]; then
        echo ""
        echo "[Git] Creating branch and PR..."
        _branch=""
        if gitops_create_branch "$SPEC_NAME" "$RUN_ID"; then
            _branch=$(git rev-parse --abbrev-ref HEAD)
            gitops_commit_changes "$SPEC_NAME" "$RUN_ID"
            if gitops_push_branch "$_branch"; then
                _pr_url=$(gitops_create_pr "$SPEC_NAME" "$_branch" "$_base_branch")
                if [[ -n "$_pr_url" ]]; then
                    write_state_or_warn "pr_url" "$_pr_url"
                    echo "  PR created: $_pr_url"
                    audit_log "pipeline" "PR_CREATED" "url=${_pr_url} branch=${_branch}"
                    notify_pr_created "$SPEC_NAME" "$_pr_url"

                    if [[ "${MERGE_QUEUE_ENABLED:-false}" == "true" ]]; then
                        gitops_merge_queue_add "$_pr_url"
                    fi
                fi
            fi
        fi
        if [[ -z "$_pr_url" ]]; then
            echo "  WARNING: PR creation failed. Code is committed locally."
            audit_log "pipeline" "PR_FAILED" "branch=${_branch:-unknown}"
            write_state_or_warn "pr_status" "failed"
        else
            write_state_or_warn "pr_status" "created"
        fi
    elif [[ "$_auto_pr" == "true" ]] && [[ "$DRY_RUN" == "true" ]]; then
        echo "[dry-run] Would create branch, commit, push, and open PR for ${SPEC_NAME}"
    fi

    # --- Notifications ---
    notify_shipped "$SPEC_NAME" "$RUN_ID" "$((ITERATION - 1))" "$_pr_url"

    write_state_or_warn "ship_count" "$(($(read_state "ship_count" "0") + 1))"
    _DF_FINAL_STATUS="shipped"
    write_state "status" "shipped" || true
    write_state_or_warn "completed_step" "99"
    cleanup_all || true
    metrics_record_end "$SPEC_NAME" "$RUN_ID" "shipped" "$((ITERATION - 1))" "$_DF_AGENT_FAILURES" "$_DF_PASS_RATES" "$_DF_SATISFACTION_SCORES"
    audit_log "pipeline" "END" "result=SHIP iterations=$((ITERATION - 1)) autonomy_level=${_autonomy}"
    audit_summary
else
    echo ""
    echo "============================================"
    echo "  ESCALATION — Max iterations reached"
    echo "  Human review required."
    echo "  See: ${REVIEW_FILE}"
    echo "  See: ${REPORT_FILE}"

    ESCALATION_FILE=".dark-factory/reviews/${SPEC_NAME}-escalation.md"
    audit_log "pipeline" "ESCALATION" "generating escalation report"

    _df_check_turn_budget "escalation"
    invoke_agent "escalation" \
        "You are the Escalation Agent. Read the agent definition at ${AGENTS_DIR}/escalation.md, then read the escalation report template at templates/escalation-report.md. Generate a completed escalation report for spec '${SPEC_NAME}'. Review all iteration feedback files at .dark-factory/reviews/${SPEC_NAME}-feedback-iter*.md, the quality gate report at ${REPORT_FILE}, and the review at ${REVIEW_FILE}. The pipeline run ID is ${RUN_ID}. Write the completed report to ${ESCALATION_FILE}." 0

    if [[ $AGENT_EXIT -ne 0 ]]; then
        echo "  WARNING: Escalation report generation failed (exit $AGENT_EXIT). Continuing."
    fi

    echo "  Escalation report: ${ESCALATION_FILE}"
    if git rev-parse --is-inside-work-tree &>/dev/null 2>&1; then
        echo "  Rollback tags available:"
        for _i in $(seq 1 $((ITERATION - 1))); do
            echo "    git reset --hard dark-factory/pre-impl-${SPEC_NAME}-iter${_i}"
        done
        unset _i
    fi
    echo "  See: docs/ESCALATION.md for next steps"
    echo "============================================"

    notify_escalated "$SPEC_NAME" "$RUN_ID" "$((ITERATION - 1))" "max iterations reached"

    _DF_FINAL_STATUS="escalated"
    write_state "status" "escalated" || true
    cleanup_all || true
    metrics_record_end "$SPEC_NAME" "$RUN_ID" "escalated" "$((ITERATION - 1))" "$_DF_AGENT_FAILURES" "$_DF_PASS_RATES" "$_DF_SATISFACTION_SCORES"
    audit_log "pipeline" "END" "result=ESCALATION iterations=$((ITERATION - 1))"
    audit_summary
    exit 2
fi
