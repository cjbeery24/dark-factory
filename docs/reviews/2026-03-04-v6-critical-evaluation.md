# Dark Factory -- Critical Evaluation (v6)

**Date:** 2026-03-04
**Reviewer:** Claude Opus 4.6 (automated)
**Scope:** Full repository audit, v5 remediation verification, new finding discovery, production-readiness gap analysis

---

## Overview

This v6 evaluation builds on the v5 review by: (1) verifying which v5 findings have been remediated, (2) identifying new bugs, security issues, and architectural gaps missed by all prior reviews (v1–v5), and (3) assessing production-readiness for a true zero-human-interaction dark factory comparable to StrongDM's approach. The analysis covers all scripts (~2,500 lines), Docker infrastructure, agent definitions (7 roles), skills (7 definitions), templates (14 files), tests (~1,275 lines), and documentation (11 files).

---

## v5 Remediation Status

### Fixed

| v5 # | Finding | Evidence |
|---|---|---|
| B1 | Cleanup trap installed after Docker operations | Trap now installed before `setup_networks`/`setup_proxies` |
| B2 | Tinyproxy `FilterURLs On` breaks CONNECT tunnels | Removed; `FilterDefaultDeny Yes` + regex allowlist; integration tests verify |
| B3 | Evaluator has unrestricted internet access | `df-evaluation` network now `--internal`; dynamic proxy with hostname extraction from `DEPLOY_BASE_URL` |
| B4 | Python `BUILD_CMD` uses `$(find ...)` (unsafe) | Replaced with `find -exec`-style patterns in quality-gate.sh |
| B5 | Quality gate failures don't block pipeline | Exit code 2 for build failures; `HARD_GATE_BUILD=true` blocks pipeline |
| B6 | Oracle feedback file not validated for existence | Audit log warning when feedback file absent |
| B7 | Regression failures silently continue | `REGRESSION_POLICY=block` (default); exits with code 2 |
| B8 | `read_state` returns stale default on empty value | Empty value handling with explicit defaults tested in `test-state.sh` |
| B9 | Agent retries not logged | Retry count and reason logged to audit trail |
| B10 | No post-build image verification | `build-images.sh` runs verification container to check installed claude-code version |

### Still Open

| v5 # | Finding | Severity | Notes |
|---|---|---|---|
| S3 | Prompt injection via spec content | Medium-High | No spec sanitization implemented; specs remain a trusted input boundary |
| S5 | Implementer has full project write access | Medium | Still mounts entire project as `:rw`; can modify `config.sh`, pipeline scripts, agent definitions |
| S8 | Audit log detects only naive violations | Medium | Path-string matching only; no semantic leakage detection |
| 8 | Go scenario runner silently finds no tests | Low | Documented; requires user-set `SCENARIO_TEST_CMD` |
| 15 | Skills bypass all enforcement layers | High | Warning banners only; no technical barrier possible in current architecture |
| 22 | `noexec` and `--read-only` not implemented | Low | Agents can write/execute arbitrary binaries in container |
| 24 | Mask config procedural, not declarative | Low-Medium | `case` statement in `docker.sh` |

---

## New Findings (Not in v1–v5)

### N1. Arbitrary Code Execution via `eval "$DEPLOY_CMD"`

**Severity: Critical**

**Files:** `scripts/dark-factory.sh:51` (teardown), `scripts/dark-factory.sh:695` (deploy)

```bash
eval "$DEPLOY_CMD"
eval "$DEPLOY_TEARDOWN_CMD"
```

`DEPLOY_CMD` and `DEPLOY_TEARDOWN_CMD` are sourced from `config.sh`, which undergoes `validate_config_values_raw()` pre-source validation. However, the raw-value check only catches `$`, backticks, and `$()` on the right-hand side of assignments. Once the config is sourced as bash, values are shell-expanded, then passed to `eval` — creating a double-evaluation. A carefully crafted config value that passes the regex checks but contains shell metacharacters after expansion (e.g., newlines, semicolons, or heredoc constructs within quoted strings) could execute arbitrary commands.

