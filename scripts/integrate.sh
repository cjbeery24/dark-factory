#!/usr/bin/env bash
set -euo pipefail

# Dark Factory Integration Script
# Usage: ./scripts/integrate.sh <target-project-path> [options]
#
# Integrates the Dark Factory framework into an existing project.
#
# Options:
#   --symlink       Symlink framework files instead of copying
#   --force         Overwrite existing files
#   --language      Override auto-detected language (node|python|go|rust)
#   --dry-run       Show what would be done without making changes

# ------------------------------------------------------------------------------
# Globals
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DARK_FACTORY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

USE_SYMLINK=false
FORCE=false
LANGUAGE_OVERRIDE=""
DRY_RUN=false
TARGET=""

# ------------------------------------------------------------------------------
# Parse arguments
# ------------------------------------------------------------------------------
usage() {
    echo "Usage: $0 <target-project-path> [--symlink] [--force] [--language <lang>] [--dry-run]"
    echo ""
    echo "Integrates Dark Factory into an existing project."
    echo ""
    echo "Arguments:"
    echo "  <target-project-path>  Path to the project to integrate into"
    echo ""
    echo "Options:"
    echo "  --symlink       Symlink framework files instead of copying (portable)"
    echo "  --force         Overwrite existing files without prompting"
    echo "  --language      Override language detection (node|python|go|rust)"
    echo "  --dry-run       Preview changes without applying them"
    exit 1
}

