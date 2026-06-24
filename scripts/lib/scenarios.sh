#!/usr/bin/env bash
# scripts/lib/scenarios.sh — Scenario file detection and spec validation
#
# Provides: has_scenario_files, detect_scenario_formats, validate_scenario_files,
#           validate_spec_result

[[ -n "${_DF_LIB_SCENARIOS_LOADED:-}" ]] && return 0
_DF_LIB_SCENARIOS_LOADED=1

# ==============================================================================
# Format pattern constants
#
# STRUCTURAL CONSTRAINT: Each array must contain only flat -name/-iname predicates
# joined by -o.  They are spliced bare into `find \( ... \)` groups; any grouping
# operators (\(, \), -prune, etc.) inside an array will break the outer expression.
# ==============================================================================

# Executable test files (pytest / jest / go / rust).
# NOTE: *.rs is intentionally broad — Rust tests live in the same file as source.
# Callers MUST pass the scenarios/ subdirectory, never the full source tree.
_DF_FORMAT_PATTERNS_EXECUTABLE=( -name '*.test.*' -o -name '*.spec.*' -o -name 'test_*.py' -o -name '*_test.py' -o -name '*_test.go' -o -name '*.rs' )
# Format A (HTTP behavioral scenarios, *.scenarios.md) retired in Phase 4.6b.1a.
# Pattern kept as an empty placeholder so downstream find expressions that splice
# all three arrays together continue to parse without surgery at every call site.
_DF_FORMAT_PATTERNS_HTTP=( -false )
# Browser journey scenarios (Format B)
_DF_FORMAT_PATTERNS_JOURNEY=( -name '*.journey.md' )

# Sentinel filename constant (must match darkfactory/scenarios/detection.py).
_DF_SENTINEL_FILENAME="SCENARIOS-NOT-APPLICABLE.md"

# ==============================================================================
# Detection helpers
# ==============================================================================

# has_sentinel_file <dir>
#   Returns 0 if the directory contains a SCENARIOS-NOT-APPLICABLE.md sentinel.
has_sentinel_file() {
    [[ -f "${1}/${_DF_SENTINEL_FILENAME}" ]]
}

# has_scenario_files <dir>
#   Returns 0 if the directory contains any recognized test or scenario files.
has_scenario_files() {
    local dir="$1"
    [[ -n "$(find "$dir" -type f \( \
        "${_DF_FORMAT_PATTERNS_EXECUTABLE[@]}" -o \
        "${_DF_FORMAT_PATTERNS_JOURNEY[@]}" \
    \) -print -quit 2>/dev/null)" ]]
}

# detect_scenario_formats <dir>
#   Sets globals: _DF_HAS_EXECUTABLE, _DF_HAS_HTTP, _DF_HAS_JOURNEY (true/false).
#   _DF_HAS_HTTP is always false — Format A (*.scenarios.md) retired in Phase 4.6b.1a.
detect_scenario_formats() {
    local dir="$1"
    _DF_HAS_EXECUTABLE=false
    _DF_HAS_HTTP=false
    _DF_HAS_JOURNEY=false
    [[ -n "$(find "$dir" -type f \( "${_DF_FORMAT_PATTERNS_EXECUTABLE[@]}" \) -print -quit 2>/dev/null)" ]] && _DF_HAS_EXECUTABLE=true || true
    [[ -n "$(find "$dir" -type f \( "${_DF_FORMAT_PATTERNS_JOURNEY[@]}" \) -print -quit 2>/dev/null)" ]] && _DF_HAS_JOURNEY=true || true
}

# detect_executable_languages <dir>
#   Sets globals: _DF_HAS_TS_TESTS, _DF_HAS_PY_TESTS (true/false)
#   Call after detect_scenario_formats confirms _DF_HAS_EXECUTABLE=true.
#   Uses the same broad *.test.* / *.spec.* glob as _DF_FORMAT_PATTERNS_EXECUTABLE
#   so all JS/TS variants (.ts, .tsx, .mts, .js, .jsx, .mjs, .spec.tsx, etc.) are
#   captured without having to enumerate every extension.
detect_executable_languages() {
    local dir="$1"
    _DF_HAS_TS_TESTS=false
    _DF_HAS_PY_TESTS=false
    [[ -n "$(find "$dir" -type f \( -name '*.test.*' -o -name '*.spec.*' \) ! -name '*.py' -print -quit 2>/dev/null)" ]] && _DF_HAS_TS_TESTS=true || true
    [[ -n "$(find "$dir" -type f \( -name 'test_*.py' -o -name '*_test.py' \) -print -quit 2>/dev/null)" ]] && _DF_HAS_PY_TESTS=true || true
}