**Recommendation:** Replace `eval "$DEPLOY_CMD"` with `bash -c "$DEPLOY_CMD"` (single evaluation, isolated subshell) or better, require `DEPLOY_CMD` to be a path to an executable script rather than an arbitrary shell string. Validate that the path exists and is a regular file before execution.

---

### N2. State Lock Falls Back to No-Lock on Timeout

**Severity: High**

**File:** `scripts/lib/state.sh` — `_df_acquire_lock()`

The locking mechanism retries `mkdir` for up to 10 seconds, then returns failure. However, `write_state()` calls `_df_acquire_lock ... || true`, meaning lock acquisition failure is silently ignored and the write proceeds without synchronization.

```bash
_df_acquire_lock "$lockdir" "$timeout" || true  # PROCEEDS WITHOUT LOCK
```

Two concurrent pipeline runs on the same spec will race on state file reads/writes after the timeout. Corruption of iteration counters, status flags, or spec hashes could cause one run to skip quality gates or repeat already-completed iterations.

**Recommendation:** Make locking fail-closed: if `_df_acquire_lock` fails, `write_state` should return non-zero and the caller should abort. Add a global per-spec mutex (e.g., `mkdir .dark-factory/state/${SPEC_NAME}.lock`) acquired at pipeline start and held for the duration.

---

### N3. Oracle Feedback Has No Technical Barrier Against Scenario Leakage

**Severity: High**

**Files:** `agents/oracle.md`, `scripts/dark-factory.sh` (feedback file write)

Oracle feedback uses "spec-section-anchored references (FR-N, EC-N + behavioral category)" per the spec. However, there is **zero automated enforcement**. Nothing prevents the Oracle from writing:

```
Scenario scenario_07_concurrent_requests.test.js fails because your socket cleanup
has a race condition in the close handler.
```

Instead of the intended:

```
EC-4 (concurrent request handling) needs review. Failures suggest race conditions
in resource cleanup logic.
```

The audit log (`audit_check_output`) only detects path-string patterns like `.dark-factory/scenarios` — it does not detect scenario filenames, assertion text, or test framework method names embedded in natural language.

Furthermore, the barrier **weakens with each iteration**: the Implementer can infer which spec sections have test coverage from the referenced sections, and by iteration 3 can reconstruct a substantial portion of the test strategy.

**Recommendation:**
1. Add a feedback validation step between Oracle output and Implementer input that scans for patterns: `scenario_\d+`, `\.test\.(js|ts|py|go|rs)`, `\.spec\.`, `test_\w+`, `assert`, `expect(`, `it('`, `describe('`
2. If barrier-violating content is detected, regenerate the feedback with an explicit "rewrite without test details" instruction
3. Log violations as `SEMANTIC_LEAKAGE_DETECTED` in audit

---

### N4. `curl` Health Check Has No Connect/Max-Time Timeout

**Severity: Medium**

**File:** `scripts/dark-factory.sh:704`

```bash
curl -sf -o /dev/null "$_health_url"
```

This runs inside a loop with 1-second sleep and `DEPLOY_HEALTH_TIMEOUT` as the outer bound. However, `curl` defaults to ~300 seconds for connection timeout. If the health endpoint accepts TCP connections but never responds (SYN-ACK, then hang), each `curl` invocation blocks for up to 5 minutes. With a 60-second outer timeout and 1-second sleep, only ~1 health check runs before the outer timeout fires — and the outer timeout itself may not kill the blocking `curl` child process.

**Recommendation:** Add explicit timeouts:
```bash
curl --connect-timeout 5 --max-time 10 -sf -o /dev/null "$_health_url"
```

---

### N5. Docker Network RUN_ID Collision in CI

**Severity: Medium**

**File:** `scripts/lib/docker.sh:28-35` (`_df_net()`)

