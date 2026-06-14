#!/usr/bin/env bash
# tests/shell/test_resolve_vitest_config.sh — Tests for _df_resolve_vitest_config
#
# Verifies that the vitest config flag is correctly extracted from the architect
# manifest and that the wrapper config is generated when needed.
#
# Usage: bash tests/shell/test_resolve_vitest_config.sh
# Exit:  0 on all pass, 1 on any failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TMPDIR_BASE="$(mktemp -d)"
PASS=0
FAIL=0

cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $label"
        ((PASS++))
    else
        echo "  FAIL: $label (expected='$expected', got='$actual')"
        ((FAIL++))
    fi
}

assert_contains() {
    local label="$1" needle="$2" haystack="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        echo "  PASS: $label"
        ((PASS++))
    else
        echo "  FAIL: $label (expected to contain '$needle', got='$haystack')"
        ((FAIL++))
    fi
}

# Source the library.
_DF_LIB_SCENARIOS_LOADED=""
source "$PROJECT_ROOT/scripts/lib/scenarios.sh"

# ---------------------------------------------------------------------------
# Test 1: No manifest file — flag should be empty
# ---------------------------------------------------------------------------
echo "--- Test 1: no manifest file ---"

T1="$TMPDIR_BASE/t1"
mkdir -p "$T1"

pushd "$T1" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_df_resolve_vitest_config "nonexistent-spec"
assert_eq "flag empty when no manifest" "" "$_DF_VITEST_CONFIG_FLAG"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 2: Manifest with vitest_config pointing to existing file
# ---------------------------------------------------------------------------
echo "--- Test 2: manifest with vitest_config ---"

T2="$TMPDIR_BASE/t2"
mkdir -p "$T2/.dark-factory/architecture"
# Root-level vitest config (non-workspace — no subdirectory prefix)
cat > "$T2/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({
  test: { environment: 'node' }
})
EOF
# Create a manifest that declares the vitest config at project root
cat > "$T2/.dark-factory/architecture/my-spec-manifest.yaml" <<'EOF'
version: 1
spec: my-spec
language: node
test_integration:
  module_system: esm
  vitest_config: vitest.config.ts
  entry_points:
    - path: src/server.ts
      specifier: "../../../src/server.js"
      exports: [createServer]
EOF

pushd "$T2" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
_df_resolve_vitest_config "my-spec"
assert_eq "flag has --config" "--config vitest.config.ts" "$_DF_VITEST_CONFIG_FLAG"
assert_eq "no workspace detected" "" "$_DF_WORKSPACE_DIR"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 3: Manifest with vitest_config but file doesn't exist
# ---------------------------------------------------------------------------
echo "--- Test 3: vitest_config file missing from disk ---"

T3="$TMPDIR_BASE/t3"
mkdir -p "$T3/.dark-factory/architecture"
cat > "$T3/.dark-factory/architecture/missing-spec-manifest.yaml" <<'EOF'
version: 1
spec: missing-spec
language: node
test_integration:
  vitest_config: does/not/exist.ts
EOF

pushd "$T3" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_df_resolve_vitest_config "missing-spec"
assert_eq "flag empty when file missing" "" "$_DF_VITEST_CONFIG_FLAG"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 4: Manifest with no vitest_config field
# ---------------------------------------------------------------------------
echo "--- Test 4: manifest with no vitest_config ---"

T4="$TMPDIR_BASE/t4"
mkdir -p "$T4/.dark-factory/architecture"
cat > "$T4/.dark-factory/architecture/no-config-spec-manifest.yaml" <<'EOF'
version: 1
spec: no-config-spec
language: node
test_integration:
  module_system: esm
EOF

pushd "$T4" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_df_resolve_vitest_config "no-config-spec"
assert_eq "flag empty when no vitest_config" "" "$_DF_VITEST_CONFIG_FLAG"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 5: vitest config with restrictive include patterns → wrapper generated
# ---------------------------------------------------------------------------
echo "--- Test 5: restrictive include patterns (root config) → wrapper config ---"