# _DF_NODE_SCENARIO_RUNNER — cached by _df_resolve_node_runner.
# Values: pnpm_vitest | workspace_vitest | npx_vitest | pnpm_jest | npx_jest | none
_DF_NODE_SCENARIO_RUNNER=""
# _DF_VITEST_BIN — set when _DF_NODE_SCENARIO_RUNNER=workspace_vitest.
# Points to the vitest binary found in a workspace package's node_modules/.bin/.
_DF_VITEST_BIN=""
# _DF_VITEST_CONFIG_FLAG — set by _df_resolve_vitest_config.
# Contains '--config <path>' or '' (empty) if no manifest or no vitest_config declared.
_DF_VITEST_CONFIG_FLAG=""
# _DF_WORKSPACE_DIR — set by _df_resolve_vitest_config when vitest_config is inside
# a subdirectory (monorepo workspace). e.g., "apps/api" or "" for non-monorepo.
_DF_WORKSPACE_DIR=""
# _DF_WORKSPACE_UP — traversal from workspace dir back to project root.
# e.g., "../../" for depth-2 workspace "apps/api", "../" for depth-1. Empty when
# _DF_WORKSPACE_DIR is empty.
_DF_WORKSPACE_UP=""
# _DF_VITEST_PROJECT_CONFIG / _DF_SETUP_INJECT — set by _df_resolve_vitest_config;
# consumed by _df_run_holdout_gate to build a gate-owned holdout wrapper config
# (correct globalSetup/server-boot + DB reset) without re-parsing the manifest.
_DF_VITEST_PROJECT_CONFIG=""
_DF_SETUP_INJECT=""
# _DF_WRAPPER_FILE — absolute-or-project-relative path to the workspace wrapper
# config file generated by _df_resolve_vitest_config. Used by the quality-gate
# EXIT trap to clean up the wrapper on any exit path (including signals and
# unexpected failures). Empty when workspace mode is inactive.
_DF_WRAPPER_FILE=""