`RUN_ID` is composed of timestamp + `$$` (PID). PIDs recycle on most systems within the range 1–32768 (or 1–4194304 on Linux). In CI environments with parallel jobs:
- Two jobs starting at the same second with recycled PIDs produce identical `RUN_ID`
- `cleanup_networks()` deletes all networks matching the `RUN_ID`, potentially destroying another job's networks mid-run
- `cleanup_containers()` kills containers with the same prefix

**Recommendation:** Add entropy to `RUN_ID` — e.g., append `$RANDOM` or use `uuidgen`/`cat /proc/sys/kernel/random/uuid` when available. Alternatively, use Docker's built-in `--label` for container identification instead of name-based matching.

---

### N6. Quality Gate Report Format Leaks to Reviewer via Prompt

**Severity: Medium**

**Files:** `scripts/lib/docker.sh:224` (reviewer mask), `scripts/quality-gate.sh` (report generation)

The Reviewer agent has `.dark-factory/reports/` masked via tmpfs. However, the quality gate report **summary** is passed directly in the Reviewer's system prompt by the orchestration script. The report format is not schema-controlled — if `quality-gate.sh` captures raw test output, assertion failures, or stack traces, that content is injected into the Reviewer's prompt, bypassing the filesystem mask entirely.

Current output includes `tail -50` of gate output, which for test failures typically contains assertion text, expected/actual values, and test names — all of which leak scenario details.

**Recommendation:**
1. Define a strict schema for the quality gate summary passed to Reviewer: pass/fail counts per category, spec-section references, build/lint status only
2. Strip raw test output, assertion content, and scenario filenames before prompt injection
3. Consider a dedicated "sanitize_report_for_reviewer" function in `quality-gate.sh`

---

### N7. Regression Testing Silently Skips on Missing State

**Severity: Medium**

**File:** `scripts/dark-factory.sh:881`

```bash
[[ "$_other_status" != "shipped" ]] && continue
```

If a previously-shipped spec's `.state` file is deleted, corrupted, or unreadable, the regression loop silently skips that spec. No warning is emitted. The pipeline reports success without running regression tests against all shipped specs, giving false confidence in backward compatibility.

**Recommendation:** Log a warning when a shipped spec's state is unreadable. Consider tracking shipped specs in a dedicated manifest file (`.dark-factory/state/shipped-specs.txt`) that is independent of individual state files.

---

### N8. Config Validation Doesn't Catch All Injection Vectors

**Severity: Medium**

**File:** `scripts/lib/config.sh:88-95`

`validate_config_values_raw()` checks for `$`, backticks, and `$(` in config values before sourcing. However, it does not catch:
- Null bytes (`\x00`) which can truncate strings in some bash versions
- Carriage returns (`\r`) which can hide malicious content in terminal output
- Escape sequences (`\e[`) which can manipulate terminal display
- Semicolons after quoted values (e.g., `VAR="safe"; rm -rf /`)
- Heredoc constructs within values

While the structural validation (`validate_config`) rejects non-assignment lines, a single-line assignment with embedded semicolons after the value would pass both checks if the semicolon is inside the quoted value and the command follows after the closing quote.

**Recommendation:** Add validation for control characters (bytes < 0x20 except tab/newline), semicolons outside quotes, and pipe characters. Consider parsing config with a restricted evaluator rather than sourcing as bash.

---

### N9. Implementer Can Modify Pipeline Infrastructure

**Severity: Medium (escalation of v5-S5)**

**File:** `scripts/lib/docker.sh:223` (`project_mode="rw"`)

This is an escalation of v5-S5 (Implementer has full project write access). The Implementer container can write to:
- `scripts/` — modify pipeline orchestration, quality gates, barrier logic
- `docker/` — modify Dockerfiles, proxy configs, filter allowlists
- `agents/` — modify agent role definitions and barrier instructions
- `.dark-factory/config.sh` — disable egress filtering, change timeouts, alter policies
- `CLAUDE.md` — modify project-level instructions