if [[ $# -lt 1 ]]; then
    usage
fi

TARGET="$1"
shift

while [[ $# -gt 0 ]]; do
    case $1 in
        --symlink)
            USE_SYMLINK=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --language)
            LANGUAGE_OVERRIDE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Resolve target to absolute path
if [[ ! -d "$TARGET" ]]; then
    echo "ERROR: Target directory does not exist: $TARGET"
    exit 1
fi
TARGET="$(cd "$TARGET" && pwd)"

# Don't integrate into ourselves
if [[ "$TARGET" == "$DARK_FACTORY_ROOT" ]]; then
    echo "ERROR: Cannot integrate Dark Factory into itself."
    exit 1
fi

echo "============================================"
echo "  Dark Factory Integration"
echo "  Source:  ${DARK_FACTORY_ROOT}"
echo "  Target:  ${TARGET}"
echo "  Mode:    $(if $USE_SYMLINK; then echo "symlink"; else echo "copy"; fi)"
echo "  Force:   ${FORCE}"
echo "  Dry run: ${DRY_RUN}"
echo "============================================"
echo ""

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
do_action() {
    local description="$1"
    shift
    if $DRY_RUN; then
        echo "  [dry-run] $description"
    else
        echo "  $description"
        "$@"
    fi
}

make_dir() {
    local dir="$1"
    if [[ ! -d "$dir" ]]; then
        do_action "mkdir $dir" mkdir -p "$dir"
    fi
}

# Copy or symlink a file, respecting --force
install_file() {
    local src="$1"
    local dst="$2"

    if [[ -e "$dst" ]] && ! $FORCE; then
        echo "  SKIP $dst (exists, use --force to overwrite)"
        return 0
    fi

    if $USE_SYMLINK; then
        do_action "symlink $dst -> $src" ln -sf "$src" "$dst"
    else
        do_action "copy $src -> $dst" cp "$src" "$dst"
    fi
}

# Copy or symlink a directory, respecting --force
install_dir() {
    local src="$1"
    local dst="$2"

    if [[ -e "$dst" ]] && ! $FORCE; then
        echo "  SKIP $dst (exists, use --force to overwrite)"
        return 0
    fi

    if $USE_SYMLINK; then
        # L16: ln -sf cannot replace an existing non-symlink directory. Remove it first.
        if [[ -d "$dst" ]] && ! [[ -L "$dst" ]] && $FORCE; then
            do_action "remove existing directory $dst" rm -rf "$dst"
        fi
        do_action "symlink $dst -> $src" ln -sf "$src" "$dst"
    else
        if [[ -d "$dst" ]] && $FORCE; then
            do_action "remove existing $dst" rm -rf "$dst"
        fi
        do_action "copy $src -> $dst" cp -r "$src" "$dst"
    fi
}

# ------------------------------------------------------------------------------
# Step 1: Create .dark-factory directory structure
# ------------------------------------------------------------------------------
echo "[Step 1] Creating .dark-factory/ directory structure..."

make_dir "${TARGET}/.dark-factory"
make_dir "${TARGET}/.dark-factory/specs"
make_dir "${TARGET}/.dark-factory/scenarios"
make_dir "${TARGET}/.dark-factory/reviews"
make_dir "${TARGET}/.dark-factory/reports"
make_dir "${TARGET}/.dark-factory/audit"
make_dir "${TARGET}/.dark-factory/state"
make_dir "${TARGET}/.dark-factory/plans"

# Generate .gitignore for runtime artifacts
GITIGNORE_FILE="${TARGET}/.dark-factory/.gitignore"
if [[ ! -f "$GITIGNORE_FILE" ]] || $FORCE; then
    if ! $DRY_RUN; then
        cat > "$GITIGNORE_FILE" <<'GITIGNORE'
# Runtime artifacts — these are generated during pipeline runs
# and should not be committed to version control.
audit/*
state/*
reports/*
metrics.jsonl
worktrees/
shipped-scenarios/

# Keep directory structure
!audit/.gitkeep
!state/.gitkeep
!reports/.gitkeep
GITIGNORE
        echo "  Generated .dark-factory/.gitignore"
    else
        echo "  [dry-run] Would generate .dark-factory/.gitignore"
    fi
else
    echo "  SKIP .dark-factory/.gitignore (exists, use --force to overwrite)"
fi

# Create .gitkeep files for tracked empty directories
for keepdir in audit state; do
    make_dir "${TARGET}/.dark-factory/${keepdir}"
    if ! $DRY_RUN; then
        touch "${TARGET}/.dark-factory/${keepdir}/.gitkeep"
    fi
done

echo ""

# ------------------------------------------------------------------------------
# Step 2: Install framework files
# ------------------------------------------------------------------------------
echo "[Step 2] Installing framework files to dark-factory/..."

DF_TARGET="${TARGET}/dark-factory"
make_dir "$DF_TARGET"

install_dir "${DARK_FACTORY_ROOT}/agents" "${DF_TARGET}/agents"
install_dir "${DARK_FACTORY_ROOT}/templates" "${DF_TARGET}/templates"
install_dir "${DARK_FACTORY_ROOT}/docs" "${DF_TARGET}/docs"

# Install scripts individually so we can make them executable
make_dir "${DF_TARGET}/scripts"
for script in dark-factory.sh dark-factory-plan.sh quality-gate.sh run-scenarios.sh run-regression.sh validate-setup.sh validate-scenarios.sh; do
    if [[ -f "${DARK_FACTORY_ROOT}/scripts/${script}" ]]; then
        install_file "${DARK_FACTORY_ROOT}/scripts/${script}" "${DF_TARGET}/scripts/${script}"
        if ! $DRY_RUN && [[ -f "${DF_TARGET}/scripts/${script}" ]]; then
            chmod +x "${DF_TARGET}/scripts/${script}"
        fi
    fi
done

# Install shared library modules (required by all scripts)
if [[ -d "${DARK_FACTORY_ROOT}/scripts/lib" ]]; then
    install_dir "${DARK_FACTORY_ROOT}/scripts/lib" "${DF_TARGET}/scripts/lib"
fi

# Copy AGENT.md if it exists
if [[ -f "${DARK_FACTORY_ROOT}/AGENT.md" ]]; then
    install_file "${DARK_FACTORY_ROOT}/AGENT.md" "${DF_TARGET}/AGENT.md"
fi

# Install Claude Code skills (slash commands)
SKILLS_SRC="${DARK_FACTORY_ROOT}/.claude/skills"
SKILLS_DST="${TARGET}/.claude/skills"
if [[ -d "$SKILLS_SRC" ]]; then
    echo ""
    echo "[Step 2b] Installing Claude Code skills (slash commands)..."
    make_dir "${TARGET}/.claude"
    for skill_dir in "$SKILLS_SRC"/*/; do
        skill_name="$(basename "$skill_dir")"
        make_dir "${SKILLS_DST}/${skill_name}"
        install_file "${skill_dir}SKILL.md" "${SKILLS_DST}/${skill_name}/SKILL.md"
    done
fi

echo ""

# ------------------------------------------------------------------------------
# Step 3: Detect language and generate config
# ------------------------------------------------------------------------------
echo "[Step 3] Detecting language and generating config..."

detect_language() {
    local dir="$1"
    if [[ -f "${dir}/package.json" ]]; then
        echo "node"
    elif [[ -f "${dir}/pyproject.toml" ]] || [[ -f "${dir}/setup.py" ]] || [[ -f "${dir}/requirements.txt" ]]; then
        echo "python"
    elif [[ -f "${dir}/go.mod" ]]; then
        echo "go"
    elif [[ -f "${dir}/Cargo.toml" ]]; then
        echo "rust"
    else
        echo "unknown"
    fi
}

if [[ -n "$LANGUAGE_OVERRIDE" ]]; then
    LANGUAGE="$LANGUAGE_OVERRIDE"
    echo "  Using override: ${LANGUAGE}"
else
    LANGUAGE=$(detect_language "$TARGET")
    echo "  Auto-detected: ${LANGUAGE}"
fi

# Set language-specific defaults
case "$LANGUAGE" in
    node)
        SOURCE_DIR="src"
        TEST_DIR="tests"
        BUILD_CMD="npm run build"
        LINT_CMD="npm run lint"
        TYPECHECK_CMD="npx tsc --noEmit"
        TEST_CMD="npm test"
        SCENARIO_TEST_CMD="npx vitest run {SCENARIOS_DIR}"
        MUTATION_CMD=""
        ;;
    python)
        SOURCE_DIR="src"
        TEST_DIR="tests"
        BUILD_CMD=""
        LINT_CMD="ruff check ."
        TYPECHECK_CMD="mypy ."
        TEST_CMD="pytest tests/ -v"
        SCENARIO_TEST_CMD="pytest {SCENARIOS_DIR} -v"
        MUTATION_CMD=""
        ;;
    go)
        SOURCE_DIR="."
        TEST_DIR="."
        BUILD_CMD="go build ./..."
        LINT_CMD="golangci-lint run"
        TYPECHECK_CMD=""
        TEST_CMD="go test ./... -v"
        # L5: Go ignores dotted directories — go test cannot reach .dark-factory/scenarios/.
        # Set a custom runner in config.sh that copies/symlinks scenario files into a valid Go package path.
        SCENARIO_TEST_CMD=""
        MUTATION_CMD=""
        ;;
    rust)
        SOURCE_DIR="src"
        TEST_DIR="tests"
        BUILD_CMD="cargo build"
        LINT_CMD="cargo clippy -- -D warnings"
        TYPECHECK_CMD=""
        TEST_CMD="cargo test"
        # L6: cargo test --test runs tests/*, not .dark-factory/scenarios/.
        # Set a custom runner in config.sh for Rust scenarios.
        SCENARIO_TEST_CMD=""
        MUTATION_CMD=""
        ;;
    *)
        SOURCE_DIR="src"
        TEST_DIR="tests"
        BUILD_CMD=""
        LINT_CMD=""
        TYPECHECK_CMD=""
        TEST_CMD=""
        SCENARIO_TEST_CMD=""
        MUTATION_CMD=""
        ;;