# _df_resolve_node_runner
#   Determines which vitest/jest binary is available and caches the result in
#   _DF_NODE_SCENARIO_RUNNER. Prefer direct binary on disk (immune to pnpm exec
#   PATH instability in tmpfs containers) over pnpm exec, over npx. Prefer
#   vitest over jest. Cached after first call to avoid repeated subprocess
#   spawning in multi-run per-file loops.
#
#   Workspace vitest detection: in pnpm monorepos vitest is often only installed
#   as a devDependency of a workspace package (e.g. apps/web), not at the root.
#   pnpm exec only looks at the root package's node_modules/.bin, so the standard
#   probe fails. The workspace_vitest branch finds the binary in a workspace
#   package's node_modules/.bin/ and stores it in _DF_VITEST_BIN for direct use.
_df_resolve_node_runner() {
    if [[ -n "$_DF_NODE_SCENARIO_RUNNER" ]]; then
        return 0
    fi
    # Prefer direct binary on disk — immune to pnpm exec PATH instability
    # in containers with tmpfs overlays on node_modules/.
    _DF_VITEST_BIN="$(find . -maxdepth 6 -name vitest -path "*/node_modules/.bin/vitest" \
            ! -path "./.dark-factory/*" -print -quit 2>/dev/null)"
    if [[ -n "$_DF_VITEST_BIN" ]] && [[ -x "$_DF_VITEST_BIN" ]] && \
       "$_DF_VITEST_BIN" --version >/dev/null 2>&1; then
        _DF_NODE_SCENARIO_RUNNER="workspace_vitest"
    elif pnpm exec vitest --version >/dev/null 2>&1; then
        _DF_NODE_SCENARIO_RUNNER="pnpm_vitest"
    elif npx vitest --version >/dev/null 2>&1; then
        _DF_NODE_SCENARIO_RUNNER="npx_vitest"
    elif pnpm exec jest --version >/dev/null 2>&1; then
        _DF_NODE_SCENARIO_RUNNER="pnpm_jest"
    elif npx jest --version >/dev/null 2>&1; then
        _DF_NODE_SCENARIO_RUNNER="npx_jest"
    else
        _DF_NODE_SCENARIO_RUNNER="none"
    fi
    echo "  Node scenario runner: ${_DF_NODE_SCENARIO_RUNNER}${_DF_VITEST_BIN:+ (${_DF_VITEST_BIN})}"
}

# _df_resolve_vitest_config <spec_name>
#   Reads test_integration.vitest_config from the architect manifest and builds
#   _DF_VITEST_CONFIG_FLAG. Uses grep/sed — no Python/PyYAML in the gate container.
#   Cached after first call.
#
#   Workspace detection: when vitest_config is inside a subdirectory (e.g.,
#   apps/api/vitest.config.ts), sets _DF_WORKSPACE_DIR and _DF_WORKSPACE_UP so
#   vitest runs from the workspace directory. This makes workspace-scoped
#   dependencies (e.g., drizzle-orm in apps/api/node_modules/) resolvable.
#
#   Wrapper generation: ALWAYS generates a wrapper when workspace mode is active
#   (include patterns must be relative to the workspace CWD). For non-workspace
#   projects, generates a wrapper only when the vitest config has include patterns
#   that would exclude .dark-factory/scenarios/.
_df_resolve_vitest_config() {
    # Already resolved — return cached value.
    if [[ -n "${_DF_VITEST_CONFIG_RESOLVED:-}" ]]; then
        return 0
    fi
    _DF_VITEST_CONFIG_RESOLVED=1
    _DF_VITEST_CONFIG_FLAG=""
    _DF_WORKSPACE_DIR=""
    _DF_WORKSPACE_UP=""
    _DF_WRAPPER_FILE=""

    local spec_name="${1:-${SPEC_NAME:-}}"
    if [[ -z "$spec_name" ]]; then
        return 0
    fi

    local manifest_file=".dark-factory/architecture/${spec_name}-manifest.yaml"
    if [[ ! -f "$manifest_file" ]]; then
        return 0
    fi

    # Extract vitest_config value. The field sits inside the test_integration
    # block — grab the first match of the key, strip key name and quotes.
    local vitest_config
    vitest_config=$(grep -m1 '^[[:space:]]*vitest_config:' "$manifest_file" \
        | sed 's/.*vitest_config:[[:space:]]*//' \
        | sed 's/^["'"'"']//' \
        | sed 's/["'"'"']$//' \
        || true)

    if [[ -z "$vitest_config" ]]; then
        return 0
    fi

    # Only use it if the file actually exists on disk.
    if [[ ! -f "$vitest_config" ]]; then
        echo "  WARNING: Manifest declares vitest_config='${vitest_config}' but the file does not exist. Ignoring."
        return 0
    fi

    echo "  Vitest config from manifest: ${vitest_config}"

    # --- Per-test DB reset setupFile (postgres adapter, Sprint 28) ---
    # When the postgres adapter staged its reset setupFile (only for
    # postgres-active specs, via spec_triggers), inject it into the generated
    # vitest wrapper's setupFiles so every scenario starts from a clean,
    # identity-reset DB. ABSOLUTE path → survives the `cd <workspace>` the gate
    # does before running vitest. setup_inject is appended to the `merged` config
    # in each wrapper branch; empty (no-op, byte-identical wrappers) for every
    # non-postgres spec, confining all risk to postgres-active runs.
    local proj_root
    proj_root="$(pwd)"
    # Sprint 29: the reset stages under _df_setup/ (sibling of _contracts/), gated
    # by the orchestrator on a DATABASE-active signal. Inject it ONLY when the
    # staged file exists AND DATABASE_URL is set at run time — the file's presence
    # and the DB being live are decided by different processes at different times,
    # so a stray staged file without a live DB must be a no-op, not a per-test
    # throw. (Same DATABASE_URL predicate the gate's DB-driver preflight uses.)
    local reset_setup_abs="${proj_root}/.dark-factory/scenarios/${spec_name}/_df_setup/postgres/_df_reset.setup.ts"
    local setup_inject=""
    if [[ -f "$reset_setup_abs" && -n "${DATABASE_URL:-}" ]]; then
        # Three lines appended to the `merged` config:
        # 1. Normalize setupFiles to an array — vitest also accepts the STRING
        #    form (setupFiles: './x.ts'); spreading a string shatters it into
        #    single-character paths. Array-or-wrap before appending.
        # 2. Append the reset setupFile (additive — preserve project setupFiles).
        # 3. Disable file parallelism — vitest runs test FILES in parallel by
        #    default, so a per-test TRUNCATE in one file would race another
        #    file's test mid-flight against the shared DB. Sequential files keep
        #    the per-test reset deterministic.
        setup_inject="const __df_setup = Array.isArray(merged.test.setupFiles) ? merged.test.setupFiles : (merged.test.setupFiles ? [merged.test.setupFiles] : [])