A misbehaving Implementer could weaken its own barriers for subsequent iterations by modifying the config or agent definitions. While the Implementer cannot read scenarios (tmpfs mask), it could modify the pipeline to skip quality gates on the next iteration.

**Impact is bounded:** Changes would only affect the current iteration if the pipeline re-sources config. However, if the pipeline is run again (e.g., after a NO-SHIP), the modified infrastructure persists.

**Recommendation:** Mount the project with targeted write access:
```bash
# Read-only base mount
-v "$PROJECT_DIR:/workspace:ro"
# Write-only overlays for source and test directories
-v "$PROJECT_DIR/src:/workspace/src:rw"
-v "$PROJECT_DIR/tests:/workspace/tests:rw"
```
This requires iterating over `SOURCE_DIRS` and `TEST_DIR` to create per-directory bind mounts.

---

### N10. No Oscillation Detection in Iteration Loop

**Severity: Medium**

**File:** `scripts/dark-factory.sh` (iteration loop, lines ~538-700)

The pipeline tracks `SCENARIO_PASS_RATE` per iteration and stores it in `_DF_PASS_RATES` (pipe-delimited). However, there is no analysis of whether the pass rate is oscillating (e.g., 60% → 80% → 60%) or monotonically degrading (80% → 50% → 30%). The pipeline continues blindly until max iterations, then escalates.

An oscillating pass rate strongly suggests the Implementer is fixing one set of failures while introducing another — a pattern that additional iterations will not resolve. Detecting this early (after iteration 2) would save compute and provide better diagnostic information for escalation.

**Recommendation:** After iteration 2, compare pass rates:
- If `rate[N] <= rate[N-1]` and `rate[N] <= rate[N-2]` (monotonic degradation): escalate early with "degrading quality" diagnosis
- If `|rate[N] - rate[N-2]| < threshold` and direction alternates: escalate with "oscillation" diagnosis
- Log pass-rate trajectory in metrics for post-mortem analysis

---

### N11. Temporary Files Not Collision-Safe in Evaluator Proxy Setup

**Severity: Low-Medium**

**File:** `scripts/lib/docker.sh:154-158`

```bash
_DF_EVAL_FILTER_TMP="$(mktemp)"
_DF_EVAL_CONF_TMP="$(mktemp)"
```

While `mktemp` itself is safe, the variables are global and set without checking for existing values. If `setup_proxies()` is called twice (e.g., during retry logic), the previous temp file paths are overwritten without cleanup, leaking the original files.

**Recommendation:** Check and clean existing temp files before creating new ones:
```bash
[[ -n "${_DF_EVAL_FILTER_TMP:-}" ]] && rm -f "$_DF_EVAL_FILTER_TMP"
_DF_EVAL_FILTER_TMP="$(mktemp)"
```

---

### N12. Integer Division in Pass Rate Calculation

**Severity: Low**

**File:** `scripts/quality-gate.sh:270`

```bash
rate=$((passed * 100 / total))
```

Bash integer division truncates: 1 pass out of 3 total = 33%, not 33.33%. More critically, 99 out of 100 = 99%, but 2 out of 3 = 66% (not 67%). This affects pass-rate thresholds — a `MIN_SHIP_PASS_RATE=67` would fail a 2/3 pass rate even though it rounds to 67%.

**Recommendation:** Use rounding: `rate=$(( (passed * 100 + total / 2) / total ))` or document that pass rates use floor division.

---

### N13. `test-dark-factory-args.sh` Minimal Coverage

**Severity: Low**

**File:** `tests/test-dark-factory-args.sh` (39 lines)

This test file only covers `--max-iterations` and `--test-cmd` missing-value errors. It does not test:
- `--fresh` flag behavior
- `--resume` flag behavior
- `--regression` flag behavior
- `--regen-scenarios` flag behavior
- Multiple flags in combination
- Invalid spec name as positional argument

**Recommendation:** Expand argument parsing tests to cover all CLI flags, especially `--fresh` (which deletes state) and `--resume` (which reads state).

