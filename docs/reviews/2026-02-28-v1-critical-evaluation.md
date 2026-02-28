# Dark Factory Critical Evaluation — v1

**Date:** 2026-02-28
**Scope:** Full repository audit for autonomous zero-human-interaction software development
**Commit:** fa4b703 (Harden security, add operational tooling, and complete template coverage)

---

## Executive Summary

Dark Factory is architecturally well-conceived — the dual-blind information barrier model is sound in theory, the agent decomposition is clean, and the codebase shows genuine engineering rigor (config validation, audit logging, state management, resume/recovery). However, the implementation has **fundamental gaps between its security claims and actual enforcement** that must be resolved before it can be trusted for autonomous, zero-human-interaction production use.

**Overall assessment:** The framework produces meaningfully better output than a naive single-agent approach due to prompt-level separation, but the information barriers are defense-in-depth suggestions rather than hard technical controls.

---

## Findings by Severity

### CRITICAL

#### C1. Information Barriers Are Trivially Bypassable

The entire framework's value proposition rests on the information barrier — implementers can't see tests, test generators can't see code. This barrier is **enforced only for named file-access tools but not for the Bash tool**.

The deny list in `scripts/dark-factory.sh:426-428`:
```bash
IMPLEMENTER_DENY="Read(.dark-factory/scenarios/**),Edit(...),...,Glob(...)"
```

Blocks `Read`, `Edit`, `Write`, `Grep`, `Glob` — but any agent can run:
```
Bash("cat .dark-factory/scenarios/my-spec/edge-cases.test.ts")
```

Since every agent invocation uses `--dangerously-skip-permissions`, the Bash tool has unrestricted filesystem access. The `--disallowedTools` mechanism doesn't support blocking arbitrary Bash commands by content pattern in a way that can't be circumvented (the agent could use `less`, `head`, `find -exec`, `python -c "open(...).read()"`, etc.).

The post-hoc audit check (`audit_check_output` at line 108) is heuristic grep matching on agent stdout — it catches only blatant path mentions, not an agent that reads content and acts on it silently.

**Impact:** The core framework premise is unenforceable with current architecture.
**Recommendation:** True enforcement requires running agents in isolated filesystem environments (containers, chroot, or separate worktrees with restricted files physically removed). Alternatively, accept and document this as "defense-in-depth" rather than "guaranteed isolation."

#### C2. Quality Gate Failure Crashes the Pipeline Instead of Iterating

The pipeline uses `set -euo pipefail` (line 2). When `quality-gate.sh` exits with code 1 on failure (line 251), this propagates up and **terminates the entire pipeline** — the iteration loop never triggers.

The documented flow (AGENT.md, WORKFLOW.md, skill files) says: quality gate fails -> reviewer analyzes -> oracle decides NO-SHIP -> implementer gets feedback -> iterate. The **actual** flow is: quality gate fails -> pipeline aborts -> user sees error.

This breaks the core iteration loop. The only way the review/oracle/iterate cycle runs is if quality gates **pass** but the Oracle says NO-SHIP — a narrow and unlikely scenario.

**Impact:** The iterative convergence mechanism — the framework's self-healing capability — is non-functional for the most common failure mode.
**Recommendation:** Wrap the quality gate invocation in an explicit `if` block instead of relying on `set -e`. Capture the exit code and pass the quality gate report to the reviewer regardless.

---

### HIGH

#### H1. No Timeouts or Turn Limits on Claude Invocations

- `AGENT_MAX_TURNS` is defined in `config.sh.template` (line 110) and set to 200 by `integrate.sh` — but **never read or used by any script**. It is dead configuration.
- No `--max-turns` flag is passed to any `claude` invocation.
- No `timeout` command wraps the `claude --print` calls (unlike quality gate commands which do use `TIMEOUT_CMD`).

A single agent that enters an infinite tool-call loop would hang the pipeline forever, consuming unlimited API tokens and wall-clock time.