merged.test.setupFiles = [...__df_setup, '${reset_setup_abs}']
merged.test.fileParallelism = false"
        echo "  Postgres per-test reset setupFile detected: ${reset_setup_abs}"
    fi

    # Expose the resolved project config path + the (postgres) setup-injection
    # snippet so the blind-holdout runner (_df_run_holdout_gate) can build its OWN
    # gate-owned wrapper (correct paths + same DB-reset) WITHOUT re-parsing the
    # manifest or coupling to this function's locals. Set once, before the
    # workspace-branch early return, so both branches expose them.
    _DF_VITEST_PROJECT_CONFIG="$vitest_config"
    _DF_SETUP_INJECT="$setup_inject"

    # --- Workspace detection ---
    # When the vitest config is inside a subdirectory (e.g., apps/api/vitest.config.ts),
    # we're in a monorepo workspace. Set _DF_WORKSPACE_DIR so the quality gate runs
    # vitest from that directory, giving it access to workspace-scoped node_modules.
    local config_dir
    config_dir=$(dirname "$vitest_config")
    if [[ "$config_dir" != "." && -d "$config_dir" ]]; then
        _DF_WORKSPACE_DIR="$config_dir"
        # Compute depth for ../../../ traversal from workspace to project root.
        local depth
        depth=$(echo "$_DF_WORKSPACE_DIR" | tr '/' '\n' | grep -c .)
        _DF_WORKSPACE_UP=""
        for ((i=0; i<depth; i++)); do _DF_WORKSPACE_UP+="../"; done
        echo "  Workspace detected: ${_DF_WORKSPACE_DIR} (depth=${depth}, up=${_DF_WORKSPACE_UP})"
    fi

    # --- Wrapper generation ---
    local wrapper_dir=".dark-factory/scenarios/${spec_name}"

    if [[ -n "$_DF_WORKSPACE_DIR" ]]; then
        # Workspace mode: generate a wrapper INSIDE the workspace dir so that
        # the wrapper's bare-specifier imports (vitest/config) resolve from the
        # workspace's node_modules. Placing the wrapper at .dark-factory/scenarios/
        # fails because Node's ESM resolver walks up from the FILE's location,
        # not from CWD, and never reaches <workspace>/node_modules/.
        #
        # We explicitly OVERWRITE test.include after mergeConfig because Vite's
        # mergeConfig concatenates array fields — if the base config pins
        # test.include (e.g., ['src/**/*.test.ts']), the merged include would
        # run both unit tests AND scenarios through the gate, polluting
        # satisfaction scoring and implementer feedback.
        local ws_config_basename
        ws_config_basename=$(basename "$vitest_config")
        # When injecting the postgres reset setupFile, resolve its pg driver from
        # the WORKSPACE package (where the project installed pg/postgres) — the
        # staged setupFile lives outside the workspace, so it can't resolve the
        # driver from its own location in a monorepo.
        if [[ -n "$setup_inject" ]]; then
            export DF_RESET_RESOLVE_BASE="${proj_root}/${_DF_WORKSPACE_DIR}/package.json"
        fi
        {
            echo "import config from './${ws_config_basename}'"
            echo "import { defineConfig, mergeConfig } from 'vitest/config'"
            echo "const merged = mergeConfig(config, defineConfig({ test: {} }))"
            echo "merged.test = merged.test || {}"
            echo "merged.test.include = ['${_DF_WORKSPACE_UP}.dark-factory/scenarios/${spec_name}/**/*.{test,spec}.*']"
            [[ -n "$setup_inject" ]] && echo "$setup_inject"
            echo "export default merged"
        } > "${_DF_WORKSPACE_DIR}/.df-vitest-scenarios.config.ts"
        # Track for EXIT-trap cleanup (see quality-gate.sh:_df_cleanup_wrapper).
        _DF_WRAPPER_FILE="${_DF_WORKSPACE_DIR}/.df-vitest-scenarios.config.ts"
        # Config flag is relative to the workspace dir (where vitest runs via cd).
        _DF_VITEST_CONFIG_FLAG="--config .df-vitest-scenarios.config.ts"
        echo "  Using workspace wrapper config: ${_DF_WRAPPER_FILE}"
        return 0
    fi

    # --- Non-workspace wrapper (existing logic) ---
    # Guard: if the vitest config has an `include` array that doesn't mention
    # .dark-factory or scenarios, blind scenario files will be excluded by the
    # runner. Generate a wrapper config that extends the project config but
    # widens the include pattern to cover the scenarios directory.
    #
    # Known limitations of this heuristic:
    # - Parses JS/TS as plain text; a comment like `// include all [tests]`
    #   triggers a false positive (harmless — mergeConfig just widens include).
    # - A comment mentioning "scenarios" suppresses the wrapper (false negative).
    # - Escape hatch: set `scenario_test_cmd` in config to bypass auto-detection.
    # Generate a wrapper when EITHER (a) the project config's include array would
    # exclude scenario files, OR (b) the postgres reset setupFile must be injected.
    local need_include_widen=false
    if grep -qE 'include.*\[' "$vitest_config" 2>/dev/null \
       && ! grep -qE '\.dark-factory|scenarios' "$vitest_config" 2>/dev/null; then
        need_include_widen=true
        echo "  WARNING: vitest config has include patterns that may exclude scenario files."
        echo "  Constructing wrapper config to override include pattern."
    fi
    if [[ "$need_include_widen" == "true" || -n "$setup_inject" ]]; then
        if [[ -n "$setup_inject" ]]; then
            # Non-monorepo: resolve the reset's pg driver from the project root.
            export DF_RESET_RESOLVE_BASE="${proj_root}/package.json"
        fi
        mkdir -p "$wrapper_dir"
        # Wrapper lives at .dark-factory/scenarios/<spec>/vitest.config.wrapper.ts.
        # That's 3 levels below the project root (.dark-factory → scenarios → <spec>),
        # so the import uses ../../../ to reach the project root. The `merged` form
        # (vs a bare mergeConfig export) lets us append setupFiles after the merge.
        {
            echo "import config from '../../../${vitest_config}'"
            echo "import { defineConfig, mergeConfig } from 'vitest/config'"
            if [[ "$need_include_widen" == "true" ]]; then
                echo "const merged = mergeConfig(config, defineConfig({ test: { include: ['**/*.test.*', '**/*.spec.*'] } }))"
            else
                echo "const merged = mergeConfig(config, defineConfig({ test: {} }))"
            fi
            echo "merged.test = merged.test || {}"
            [[ -n "$setup_inject" ]] && echo "$setup_inject"
            echo "export default merged"
        } > "${wrapper_dir}/vitest.config.wrapper.ts"
        # Intentionally NOT tracked in _DF_WRAPPER_FILE for EXIT-trap cleanup:
        # this wrapper lives under .dark-factory/scenarios/<spec>/ (a per-run
        # artifact dir wiped by the framework reset), unlike the workspace
        # wrapper which lands in the project's own source tree and MUST be
        # cleaned. Sprint 28 widens when this branch fires (every postgres-active
        # non-workspace spec), so the asymmetry is called out explicitly.
        _DF_VITEST_CONFIG_FLAG="--config .dark-factory/scenarios/${spec_name}/vitest.config.wrapper.ts"
        echo "  Using wrapper config: ${_DF_VITEST_CONFIG_FLAG}"
        return 0
    fi

    _DF_VITEST_CONFIG_FLAG="--config ${vitest_config}"
}