---

### N14. No Log Rotation for Audit or Metrics

**Severity: Low**

**Files:** `scripts/lib/audit.sh`, `scripts/lib/metrics.sh`

Audit logs (`.dark-factory/audit/`) and metrics (`.dark-factory/metrics.jsonl`) grow unbounded. After 100+ pipeline runs, audit logs could reach 10+ MB and metrics files could accumulate thousands of JSONL records. There is no rotation, compression, or archival mechanism.

**Recommendation:** Add optional log rotation: after N runs or M megabytes, rotate `metrics.jsonl` to `metrics.jsonl.1` and truncate. For audit logs, consider per-run files (already partially implemented via `RUN_ID`) with periodic cleanup of logs older than N days.

---

### N15. Platform-Specific `date` Commands Fragile on Non-Standard Systems

**Severity: Low**

**Files:** `scripts/dark-factory.sh:252,258`, `scripts/lib/tui.sh:21-24`

The codebase uses `date -j -f` (macOS) with fallback to `date -d` (GNU/Linux). This fails silently on:
- Alpine Linux (BusyBox `date` has neither `-j` nor `-d` in some builds)
- Windows Git Bash (MSYS2 `date` varies by installation)
- FreeBSD (has `-j` but different `-f` semantics)

While Dark Factory targets Docker containers (Alpine with coreutils installed), the host-side scripts that set up Docker also use these date commands.

**Recommendation:** Pin `coreutils` in host-side prerequisites documentation, or use a POSIX-only date approach where possible.

---

## Production-Readiness Gap Analysis

### Comparison to StrongDM-Style Dark Factory

| Capability | Dark Factory (Current) | Production Dark Factory | Gap Severity |
|---|---|---|---|
| **Information barriers** | Strong (tmpfs isolation, defense-in-depth) | Equivalent or stronger | Low |
| **Supply chain hardening** | Strong (digest pins, lockfiles, post-build verify) | Equivalent | Low |
| **Egress filtering** | Strong (tinyproxy allowlists, `--internal` networks) | Equivalent | Low |
| **CI/CD integration** | Manual-trigger GitHub Actions, no resume | Durable CI pipelines with auto-resume, parallel spec execution | High |
| **Cross-spec regression** | Per-spec only, fragile state files | Suite-level regression with persistent test registry | High |
| **Ephemeral environments** | Optional evaluator with manual `DEPLOY_CMD` | Automated provisioning, health verification, teardown | Medium-High |
| **Metrics-driven autonomy** | Static `AUTONOMY_LEVEL` config flag | Dynamic trust scoring based on historical success rates | Medium-High |
| **Oscillation/degradation detection** | None (blind iteration until max) | Pass-rate trajectory analysis, early escalation | Medium |
| **Canary/progressive rollout** | None (quality gate → shipped) | Staged deployment, traffic shifting, rollback triggers | Medium |
| **Concurrent execution** | Unsafe (state race conditions, network collisions) | Process-safe with proper locking and isolation | Medium |
| **Observability** | Basic audit log + JSONL metrics | Structured logging, dashboards, alerting | Medium |
| **Spec sanitization** | None (trusted input) | Input validation, injection detection | Medium |
| **Cost controls** | Turn-budget proxy only | Token tracking, cost attribution per spec, budget alerts | Low-Medium |

### Missing Capabilities for Zero-Human-Interaction

1. **Durable CI pipeline integration.** The GitHub Actions workflow is manual-trigger only with a 60-minute hard timeout and no resume support. A production dark factory needs: auto-triggered pipeline on spec commit, durable job state across CI restarts, parallel spec execution, and artifact persistence.

2. **Cross-spec regression suite.** After spec F-4 ships, specs F-1 through F-3 are never re-tested. Shipping a new spec can silently break previously-shipped functionality. Need: a shipped-scenario registry, automatic regression runs after any SHIP decision, and regression failure blocking.