**Impact:** No guaranteed termination for a system claiming zero-human-interaction operation.
**Recommendation:** Pass `--max-turns "$AGENT_MAX_TURNS"` to every `claude` invocation and wrap them in `timeout`.

#### H2. `--dangerously-skip-permissions` on Every Agent

Every `claude` call (7 invocations in `dark-factory.sh`) uses `--dangerously-skip-permissions`. This means every agent has unrestricted OS access — it can delete files, modify git history, install packages, make network requests, or exfiltrate data. Combined with the Bash tool gap (C1), the agents run with root-level trust.

**Impact:** No least-privilege enforcement for any agent.
**Recommendation:** Document the threat model explicitly and consider running the pipeline in a sandboxed environment (container, VM, CI runner with limited network).

#### H3. Oracle Output Parsing Is Fragile

The Oracle's output is parsed at `dark-factory.sh:650`:
```bash
head -1 | grep -qE '^SHIP\b'
```

If the Oracle's first line is anything other than exactly `SHIP` or `NO-SHIP` (e.g., `"# Oracle Decision"`, a markdown header, or an error message), **neither condition matches**. The variable `SHIP` stays `false`, the loop increments `ITERATION`, and the pipeline silently wastes an iteration with no actual oracle decision.

The Oracle agent definition (`oracle.md` line 93) specifies a structured markdown output format starting with headers, which contradicts the pipeline's expectation of a bare `SHIP`/`NO-SHIP` on line 1.

**Impact:** Malformed Oracle output causes silent iteration waste with no recovery.
**Recommendation:** Add explicit detection of malformed Oracle output and fail fast with a clear error message.

#### H4. Scenario Architect Source Barrier Is Incomplete

The deny list blocks access to `${SOURCE_DIR:-src}/**`. This means:

- Code in `lib/`, `app/`, `pages/`, `components/`, or root-level files is **unprotected**
- `package.json`, `Cargo.toml`, `go.mod` (revealing implementation dependencies) are accessible
- `git log`, `git diff` via Bash reveals all implementation changes
- If test infrastructure lives inside `SOURCE_DIR`, the Scenario Architect is blocked from legitimate access to it

**Impact:** Barrier protects only one directory; real projects have code in many locations.
**Recommendation:** Make the source barrier configurable as a list of paths, and restrict git commands.

#### H5. `|| true` Suppresses All Claude Failures

Every `claude` invocation ends with `2>&1) || true` (lines 509, 550, 591, etc.). If Claude is not installed, crashes, times out, hits rate limits, or has auth failures, the pipeline silently continues with empty output. This masks critical failures and produces confusing downstream behavior.

**Impact:** Infrastructure failures are invisible; the pipeline produces garbage output instead of failing clearly.
**Recommendation:** Check for empty or error output after each Claude invocation and fail with a clear message.

---

### MEDIUM

#### M1. Documentation-Implementation Contradictions

| Doc claim | Reality |
|-----------|---------|
| WORKFLOW.md: `--test-framework vitest` flag | Flag doesn't exist. Correct: `--test-cmd "npx vitest run"` |
| WORKFLOW.md: Steps 3 & 4 "can run in parallel" | `dark-factory.sh` runs them strictly sequentially |
| WORKFLOW.md: Step numbering 0-8 | Script uses Step 1-6 with different numbering |
| CLAUDE.md "For a single feature": skip to write spec + run pipeline | Missing prerequisite `integrate.sh` step |
| WORKFLOW.md Quick Fix: "Skip formal validation" | No flag exists to skip validation in `dark-factory.sh` |
| `write-spec.md` skill: 4 template types | 8 templates actually exist (missing db-migration, performance, security-fix, integration) |
| SPECIFICATION-GUIDE.md: 4 templates listed | Same 4 missing from the quick reference table |
| `--ci` and `--skip-dep-check` flags | Not documented anywhere in `docs/` |

#### M2. Scenario Quality Is Unvalidated

