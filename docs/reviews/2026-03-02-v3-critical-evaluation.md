# Dark Factory — Critical Evaluation (v3)

**Date:** 2026-03-02
**Reviewer:** Claude Sonnet 4.6 (automated)
**Scope:** Full repository audit for security, reliability, and design quality

---

## Overview

Dark Factory is a sophisticated, well-thought-out framework. The information barrier concept is sound and the layered enforcement is genuinely good engineering. This document records all real concerns found during a thorough review of the codebase.

---

## Findings

### 1. Network Exfiltration — The Primary Barrier Has a Hole

**Severity: High → ~~resolved~~ (see status)**

**Status: Fixed — 2026-03-02**

The Docker containers have outbound internet access by default (`--network df-pipeline` is a user-defined bridge with no egress rules). Meanwhile, the entrypoint is `claude -p --dangerously-skip-permissions`. That flag disables Claude Code's permission system entirely — the agent can run arbitrary Bash commands without prompting.

If a containment failure occurred (prompt injection in a spec, a future Claude behavior change, etc.), the agent could `curl` restricted content to an external endpoint before the audit log even catches it. The audit log only scans text output, not network traffic.

**Fix implemented:** Proxy sidecar + Docker `--internal` networks. Containers on `--internal` networks have no default gateway — there is no route to the internet at the Linux level inside Docker Desktop's VM. All outbound HTTPS is routed through a tinyproxy sidecar:

- `df-analysis` network (spec-validator, scenario-architect, reviewer, oracle, escalation): `api.anthropic.com` only
- `df-execution` network (implementer, quality-gate): Anthropic API + npm/PyPI/Go/Rust package registries

`WebSearch` and `WebFetch` tools are also added to every agent's `--disallowedTools` list as complementary defense-in-depth. A direct `curl attacker.com` now has no route — the internal network has no gateway and the proxy rejects non-allowlisted hostnames with HTTP 403.

The `EGRESS_FILTERING_ENABLED` config variable (default `true`) allows disabling for debugging; a startup warning is printed when disabled.

---

### 2. `--dangerously-skip-permissions` Is Load-Bearing and Underdocumented

**Severity: High → ~~resolved~~ (see status)**

**Status: Fixed — 2026-03-02**

`Dockerfile.base` line 11: this flag bypasses every Claude Code permission check. It's necessary for the pipeline to work (no human to approve tool calls), but it means agents can execute arbitrary shell commands, write to any bind-mounted path, and make network requests.

This isn't documented as a known risk in CLAUDE.md or the security model. The security analysis should explicitly state: **the container is the security boundary, not Claude Code's permission system**. Currently someone reading the docs might believe the `--disallowedTools` flags are primary controls when they're actually just defense-in-depth for a system where the real enforcement is Docker.

---

### 3. Credential Exposure Surface

**Severity: Medium-High → ~~accepted risk~~ (see status)**

**Status: Known and accepted risk — 2026-03-02**

Migrated from `ANTHROPIC_API_KEY` to `CLAUDE_CODE_OAUTH_TOKEN` (OAuth token via `claude setup-token`). The exposure mechanism is unchanged — the credential is still visible in `docker inspect` and `/proc/1/environ` — but the blast radius is now bounded:

- **No financial exposure:** OAuth tokens are scoped to a fixed Max subscription, not pay-per-use billing
- **Natural expiry:** Tokens expire after 1 year (tracked via `DF_TOKEN_EXPIRES` in config.sh)
- **No console access:** A stolen token cannot create API keys, access billing, or affect other Anthropic services

The remaining risk (credential exfiltration via prompt injection + unrestricted egress) is only exploitable in combination with finding #1. For a solo developer running this locally against a personal Max subscription, the realistic damage scenario is subscription exhaustion or account misuse — inconvenient but not catastrophic.

The complete fix (local proxy sidecar so the credential never enters containers) is documented but deferred. It becomes the right investment if this framework is ever used on shared CI, with team members, or with a pay-per-use API key.