esac

# Timeout defaults (same for all languages)
GATE_TIMEOUT=300
SCENARIO_TIMEOUT=600
AGENT_MAX_TURNS=200
AGENT_TIMEOUT=600

# Scenario quality validation defaults
SCENARIO_VALIDATION="strict"
SCENARIO_MIN_FILES=3
SCENARIO_MIN_ASSERTIONS_PER_FILE=1

# Docker isolation defaults
SOURCE_DIRS="$SOURCE_DIR"
AGENT_MEMORY="4g"
AGENT_CPUS="4"
DOCKER_NETWORK="df-pipeline"

# Autonomy level default
AUTONOMY_LEVEL="3"

# Deployment defaults (empty = disabled)
DEPLOY_CMD=""
DEPLOY_TEARDOWN_CMD=""
DEPLOY_BASE_URL=""
DEPLOY_HEALTH_TIMEOUT="60"
DEPLOY_HEALTH_ENDPOINT="/health"

# Regression testing defaults
REGRESSION_ON_SHIP="false"
REGRESSION_TIMEOUT="300"

# Phase 4: Concurrent execution defaults
MAX_CONCURRENT_SPECS="3"
PLAN_TOKEN_BUDGET="0"
CONFLICT_STRATEGY="abort"
WORKTREE_CLEANUP_ON_SHIP="true"
WORKTREE_CLEANUP_ON_FAIL="false"

