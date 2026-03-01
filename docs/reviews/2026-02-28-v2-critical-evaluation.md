# Dark Factory Critical Evaluation — v2 (Post-Docker Isolation)

**Date:** 2026-02-28 (updated)
**Scope:** Re-evaluation after Docker-based hard isolation implementation, agent failure handling, M1 documentation sync, and M2 scenario quality validation
**Baseline:** v1 evaluation against commit fa4b703
**Changes evaluated:** Docker container isolation, pipeline bug fixes (C2, H1, H3, H4, H5), agent failure handling (`invoke_agent` wrapper), ANTHROPIC_API_KEY pre-flight check (N1), Oracle agent definition update, config/doc updates, M1 documentation-implementation sync (all flags, templates, placeholders, workflow variants), M2 scenario quality validation (`validate-scenarios.sh` + pipeline Step 2.5 integration)

---

## Executive Summary

The Docker isolation implementation addresses the most critical finding from v1 — information barriers are now enforced at the filesystem level via tmpfs masking inside containers, not just via prompt-level `--disallowedTools` flags. The three highest-impact pipeline bugs (quality gate crash, missing timeouts, fragile Oracle parsing) are fixed. Documentation now accurately reflects the implementation — all command-line flags, templates, workflow variants, and placeholder references are synced. The framework's security claims and documentation now match its enforcement mechanisms.

