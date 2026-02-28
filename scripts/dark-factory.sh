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

MAX_ITERATIONS=3
TEST_CMD=""
RESUME_MODE=""
CI_MODE=false
SKIP_DEP_CHECK=false
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
# Timeout detection
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
# ==============================================================================

SA_SOURCE_DIR="${SOURCE_DIR:-src}"

# File-access tool restrictions (reliable — path-based patterns)
IMPLEMENTER_DENY="Read(.dark-factory/scenarios/**),Edit(.dark-factory/scenarios/**),Write(.dark-factory/scenarios/**),Grep(.dark-factory/scenarios/**),Glob(.dark-factory/scenarios/**)"

SCENARIO_ARCH_DENY="Read(${SA_SOURCE_DIR}/**),Grep(${SA_SOURCE_DIR}/**),Glob(${SA_SOURCE_DIR}/**)"

IMPLEMENTER_BARRIER="CRITICAL SECURITY RULE: You MUST NOT read, access, or examine any file in .dark-factory/scenarios/. This is an information barrier. If you see scenario content in error messages, do not use it to guide implementation."

SCENARIO_ARCH_BARRIER="CRITICAL SECURITY RULE: You MUST NOT read, access, or examine any implementation source code in ${SA_SOURCE_DIR}/. Derive all scenarios purely from the specification. If you encounter implementation details, ignore them."

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

# Crash recovery trap
trap 'ec=$?; if [[ $ec -ne 0 ]]; then write_state "status" "interrupted" 2>/dev/null || true; fi' EXIT

# ==============================================================================
# Pipeline start
# ==============================================================================

echo "============================================"
echo "  Dark Factory Pipeline"
echo "  Spec: ${SPEC_NAME}"
echo "  Max Iterations: ${MAX_ITERATIONS}"
echo "  Run ID: ${RUN_ID}"
if [[ "$RESUME" == "true" ]]; then
    echo "  Mode: RESUME"
fi
echo "============================================"
echo ""