T5="$TMPDIR_BASE/t5"
mkdir -p "$T5/.dark-factory/architecture"
mkdir -p "$T5/.dark-factory/scenarios/restrict-spec"
# Root-level config with restrictive include patterns (non-workspace)
cat > "$T5/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node'
  }
})
EOF
cat > "$T5/.dark-factory/architecture/restrict-spec-manifest.yaml" <<'EOF'
version: 1
spec: restrict-spec
language: node
test_integration:
  vitest_config: vitest.config.ts
EOF

pushd "$T5" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
_df_resolve_vitest_config "restrict-spec"
assert_contains "wrapper config used" "vitest.config.wrapper.ts" "$_DF_VITEST_CONFIG_FLAG"
assert_eq "no workspace detected" "" "$_DF_WORKSPACE_DIR"
# Verify wrapper file was actually created
if [[ -f ".dark-factory/scenarios/restrict-spec/vitest.config.wrapper.ts" ]]; then
    echo "  PASS: wrapper file created"
    ((PASS++))
else
    echo "  FAIL: wrapper file not created"
    ((FAIL++))
fi
# Verify wrapper import path is 3 levels up (../../../) to reach project root
wrapper_content=$(cat ".dark-factory/scenarios/restrict-spec/vitest.config.wrapper.ts")
assert_contains "wrapper uses ../../../ to reach project root" \
    "from '../../../vitest.config.ts'" "$wrapper_content"
# Verify include pattern uses broad globs matching _DF_FORMAT_PATTERNS_EXECUTABLE
assert_contains "wrapper include uses broad *.test.* glob" \
    "'**/*.test.*'" "$wrapper_content"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 6: vitest config with include patterns that mention scenarios → no wrapper
# ---------------------------------------------------------------------------
echo "--- Test 6: include pattern mentions scenarios → no wrapper ---"

T6="$TMPDIR_BASE/t6"
mkdir -p "$T6/.dark-factory/architecture"
# Root-level config with include that mentions scenarios (non-workspace)
cat > "$T6/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({
  test: {
    include: ['src/**/*.test.ts', '.dark-factory/scenarios/**/*.test.ts'],
    environment: 'node'
  }
})
EOF
cat > "$T6/.dark-factory/architecture/ok-spec-manifest.yaml" <<'EOF'
version: 1
spec: ok-spec
language: node
test_integration:
  vitest_config: vitest.config.ts
EOF

pushd "$T6" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
_df_resolve_vitest_config "ok-spec"
assert_eq "direct config (no wrapper)" "--config vitest.config.ts" "$_DF_VITEST_CONFIG_FLAG"
assert_eq "no workspace detected" "" "$_DF_WORKSPACE_DIR"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 7: Caching — second call returns cached value
# ---------------------------------------------------------------------------
echo "--- Test 7: caching ---"

T7="$TMPDIR_BASE/t7"
mkdir -p "$T7/.dark-factory/architecture"
cat > "$T7/vitest.config.ts" <<'EOF'
export default {}
EOF
cat > "$T7/.dark-factory/architecture/cache-spec-manifest.yaml" <<'EOF'
version: 1
spec: cache-spec
test_integration:
  vitest_config: vitest.config.ts
EOF

pushd "$T7" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
_df_resolve_vitest_config "cache-spec"
local_first="$_DF_VITEST_CONFIG_FLAG"
# Delete the manifest — if caching works, the second call still returns the same value
rm "$T7/.dark-factory/architecture/cache-spec-manifest.yaml"
_df_resolve_vitest_config "cache-spec"
assert_eq "cached value survives manifest deletion" "$local_first" "$_DF_VITEST_CONFIG_FLAG"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 8: Quoted vitest_config value in manifest
# ---------------------------------------------------------------------------
echo "--- Test 8: quoted vitest_config value ---"

T8="$TMPDIR_BASE/t8"
mkdir -p "$T8/.dark-factory/architecture"
cat > "$T8/vitest.config.ts" <<'EOF'
export default {}
EOF
# YAML double-quoted value
cat > "$T8/.dark-factory/architecture/quoted-spec-manifest.yaml" <<'EOF'
version: 1
spec: quoted-spec
test_integration:
  vitest_config: "vitest.config.ts"
EOF