# CI/CD closed-loop defaults
AUTO_PR="false"
GIT_REMOTE="origin"
GIT_BASE_BRANCH="main"
MERGE_QUEUE_ENABLED="false"
GITOPS_SKIP_HOOKS="false"
IMPLEMENTER_WRITE_DIRS=""

# Notification defaults
NOTIFY_SLACK_WEBHOOK_URL=""
NOTIFY_WEBHOOK_URL=""

CONFIG_FILE="${TARGET}/.dark-factory/config.sh"

if [[ -f "$CONFIG_FILE" ]] && ! $FORCE; then
    echo "  SKIP config.sh (exists, use --force to overwrite)"
else
    if ! $DRY_RUN; then
        # Generate config from template
        sed \
            -e "s|__LANGUAGE__|${LANGUAGE}|g" \
            -e "s|__SOURCE_DIR__|${SOURCE_DIR}|g" \
            -e "s|__TEST_DIR__|${TEST_DIR}|g" \
            -e "s|__BUILD_CMD__|${BUILD_CMD}|g" \
            -e "s|__LINT_CMD__|${LINT_CMD}|g" \
            -e "s|__TYPECHECK_CMD__|${TYPECHECK_CMD}|g" \
            -e "s|__TEST_CMD__|${TEST_CMD}|g" \
            -e "s|__SCENARIO_TEST_CMD__|${SCENARIO_TEST_CMD}|g" \
            -e "s|__MUTATION_CMD__|${MUTATION_CMD}|g" \
            -e "s|__GATE_TIMEOUT__|${GATE_TIMEOUT}|g" \
            -e "s|__SCENARIO_TIMEOUT__|${SCENARIO_TIMEOUT}|g" \
            -e "s|__AGENT_MAX_TURNS__|${AGENT_MAX_TURNS}|g" \
            -e "s|__AGENT_TIMEOUT__|${AGENT_TIMEOUT}|g" \
            -e "s|__SCENARIO_VALIDATION__|${SCENARIO_VALIDATION}|g" \
            -e "s|__SCENARIO_MIN_FILES__|${SCENARIO_MIN_FILES}|g" \
            -e "s|__SCENARIO_MIN_ASSERTIONS_PER_FILE__|${SCENARIO_MIN_ASSERTIONS_PER_FILE}|g" \
            -e "s|__SOURCE_DIRS__|${SOURCE_DIRS}|g" \
            -e "s|__AGENT_MEMORY__|${AGENT_MEMORY}|g" \
            -e "s|__AGENT_CPUS__|${AGENT_CPUS}|g" \
            -e "s|__DOCKER_NETWORK__|${DOCKER_NETWORK}|g" \
            -e "s|__DEPLOY_CMD__|${DEPLOY_CMD}|g" \
            -e "s|__DEPLOY_TEARDOWN_CMD__|${DEPLOY_TEARDOWN_CMD}|g" \
            -e "s|__DEPLOY_BASE_URL__|${DEPLOY_BASE_URL}|g" \
            -e "s|__DEPLOY_HEALTH_TIMEOUT__|${DEPLOY_HEALTH_TIMEOUT}|g" \
            -e "s|__DEPLOY_HEALTH_ENDPOINT__|${DEPLOY_HEALTH_ENDPOINT}|g" \
            -e "s|__REGRESSION_ON_SHIP__|${REGRESSION_ON_SHIP}|g" \
            -e "s|__REGRESSION_TIMEOUT__|${REGRESSION_TIMEOUT}|g" \
            -e "s|__MAX_CONCURRENT_SPECS__|${MAX_CONCURRENT_SPECS}|g" \
            -e "s|__PLAN_TOKEN_BUDGET__|${PLAN_TOKEN_BUDGET}|g" \
            -e "s|__CONFLICT_STRATEGY__|${CONFLICT_STRATEGY}|g" \
            -e "s|__WORKTREE_CLEANUP_ON_SHIP__|${WORKTREE_CLEANUP_ON_SHIP}|g" \
            -e "s|__WORKTREE_CLEANUP_ON_FAIL__|${WORKTREE_CLEANUP_ON_FAIL}|g" \
            -e "s|__AUTONOMY_LEVEL__|${AUTONOMY_LEVEL}|g" \
            -e "s|__AUTO_PR__|${AUTO_PR}|g" \
            -e "s|__GIT_REMOTE__|${GIT_REMOTE}|g" \
            -e "s|__GIT_BASE_BRANCH__|${GIT_BASE_BRANCH}|g" \
            -e "s|__MERGE_QUEUE_ENABLED__|${MERGE_QUEUE_ENABLED}|g" \
            -e "s|__GITOPS_SKIP_HOOKS__|${GITOPS_SKIP_HOOKS}|g" \
            -e "s|__IMPLEMENTER_WRITE_DIRS__|${IMPLEMENTER_WRITE_DIRS}|g" \
            -e "s|__NOTIFY_SLACK_WEBHOOK_URL__|${NOTIFY_SLACK_WEBHOOK_URL}|g" \
            -e "s|__NOTIFY_WEBHOOK_URL__|${NOTIFY_WEBHOOK_URL}|g" \
            -e "s|__DARK_FACTORY_HOME__|dark-factory|g" \
            "${DARK_FACTORY_ROOT}/templates/config.sh.template" > "$CONFIG_FILE"
        echo "  Generated config.sh for ${LANGUAGE}"
    else
        echo "  [dry-run] Would generate config.sh for ${LANGUAGE}"
    fi
