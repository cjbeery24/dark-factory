#!/usr/bin/env bash
set -euo pipefail

# Dark Factory Setup Validator
# Usage: ./scripts/validate-setup.sh [project-root]
#
# Checks that Dark Factory is correctly integrated into a project.
# Exit code 0 = all checks pass, 1 = any check failed.

PROJECT_ROOT="${1:-.}"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Library bootstrap ---
source "${SCRIPT_DIR}/lib/common.sh"

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

for dir in .dark-factory .dark-factory/specs .dark-factory/scenarios .dark-factory/reviews .dark-factory/reports .dark-factory/audit .dark-factory/state .dark-factory/plans; do
    if [[ -d "${PROJECT_ROOT}/${dir}" ]]; then
        pass "${dir}/ exists"
    else
        if [[ "$dir" == ".dark-factory/audit" ]] || [[ "$dir" == ".dark-factory/state" ]] || [[ "$dir" == ".dark-factory/plans" ]]; then
            warn "${dir}/ missing (will be created on first pipeline run)"
        else
            fail "${dir}/ missing"
        fi
    fi
done

echo ""

# ------------------------------------------------------------------------------
# 2. Framework files
# ------------------------------------------------------------------------------
echo "[Framework Files]"

# Determine framework home
if [[ -f "${PROJECT_ROOT}/.dark-factory/config.sh" ]]; then
    if validate_config "${PROJECT_ROOT}/.dark-factory/config.sh"; then
        # shellcheck source=/dev/null
        source "${PROJECT_ROOT}/.dark-factory/config.sh"
        pass "config.sh passes structural validation"
    else
        fail "config.sh contains non-assignment lines (invalid structure)"
    fi
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

for script in scripts/dark-factory.sh scripts/quality-gate.sh scripts/run-scenarios.sh scripts/validate-scenarios.sh; do
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
# 3. Information Barrier Verification (P0-1)
# ------------------------------------------------------------------------------
echo "[Information Barriers]"

DF_MAIN="${FRAMEWORK_DIR}/scripts/dark-factory.sh"
DF_SCRIPTS="${FRAMEWORK_DIR}/scripts"
if [[ -f "$DF_MAIN" ]]; then
    # Search across dark-factory.sh and lib/ for barrier definitions and usage
    # After modular refactoring, barriers may live in lib/barriers.sh and lib/docker.sh

    # Check Implementer deny list (definition in barriers.sh or dark-factory.sh, usage in docker.sh or dark-factory.sh)
    if grep -rq "IMPLEMENTER_DENY" "$DF_SCRIPTS" 2>/dev/null && grep -rq "disallowedTools.*IMPLEMENTER_DENY" "$DF_SCRIPTS" 2>/dev/null; then
        pass "Implementer --disallowedTools configured"
    else
        fail "Implementer --disallowedTools not found in scripts/"
    fi

    # Check Scenario Architect deny list
    if grep -rq "SCENARIO_ARCH_DENY" "$DF_SCRIPTS" 2>/dev/null && grep -rq "disallowedTools.*SCENARIO_ARCH_DENY" "$DF_SCRIPTS" 2>/dev/null; then
        pass "Scenario Architect --disallowedTools configured"
    else
        fail "Scenario Architect --disallowedTools not found in scripts/"
    fi

    # Check system prompts for both agents
    if grep -rq "append-system-prompt.*IMPLEMENTER_BARRIER" "$DF_SCRIPTS" 2>/dev/null; then
        pass "Implementer --append-system-prompt barrier configured"
    else
        fail "Implementer --append-system-prompt barrier not found"
    fi

    if grep -rq "append-system-prompt.*SCENARIO_ARCH_BARRIER" "$DF_SCRIPTS" 2>/dev/null; then
        pass "Scenario Architect --append-system-prompt barrier configured"
    else
        fail "Scenario Architect --append-system-prompt barrier not found"
    fi

    # Check audit logging
    if grep -rq "audit_log" "$DF_SCRIPTS" 2>/dev/null && grep -rq "audit_check_output" "$DF_SCRIPTS" 2>/dev/null; then
        pass "Audit logging configured"
    else
        warn "Audit logging not found in scripts/"
    fi
else
    warn "Cannot verify barriers — dark-factory.sh not found at expected location"
fi

echo ""

# ------------------------------------------------------------------------------
# 4. Configuration
# ------------------------------------------------------------------------------
echo "[Configuration]"