pushd "$T8" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
_df_resolve_vitest_config "quoted-spec"
assert_eq "quoted value stripped" "--config vitest.config.ts" "$_DF_VITEST_CONFIG_FLAG"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 9: Workspace depth-2 — vitest config in apps/api/ triggers workspace mode
# ---------------------------------------------------------------------------
echo "--- Test 9: workspace depth-2 (apps/api/) ---"

T9="$TMPDIR_BASE/t9"
mkdir -p "$T9/.dark-factory/architecture"
mkdir -p "$T9/.dark-factory/scenarios/ws-spec"
mkdir -p "$T9/apps/api"
cat > "$T9/apps/api/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({
  test: { environment: 'node' }
})
EOF
cat > "$T9/.dark-factory/architecture/ws-spec-manifest.yaml" <<'EOF'
version: 1
spec: ws-spec
language: node
test_integration:
  vitest_config: apps/api/vitest.config.ts
EOF

pushd "$T9" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
_df_resolve_vitest_config "ws-spec"
assert_eq "workspace dir detected" "apps/api" "$_DF_WORKSPACE_DIR"
assert_eq "workspace up traversal" "../../" "$_DF_WORKSPACE_UP"
# Config flag should be relative to workspace dir (where vitest runs)
assert_eq "config flag in workspace dir" \
    "--config .df-vitest-scenarios.config.ts" \
    "$_DF_VITEST_CONFIG_FLAG"
# Verify wrapper file was created INSIDE the workspace dir (not in .dark-factory/scenarios/)
if [[ -f "apps/api/.df-vitest-scenarios.config.ts" ]]; then
    echo "  PASS: workspace wrapper created in workspace dir"
    ((PASS++))
else
    echo "  FAIL: workspace wrapper not created in workspace dir"
    ((FAIL++))
fi
# Verify wrapper content: co-located import and targeted include
ws_wrapper=$(cat "apps/api/.df-vitest-scenarios.config.ts")
assert_contains "workspace wrapper imports co-located config" \
    "from './vitest.config.ts'" "$ws_wrapper"
assert_contains "workspace wrapper include reaches back to scenarios" \
    "../../.dark-factory/scenarios/ws-spec/" "$ws_wrapper"
# Verify _df_build_vitest_cmd produces cd command
ws_cmd=$(_df_build_vitest_cmd dir)
assert_contains "build_vitest_cmd includes cd" "cd 'apps/api'" "$ws_cmd"
assert_contains "build_vitest_cmd uses pnpm exec" "pnpm exec vitest run" "$ws_cmd"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 10: Workspace depth-1 — vitest config in api/ (single level)
# ---------------------------------------------------------------------------
echo "--- Test 10: workspace depth-1 (api/) ---"

T10="$TMPDIR_BASE/t10"
mkdir -p "$T10/.dark-factory/architecture"
mkdir -p "$T10/api"
cat > "$T10/api/vitest.config.ts" <<'EOF'
export default {}
EOF
cat > "$T10/.dark-factory/architecture/d1-spec-manifest.yaml" <<'EOF'
version: 1
spec: d1-spec
test_integration:
  vitest_config: api/vitest.config.ts
EOF

pushd "$T10" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
_df_resolve_vitest_config "d1-spec"
assert_eq "workspace dir depth-1" "api" "$_DF_WORKSPACE_DIR"
assert_eq "workspace up depth-1" "../" "$_DF_WORKSPACE_UP"
assert_eq "config flag depth-1" \
    "--config .df-vitest-scenarios.config.ts" \
    "$_DF_VITEST_CONFIG_FLAG"
# Verify wrapper created inside workspace dir
if [[ -f "api/.df-vitest-scenarios.config.ts" ]]; then
    echo "  PASS: depth-1 wrapper created in workspace dir"
    ((PASS++))
else
    echo "  FAIL: depth-1 wrapper not created in workspace dir"
    ((FAIL++))
fi
d1_wrapper=$(cat "api/.df-vitest-scenarios.config.ts")
assert_contains "depth-1 wrapper imports co-located config" \
    "from './vitest.config.ts'" "$d1_wrapper"