fi

# Install Docker infrastructure
make_dir "${DF_TARGET}/docker"
for dfile in Dockerfile.base Dockerfile.node Dockerfile.python Dockerfile.go Dockerfile.rust build-images.sh docker-compose.yml; do
    if [[ -f "${DARK_FACTORY_ROOT}/docker/${dfile}" ]]; then
        install_file "${DARK_FACTORY_ROOT}/docker/${dfile}" "${DF_TARGET}/docker/${dfile}"
        if ! $DRY_RUN && [[ "$dfile" == "build-images.sh" ]] && [[ -f "${DF_TARGET}/docker/${dfile}" ]]; then
            chmod +x "${DF_TARGET}/docker/${dfile}"
        fi
    fi
done

echo ""

# ------------------------------------------------------------------------------
# Step 3b: Build Docker images
# ------------------------------------------------------------------------------
echo "[Step 3b] Building Dark Factory Docker images for ${LANGUAGE}..."

if ! $DRY_RUN; then
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
        bash "${DF_TARGET}/docker/build-images.sh" "${LANGUAGE}" || {
            echo "  WARN: Docker image build failed. You can build later with:"
            echo "        bash ${DF_TARGET}/docker/build-images.sh ${LANGUAGE}"
        }
        docker network inspect df-pipeline &>/dev/null 2>&1 || \
            docker network create --driver bridge df-pipeline || true
    else
        echo "  SKIP Docker build (Docker not available)"
        echo "  Install Docker and run: bash ${DF_TARGET}/docker/build-images.sh ${LANGUAGE}"
    fi