audit_log "pipeline" "START" "spec=${SPEC_NAME} max_iterations=${MAX_ITERATIONS} resume=${RESUME} run_id=${RUN_ID}"

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
    VALIDATOR_OUTPUT=$(claude --print --dangerously-skip-permissions \
        "You are the Spec Validator. Read the agent definition at ${AGENTS_DIR}/spec-validator.md, then validate the specification at ${SPEC_FILE}. Produce the validation report. If the score is below 100%, output REVISE or REJECT with specific issues. Write the report to .dark-factory/reviews/${SPEC_NAME}-validation.md. Also write a machine-readable status file to .dark-factory/reviews/${SPEC_NAME}-validation.status with this exact format (one key=value per line, no quotes): spec=${SPEC_NAME} on the first line, date=<ISO 8601> on the second, score=<0-100> on the third, verdict=<PASS|REVISE|REJECT> on the fourth." 2>&1) || true
    echo "$VALIDATOR_OUTPUT"
    audit_log "spec-validator" "END" ""

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
        echo "IMPORTANT: Running scenario generation in isolation."

        audit_log "scenario-architect" "START" ""
        SA_OUTPUT=$(claude --print --dangerously-skip-permissions \
            --disallowedTools "$SCENARIO_ARCH_DENY" \
            --append-system-prompt "$SCENARIO_ARCH_BARRIER" \
            "You are the Scenario Architect. Read the agent definition at ${AGENTS_DIR}/scenario-architect.md, then generate blind test scenarios for the spec at ${SPEC_FILE}. Write executable test files to ${SCENARIOS_DIR}/. Do NOT read any implementation source code. Produce the scenario generation report." 2>&1) || true
        echo "$SA_OUTPUT"
        audit_check_output "scenario-architect" "$SA_OUTPUT" "${SA_SOURCE_DIR}/"
        audit_log "scenario-architect" "END" ""
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
        IMPL_OUTPUT=$(claude --print --dangerously-skip-permissions \
            --disallowedTools "$IMPLEMENTER_DENY" \
            --append-system-prompt "$IMPLEMENTER_BARRIER" \
            "You are the Implementer. Read the agent definition at ${AGENTS_DIR}/implementer.md, then implement the specification at ${SPEC_FILE}. Follow existing codebase conventions. Write self-tests. Do NOT read anything in .dark-factory/scenarios/." 2>&1) || true
        echo "$IMPL_OUTPUT"
        audit_check_output "implementer" "$IMPL_OUTPUT" ".dark-factory/scenarios/"
        audit_log "implementer" "END" ""
    else
        echo "[Step 3.${ITERATION}] Iterating on implementation with feedback..."
        FEEDBACK_FILE=".dark-factory/reviews/${SPEC_NAME}-feedback-iter$((ITERATION-1)).md"
        audit_log "implementer" "START" "iteration=$ITERATION"

        IMPL_PROMPT="You are the Implementer. Read the agent definition at ${AGENTS_DIR}/implementer.md. This is iteration ${ITERATION}."
        if [[ -f "$FEEDBACK_FILE" ]]; then
            IMPL_PROMPT="${IMPL_PROMPT} Read the structured feedback at ${FEEDBACK_FILE}."
        fi
        IMPL_PROMPT="${IMPL_PROMPT} Also read ${REPORT_FILE} and ${REVIEW_FILE} for additional context. Fix the issues in your implementation. Do NOT read anything in .dark-factory/scenarios/."

        IMPL_OUTPUT=$(claude --print --dangerously-skip-permissions \
            --disallowedTools "$IMPLEMENTER_DENY" \
            --append-system-prompt "$IMPLEMENTER_BARRIER" \
            "$IMPL_PROMPT" 2>&1) || true
        echo "$IMPL_OUTPUT"
        audit_check_output "implementer" "$IMPL_OUTPUT" ".dark-factory/scenarios/"
        audit_log "implementer" "END" ""
    fi
    echo ""

    write_state "phase" "quality-gate"

    # --- Quality Gates ---
    echo "[Step 4.${ITERATION}] Running quality gates..."
    echo "---"
    audit_log "quality-gate" "START" "iteration=$ITERATION"
    bash "$SCRIPT_DIR/quality-gate.sh" "$SPEC_NAME" ${TEST_CMD:+--test-cmd "$TEST_CMD"}
    audit_log "quality-gate" "END" ""
    echo ""

    write_state "phase" "review"

    # --- Review ---
    echo "[Step 5.${ITERATION}] Running review..."
    echo "---"
    audit_log "reviewer" "START" "iteration=$ITERATION"
    REVIEW_OUTPUT=$(claude --print --dangerously-skip-permissions \
        "You are the Reviewer. Read the agent definition at ${AGENTS_DIR}/reviewer.md. Review the implementation for spec ${SPEC_FILE}. Quality gate report is at ${REPORT_FILE}. Write your review to ${REVIEW_FILE}." 2>&1) || true
    echo "$REVIEW_OUTPUT"
    audit_log "reviewer" "END" ""
    echo ""

    write_state "phase" "oracle"

    # --- Oracle Decision ---
    echo "[Step 6.${ITERATION}] Oracle decision..."
    echo "---"
    audit_log "oracle" "START" "iteration=$ITERATION"
    ORACLE_OUTPUT=$(claude --print --dangerously-skip-permissions \
        "You are the Oracle. Read the agent definition at ${AGENTS_DIR}/oracle.md. Make a SHIP/NO-SHIP decision for spec ${SPEC_FILE}. Review is at ${REVIEW_FILE}. Quality gate report is at ${REPORT_FILE}. This is iteration ${ITERATION} of ${MAX_ITERATIONS}. Output ONLY 'SHIP' or 'NO-SHIP' on the first line, followed by your rationale. If NO-SHIP, also write structured iteration feedback to .dark-factory/reviews/${SPEC_NAME}-feedback-iter${ITERATION}.md using the Iteration Feedback format from your agent definition." 2>&1) || true

    echo "$ORACLE_OUTPUT"
    audit_log "oracle" "END" ""

    # Parse Oracle decision (P0-impl-note-3: anchored regex)
    if echo "$ORACLE_OUTPUT" | head -1 | grep -qE '^SHIP\b'; then
        SHIP=true
        echo ""
        echo "============================================"
        echo "  SHIP — Pipeline complete!"
        echo "  Iterations: ${ITERATION}"
        echo "============================================"
    elif echo "$ORACLE_OUTPUT" | head -1 | grep -qE '^NO-SHIP\b'; then
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

    ESCALATION_OUTPUT=$(claude --print --dangerously-skip-permissions \
        "Read the escalation report template at templates/escalation-report.md. Generate a completed escalation report for spec '${SPEC_NAME}'. Review all iteration feedback files at .dark-factory/reviews/${SPEC_NAME}-feedback-iter*.md, the quality gate report at ${REPORT_FILE}, and the review at ${REVIEW_FILE}. The pipeline run ID is ${RUN_ID}. Write the completed report to ${ESCALATION_FILE}." 2>&1) || true
    echo "$ESCALATION_OUTPUT" > /dev/null  # Suppress verbose output

    echo "  Escalation report: ${ESCALATION_FILE}"
    echo "  See: docs/ESCALATION.md for next steps"
    echo "============================================"

    write_state "status" "escalated"
    trap - EXIT  # Disarm crash recovery trap
    audit_log "pipeline" "END" "result=ESCALATION iterations=$((ITERATION - 1))"
    audit_summary
    exit 2
fi