# _df_build_vitest_cmd <mode> [<target>]
#   Builds a workspace-aware vitest command string. Only produces output when
#   _DF_WORKSPACE_DIR is set (monorepo workspace mode). Non-workspace call
#   sites keep their existing case statements — this helper is intentionally
#   workspace-only to avoid duplicating the runner dispatch logic.
#
#   mode: "dir"  — run all scenarios (included via wrapper config's include pattern)
#         "file" — run a single file (target = path relative to project root)
#
#   Globals read: _DF_WORKSPACE_DIR, _DF_WORKSPACE_UP, _DF_VITEST_CONFIG_FLAG
_df_build_vitest_cmd() {
    local mode="$1"
    local target="${2:-}"

    if [[ -z "${_DF_WORKSPACE_DIR:-}" ]]; then
        echo ""
        return
    fi

    local args="${_DF_VITEST_CONFIG_FLAG}"
    if [[ "$mode" == "file" && -n "$target" ]]; then
        # File path is relative to project root. Prepend traversal from
        # workspace dir to project root so vitest finds the file after cd.
        args+=" '${_DF_WORKSPACE_UP}${target}'"
    fi
    # In workspace mode, always use pnpm exec — the workspace has vitest in
    # its node_modules and pnpm exec resolves from CWD's package scope.
    echo "cd '${_DF_WORKSPACE_DIR}' && pnpm exec vitest run ${args} 2>&1"
}