else
    echo "  [dry-run] Would build Docker images for ${LANGUAGE}"
fi

echo ""

# ------------------------------------------------------------------------------
# Step 4: Update CLAUDE.md
# ------------------------------------------------------------------------------
echo "[Step 4] Updating CLAUDE.md..."

CLAUDE_MD="${TARGET}/CLAUDE.md"
ADDITION_TEMPLATE="${DARK_FACTORY_ROOT}/templates/project-claude-addition.md"

if [[ -f "$CLAUDE_MD" ]]; then
    # Check if already integrated
    if grep -qi "dark factory" "$CLAUDE_MD" && ! $FORCE; then
        echo "  SKIP CLAUDE.md (already contains Dark Factory section)"
    else
        if ! $DRY_RUN; then
            # Prepare the addition with source dir substituted
            ADDITION=$(sed "s|__SOURCE_DIR__|${SOURCE_DIR}|g" "$ADDITION_TEMPLATE")
            echo "" >> "$CLAUDE_MD"
            echo "$ADDITION" >> "$CLAUDE_MD"
            echo "  Appended Dark Factory section to CLAUDE.md"
        else
            echo "  [dry-run] Would append Dark Factory section to CLAUDE.md"
        fi
    fi
else
    if ! $DRY_RUN; then
        # Create new CLAUDE.md with the addition
        sed "s|__SOURCE_DIR__|${SOURCE_DIR}|g" "$ADDITION_TEMPLATE" > "$CLAUDE_MD"
        echo "  Created CLAUDE.md with Dark Factory section"
    else
        echo "  [dry-run] Would create CLAUDE.md with Dark Factory section"
    fi
fi

echo ""

# ------------------------------------------------------------------------------
# Step 4b: Generate AGENTS.md (optional)
# ------------------------------------------------------------------------------
AGENTS_MD="${TARGET}/AGENTS.md"
AGENTS_MD_TEMPLATE="${DARK_FACTORY_ROOT}/templates/agents-md.md"

if [[ -f "$AGENTS_MD_TEMPLATE" ]]; then
    if [[ -f "$AGENTS_MD" ]] && ! $FORCE; then
        echo "[Step 4b] SKIP AGENTS.md (exists, use --force to overwrite)"
    else
        echo "[Step 4b] Generating AGENTS.md template..."
        if ! $DRY_RUN; then
            sed \
                -e "s|__LANGUAGE__|${LANGUAGE}|g" \
                -e "s|__SOURCE_DIR__|${SOURCE_DIR}|g" \
                -e "s|__TEST_DIR__|${TEST_DIR}|g" \
                "$AGENTS_MD_TEMPLATE" > "$AGENTS_MD"
            echo "  Generated AGENTS.md — customize it with your project's specifics"
        else
            echo "  [dry-run] Would generate AGENTS.md"
        fi
    fi
fi

echo ""

# ------------------------------------------------------------------------------
# Step 5: Validate
# ------------------------------------------------------------------------------
echo "[Step 5] Validating setup..."
echo ""

if ! $DRY_RUN; then
    VALIDATE_SCRIPT="${DF_TARGET}/scripts/validate-setup.sh"
    if [[ -x "$VALIDATE_SCRIPT" ]]; then
        (cd "$TARGET" && bash "$VALIDATE_SCRIPT" .) || true
    else
        echo "  WARN: validate-setup.sh not found or not executable"
    fi
else
    echo "  [dry-run] Would run validate-setup.sh"
fi

echo ""
echo "============================================"
echo "  Integration complete!"
echo ""
echo "  Next steps:"
echo "    1. Review .dark-factory/config.sh and adjust commands"
echo "    2. Review CLAUDE.md Dark Factory section"
echo "    3. Write a spec:  cp dark-factory/templates/feature-spec.md .dark-factory/specs/my-feature.md"
echo "    4. Run pipeline:  ./dark-factory/scripts/dark-factory.sh my-feature"
echo ""
echo "  Docs: dark-factory/docs/INTEGRATION.md"
echo "============================================"
