#!/usr/bin/env bash
set -euo pipefail

# Dark Factory Pipeline Orchestrator
# Usage: ./scripts/dark-factory.sh [spec-name] [--max-iterations N] [--test-cmd "command"]

MAX_ITERATIONS=3
TEST_CMD=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source per-project config if it exists
CONFIG_FILE=".dark-factory/config.sh"
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
fi

# Resolve agent path: use DARK_FACTORY_HOME from config, or detect from script location
AGENTS_DIR="${DARK_FACTORY_HOME:-dark-factory}/agents"
if [[ ! -d "$AGENTS_DIR" ]]; then
    # Fallback: try relative to the script directory (running from within the repo)
    AGENTS_DIR="${SCRIPT_DIR}/../agents"
fi

# --- Interactive spec selector ---

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

# Determine spec name: from argument or interactive picker
if [[ -n "${1:-}" ]]; then
    SPEC_NAME="$1"
    shift
else
    select_spec
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
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

SPEC_FILE=".dark-factory/specs/${SPEC_NAME}.md"
SCENARIOS_DIR=".dark-factory/scenarios/${SPEC_NAME}"
REVIEW_FILE=".dark-factory/reviews/${SPEC_NAME}-review.md"
REPORT_FILE=".dark-factory/reports/${SPEC_NAME}-quality-gate.md"

echo "============================================"
echo "  Dark Factory Pipeline"
echo "  Spec: ${SPEC_NAME}"
echo "  Max Iterations: ${MAX_ITERATIONS}"
echo "============================================"
echo ""

# Step 0: Verify spec exists
if [[ ! -f "$SPEC_FILE" ]]; then
    echo "ERROR: Spec file not found: $SPEC_FILE"
    echo "Create a spec first using a template from templates/"
    exit 1
fi

echo "[Step 1] Validating specification..."
echo "---"
claude --print --dangerously-skip-permissions "You are the Spec Validator. Read the agent definition at ${AGENTS_DIR}/spec-validator.md, then validate the specification at ${SPEC_FILE}. Produce the validation report. If the score is below 100%, output REVISE or REJECT with specific issues. Write the report to .dark-factory/reviews/${SPEC_NAME}-validation.md"
echo ""

# Check validation result
if [[ -f ".dark-factory/reviews/${SPEC_NAME}-validation.md" ]]; then
    if grep -q "REJECT" ".dark-factory/reviews/${SPEC_NAME}-validation.md"; then
        echo "SPEC REJECTED. Fix the issues and re-run."
        exit 1
    fi
    if grep -q "REVISE" ".dark-factory/reviews/${SPEC_NAME}-validation.md"; then
        echo "SPEC NEEDS REVISION. Fix the issues and re-run."
        exit 1
    fi
fi

if [[ -d "$SCENARIOS_DIR" ]] && [[ -n "$(ls -A "$SCENARIOS_DIR" 2>/dev/null)" ]]; then
    echo "[Step 2] Scenarios already exist at ${SCENARIOS_DIR}, skipping regeneration."
else
    echo "[Step 2] Generating blind test scenarios..."
    echo "---"
    echo "IMPORTANT: Running scenario generation in isolation."
    claude --print --dangerously-skip-permissions "You are the Scenario Architect. Read the agent definition at ${AGENTS_DIR}/scenario-architect.md, then generate blind test scenarios for the spec at ${SPEC_FILE}. Write executable test files to ${SCENARIOS_DIR}/. Do NOT read any implementation source code. Produce the scenario generation report."
fi
echo ""

echo "[Step 3] Starting implementation..."
echo "---"

ITERATION=1
SHIP=false

while [[ $ITERATION -le $MAX_ITERATIONS ]] && [[ "$SHIP" == "false" ]]; do
    echo ""
    echo "========== Iteration ${ITERATION} of ${MAX_ITERATIONS} =========="
    echo ""

    if [[ $ITERATION -eq 1 ]]; then
        echo "[Step 3.${ITERATION}] Implementing from spec..."
        claude --print --dangerously-skip-permissions \
            --disallowedTools "Read(/.dark-factory/scenarios/**),Edit(/.dark-factory/scenarios/**),Write(/.dark-factory/scenarios/**),Grep(/.dark-factory/scenarios/**),Glob(/.dark-factory/scenarios/**)" \
            "You are the Implementer. Read the agent definition at ${AGENTS_DIR}/implementer.md, then implement the specification at ${SPEC_FILE}. Follow existing codebase conventions. Write self-tests. Do NOT read anything in .dark-factory/scenarios/."
    else
        echo "[Step 3.${ITERATION}] Iterating on implementation with feedback..."
        claude --print --dangerously-skip-permissions \
            --disallowedTools "Read(/.dark-factory/scenarios/**),Edit(/.dark-factory/scenarios/**),Write(/.dark-factory/scenarios/**),Grep(/.dark-factory/scenarios/**),Glob(/.dark-factory/scenarios/**)" \
            "You are the Implementer. Read the agent definition at ${AGENTS_DIR}/implementer.md. This is iteration ${ITERATION}. Read the feedback at ${REPORT_FILE} and ${REVIEW_FILE}. Fix the issues in your implementation. Do NOT read anything in .dark-factory/scenarios/."
    fi
    echo ""

    echo "[Step 4.${ITERATION}] Running quality gates..."
    echo "---"
    bash "$SCRIPT_DIR/quality-gate.sh" "$SPEC_NAME" ${TEST_CMD:+--test-cmd "$TEST_CMD"}
    echo ""

    echo "[Step 5.${ITERATION}] Running review..."
    echo "---"
    claude --print --dangerously-skip-permissions "You are the Reviewer. Read the agent definition at ${AGENTS_DIR}/reviewer.md. Review the implementation for spec ${SPEC_FILE}. Quality gate report is at ${REPORT_FILE}. Write your review to ${REVIEW_FILE}."
    echo ""

    echo "[Step 6.${ITERATION}] Oracle decision..."
    echo "---"
    ORACLE_OUTPUT=$(claude --print --dangerously-skip-permissions "You are the Oracle. Read the agent definition at ${AGENTS_DIR}/oracle.md. Make a SHIP/NO-SHIP decision for spec ${SPEC_FILE}. Review is at ${REVIEW_FILE}. Quality gate report is at ${REPORT_FILE}. This is iteration ${ITERATION} of ${MAX_ITERATIONS}. Output ONLY 'SHIP' or 'NO-SHIP' on the first line, followed by your rationale.")

    echo "$ORACLE_OUTPUT"

    if echo "$ORACLE_OUTPUT" | head -1 | grep -q "SHIP"; then
        if ! echo "$ORACLE_OUTPUT" | head -1 | grep -q "NO-SHIP"; then
            SHIP=true
            echo ""
            echo "============================================"
            echo "  SHIP — Pipeline complete!"
            echo "  Iterations: ${ITERATION}"
            echo "============================================"
        fi
    fi

    ITERATION=$((ITERATION + 1))
done

if [[ "$SHIP" == "false" ]]; then
    echo ""
    echo "============================================"
    echo "  ESCALATION — Max iterations reached"
    echo "  Human review required."
    echo "  See: ${REVIEW_FILE}"
    echo "  See: ${REPORT_FILE}"
    echo "============================================"
    exit 2
fi