# ---------------------------------------------------------------------------
# _df_run_holdout_gate <spec_name> <iteration>
#   Run the QA-Holdout's BLIND adversarial suite (.dark-factory/holdouts/<spec>/)
#   and gate ship/no-ship on it. This suite was previously GENERATED-AND-DISCARDED
#   (the gate had no code path that ran holdouts/) — restoring it wires the
#   blind-adversarial-holdout barrier (docs/dev/2026-06-24-wire-blind-holdout-gate-design.md).
#
#   BARRIER-CRITICAL (binary gate — Implementer gets only "blind validation failed"):
#     * COUNT-ONLY. The raw vitest output (holdout test names, assertion text, and
#       even leaked PII from adversarial cross-tenant tests) is captured to a LOCAL
#       var and DISCARDED — never written to REPORT/SUMMARY/per-test JSON/feedback.
#     * Does NOT touch the scenario count machinery (_LAST_GATE_OUTPUT,
#       _write_per_test_results, _parse_scenario_counts). Holdout failures must NOT
#       inflate scenario_failed, or the defect classifier's expected_total HARD
#       CONTRACT aborts every failing iteration (Neo barrier review, trap A).
#     * The pass/fail COUNT is carried to Python on a MASKED sidecar
#       (reports/<spec>-holdout-result.env → iter_state.holdout_failed), not via
#       Oracle-echoable report prose. The Implementer masks reports/ + holdouts/.
#   Uses a GATE-OWNED wrapper config (imports the project vitest config for its
#   globalSetup/in-process server + resolve aliases, OVERRIDES include to the holdout
#   dir) — bypassing the QA-Holdout's own (path-buggy) vitest.config.
# ---------------------------------------------------------------------------
_df_run_holdout_gate() {
    local spec_name="$1"
    local holdouts_dir=".dark-factory/holdouts/${spec_name}"
    local sidecar=".dark-factory/reports/${spec_name}-holdout-result.env"

    # Guard: no holdout dir, an explicit sentinel, or no executable test files →
    # no row, no false coverage-hole (pure-library / no-blind-suite specs).
    if [[ ! -d "$holdouts_dir" ]] \
       || [[ -f "${holdouts_dir}/${_DF_SENTINEL_FILENAME}" ]] \
       || ! has_scenario_files "$holdouts_dir"; then
        return 0
    fi

    # Holdouts run on the same vitest toolchain as scenarios. Resolve (cached if
    # the scenario pass already resolved it). If no vitest runner resolves
    # (non-node / no config), do NOT silently pass — surface a non-PASS row.
    _df_resolve_vitest_config "$spec_name"
    if [[ -z "${_DF_VITEST_PROJECT_CONFIG:-}" ]]; then
        echo "| Blind Holdouts | FAIL | blind suite present but no vitest runner resolved (cannot execute) |" >> "$REPORT_FILE"
        echo "| Blind Holdouts | FAIL |" >> "$SUMMARY_FILE"
        { echo "HOLDOUT_RAN=0"; echo "HOLDOUT_FAILED=-1"; echo "HOLDOUT_TOTAL=0"; } > "$sidecar"
        GATE_FAILED=true
        return 0
    fi

    # Gate-owned wrapper next to the project config (same dir → './<basename>'
    # import). Run from the workspace dir so pnpm exec resolves workspace vitest.
    local run_dir="${_DF_WORKSPACE_DIR:-.}"
    local up="${_DF_WORKSPACE_UP:-}"
    local cfg_base; cfg_base="$(basename "$_DF_VITEST_PROJECT_CONFIG")"
    local wrapper="${run_dir}/.df-holdout-wrapper.vitest.config.ts"
    {
        echo "import base from './${cfg_base}'"
        echo "import { defineConfig, mergeConfig } from 'vitest/config'"
        echo "const merged = mergeConfig(base, defineConfig({ test: {} }))"
        echo "merged.test = merged.test || {}"
        # OVERRIDE (not concat) so ONLY the holdout dir runs, with base's
        # globalSetup (in-process server) preserved.
        echo "merged.test.include = ['${up}.dark-factory/holdouts/${spec_name}/**/*.test.*']"
        [[ -n "${_DF_SETUP_INJECT:-}" ]] && echo "$_DF_SETUP_INJECT"
        echo "export default merged"
    } > "$wrapper"

    echo "  Running gate: Blind Holdouts (blind adversarial suite)..."
    # Capture to a LOCAL var that is DISCARDED after count extraction.
    local out exit_code=0
    local to="${SCENARIO_TIMEOUT:-${GATE_TIMEOUT:-0}}"
    if [[ -n "$TIMEOUT_CMD" ]] && [[ "${to:-0}" -gt 0 ]]; then
        out=$(cd "$run_dir" && $TIMEOUT_CMD --kill-after="${GATE_KILL_AFTER:-15}s" "${to}s" \
              pnpm exec vitest run --config .df-holdout-wrapper.vitest.config.ts 2>&1) || exit_code=$?
    else
        out=$(cd "$run_dir" && pnpm exec vitest run --config .df-holdout-wrapper.vitest.config.ts 2>&1) || exit_code=$?
    fi
    rm -f "$wrapper"

    # Parse COUNT ONLY from the vitest summary ("Tests  N failed | M passed (T)"
    # or "Tests  M passed (T)"). Everything else in `out` is discarded.
    local failed total
    failed=$(printf '%s\n' "$out" | grep -oE 'Tests[[:space:]]+[0-9]+ failed' | grep -oE '[0-9]+' | head -1)
    total=$(printf '%s\n' "$out" | grep -oE '\([0-9]+\)' | tail -1 | tr -d '()')
    [[ -z "$failed" ]] && failed=0
    [[ -z "$total" ]] && total=0

    # Timeout / run error / "No test files found" = can't-execute → FAIL
    # (surface the gap; never a silent PASS).
    local cant_run=false
    if [[ $exit_code -eq 124 || $exit_code -eq 137 || $exit_code -eq 143 ]]; then cant_run=true; fi
    if printf '%s\n' "$out" | grep -qF "No test files found"; then cant_run=true; fi
    if [[ "$total" -eq 0 && $exit_code -ne 0 ]]; then cant_run=true; fi

    out=""  # explicit discard of the barrier-sensitive raw output

    if [[ "$cant_run" == "true" ]]; then
        echo "| Blind Holdouts | FAIL | blind suite could not execute (timeout/error/no-files) |" >> "$REPORT_FILE"
        echo "| Blind Holdouts | FAIL |" >> "$SUMMARY_FILE"
        { echo "HOLDOUT_RAN=0"; echo "HOLDOUT_FAILED=-1"; echo "HOLDOUT_TOTAL=0"; } > "$sidecar"
        GATE_FAILED=true
        return 0
    fi

    { echo "HOLDOUT_RAN=1"; echo "HOLDOUT_FAILED=${failed}"; echo "HOLDOUT_TOTAL=${total}"; } > "$sidecar"
    if [[ "$failed" -gt 0 ]]; then
        # COUNT-ONLY row (numbers, never names) in REPORT (Oracle-readable, masked
        # from Implementer); bare FAIL in SUMMARY. The oracle.md no-echo directive
        # stops the Oracle paraphrasing even the count into Implementer feedback.
        echo "| Blind Holdouts | FAIL | ${failed}/${total} blind holdout tests failed |" >> "$REPORT_FILE"
        echo "| Blind Holdouts | FAIL |" >> "$SUMMARY_FILE"
        echo "    FAIL (${failed}/${total} blind)"
        GATE_FAILED=true
    else
        echo "| Blind Holdouts | PASS | ${total}/${total} blind holdout tests passed |" >> "$REPORT_FILE"
        echo "| Blind Holdouts | PASS |" >> "$SUMMARY_FILE"
        echo "    PASS (${total} blind)"
    fi
}