assert_contains "depth-1 include uses ../ traversal" \
    "../.dark-factory/scenarios/d1-spec/" "$d1_wrapper"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 11: _df_build_vitest_cmd returns empty when no workspace
# ---------------------------------------------------------------------------
echo "--- Test 11: build_vitest_cmd empty without workspace ---"

_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
no_ws_cmd=$(_df_build_vitest_cmd dir)
assert_eq "no workspace → empty command" "" "$no_ws_cmd"

# ---------------------------------------------------------------------------
# Test 12: Workspace wrapper force-overwrites test.include (not mergeConfig concat)
# ---------------------------------------------------------------------------
# Vite's mergeConfig concatenates array fields. If the base workspace config
# pins test.include (e.g., ['src/**/*.test.ts']), a naive
# `mergeConfig(base, { test: { include: [...] } })` would merge both patterns
# into the include list, silently running the workspace's own unit tests
# through the scenario gate alongside the blind scenarios. The wrapper must
# explicitly assign `merged.test.include = [...]` after mergeConfig so the
# scenario gate only runs scenarios.
echo "--- Test 12: wrapper force-overwrites include (no concat with base) ---"

T12="$TMPDIR_BASE/t12"
mkdir -p "$T12/.dark-factory/architecture"
mkdir -p "$T12/apps/api"
# Base config that pins test.include — the hazard case.
cat > "$T12/apps/api/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.spec.ts'],
  }
})
EOF
cat > "$T12/.dark-factory/architecture/overwrite-spec-manifest.yaml" <<'EOF'
version: 1
spec: overwrite-spec
language: node
test_integration:
  vitest_config: apps/api/vitest.config.ts
EOF

pushd "$T12" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
_DF_WRAPPER_FILE=""
_df_resolve_vitest_config "overwrite-spec"

# Verify the wrapper contains the explicit assignment pattern, not a plain
# mergeConfig-with-include call (which would concatenate).
ovr_wrapper=$(cat "apps/api/.df-vitest-scenarios.config.ts")
assert_contains "wrapper uses const merged = mergeConfig" \
    "const merged = mergeConfig(config, defineConfig({ test: {} }))" "$ovr_wrapper"
assert_contains "wrapper explicitly overwrites merged.test.include" \
    "merged.test.include = [" "$ovr_wrapper"
assert_contains "wrapper exports merged (not mergeConfig result directly)" \
    "export default merged" "$ovr_wrapper"
# Negative assertion: the wrapper must NOT contain the old concatenating
# pattern where include sits inside the second mergeConfig arg.
if [[ "$ovr_wrapper" == *"mergeConfig(config, defineConfig({"*"include:"* ]]; then
    # Look for the specific problematic shape: `defineConfig({ test: { include:`
    if [[ "$ovr_wrapper" == *"defineConfig({"*"test:"*"include:"* ]]; then
        echo "  FAIL: wrapper still passes include through defineConfig (concat hazard)"
        ((FAIL++))
    else
        echo "  PASS: wrapper does not pass include through defineConfig"
        ((PASS++))
    fi
else
    echo "  PASS: wrapper does not pass include through defineConfig"
    ((PASS++))
fi
# Verify _DF_WRAPPER_FILE global is set for EXIT-trap cleanup.
assert_eq "wrapper file path tracked for cleanup" \
    "apps/api/.df-vitest-scenarios.config.ts" "$_DF_WRAPPER_FILE"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 13: Sprint 28 — postgres reset setupFile staged → injected (workspace)
# ---------------------------------------------------------------------------
echo "--- Test 13: postgres reset setupFile injected into workspace wrapper ---"

T13="$TMPDIR_BASE/t13"
mkdir -p "$T13/.dark-factory/architecture"
mkdir -p "$T13/.dark-factory/scenarios/pg-spec/_contracts/postgres"
mkdir -p "$T13/apps/api"
cat > "$T13/apps/api/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { environment: 'node' } })
EOF
# The staged reset setupFile (presence = postgres-active for the spec).
echo "// staged reset" > "$T13/.dark-factory/scenarios/pg-spec/_contracts/postgres/_df_reset.setup.ts"
cat > "$T13/.dark-factory/architecture/pg-spec-manifest.yaml" <<'EOF'
version: 1
spec: pg-spec
language: node
test_integration:
  vitest_config: apps/api/vitest.config.ts