**Overall assessment:** The information barriers are now **hard technical controls** (files don't exist in the container filesystem) with prompt-level barriers retained as defense-in-depth. Agent failures are now detected and handled per-role (halt vs. warn) rather than silently swallowed. The remaining open issues are medium-severity data flow problems and edge cases — none undermine the core architecture.

---

## v1 Findings: Resolution Status

### CRITICAL — Both Resolved

#### C1. Information Barriers Are Trivially Bypassable — RESOLVED

**v1 problem:** `--disallowedTools` blocked named tools but agents could use `Bash("cat .dark-factory/scenarios/...")` to read restricted files.

**v2 fix:** Each agent runs inside a Docker container. Restricted directories are masked with empty read-only tmpfs overlays. The Implementer's container has no `.dark-factory/scenarios/` directory — `cat`, `ls`, `find`, `python -c "open(...)"`, or any other technique returns nothing because the files don't exist in the container's filesystem.

**Implementation details** (`dark-factory.sh:286-416`, `run_agent()`):
- Implementer: `.dark-factory/scenarios/` and `.git/` masked with `--tmpfs /workspace/.dark-factory/scenarios:ro,size=1k`
- Scenario Architect: `SOURCE_DIRS[]` (configurable, multi-directory) and `.git/` masked
- `.git/` masking prevents `git log`/`git show` from leaking restricted content
- Pre-flight `check_symlink_safety()` (line 270) detects symlinks crossing barrier boundaries before container launch
- `--disallowedTools` and `--append-system-prompt` retained as defense-in-depth layers inside containers
- Per-role cache volumes prevent cache poisoning between agents (e.g., `df-implementer-npm` vs `df-qg-npm`)

**Residual risk:** Low. Network egress is unrestricted — an agent could theoretically exfiltrate data via HTTP. Hardlink traversal is theoretically possible but practically unlikely (git doesn't create cross-directory hardlinks). Both are documented as known limitations.

#### C2. Quality Gate Failure Crashes the Pipeline — RESOLVED

**v1 problem:** `set -euo pipefail` caused `quality-gate.sh` exit code 1 to terminate the pipeline, preventing the iteration loop from running.

**v2 fix** (`dark-factory.sh:883-884`):
```bash
QG_EXIT=0
run_quality_gate "$SPEC_NAME" || QG_EXIT=$?
```

The exit code is captured without triggering `set -e`. The pipeline logs the failure and proceeds to the reviewer/oracle regardless. This restores the intended flow: quality gate fails → reviewer analyzes → oracle decides → iterate or escalate.

**Verified:** The `|| QG_EXIT=$?` pattern is the standard bash idiom for this. The quality gate report is still written to disk before the exit, so the reviewer has full failure details.

---

### HIGH — All 5 Resolved

#### H1. No Timeouts or Turn Limits — RESOLVED

**v1 problem:** `AGENT_MAX_TURNS` was dead config. No `--max-turns` or `timeout` on any `claude` invocation.

**v2 fix** (`dark-factory.sh:382, 400-407`):
- `--max-turns "${AGENT_MAX_TURNS:-200}"` passed to every agent invocation via `run_agent()`
- `timeout "$timeout_seconds"` (via `$TIMEOUT_CMD`) wraps every `docker run` call
- `AGENT_TIMEOUT` config variable (default 600s) controls wall-clock limit
- Exit code 124 detected and logged as `TIMEOUT` in audit
- Quality gate has separate `GATE_TIMEOUT` (default 300s)

#### H2. `--dangerously-skip-permissions` on Every Agent — MITIGATED

**v1 problem:** Agents had unrestricted OS access via `--dangerously-skip-permissions`.

**v2 status:** Still uses `--dangerously-skip-permissions` (it's in the Docker image entrypoint) — but now inside containers with:
- `--cap-drop=ALL` — no Linux capabilities
- `--security-opt no-new-privileges:true` — no privilege escalation
- `--pids-limit=512` — fork bomb protection
- `--memory=4g`, `--cpus=4` — resource limits
- `--user $(id -u):$(id -g)` — runs as host user, not root
- Restricted bind mounts — agents can only persist writes to mounted host directories
- Containers are ephemeral (`--rm`) — writes to the container filesystem are discarded

**Residual risk:** Medium-low. The agent has write access to its mounted project directories (Implementer gets `:rw` on project root). Network egress remains unrestricted. This is the expected threat model for a sandboxed build agent.

**Downgraded from HIGH to MEDIUM.** The container hardening substantially reduces the blast radius. The remaining risk is network egress and project-scoped file access, which are inherent to the agent's purpose.

#### H3. Oracle Output Parsing Is Fragile — RESOLVED

**v1 problem:** Parser expected exact `SHIP`/`NO-SHIP` on line 1, but the Oracle agent definition specified markdown headers as output format. Malformed output silently wasted iterations.

**v2 fix — two changes:**

1. **Pipeline parser** (`dark-factory.sh:917-937`): Strips whitespace, normalizes to uppercase, matches `SHIP` exactly or `NO-?SHIP` (with or without hyphen). Unrecognized output logs a `MALFORMED_OUTPUT` audit event, prints a warning, and defaults to NO-SHIP for safety.

2. **Oracle agent definition** (`agents/oracle.md`): Updated to explicitly require `SHIP` or `NO-SHIP` as plain text on the first line — no markdown headers, no formatting. Includes correct/incorrect examples.

#### H4. Scenario Architect Source Barrier Is Incomplete — RESOLVED

**v1 problem:** Deny list only blocked `${SOURCE_DIR:-src}/**`. Code in `lib/`, `app/`, `components/`, etc. was unprotected. `git log` could reveal implementation changes.

**v2 fix** (`dark-factory.sh:303-307`, `config.sh.template`):
- New `SOURCE_DIRS` config variable (space-separated list): `SOURCE_DIRS="src lib app components pages"`
- `run_agent()` iterates over `SOURCE_DIRS` and masks each directory that exists
- `.git/` is always masked in the Scenario Architect's container, preventing `git log`/`git show` leaks
- Backward compatible: falls back to `${SOURCE_DIR:-src}` if `SOURCE_DIRS` is not set

#### H5. `|| true` Suppresses All Claude Failures — RESOLVED

**v1 problem:** Every `claude` invocation ends with `2>&1) || true`. Infrastructure failures (Claude not installed, auth failures, rate limits) produce empty output that the pipeline silently continues with.

**v2 fix:** All 7 `run_agent()` call sites replaced with `invoke_agent()` wrapper (`dark-factory.sh:418-447`). The wrapper:

1. **Captures both exit code and output** into `AGENT_EXIT`/`AGENT_OUTPUT` globals (safe because pipeline execution is strictly sequential — no concurrent agents).
2. **Detects silent failures** — if exit code is 0 but output is below a configurable threshold (`min_output`, default 20 chars), assigns synthetic exit code 99 and logs `AGENT_LOW_OUTPUT` + `AGENT_FAILURE` audit events.
3. **Logs all failures** to the audit trail with exit code and output length.

**Per-agent failure behavior:**

| Agent | On failure | Rationale |
|-------|-----------|-----------|
| Spec Validator | **HALT** pipeline | Can't proceed without validated spec |
| Scenario Architect | **HALT** pipeline | Can't proceed without scenarios |
| Implementer (iter 1 & N) | **WARN**, write failure marker, continue | Oracle will NO-SHIP; preserves iteration loop |
| Reviewer | **WARN**, continue to oracle | Oracle can decide from QG report alone |
| Oracle | No special handling | Existing parser already treats empty/malformed output as NO-SHIP |
| Escalation | **WARN** only (`min_output=0`) | Best-effort, pipeline already terminal |

**Additional details:**
- Implementer failure writes a marker file to `.dark-factory/reviews/${SPEC_NAME}-impl-failure-iter${ITERATION}.md` as an audit trail
- `mkdir -p .dark-factory/reviews` guards against `--resume` case where spec-validator (which creates the directory) was skipped
- Synthetic exit code 99 doesn't conflict with Docker (125), timeout (124), or OOM (137)
- Old per-agent variables (`VALIDATOR_OUTPUT`, `SA_OUTPUT`, `IMPL_OUTPUT`, `REVIEW_OUTPUT`, `ESCALATION_OUTPUT`) replaced by shared `AGENT_OUTPUT`; `ORACLE_OUTPUT` preserved (assigned from `AGENT_OUTPUT`) because downstream parsing references it

**Also resolved as part of this fix — N1 (ANTHROPIC_API_KEY pre-flight check):** Added after `require_docker()` and before `setup_network()`. Fails fast with a clear error message explaining that Docker containers cannot use config-file auth. This prevents the most common cause of "all agents fail with opaque errors" — an unset API key that works fine on the host CLI but produces empty failures inside containers.

---

### MEDIUM — Status Summary

| ID | Finding | Status | Notes |
|----|---------|--------|-------|
| M1 | Documentation-implementation contradictions | **Resolved** | All doc-implementation contradictions fixed: templates, flags, placeholders, workflow variants synced. |
| M2 | Scenario quality is unvalidated | **Resolved** | Deterministic validation gate (`validate-scenarios.sh`) checks file count, assertion presence, trivial assertions, and empty test bodies. Runs as Step 2.5 in pipeline. |
| M3 | Spec validation score is non-deterministic | **Open** | Inherent to LLM-based scoring. No change. |
| M4 | Reviewer can leak scenarios to Implementer | **Open** | Acknowledged as known limitation. Docker cannot fix data flow leaks through feedback text. |
| M5 | Scenario Architect markdown fallback incompatible | **Open** | No change. |
| M6 | No path to regenerate buggy scenarios | **Open** | No change. Iteration loop only re-runs Implementer. |
| M7 | Resume logic skips on `.DS_Store` | **Resolved** | `has_scenario_files()` helper uses `find` with test file patterns instead of `ls -A`. Applied to all 4 scripts. |
| M8 | Config command variables allow arbitrary execution | **Open** | By design. Now runs inside containers (reduced blast radius). |

#### M1 Detail: Documentation-Implementation Contradictions — Resolved

All contradictions identified in v1 have been fixed:

- `--test-framework vitest` placeholder removed (prior commit)
- `[test framework]` / `[TEST_FRAMEWORK]` placeholders replaced with "the project's test framework" in WORKFLOW.md, skills/dark-factory-cycle.md, and .claude/skills/df-cycle/SKILL.md
- "Skip formal validation" replaced with "Run Steps 2-7 as normal (validation is fast for focused specs)"
- Parallel execution claim clarified: manual mode can parallelize, automated pipeline runs sequentially
- `write-spec.md` skill and `.claude/skills/df-write-spec/SKILL.md` updated to list all 8 template types
- SPECIFICATION-GUIDE.md template quick reference expanded to all 8 templates
- All 7 command-line flags (`--max-iterations`, `--test-cmd`, `--resume`, `--fresh`, `--ci`, `--skip-dep-check`, `--debug`) documented in WORKFLOW.md
- WORKFLOW.md workflow variants expanded from 3 to 7 (added DB Migration, Security Fix, Performance, Integration)

---

### LOW / MINOR — Status Summary

| ID | Issue | Status |
|----|-------|--------|
| L1 | `--max-iterations` accepts non-numeric values | **Resolved** — regex validation rejects non-positive-integer values |
| L2 | `mkdir -p` missing for `.dark-factory/reports/` | **Open** |
| L3 | Audit/state dirs not created by repo setup | **Open** (created on first run) |
| L4 | Python build check limited to 20 files | **Open** |
| L5 | Go scenario paths unlikely to work as packages | **Open** |
| L6 | Rust scenario test runs all integration tests | **Open** |
| L7 | Concurrent runs can corrupt state | **Open** |
| L8 | `stat` portability on Alpine | **Mitigated** — agents now run in containers where `coreutils` is installed, but stale-scenario check still runs on host |
| L9 | Spec Validator has no `--disallowedTools` | **Open** — now runs in a container with `:ro` mount, which limits write access |
| L10 | Escalation agent undocumented | **Open** |
| L11 | No circular dependency detection | **Open** |
| L12 | Template sections vs. validator rubric mismatch | **Open** |
| L13 | Scenario report has no canonical path | **Open** |
| L14 | Spec selector limited to 9 number keys | **Open** |
| L15 | Timeout configs not validated as integers | **Open** |
| L16 | Symlink risk with `--force` in integrate.sh | **Open** |
| L17 | Spec Validator access rules inconsistent across docs | **Open** |
| L18 | `PROJECT_ROOT` unused in quality-gate.sh | **Open** |

---

## New Observations (v2)

### N1. ANTHROPIC_API_KEY Must Be Set as Environment Variable — RESOLVED

The `run_agent()` function passes `-e ANTHROPIC_API_KEY` to Docker (line 344), which forwards the host environment variable into the container. If the variable is unset, Claude will fail to authenticate inside the container.

**Fix:** Pre-flight check added after `require_docker()` (line 727-732). Fails with a clear error message explaining that Docker containers cannot use config-file auth and provides the fix command (`export ANTHROPIC_API_KEY=your-key`). Resolved as part of the H5 fix.

### N2. Container Name Collisions on Parallel Runs (Low)

Container names use the pattern `df-${SPEC_NAME}-${role}-$$` (line 295). The PID (`$$`) provides uniqueness within a single shell session, but two users running the same spec simultaneously could collide. Docker would reject the second container with a name conflict error.

**Impact:** Low — parallel runs of the same spec are unlikely and would cause other problems (state file corruption, L7).

### N3. `check_symlink_safety` Scans Entire Project Tree — RESOLVED

**v2 problem:** The symlink check ran `find . -type l` from the project root for each masked directory. Slow on large repos.

**Fix:** Added optional `max_depth` parameter to `check_symlink_safety()`. Caller now scans project root at depth 1 (top-level symlinks), `.dark-factory/` recursively (framework directory), and each `SOURCE_DIRS` entry recursively (typically small dirs like `src/`, `lib/`). Avoids walking `node_modules/`, `.git/`, or other large trees.

### N4. `--read-only` Not Used on Containers — RESOLVED

**v2 problem:** The plan documented why `--read-only` was not used, but this reasoning was absent from the codebase.

**Fix:** Added explanatory comment in `run_agent()` after the tmpfs lines documenting that `--read-only` is intentionally omitted because Claude Code writes to arbitrary paths during execution, and the container is ephemeral (`--rm`) so writes are discarded.

---

## Structural Strengths (Updated)

The v1 strengths remain, plus:

1. **Docker isolation is architecturally sound** — The "mount is the barrier" approach (tmpfs masking over bind mounts) is elegant and impossible to circumvent from inside the container. This is the correct solution to C1.
2. **Defense-in-depth layering** — Three independent barrier mechanisms (Docker mounts, `--disallowedTools`, `--append-system-prompt`) means a failure in any one layer doesn't compromise the barrier.
3. **Per-role cache volumes** prevent a subtle attack vector (cache poisoning between agents).
4. **Container security hardening** (`--cap-drop=ALL`, `--security-opt=no-new-privileges`, resource limits) is thorough and follows Docker security best practices.
5. **The `--debug` flag** for container inspection is a practical operational tool.
6. **Cleanup trap** (`cleanup_containers` in EXIT handler) prevents orphaned container accumulation.
7. **Configurable source barrier** (`SOURCE_DIRS` as space-separated list) correctly handles multi-directory project layouts.
8. **Oracle parsing robustness** — case normalization + whitespace stripping + explicit malformed-output handling is the right pattern.
9. **`invoke_agent()` wrapper** centralizes failure detection (exit code + output length threshold), audit logging, and per-role failure policy in one place rather than duplicating error handling at every call site.
10. **API key pre-flight check** prevents the most common "all agents fail silently" scenario with a clear, actionable error message.

---

## Priority Recommendations (Ordered)

### Remaining from v1

1. ~~**Add scenario quality validation (M2)** — RESOLVED. Deterministic script-based validation gate added as Step 2.5 in the pipeline.~~
2. ~~**Fix `.DS_Store` resume skip (M7)** — RESOLVED. `has_scenario_files()` helper uses `find` with test file patterns.~~
3. ~~**Validate `--max-iterations` as integer (L1)** — RESOLVED. Regex validation rejects non-positive-integer values.~~

### New in v2

4. ~~**Scope symlink check to relevant dirs (N3)** — RESOLVED. Scoped to project root (depth 1), `.dark-factory/` (recursive), and `SOURCE_DIRS` (recursive).~~
5. ~~**Add comment explaining no `--read-only` (N4)** — RESOLVED. Comment added in `run_agent()`.~~

---

## Review Metadata

- **Reviewer:** Claude Opus 4.6 (automated)
- **Method:** Line-by-line audit of v2 implementation against v1 findings, reading actual source code for each claimed fix
- **Files analyzed:** `scripts/dark-factory.sh` (~1100 lines post-M2), `scripts/validate-scenarios.sh` (new, ~280 lines), `agents/oracle.md`, `agents/scenario-architect.md`, `templates/config.sh.template`, `scripts/integrate.sh`, `scripts/validate-setup.sh`, `docker/Dockerfile.*`, `docker/build-images.sh`, all updated documentation; M1 sync: `docs/WORKFLOW.md`, `docs/SPECIFICATION-GUIDE.md`, `skills/write-spec.md`, `skills/dark-factory-cycle.md`, `.claude/skills/df-cycle/SKILL.md`, `.claude/skills/df-write-spec/SKILL.md`
- **v1 findings resolved:** 11 of 10+2 (C1, C2, H1, H3, H4, H5, M1, M2, M7 resolved; H2 mitigated/downgraded; L1 resolved)
- **v1 findings open:** 5 medium (M3-M6, M8) + 17 low (L2-L18)
- **New findings:** 4 (N1, N3, N4 resolved; N2 low severity)