**Residual exposure:**
- Credential visible in `docker inspect` and `/proc/1/environ`
- A compromised agent could still capture and exfiltrate it if egress (finding #1) is unrestricted

**Reopen trigger:** shared CI, team use, or switch to a pay-per-use API key.

---

### 4. `--disallowedTools` Covers Only One Source Directory

**Severity: Medium → ~~resolved~~ (see status)**

**Status: Fixed — 2026-03-02**

`dark-factory.sh` lines 790 and 795:

```bash
SA_SOURCE_DIR="${SOURCE_DIR:-src}"
SCENARIO_ARCH_DENY="Read(${SA_SOURCE_DIR}/**),Grep(${SA_SOURCE_DIR}/**),Glob(${SA_SOURCE_DIR}/**)"
```

But Docker masks ALL of `SOURCE_DIRS` (plural, line 344: `for dir in ${SOURCE_DIRS:-...}`). If a project has multiple source directories (e.g., `src`, `lib`, `packages/`), the Docker filesystem barrier correctly masks all of them, but the defense-in-depth `--disallowedTools` only blocks the first one (`SOURCE_DIR`).

The secondary enforcement layer is weaker than the primary. If someone found a way around the tmpfs mask, the tool restriction wouldn't cover the additional directories.

**Recommended fix:** Build `SCENARIO_ARCH_DENY` by iterating over all entries in `SOURCE_DIRS`, the same way Docker masks are applied.

---

### 5. Semantic Leakage Through Oracle Feedback

**Severity: ~~Medium~~ Mitigated — 2026-03-02**

When Oracle issues a NO-SHIP, it writes iteration feedback for the Implementer. Previously, this feedback included failed scenario names and raw assertion messages (e.g., `test_when_user_email_is_null_should_return_400 | Expected status 400 but got 200`), which effectively transcribed the test's intent across the barrier.

**Mitigation implemented:** Three changes reduce the leakage surface:

1. **Oracle feedback template** (`agents/oracle.md`): Replaced the `Failed Scenarios` table (scenario names + assertion messages) with a `Spec Section Failures` table (spec section IDs + behavioral categories). A Semantic Barrier Rule explicitly prohibits scenario names, assertion messages, and raw test output in feedback.
2. **Implementer prompt** (`scripts/dark-factory.sh`): Iteration 2+ prompt no longer passes `${REPORT_FILE}` or `${REVIEW_FILE}` to the Implementer. The Implementer reads only the Oracle feedback file and the spec.
3. **Docker filesystem mask** (`scripts/dark-factory.sh`): `.dark-factory/reports/` is masked (empty tmpfs) in the Implementer container, closing the richest leakage channel (raw test runner output) at the filesystem level.

**Residual leakage (accepted):** The Implementer can infer which spec sections have test coverage from the referenced sections in feedback (e.g., "FR-3: boundary condition handling, 3 failures" reveals FR-3 is tested). Zero leakage is impossible with any feedback loop — this is documented as a known limitation.

---

### 6. Audit Log Pattern Matching Is Easily Defeated

**Severity: ~~Medium~~ Accepted — 2026-03-02**

**Status: Known and accepted risk — 2026-03-02**

`audit_check_output()` (lines 121–130) searches agent output for exact path strings like `.dark-factory/scenarios/`. An AI that encountered restricted content could describe it without mentioning the path:

> "I notice the test expects the function to return null for empty inputs..."

This would pass the audit check completely. The audit log is useful for detecting accidental leakage in filenames but provides no security guarantee against intentional or sophisticated leakage.

**Rationale for acceptance:** The audit log is the *last* layer in a defense stack that prevents the scenario from arising in the first place:

1. **Docker tmpfs masks** (primary barrier): Restricted directories are overlaid with empty tmpfs — the files don't exist inside the container. An agent cannot describe content it never received.
2. **Egress filtering** (finding #1 fix): Even if an agent somehow obtained restricted content, `--internal` Docker networks block exfiltration — there is no route to the internet.
3. **Prompt-level barriers + `--disallowedTools`** (defense-in-depth): Secondary layers inside the container further restrict access attempts.

The audit log catches naive/accidental path-string leakage (e.g., an agent referencing `.dark-factory/scenarios/` in its output). It was never designed to detect semantic or natural-language leakage, and CLAUDE.md documents this limitation explicitly. Investing in NLP-based semantic detection would add complexity disproportionate to the residual risk given the upstream controls.

---

### 7. The 1,200-Line Bash Orchestrator Is a Reliability Risk

**Severity: Medium → ~~resolved~~ (see status)**

**Status: Fixed — 2026-03-03**

`dark-factory.sh` does TUI rendering, Docker orchestration, state management, audit logging, symlink safety checks, spec selection, config validation, and pipeline control flow — all in one file with no unit tests.

`set -euo pipefail` helps, but:
- The `flock` at line 99 uses `|| true`, so state file corruption is silently ignored under concurrent access
- The `AGENT_OUTPUT` and `AGENT_EXIT` globals (lines 488–489) work fine in sequential execution but are a refactoring trap
- The 60-line `run_agent()` function builds a dynamic Docker argument array — any quoting or array handling mistake could silently produce the wrong container configuration

There are no tests for the orchestrator itself. The framework validates everything it produces, but not its own correctness.

**Fix implemented:** Extracted 15 inline functions from the 1,338-line monolith into 9 library modules under `scripts/lib/`, reducing `dark-factory.sh` to 653 lines of pure pipeline orchestration. De-duplicated functions that were copy-pasted across scripts (`validate_config` in 5 scripts, `has_scenario_files` in 4, `detect_toolchain` in 2, timeout detection in 3). De-duplicated `run_agent()`/`run_quality_gate()` shared Docker plumbing via `_build_docker_base_args()`. Added `--dry-run` mode that prints full Docker commands without executing. Built a 117-test pure-bash test suite (`tests/run-tests.sh`) covering barriers, Docker arg building, symlink safety, config validation, and state management.

---

### 8. Spec Validator Is Subject to Goodhart's Law

**Severity: ~~Medium~~ Mitigated — 2026-03-03**

**Status: Mitigated — 2026-03-03**

The hard gate "score must be exactly 100/100" is evaluated by an AI agent reading its own checklist. Teams will learn to write specs that score 100/100 rather than specs that are genuinely good. A spec can be verbose, internally consistent, use all the right section headers, and score perfectly — while still being fundamentally ambiguous or specifying the wrong behavior.

The 100/100 gate creates compliance pressure, not quality pressure. There's no adversarial validation (e.g., "does this spec have multiple equally-valid interpretations?") and no second reviewer for specs.

**Fix implemented:** Added a Precision Analysis (Implementation Divergence Test) to the spec-validator agent. The validator now sketches 3 maximally-different implementations that satisfy the spec literally, then rates divergence as LOW/MEDIUM/HIGH:

- **LOW:** All sketches produce identical externally observable behavior. Differences are purely internal.
- **MEDIUM:** Sketches differ on behavior in areas the spec doesn't address. Explicit requirements are unambiguous.
- **HIGH:** Sketches differ on behavior the spec explicitly addresses — the spec permits contradictory interpretations. **Blocks the pipeline** even at 100/100.

The `divergence` key is enforced as a hard gate in the pipeline (`scripts/lib/scenarios.sh`). Missing `divergence` key (from old validation runs) also fails with a clear re-run message. The gate is tested in `tests/test-scenarios.sh`.

**Residual risk (why Mitigated, not Resolved):** This is self-adversarial validation, not true adversarial validation. The same agent that scores the checklist also sketches the implementations — its own reading of the spec anchors all three sketches. Forcing concrete sketches is harder to fake than checking boxes, which meaningfully improves spec precision, but it does not fully escape the self-grading problem. A second independent validator agent would provide true adversarial validation (future improvement).

---

### 9. Supply Chain Risk in Docker Images

**Severity: Medium → ~~resolved~~ (see status)**

**Status: Fixed — 2026-03-02**

`Dockerfile.base`:
```dockerfile
RUN npm install -g @anthropic-ai/claude-code
RUN apk add --no-cache bash coreutils git
```

No version pins. No digest pinning. Every image build pulls from npm and Alpine's package repositories at that moment. A compromised `claude-code` npm package or a malicious package in the dependency tree runs inside every agent container with `--dangerously-skip-permissions`.

**Fix implemented:** All dependency versions are centralized in `docker/versions.env` (single source of truth). Dockerfiles consume them via `ARG` instructions with no defaults on base image ARGs — an unprovided `NODE_IMAGE` or `ALPINE_IMAGE` fails the build immediately rather than silently pulling a floating tag. `build-images.sh` sources and validates `versions.env`, passes `--build-arg` to every build, and post-build verifies the installed `claude-code` version via `npm list -g` (deterministic; not `claude --version` which has unstable output).

Pinned as of 2026-03-02:
- Base image: `node:22-alpine@sha256:e4bf2a82...` (Alpine 3.23.3)
- Proxy image: `alpine:3.19@sha256:6baf4358...`
- `@anthropic-ai/claude-code@2.1.63`
- All apk packages (bash, coreutils, git, go, rust, cargo, python3, tinyproxy) pinned to exact apk version strings
- All npm/pip tools (typescript, eslint, prettier, ruff, mypy, pytest) pinned to exact versions

Use `bash docker/update-pins.sh` to print current latest versions for review when updating.

**Known limitation:** npm transitive dependencies of `@anthropic-ai/claude-code` are still resolved via semver ranges at install time. A `npm ci` from a checked-in lockfile would fully close this gap (future improvement).

---

### 10. "Clean Session" Check in Skills Is an Honor System

**Severity: Medium**

In `df-implement` and `df-generate-scenarios` skills, the pre-flight check "verify this session hasn't seen scenario files / implementation code" is answered by the agent evaluating its own conversation history. This is self-attestation, not verification.

The `dark-factory.sh` script provides real isolation through Docker. The skills-based workflow runs in the user's current Claude Code session with no filesystem isolation. A user who runs both skills in the same session, or forgets the rule, gets no technical enforcement — only the agent's promise.

**Recommended action:** Add a prominent warning to all skills-based documentation: **skills do not enforce information barriers; only `dark-factory.sh` does**. Skills should be treated as developer convenience tools for exploration, not as a compliant pipeline execution method.

---

### 11. Reviewer Agent Failure Is Non-Fatal to the SHIP Gate

**Severity: Low-Medium**

**Status: Fixed — 2026-03-02**

`dark-factory.sh` lines 1115–1117:
```bash
if [[ $AGENT_EXIT -ne 0 ]]; then
    echo "WARNING: Reviewer agent failed (exit $AGENT_EXIT). Proceeding to oracle without review."
fi
```

The Oracle then makes a SHIP/NO-SHIP decision without reviewer input. Given that "Reviewer verdict is APPROVE" is listed as a requirement for Automatic SHIP in `oracle.md`, an Oracle operating without a review should automatically NO-SHIP.

**Recommended fix:** When the reviewer fails, write a synthetic review file containing `VERDICT: REVIEW_FAILED` and ensure the Oracle prompt explicitly notes that no review was produced. Do not allow SHIP when the review agent failed.

---

### 12. Oracle Non-Determinism

**Severity: Low — but underappreciated**

The Oracle operates in a "Judgment Zone" for ambiguous cases. Two identical pipeline states (same scenario results, same reviewer report) could produce different SHIP/NO-SHIP decisions on different runs. The pipeline is not reproducible in the judgment zone.

This matters for auditability. If someone asks "why was this shipped?", the answer is "because the Oracle decided so on that run" — not a deterministic, verifiable condition.

**Recommended action:** Document this explicitly. For high-stakes pipelines, consider requiring ALL Automatic SHIP conditions to be met (removing the judgment zone entirely) and treating anything outside those conditions as automatic NO-SHIP or human escalation.

---

### 13. `chmod 777 /home/agent`

**Severity: Low**

`Dockerfile.base` line 7. World-writable home directory is unnecessary and fails basic security hygiene checks. Should be `chmod 755` (or `chmod 700` if only the agent user needs access).

---

### 14. No Cost Controls or Token Budget

**Severity: Low**

`--max-turns 200` per agent × 3 iterations × 4+ agents = potentially thousands of API calls per spec. There's no maximum token budget, no cost ceiling, and no alerting when a run is anomalously expensive. A poorly-specified spec that triggers all three iterations could cost significantly more than expected without any notification.

**Recommended fix:** Add an optional `AGENT_MAX_COST_USD` config variable and a pre-flight check that estimates expected maximum cost. Log token usage per agent to the audit file.

---

### 15. State Files Have No Integrity Protection

**Severity: Low**

`.dark-factory/state/[spec].state` files are plain text and can be manually or programmatically tampered with to skip pipeline stages (e.g., writing `completed_step=99`). There's no cryptographic MAC.

This is low severity in a context where the project team controls the filesystem, but it matters in adversarial environments or shared CI systems.

---

## Summary

| # | Issue | Severity | Category |
|---|---|---|---|
| 1 | Network exfiltration via unrestricted egress | ~~High~~ **Fixed** | Security |
| 2 | `--dangerously-skip-permissions` underdocumented | ~~High~~ **Fixed** | Security/Docs |
| 3 | Credential in container environment | ~~Medium-High~~ **Accepted** | Security |
| 4 | `--disallowedTools` covers only one source dir | ~~Medium~~ **Fixed** | Barrier Integrity |
| 5 | Oracle feedback leaks scenario semantics | ~~Medium~~ **Mitigated** | Design |
| 6 | Audit log defeated by natural language | ~~Medium~~ **Accepted** | Security |
| 7 | 1,200-line untested bash orchestrator | ~~Medium~~ **Fixed** | Reliability |
| 8 | Goodhart's Law on spec scoring | ~~Medium~~ **Mitigated** | Design |
| 9 | No supply chain security (no version pinning) | ~~Medium~~ **Fixed** | Security |
| 10 | Skills use honor-system barrier checking | **Medium** | Design |
| 11 | Reviewer failure is non-fatal to SHIP gate | ~~Low-Medium~~ **Fixed** | Logic |
| 12 | Oracle non-determinism in judgment zone | **Low** | Design |
| 13 | `chmod 777` on home directory | **Low** | Security |
| 14 | No cost controls or token budget | **Low** | Operations |
| 15 | State files have no integrity protection | **Low** | Security |

---

## What Is Working Well

- Docker tmpfs masking for barriers is genuinely robust and the correct primary control
- Layered enforcement (Docker → prompt → tool restrictions → audit) follows real defense-in-depth principles
- Scenario validation (detecting trivial assertions and empty test bodies) addresses a failure mode most frameworks ignore
- Config injection validation is thorough and correctly distinguishes pre-source structural checks from post-source value checks
- Escalation path and human handoff protocol are well-defined
- Per-role cache volumes to prevent cache poisoning is a thoughtful detail
- Symlink safety checks with scoped directory walking (avoiding node_modules) are well-implemented

---

## Highest-Priority Recommendations

1. ~~**Egress filtering**~~ — **Done (2026-03-02).** Proxy sidecar + `--internal` networks; `WebSearch`/`WebFetch` added to all deny lists.
2. ~~**Docker image version pinning**~~ — **Done (2026-03-02).** `versions.env` + ARG pinning + post-build verification in `build-images.sh`.
3. ~~**Document `--dangerously-skip-permissions`**~~ — **Done (2026-03-02).** Security Model section added to CLAUDE.md; WORKFLOW.md and INTEGRATION.md updated.
4. ~~**Fix SCENARIO_ARCH_DENY**~~ — **Done (2026-03-02).** Deny list, barrier prompt, and audit check now iterate over all `SOURCE_DIRS`.
5. ~~**Reviewer failure → automatic NO-SHIP**~~ — **Done (2026-03-02).** Reviewer failure writes a synthetic `VERDICT: REVIEW_FAILED` review; oracle treats `REVIEW_FAILED` as Automatic NO-SHIP.
6. ~~**Reduce Oracle feedback leakage**~~ — **Done (2026-03-02).** Oracle feedback uses spec-section-anchored references (not scenario names/assertions); `.dark-factory/reports/` masked in Implementer container; Implementer prompt no longer receives QG report or review file.
7. ~~**Modular refactoring + test suite**~~ — **Done (2026-03-03).** `dark-factory.sh` decomposed from 1,338 to 653 lines; 9 library modules in `scripts/lib/`; 117-test suite; `--dry-run` mode; de-duplicated shared functions across 5 scripts.
8. ~~**Goodhart's Law on spec scoring**~~ — **Mitigated (2026-03-03).** Added Precision Analysis (Implementation Divergence Test) to spec-validator: sketches 3 maximally-different implementations, rates divergence LOW/MEDIUM/HIGH. HIGH blocks the pipeline. Self-adversarial (same agent), not true adversarial — a second independent validator agent would fully resolve this.