# validate_scenario_files <dir>
#   Retired in Phase 4.6b.1a. Format A/B markdown scenarios are no longer
#   emitted. Executable test files are validated by the test runner itself.
#   Kept as a no-op to avoid breaking any stale callers.
validate_scenario_files() {
    local dir="$1"
    local rc=0

    while IFS= read -r f; do
        if [[ ! -s "$f" ]]; then
            echo "ERROR: Scenario file is empty: $f"
            rc=1
            continue
        fi
        if ! grep -q '^##' "$f" 2>/dev/null; then
            echo "WARNING: Scenario file has no section headers (## ...): $f"
        fi
    done < <(find "$dir" -type f \( \
        "${_DF_FORMAT_PATTERNS_JOURNEY[@]}" \
    \) 2>/dev/null | sort)

    return $rc
}

# validate_spec_result
#   Checks the spec validation status/report for the current SPEC_NAME.
#   Requires global: SPEC_NAME
#   Returns 0 on PASS with score 100, 1 otherwise.
validate_spec_result() {
    local status_file=".dark-factory/reviews/${SPEC_NAME}-validation.status"
    local report_file=".dark-factory/reviews/${SPEC_NAME}-validation.md"
    local verdict="" score="" divergence=""

    # Primary: parse .status file (key=value)
    if [[ -f "$status_file" ]]; then
        verdict=$(grep '^verdict=' "$status_file" | cut -d= -f2-)
        score=$(grep '^score=' "$status_file" | cut -d= -f2-)
        divergence=$(grep '^divergence=' "$status_file" | cut -d= -f2-)
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

    # Fallback: parse markdown report for divergence rating
    if [[ -z "$divergence" ]] && [[ -f "$report_file" ]]; then
        divergence=$(grep -i 'Divergence Rating:' "$report_file" | grep -oE '(LOW|MEDIUM|HIGH)' | head -1) || divergence=""
    fi

    # Enforce hard gate: score
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

    # Enforce hard gate: divergence
    if [[ -z "$divergence" ]]; then
        echo "SPEC VALIDATION FAILED: Missing divergence rating. Re-run spec validation with the current spec-validator agent definition."
        return 1
    fi
    divergence=$(echo "$divergence" | tr '[:lower:]' '[:upper:]')
    if [[ "$divergence" == "HIGH" ]]; then
        echo "SPEC VALIDATION FAILED: Divergence is HIGH — spec permits contradictory implementations. Revise the spec to eliminate ambiguity."
        return 1
    fi

    echo "Spec validated: PASS (${score}/100, divergence=${divergence})"
}