No gate verifies that the Scenario Architect produced **meaningful** test scenarios. The pipeline checks:
- Do scenario files exist? (line 531)
- Can they execute? (via `run-scenarios.sh`)

But it doesn't check:
- Are the scenarios testing the right things?
- Do they have adequate coverage?
- Are they trivially passing? (e.g., `assert(true)`)
- Are they actually derived from the spec vs. generic boilerplate?

Empty or trivially-passing scenarios would let bad code ship, defeating the framework's purpose.

#### M3. Spec Validation Score Is Non-Deterministic

The 100% completeness gate (`dark-factory.sh:163`) depends on an LLM-produced score. The same spec can receive different scores across runs. This makes the quality standard probabilistic rather than deterministic. A mediocre spec could pass on a "generous" run.

#### M4. Reviewer Can Leak Scenarios to Implementer

The Reviewer has full access to everything including scenario source code. The agent definition says "Do NOT reveal test scenario source code" — but this is prompt-enforced only. If the Reviewer quotes test assertions or scenario logic in its feedback, that feedback is passed directly to the Implementer in the next iteration. Scenario names often encode what's being tested (e.g., `test_empty_input_returns_400`), leaking intent even without quoting code.

#### M5. Scenario Architect Markdown Fallback Is Incompatible with Runner

The Scenario Architect agent definition provides a "portable markdown format" fallback when no test framework is specified. But `run-scenarios.sh` auto-detects a test framework (`vitest`/`jest`/`pytest`/`go test`/`cargo test`) and runs the scenario directory with it. These test runners cannot execute markdown files. If the Scenario Architect produces the markdown format, the pipeline will fail at quality gate with a confusing error.

#### M6. No Path to Regenerate Buggy Scenarios During Iteration

If blind scenarios themselves are buggy (testing something not in spec, wrong imports/syntax), the pipeline iterates the implementation three times against broken tests, then escalates. No mechanism in any skill detects "scenarios are the problem" or triggers re-generation. The iteration loop only re-runs the Implementer, never the Scenario Architect.

#### M7. Resume Logic Can Skip Scenario Generation Incorrectly

At `dark-factory.sh:531`, the check is `[[ -d "$SCENARIOS_DIR" ]] && [[ -n "$(ls -A "$SCENARIOS_DIR")" ]]`. If the directory exists and contains **any** file (even `.DS_Store` on macOS), scenario generation is skipped. This could silently skip generation after a failed/partial previous run.

#### M8. Config Command Variables Allow Arbitrary Code Execution

Variables like `BUILD_CMD`, `LINT_CMD`, `TEST_CMD` are designed to contain shell commands and are passed to `bash -c`. This is by design, but it means anyone who can modify `config.sh` has arbitrary code execution. The config validation only checks structural format, not the safety of command values.

---

### LOW / MINOR