EOF

pushd "$T13" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
unset DF_RESET_RESOLVE_BASE
_df_resolve_vitest_config "pg-spec"
pg_wrapper=$(cat "apps/api/.df-vitest-scenarios.config.ts")
assert_contains "workspace wrapper injects setupFiles" "merged.test.setupFiles" "$pg_wrapper"
assert_contains "setupFiles references the staged reset file" "_df_reset.setup.ts" "$pg_wrapper"
assert_contains "setupFiles is additive (preserves existing)" "merged.test.setupFiles || []" "$pg_wrapper"
assert_contains "resolve base points at workspace package" "apps/api/package.json" "${DF_RESET_RESOLVE_BASE:-}"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 14: Sprint 28 — reset injected in non-workspace (no include-widen needed)
# ---------------------------------------------------------------------------
echo "--- Test 14: postgres reset injected, non-workspace, plain config ---"

T14="$TMPDIR_BASE/t14"
mkdir -p "$T14/.dark-factory/architecture"
mkdir -p "$T14/.dark-factory/scenarios/pg2-spec/_contracts/postgres"
# Root config with NO restrictive include — wrapper would NOT be generated
# pre-Sprint-28; now it must be, solely to inject setupFiles.
cat > "$T14/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { environment: 'node' } })
EOF
echo "// staged reset" > "$T14/.dark-factory/scenarios/pg2-spec/_contracts/postgres/_df_reset.setup.ts"
cat > "$T14/.dark-factory/architecture/pg2-spec-manifest.yaml" <<'EOF'
version: 1
spec: pg2-spec
language: node
test_integration:
  vitest_config: vitest.config.ts
EOF

pushd "$T14" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
unset DF_RESET_RESOLVE_BASE
_df_resolve_vitest_config "pg2-spec"
assert_contains "non-workspace wrapper generated for reset" "vitest.config.wrapper.ts" "$_DF_VITEST_CONFIG_FLAG"
pg2_wrapper=$(cat ".dark-factory/scenarios/pg2-spec/vitest.config.wrapper.ts")
assert_contains "non-ws wrapper injects setupFiles" "merged.test.setupFiles" "$pg2_wrapper"
assert_contains "non-ws resolve base points at project root" "package.json" "${DF_RESET_RESOLVE_BASE:-}"
popd > /dev/null

# ---------------------------------------------------------------------------
# Test 15: Sprint 28 negative — no staged reset → no setupFiles, unchanged path
# ---------------------------------------------------------------------------
echo "--- Test 15: no reset setupFile → no injection (non-postgres unchanged) ---"

T15="$TMPDIR_BASE/t15"
mkdir -p "$T15/.dark-factory/architecture"
mkdir -p "$T15/apps/api"
cat > "$T15/apps/api/vitest.config.ts" <<'EOF'
import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { environment: 'node' } })
EOF
cat > "$T15/.dark-factory/architecture/nopg-spec-manifest.yaml" <<'EOF'
version: 1
spec: nopg-spec
language: node
test_integration:
  vitest_config: apps/api/vitest.config.ts
EOF

pushd "$T15" > /dev/null
_DF_VITEST_CONFIG_FLAG=""
_DF_VITEST_CONFIG_RESOLVED=""
_DF_WORKSPACE_DIR=""
_DF_WORKSPACE_UP=""
unset DF_RESET_RESOLVE_BASE
_df_resolve_vitest_config "nopg-spec"
nopg_wrapper=$(cat "apps/api/.df-vitest-scenarios.config.ts")
if [[ "$nopg_wrapper" == *"setupFiles"* ]]; then
    echo "  FAIL: non-postgres wrapper must NOT inject setupFiles"
    ((FAIL++))
else
    echo "  PASS: non-postgres wrapper has no setupFiles injection"
    ((PASS++))
fi
assert_eq "no resolve base exported for non-postgres" "" "${DF_RESET_RESOLVE_BASE:-}"
popd > /dev/null

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