3. **Ephemeral environment provisioning.** The evaluator agent requires manual `DEPLOY_CMD` configuration. A production system needs: automated environment creation from infrastructure-as-code, health verification with exponential backoff, deterministic teardown, and cost tracking.

4. **Dynamic trust calibration.** `AUTONOMY_LEVEL` is a static integer in `config.sh`. A production system should adjust oversight dynamically: specs with high historical first-pass rates get less oversight, new spec types or high-complexity specs get more. Feedback loop from SHIP/NO-SHIP outcomes into trust scores.

5. **Intelligent iteration control.** The current pipeline iterates blindly up to `MAX_ITERATIONS` (default: 3). A production system should: detect oscillating pass rates and escalate early, detect monotonic degradation and switch strategies (e.g., smaller implementation scope), and track which spec sections cause persistent failures.

---

## Findings Summary

### By Severity

| Severity | Count | Finding IDs |
|---|---|---|
| Critical | 1 | N1 |
| High | 2 | N2, N3 |
| Medium | 6 | N4, N5, N6, N7, N8, N9, N10 |
| Low-Medium | 1 | N11 |
| Low | 4 | N12, N13, N14, N15 |

### By Category

| Category | Findings |
|---|---|
| Security | N1, N3, N6, N8, N9 |
| Reliability | N2, N4, N5, N7, N11 |
| Quality | N10, N12, N13 |
| Operations | N14, N15 |

---

## Recommended Remediation Priority

### P0 — Must Fix Before Production Use

1. **N1: Replace `eval` with direct execution** — Use `bash -c` or require script paths for `DEPLOY_CMD`/`DEPLOY_TEARDOWN_CMD`
2. **N9/S5: Scope Implementer write access** — Mount only `SOURCE_DIRS` + `TEST_DIR` as `:rw`, everything else `:ro`
3. **N2: Make state locking fail-closed** — Abort on lock timeout instead of proceeding without lock

### P1 — High Priority

4. **N3: Add Oracle feedback validation** — Scan for scenario filenames and test framework patterns; reject and regenerate on violation
5. **N6: Schema-control quality gate report for Reviewer** — Strip raw test output before prompt injection
6. **N5: Add entropy to RUN_ID** — Prevent network collisions in CI environments

### P2 — Medium Priority

7. **N4: Add curl timeouts to health check** — `--connect-timeout 5 --max-time 10`
8. **N7: Warn on missing regression state** — Log when shipped spec state is unreadable
9. **N8: Expand config validation** — Catch control characters, semicolons, escape sequences
10. **N10: Add oscillation detection** — Compare pass-rate trajectory after iteration 2

### P3 — Low Priority

11. **N11: Guard temp file creation** — Clean existing before recreating
12. **N12: Fix integer division rounding** — Use ceiling or rounding in pass rate calculation
13. **N13: Expand CLI argument tests** — Cover all flags and combinations
14. **N14: Add log rotation** — Prevent unbounded growth of audit and metrics files
15. **N15: Document platform requirements** — Pin coreutils for host-side scripts

---

## Strengths Acknowledged

This evaluation focuses on findings, but the framework has significant strengths that should not be overlooked:

1. **Tmpfs-based information barriers** are genuinely novel and effective — files don't exist in the container, not merely permission-blocked
2. **Three-layer defense-in-depth** (Docker isolation → `--disallowedTools` → prompt barriers) catches failures at multiple levels
3. **Digest-pinned supply chain** with lockfile-based transitive dependency pinning exceeds most production systems
4. **Comprehensive test suite** (1,275 lines, 187+ assertions) covering security-critical paths
5. **Pre-flight symlink and hardlink checks** close filesystem-based barrier circumvention vectors
6. **Egress filtering with integration tests** verifies proxy behavior, not just configuration
7. **Iterative security improvement** — five prior evaluations with systematic remediation demonstrates a mature security posture

The framework's core isolation architecture is sound. The findings above are primarily about hardening edge cases, closing operational gaps, and adding the infrastructure needed for true autonomous operation at scale.