| ID | Issue | Location |
|----|-------|----------|
| L1 | `--max-iterations` accepts non-numeric values, crashes with confusing error | `dark-factory.sh:358` |
| L2 | `mkdir -p` missing for `.dark-factory/reports/` before writing report | `quality-gate.sh:93` |
| L3 | `.dark-factory/audit/` and `.dark-factory/state/` referenced in CLAUDE.md but directories not created by repo setup | Directory structure |
| L4 | Python build check limited to first 20 files | `quality-gate.sh:164` |
| L5 | Go scenario test paths (`.dark-factory/scenarios/spec-name/...`) unlikely to work as Go package paths | `run-scenarios.sh:128` |
| L6 | Rust scenario test command (`cargo test --test '*'`) runs all integration tests, not just blind scenarios | `quality-gate.sh:225` |
| L7 | Concurrent runs can corrupt state (flock is best-effort with `\|\| true`) | `dark-factory.sh:86` |
| L8 | `stat` command portability issues on minimal Linux systems (Alpine) | `dark-factory.sh:243-246` |
| L9 | Spec Validator invoked with no `--disallowedTools` at all (agent definition says it has restrictions) | `dark-factory.sh:508` |
| L10 | Escalation report agent is undocumented as a pipeline stage | `dark-factory.sh:687` |
| L11 | No circular dependency detection at pipeline runtime (only at plan creation) | `dark-factory.sh:172-226` |
| L12 | Spec template sections don't map 1:1 to validator's 10-section scoring rubric | Template/agent mismatch |
| L13 | Scenario generation report has no canonical output path; downstream agents can't reliably locate it | Agent definition gap |
| L14 | Spec selector only handles up to 9 specs via number keys | `dark-factory.sh:321-326` |
| L15 | `GATE_TIMEOUT` and `SCENARIO_TIMEOUT` not validated as integers | Config validation gap |
| L16 | Symlink following risk when `--force` used with `integrate.sh` (`rm -rf` on destination) | `integrate.sh:155-156` |
| L17 | Spec Validator access rules differ between CLAUDE.md (no restrictions) and `spec-validator.md` (cannot read code/tests/reviews) | Documentation inconsistency |
| L18 | `PROJECT_ROOT` computed but unused in `quality-gate.sh` | `quality-gate.sh:10` |

---

## Structural Strengths

Despite the issues above, the framework demonstrates real sophistication:

1. **The information barrier concept is genuinely novel and valuable** — even with soft enforcement, the prompt-level separation produces meaningfully different behavior than a single-agent approach
2. **The escalation protocol** (max 3 iterations, then human review) prevents infinite loops and has excellent documentation (`ESCALATION.md`)
3. **Config validation** (structural regex + metacharacter checks) shows security awareness
4. **Audit logging** with heuristic violation detection is better than nothing
5. **Resume/recovery support** with state files enables crash recovery
6. **Template coverage** is exceptional (8 spec types + project plan + escalation report + config + CLAUDE.md addition)
7. **The 8-layer scenario generation strategy** in the Scenario Architect definition is genuinely thorough
8. **The `integrate.sh` script** is well-designed with dry-run support, symlink mode, force-overwrite, and automatic language detection
9. **The `ESCALATION.md` document** is particularly strong — real decision trees, common failure patterns, re-entry procedures, stale scenario warnings

---

## Priority Recommendations (Ordered)

1. **Fix `set -e` vs. iteration contradiction (C2)** — Easiest high-impact fix. Wrap quality gate invocation to capture exit code instead of crashing.
2. **Wire up `--max-turns` and timeouts for Claude invocations (H1)** — Connect the existing dead `AGENT_MAX_TURNS` config to actual `claude` flags.
3. **Add Oracle output validation (H3)** — Detect and handle malformed oracle decisions instead of silently wasting iterations.
4. **Document the actual security model honestly (C1, H2)** — The barriers are defense-in-depth, not hard isolation. Document this clearly.
5. **Handle empty/error Claude output (H5)** — Check for empty output after each invocation and fail with a clear message.
6. **Sync documentation with implementation (M1)** — Fix step numbering, remove nonexistent `--test-framework` flag, document `--ci`/`--resume`/`--fresh`/`--skip-dep-check`.
7. **Expand `write-spec` skill and SPECIFICATION-GUIDE.md (M1)** — Reference all 8 template types.
8. **Make source barrier configurable (H4)** — Support a list of paths instead of a single `SOURCE_DIR`.
9. **Consider container/sandbox deployment for true isolation (C1)** — Long-term architectural improvement.
10. **Add scenario quality validation (M2)** — At minimum, check that scenarios contain assertions and cover spec requirements.

---

## Review Metadata

- **Reviewer:** Claude Opus 4.6 (automated)
- **Method:** 6 parallel sub-agent audits covering: repository structure, shell scripts, agent definitions, skill files, templates/docs, and root config files
- **Files analyzed:** All 52 content files in the repository
- **Lines analyzed:** ~5,021 lines of documentation, ~1,834 lines of shell scripts
