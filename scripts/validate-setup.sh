#!/usr/bin/env bash
set -euo pipefail

# Dark Factory Setup Validator
# Usage: ./scripts/validate-setup.sh [project-root]
#
# Checks that Dark Factory is correctly integrated into a project.
# Exit code 0 = all checks pass, 1 = any check failed.

PROJECT_ROOT="${1:-.}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

pass() {
    echo "  PASS  $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    echo "  FAIL  $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

warn() {
    echo "  WARN  $1"
    WARN_COUNT=$((WARN_COUNT + 1))
}

echo "Dark Factory Setup Validation"
echo "Project: ${PROJECT_ROOT}"
echo "=============================="
echo ""

# ------------------------------------------------------------------------------
# 1. Directory structure
# ------------------------------------------------------------------------------
echo "[Directory Structure]"

for dir in .dark-factory .dark-factory/specs .dark-factory/scenarios .dark-factory/reviews .dark-factory/reports; do
    if [[ -d "${PROJECT_ROOT}/${dir}" ]]; then
        pass "${dir}/ exists"
    else
        fail "${dir}/ missing"
    fi
done

echo ""

# ------------------------------------------------------------------------------
# 2. Framework files
# ------------------------------------------------------------------------------
echo "[Framework Files]"

# Determine framework home
if [[ -f "${PROJECT_ROOT}/.dark-factory/config.sh" ]]; then
    # shellcheck source=/dev/null
    source "${PROJECT_ROOT}/.dark-factory/config.sh"
    DF_HOME="${DARK_FACTORY_HOME:-dark-factory}"
else
    DF_HOME="dark-factory"
fi

FRAMEWORK_DIR="${PROJECT_ROOT}/${DF_HOME}"

if [[ -d "$FRAMEWORK_DIR" ]]; then
    pass "Framework directory (${DF_HOME}/) exists"
else
    # Check if we're running from within the dark-factory repo itself
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    if [[ "$PROJECT_ROOT" == "$REPO_ROOT" ]]; then
        FRAMEWORK_DIR="$REPO_ROOT"
        pass "Running from within dark-factory repo"
    else
        fail "Framework directory (${DF_HOME}/) missing"
    fi
fi

for file in agents/implementer.md agents/scenario-architect.md agents/spec-validator.md agents/reviewer.md agents/oracle.md; do
    if [[ -f "${FRAMEWORK_DIR}/${file}" ]]; then
        pass "${DF_HOME}/${file}"
    else
        fail "${DF_HOME}/${file} missing"
    fi
done

for script in scripts/dark-factory.sh scripts/quality-gate.sh scripts/run-scenarios.sh; do
    if [[ -f "${FRAMEWORK_DIR}/${script}" ]]; then
        if [[ -x "${FRAMEWORK_DIR}/${script}" ]]; then
            pass "${DF_HOME}/${script} (executable)"
        else
            warn "${DF_HOME}/${script} exists but is not executable"
        fi
    else
        fail "${DF_HOME}/${script} missing"
    fi
done

echo ""

# ------------------------------------------------------------------------------
# 3. Configuration
# ------------------------------------------------------------------------------
echo "[Configuration]"

CONFIG_FILE="${PROJECT_ROOT}/.dark-factory/config.sh"

if [[ -f "$CONFIG_FILE" ]]; then
    pass "config.sh exists"

    # Validate required variables are set (non-empty)
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"

    if [[ -n "${LANGUAGE:-}" ]]; then
        case "$LANGUAGE" in
            node|python|go|rust)
                pass "LANGUAGE=${LANGUAGE} (recognized)"
                ;;
            unknown)
                warn "LANGUAGE=unknown — auto-detection will be used"
                ;;
            *)
                warn "LANGUAGE=${LANGUAGE} (not a standard value: node|python|go|rust)"
                ;;
        esac
    else
        warn "LANGUAGE not set in config.sh"
    fi

    if [[ -n "${BUILD_CMD:-}" ]]; then
        pass "BUILD_CMD is configured"
    else
        warn "BUILD_CMD not set — will fall back to auto-detection"
    fi

    if [[ -n "${TEST_CMD:-}" ]]; then
        pass "TEST_CMD is configured"
    else
        warn "TEST_CMD not set — will fall back to auto-detection"
    fi

    if [[ -n "${SCENARIO_TEST_CMD:-}" ]]; then
        pass "SCENARIO_TEST_CMD is configured"
    else
        warn "SCENARIO_TEST_CMD not set — will fall back to auto-detection"
    fi
else
    warn "config.sh not found — scripts will use auto-detection"
fi

echo ""

# ------------------------------------------------------------------------------
# 4. CLAUDE.md
# ------------------------------------------------------------------------------
echo "[CLAUDE.md]"

CLAUDE_MD="${PROJECT_ROOT}/CLAUDE.md"

if [[ -f "$CLAUDE_MD" ]]; then
    pass "CLAUDE.md exists"

    if grep -qi "dark factory" "$CLAUDE_MD"; then
        pass "CLAUDE.md mentions Dark Factory"
    else
        fail "CLAUDE.md does not mention Dark Factory"
    fi

    if grep -qi "scenarios" "$CLAUDE_MD" && grep -qi "NEVER\|MUST NOT\|never read\|must not read" "$CLAUDE_MD"; then
        pass "CLAUDE.md contains information barrier rules"
    else
        warn "CLAUDE.md may be missing strong information barrier language"
    fi
else
    fail "CLAUDE.md not found"
fi

echo ""

# ------------------------------------------------------------------------------
# 5. Tool availability
# ------------------------------------------------------------------------------
echo "[Tool Availability]"

if command -v claude &>/dev/null; then
    pass "claude CLI found"
else
    fail "claude CLI not found (install: npm install -g @anthropic-ai/claude-code)"
fi

if command -v git &>/dev/null; then
    pass "git found"
else
    fail "git not found"
fi

# Language-specific tools
if [[ -n "${LANGUAGE:-}" ]]; then
    case "$LANGUAGE" in
        node)
            command -v node &>/dev/null && pass "node found" || warn "node not found"
            command -v npm &>/dev/null && pass "npm found" || warn "npm not found"
            ;;
        python)
            command -v python3 &>/dev/null && pass "python3 found" || command -v python &>/dev/null && pass "python found" || warn "python not found"
            command -v pytest &>/dev/null && pass "pytest found" || warn "pytest not found"
            ;;
        go)
            command -v go &>/dev/null && pass "go found" || warn "go not found"
            ;;
        rust)
            command -v cargo &>/dev/null && pass "cargo found" || warn "cargo not found"
            ;;
    esac
fi

echo ""

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
echo "=============================="
TOTAL=$((PASS_COUNT + FAIL_COUNT + WARN_COUNT))
echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${WARN_COUNT} warnings (${TOTAL} checks)"

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo "Status: FAIL"
    exit 1
else
    if [[ $WARN_COUNT -gt 0 ]]; then
        echo "Status: PASS (with warnings)"
    else
        echo "Status: PASS"
    fi
    exit 0
fi