CONFIG_FILE="${PROJECT_ROOT}/.dark-factory/config.sh"

if [[ -f "$CONFIG_FILE" ]]; then
    pass "config.sh exists"

    # Validate config structure (P0-2)
    if validate_config "$CONFIG_FILE"; then
        pass "config.sh structural validation passes"
    else
        fail "config.sh contains non-assignment lines"
    fi

    # Re-source for variable checks (already sourced above, but be safe)
    # shellcheck source=/dev/null
    source "$CONFIG_FILE" 2>/dev/null || true

    # Post-source metacharacter check (P0-2)
    local_check_failed=false
    for var in LANGUAGE SOURCE_DIR TEST_DIR DARK_FACTORY_HOME; do
        val="${!var:-}"
        if [[ "$val" =~ [\$\`\(] ]]; then
            fail "Config variable $var contains shell metacharacters: $val"
            local_check_failed=true
        fi
    done
    if [[ "$local_check_failed" == "false" ]]; then
        pass "Config variables pass metacharacter check"
    fi

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

    if [[ -n "${GATE_TIMEOUT:-}" ]] && [[ "${GATE_TIMEOUT}" -gt 0 ]]; then
        pass "GATE_TIMEOUT=${GATE_TIMEOUT}s"
    else
        warn "GATE_TIMEOUT not set — no time limits on gate commands"
    fi
else
    warn "config.sh not found — scripts will use auto-detection"
fi

echo ""

# ------------------------------------------------------------------------------
# 5. CLAUDE.md
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
# 6. Docker isolation
# ------------------------------------------------------------------------------
echo "[Docker Isolation]"

if command -v docker &>/dev/null; then
    pass "Docker installed"

    if docker info &>/dev/null 2>&1; then
        pass "Docker daemon running"
    else
        fail "Docker daemon not running — start Docker Desktop or the Docker service"
    fi

    LANG_TO_CHECK="${LANGUAGE:-node}"
    if docker image inspect "dark-factory-${LANG_TO_CHECK}" &>/dev/null 2>&1; then
        pass "Docker image dark-factory-${LANG_TO_CHECK} found"
    else
        fail "Docker image dark-factory-${LANG_TO_CHECK} not built — run: bash docker/build-images.sh ${LANG_TO_CHECK}"
    fi

    if docker image inspect "dark-factory-base" &>/dev/null 2>&1; then
        pass "Docker image dark-factory-base found"
    else
        fail "Docker image dark-factory-base not built — run: bash docker/build-images.sh"
    fi

    if docker network inspect "${DOCKER_NETWORK:-df-pipeline}" &>/dev/null 2>&1; then
        pass "Docker network ${DOCKER_NETWORK:-df-pipeline} exists"
    else
        warn "Docker network ${DOCKER_NETWORK:-df-pipeline} not found — will be created on first pipeline run"
    fi
else
    fail "Docker not installed — required for information barrier enforcement"
    echo "       Install Docker: https://docs.docker.com/get-docker/"
fi

echo ""

# ------------------------------------------------------------------------------
# 7. Tool availability
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

# Timeout command
if command -v timeout &>/dev/null; then
    pass "timeout command found"
elif command -v gtimeout &>/dev/null; then
    pass "gtimeout command found (macOS coreutils)"
else
    warn "timeout/gtimeout not found — no execution time limits available"
fi

# Language-specific tools (note: these are inside Docker containers now, host availability is optional)
if [[ -n "${LANGUAGE:-}" ]]; then
    case "$LANGUAGE" in
        node)
            command -v node &>/dev/null && pass "node found (host)" || warn "node not found on host (available inside Docker container)"
            command -v npm &>/dev/null && pass "npm found (host)" || warn "npm not found on host (available inside Docker container)"
            ;;
        python)
            command -v python3 &>/dev/null && pass "python3 found (host)" || command -v python &>/dev/null && pass "python found (host)" || warn "python not found on host (available inside Docker container)"
            command -v pytest &>/dev/null && pass "pytest found (host)" || warn "pytest not found on host (available inside Docker container)"
            ;;
        go)
            command -v go &>/dev/null && pass "go found (host)" || warn "go not found on host (available inside Docker container)"
            ;;
        rust)
            command -v cargo &>/dev/null && pass "cargo found (host)" || warn "cargo not found on host (available inside Docker container)"
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
