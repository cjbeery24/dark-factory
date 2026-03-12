# Dark Factory Workflow Guide

## Overview

This document describes the complete Dark Factory workflow — from idea to shipped code — using Claude Code as your AI development tool.

## Prerequisites

- **Docker** installed and running (Docker Desktop or Docker Engine)
- Dark Factory Docker images built: `bash docker/build-images.sh <language>`
- Claude Code installed and configured
- Git repository initialized for your project
- Test framework installed for your language (pytest, vitest, jest, go test, etc.)
- This dark-factory framework accessible (in your project or as a reference)

---

## The Complete Pipeline

### Step 0: Decide What to Build

Before entering the pipeline, you (the human) decide what needs to be built or changed. This is the only step that always requires human judgment.

**Planning a whole project?** Use `/df-write-plan` to make architectural decisions (tech stack, data model, directory structure) and decompose the project into ordered feature specs. The plan generates stub specs in `.dark-factory/specs/` that you complete with `/df-write-spec` before entering the pipeline. See `skills/write-plan.md` for details.

### Step 1: Write the Specification

**Who:** You (human), optionally assisted by Claude Code
**Time:** 10-30 minutes
**Output:** A spec file in `.dark-factory/specs/`

```bash
# Start from a template
cp templates/feature-spec.md .dark-factory/specs/my-feature.md

# Edit the spec (manually or with Claude Code assistance)
# Claude Code can help you think through edge cases and refine requirements
claude "Help me fill out this spec template for [feature description].
Read the template at .dark-factory/specs/my-feature.md and help me
complete each section. Ask me questions to fill gaps."
```

**Key principle:** Spend the most time here. A perfect spec produces perfect code in one iteration. A vague spec wastes cycles.

### Step 2: Validate the Specification

**Who:** Spec Validator agent
**Time:** 1-2 minutes
**Output:** Validation report

```bash
claude "You are the Spec Validator. Read the agent definition at
agents/spec-validator.md, then validate the specification at
.dark-factory/specs/my-feature.md. Produce the validation report
exactly as specified in your agent definition."
```

**If the spec fails validation:** Fix the issues and re-validate. Do NOT proceed to implementation with an incomplete spec.

### Step 3: Generate Blind Test Scenarios

**Who:** Scenario Architect agent (in a SEPARATE Claude Code session)
**Time:** 3-10 minutes
**Output:** Test files in `.dark-factory/scenarios/my-feature/`

```bash
# IMPORTANT: Use a fresh Claude Code session
# This agent must NOT have seen any implementation code

claude "You are the Scenario Architect. Read the agent definition at
agents/scenario-architect.md, then read the validated specification at
.dark-factory/specs/my-feature.md. Generate comprehensive blind test
scenarios. Write executable test files to .dark-factory/scenarios/my-feature/.
You MUST NOT read any implementation source code. Use the project's test framework
for the test files."
```

### Step 3b: Scenario Quality Validation (Automatic)

**Who:** Automated script (`validate-scenarios.sh`)
**Time:** <1 second
**Output:** Validation report in `.dark-factory/reports/[spec]-scenario-validation.md`

After scenario generation, the pipeline automatically validates that the generated scenarios contain real, non-trivial tests. This prevents a Scenario Architect that produces `assert(true)` or empty test bodies from passing all quality gates.

**Four checks performed:**

| Check | What It Catches |
|-------|----------------|
| File count | Too few scenario files generated (default minimum: 3) |
| Assertion presence | Files with zero real assertion calls |
| Trivial assertions | Files where ALL assertions are trivial (e.g., `expect(true)`, `assert True`) |
| Empty test bodies | Test functions with no logic in the body |

**Configuration** (in `.dark-factory/config.sh`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SCENARIO_VALIDATION` | `strict` | `strict` = halt on failure, `warn` = log and continue, `off` = skip |
| `SCENARIO_MIN_FILES` | `3` | Minimum number of scenario test files required |
| `SCENARIO_MIN_ASSERTIONS_PER_FILE` | `1` | Minimum real assertion calls per file |

**Failure behavior:**
- **strict mode (default):** Pipeline halts with exit code 1. Fix the scenarios and re-run.
- **warn mode:** Issues are logged as warnings, pipeline continues to implementation.
- **off:** Validation is skipped entirely.

### Step 4: Implement from Spec

**Who:** Implementer agent (in a SEPARATE Claude Code session)
**Time:** 5-30 minutes
**Output:** Implementation code + self-tests

```bash
# IMPORTANT: Use a fresh Claude Code session
# This agent must NOT have access to scenarios

claude "You are the Implementer. Read the agent definition at
agents/implementer.md, then implement the specification at
.dark-factory/specs/my-feature.md. You MUST NOT read anything in
.dark-factory/scenarios/. Follow existing codebase conventions.
Write your own unit tests based on the spec."
```

### Step 5: Run Quality Gates

**Who:** Automated scripts
**Time:** 1-5 minutes
**Output:** Quality gate report

```bash
# Run the quality gate script
./scripts/quality-gate.sh my-feature

# Or manually run each gate:
# 1. Build
# 2. Lint
# 3. Type check
# 4. Implementer's self-tests
# 5. Blind scenarios
```

### Step 5.5: Ephemeral Deployment & Evaluation (Optional)

**Who:** Automated deployment + Evaluator agent
**Time:** 1-5 minutes
**Output:** Evaluation report in `.dark-factory/reports/`
**Requires:** `DEPLOY_CMD` configured in `.dark-factory/config.sh`

When deployment is configured, the pipeline:
1. **Step 4.5a:** Deploys the application (runs `DEPLOY_CMD` on host)
2. **Step 4.5a:** Waits for health check (`DEPLOY_BASE_URL` + `DEPLOY_HEALTH_ENDPOINT`)
3. **Step 4.5b:** Runs the Evaluator agent against `.eval.md` behavioral scenarios
4. **Step 4.5c:** Tears down the deployment (`DEPLOY_TEARDOWN_CMD`)

See `docs/DIGITAL-TWINS.md` for setup and `.eval.md` format.

### Step 6: Review

**Who:** Deterministic pre-pass + Reviewer agent
**Time:** 3-10 minutes
**Output:** Pre-pass report in `.dark-factory/reports/`, review report in `.dark-factory/reviews/`

Before the LLM reviewer runs, a **deterministic pre-pass** automatically computes objective scores for 3 of 5 review dimensions:

| Dimension | Weight | Source |
|-----------|--------|--------|
| Test Results Analysis | 25% | Quality gate pass/fail counts, failure pattern classification |
| Code Quality | 20% | Lint status from gate summary + complexity metrics (function length, file length, nesting depth) |
| Security | 15% | Regex-based pattern matching for language-specific anti-patterns (SQL injection, XSS, hardcoded secrets, etc.) |
| Spec Compliance | 30% | LLM reviewer (no deterministic substitute) |
| Robustness | 10% | LLM reviewer (no deterministic substitute) |

The pre-pass writes its scores to `.dark-factory/reports/{spec}-review-prepass.md` and `.env`. The LLM reviewer receives a reference to these scores in its prompt and can focus on Spec Compliance and Robustness. The reviewer retains full authority to override any deterministic score with justification.

If the pre-pass fails, the reviewer proceeds without it (no pipeline disruption). If the reviewer fails, the pre-pass scores are used as a fallback for its 3 dimensions (Spec Compliance and Robustness default to 0).

```bash
claude "You are the Reviewer. Read the agent definition at
agents/reviewer.md. Review the implementation for spec
.dark-factory/specs/my-feature.md. The implementation is in
[paths]. Test results are in [path]. Produce the review report
exactly as specified in your agent definition. Write it to
.dark-factory/reviews/my-feature-review.md."
```

### Step 7: Oracle Decision

**Who:** Oracle agent
**Time:** 1-2 minutes
**Output:** SHIP or NO-SHIP decision

```bash
claude "You are the Oracle. Read the agent definition at
agents/oracle.md. Make a SHIP/NO-SHIP decision for spec
.dark-factory/specs/my-feature.md. The review report is at
.dark-factory/reviews/my-feature-review.md. Test results are
in [path]. This is iteration [N] of 3."
```

### Step 8: Iterate or Ship

**If SHIP:**
1. If `AUTO_PR` is enabled (or `--auto-pr` flag): create branch, commit changes, push, and open PR via `gh` CLI
2. If `MERGE_QUEUE_ENABLED=true`: add PR to merge queue
3. Send notifications (Slack/webhook) if configured
4. Otherwise: code is ready locally for manual commit/push

**If NO-SHIP (iteration < 3):**
1. The Oracle writes structured feedback to `.dark-factory/reviews/[spec]-feedback-iter[N].md`
2. Start a new Implementer session (Step 4) pointing to the feedback file
3. The Implementer gets: structured feedback with spec-section-anchored references (FR-N, EC-N + behavioral category), error messages, and priority-ordered fix list (never test source code)
4. Rerun from Step 5

**If NO-SHIP (iteration = 3):** The pipeline generates an escalation report at `.dark-factory/reviews/[spec]-escalation.md`. See `docs/ESCALATION.md` for the decision tree and re-entry procedures.

---

## Quick Reference: Docker Isolation

Each agent runs in an isolated Docker container with restricted filesystem mounts. This is the primary information barrier mechanism.

| Step | Agent | Container Mount | Masked (tmpfs) |
|------|-------|:---:|---|
| 2 | Spec Validator | `:ro` | — |
| 3 | Scenario Architect | `:ro` | `SOURCE_DIRS[]`, `.git/` |
| 4 | Implementer | `:rw` | `.dark-factory/scenarios/`, `.dark-factory/reports/`, `.dark-factory/audit/`, `.dark-factory/state/`, `.git/` |
| 5 | Quality Gate | `:rw` | — |
| 5.5 | Evaluator | `:ro` | `SOURCE_DIRS[]`, `.git/` |
| 6 | Reviewer | `:ro` | `.dark-factory/scenarios/`, `.git/` |
| 7 | Oracle | `:ro` | — |

**How it works:** Instead of telling agents "don't read X", the pipeline never mounts X into the container. The Implementer's container has no `.dark-factory/scenarios/` directory — not empty, not permission-denied, simply nonexistent. No tool or technique can read files that don't exist in the filesystem.

**Defense-in-depth:** `--disallowedTools` flags and `--append-system-prompt` barriers are
also applied inside containers as secondary enforcement. Note that all agents run with
`--dangerously-skip-permissions`, which disables Claude Code's built-in permission prompts
entirely — the container's restricted filesystem is the actual enforcement layer. The tool
restrictions add a behavioral layer on top but do not substitute for filesystem isolation.

### Debugging containers

Use the `--debug` flag to keep containers alive on failure:

```bash
./scripts/dark-factory.sh my-feature --debug
# Failed containers can be inspected with:
docker exec -it df-my-feature-implementer-<pid> sh
```

---

## Workflow Variants

### Quick Fix (Bug Fix)
For small, well-understood bugs:
1. Write a bug-fix spec (use `templates/bug-fix-spec.md`)
2. Run Steps 2-7 as normal (validation is fast for focused specs)

### Refactoring
For refactoring existing code:
1. Write a refactor spec (use `templates/refactor-spec.md`)
2. Scenarios focus on behavioral preservation (outputs should not change)
3. Run Steps 2-7 as normal

### API Development
For new API endpoints:
1. Write an API spec (use `templates/api-spec.md`)
2. Scenarios include HTTP-level tests (status codes, headers, body)
3. Run Steps 2-7 as normal

### Database Migration
For schema changes and data migrations:
1. Write a migration spec (use `templates/db-migration-spec.md`)
2. Run Steps 2-7 as normal

### Security Fix
For security vulnerabilities and hardening:
1. Write a security fix spec (use `templates/security-fix-spec.md`)
2. Run Steps 2-7 as normal

### Performance Optimization
For latency, throughput, or resource improvements:
1. Write a performance spec (use `templates/performance-spec.md`)
2. Scenarios focus on measurable performance criteria
3. Run Steps 2-7 as normal

### Integration
For multi-component integration work:
1. Write an integration spec (use `templates/integration-spec.md`)
2. Run Steps 2-7 as normal

---

## Automation

### Full Pipeline Script

```bash
# Build Docker images first (one-time setup)
bash docker/build-images.sh node

# Run the complete pipeline
./scripts/dark-factory.sh my-feature

# With options
./scripts/dark-factory.sh my-feature --max-iterations 3

# Debug mode: keep containers alive on failure
./scripts/dark-factory.sh my-feature --debug
```

### Command-Line Options

| Option | Description |
|--------|-------------|
| `--max-iterations N` | Maximum iteration cycles (default: 3) |
| `--test-cmd "command"` | Override test command for quality gates |
| `--resume` | Resume a previously interrupted pipeline run |
| `--fresh` | Start fresh, ignoring any previous run state |
| `--ci` | CI mode: auto-resume on previous state, no interactive prompts |
| `--skip-dep-check` | Skip dependency checking for multi-spec plans |
| `--debug` | Keep Docker containers alive on failure for inspection |
| `--regen-scenarios` | Clear and regenerate scenarios before implementation (use when scenarios are semantically incorrect) |
| `--regression` | Run cross-spec regression testing before finalizing SHIP |
| `--auto-pr` | Create PR after SHIP (overrides config `AUTO_PR`) |
| `--no-pr` | Skip PR creation (overrides config `AUTO_PR`) |
| `--base-branch NAME` | Override `GIT_BASE_BRANCH` for PR target |

### Autonomy Levels

Control how much human oversight the pipeline requires via `AUTONOMY_LEVEL` in `.dark-factory/config.sh`:

| Level | Name | Behavior |
|-------|------|----------|
| 1 | Assisted | Pipeline pauses after Oracle SHIP for human confirmation |
| 2 | Supervised | Auto-proceed, log decision, track pass-rate metrics |
| 3 | Autonomous | Current behavior — full dark factory (default) |

**Level 1** is recommended for teams adopting Dark Factory for the first time. Move to Level 2 after building confidence in spec quality and scenario coverage. Level 3 is the end state for mature pipelines.

In CI mode (`--ci`), Level 1 auto-accepts (no TTY available) but logs a prominent notice.

### Cross-Spec Regression Testing

When multiple specs have shipped, later specs can silently break earlier ones. Enable regression testing to catch this:

```bash
# Via CLI flag
./scripts/dark-factory.sh my-feature --regression

# Via config (always-on)
# In .dark-factory/config.sh:
REGRESSION_ON_SHIP="true"
REGRESSION_TIMEOUT="300"
```

After the Oracle says SHIP but before the autonomy-level gate, the pipeline runs `run-scenarios.sh` for each previously-shipped spec. Results are logged to audit. Any failures produce a WARNING but do not block SHIP (the human at Level 1 sees the results before confirming).

### Probabilistic Satisfaction Scoring

By default, the pipeline uses binary pass/fail counting: each scenario either passes or fails, and the pass rate is `passed / total`. Probabilistic satisfaction scoring extends this with two capabilities:

1. **Multi-run execution** — Run each scenario file N times and require K successes (handles flaky tests)
2. **Weighted scoring** — Weight scenario categories differently (e.g., security tests count more than adversarial tests)

#### Enabling Satisfaction Scoring

Set these variables in `.dark-factory/config.sh`:

```bash
# Run each scenario 5 times, require 3 successes
SCENARIO_MULTI_RUN_N="5"
SCENARIO_MULTI_RUN_K="3"

# Require a minimum weighted satisfaction score of 70%
MIN_SATISFACTION_SCORE="70"
```

Setting `SCENARIO_MULTI_RUN_N > 1` enables multi-run execution. Setting `MIN_SATISFACTION_SCORE > 0` enables weighted scoring and the hard override floor (Oracle SHIP is rejected if satisfaction is below this floor).

#### How the Score Is Computed

1. Each scenario file is run N times. It passes if >= K runs succeed (early exit on K successes or when K becomes mathematically impossible).
2. Files are grouped by their `NN-` filename prefix into categories (01-Contract, 02-Happy Path, etc.).
3. Per-category pass rates are computed: `category_passed / category_total`.
4. The satisfaction score is a weighted average: `Σ(weight × category_rate) / Σ(weight)`, normalized to 0-100%.

#### Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `SCENARIO_MULTI_RUN_N` | `1` | Times to run each scenario file (1 = single-run) |
| `SCENARIO_MULTI_RUN_K` | `1` | Minimum successes required per file |
| `MIN_SATISFACTION_SCORE` | `0` | Minimum weighted score for SHIP (0 = disabled) |
| `SCENARIO_WEIGHT_CONTRACT` | `100` | Weight for 01- Contract scenarios |
| `SCENARIO_WEIGHT_HAPPY_PATH` | `100` | Weight for 02- Happy Path scenarios |
| `SCENARIO_WEIGHT_EDGE_CASES` | `80` | Weight for 03- Edge Case scenarios |
| `SCENARIO_WEIGHT_ERROR_CONDITIONS` | `90` | Weight for 04- Error Condition scenarios |
| `SCENARIO_WEIGHT_STATE_SEQUENCE` | `80` | Weight for 05- State & Sequence scenarios |
| `SCENARIO_WEIGHT_SECURITY` | `120` | Weight for 06- Security scenarios |
| `SCENARIO_WEIGHT_PROPERTY_BASED` | `70` | Weight for 07- Property-Based scenarios |
| `SCENARIO_WEIGHT_ADVERSARIAL` | `60` | Weight for 08- Adversarial scenarios |
| `TRAJECTORY_PLATEAU_THRESHOLD` | `5` | Percentage points for plateau detection |

#### Trajectory Classification

Across iterations, the pipeline classifies the satisfaction score trajectory to guide the Oracle:

| Type | Condition | Meaning |
|------|-----------|---------|
| INSUFFICIENT | < 2 data points | No trend available yet |
| IMPROVING | All deltas positive | Scores consistently rising |
| DEGRADING | All deltas negative | Scores consistently falling |
| PLATEAU | All deltas within ±threshold | Scores stagnant |
| OSCILLATING | Deltas alternate signs | Fixing one area breaks another |
| RECOVERING | Last delta positive after previous negative | Bouncing back after a dip |
| MIXED | No pattern matched | No clear trend |

The trajectory type and per-category breakdown are included in the Oracle's analysis block, along with a strategy recommendation (e.g., "narrow scope" for OSCILLATING, "consider rollback" for DEGRADING).

#### Hard Override Behavior

When `MIN_SATISFACTION_SCORE` is non-zero and the satisfaction score is below that floor, the pipeline **rejects an Oracle SHIP decision**. This is logged to the audit trail. The Oracle cannot override the floor — it is an orchestrator-level enforcement, similar to `MIN_SHIP_PASS_RATE`.

#### Interaction with `MIN_SHIP_PASS_RATE`

Both thresholds can coexist. `MIN_SHIP_PASS_RATE` uses unweighted binary pass rate; `MIN_SATISFACTION_SCORE` uses weighted category scoring. When `MIN_SATISFACTION_SCORE > 0`, the weighted score is the primary quality signal displayed to the Oracle. Both floors are enforced independently — failing either one blocks SHIP.

### CI/CD Integration

Use the included GitHub Actions workflow for full pipeline runs:

```yaml
# Trigger manually via workflow_dispatch
# See .github/workflows/dark-factory.yml
```

Or add quality gate checks to your PR pipeline:

```yaml
# .github/workflows/quality-gate.yml
name: Dark Factory Quality Gate
on: pull_request
jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build Docker images
        run: bash docker/build-images.sh node
      - name: Run blind scenarios
        run: ./scripts/quality-gate.sh
```

---

## Metrics to Track

Over time, track these metrics to improve your specs and pipeline:

| Metric | What It Tells You |
|--------|-------------------|
| **First-pass rate** | % of specs that SHIP on iteration 1 (target: >60%) |
| **Avg iterations** | Mean iterations to SHIP (target: <2) |
| **Spec rejection rate** | % of specs that fail validation (target: decreasing over time) |
| **Scenario catch rate** | % of bugs caught by blind scenarios vs. found later |
| **Time to ship** | End-to-end pipeline time per feature |
| **Escalation rate** | % that hit iteration 3 and need human intervention |

---

## Troubleshooting

### "The implementer keeps failing the same scenarios"
- Check if the spec is ambiguous about that behavior
- The feedback loop may not have enough detail — enrich the Oracle's error messages
- Consider whether the scenarios are testing something not in the spec

### "Scenarios are too easy / not catching real bugs"
- Review the Scenario Architect's output for coverage gaps
- Add domain-specific adversarial patterns to the scenario architect agent definition
- Consider adding property-based testing requirements

### "Spec validation takes too long"
- Start with the template — it guides you to completeness faster
- Use Claude Code to help fill out sections interactively
- Common patterns (CRUD, auth, etc.) should have pre-filled templates

### "The whole pipeline is too slow"
- In manual mode, Steps 3 and 4 can run in parallel in separate terminals (scenario generation and initial implementation)
- The automated pipeline (`dark-factory.sh`) runs them sequentially
- Scenarios are generated once and reused across iteration cycles

### Go and Rust Scenario Runners

Go (`go test`) and Rust (`cargo test`) ignore dotted directories by convention. Since blind scenarios live under `.dark-factory/scenarios/`, these toolchains cannot discover them natively.

**Workaround:** Set `SCENARIO_TEST_CMD` in `.dark-factory/config.sh` to a custom shell script that copies or symlinks scenario files into a toolchain-visible path before running tests.

**Go example** (`.dark-factory/run-go-scenarios.sh`):
```bash
#!/usr/bin/env bash
set -euo pipefail
SPEC="$1"
STAGING_DIR="test_scenarios_${SPEC}"
rm -rf "$STAGING_DIR"
cp -r ".dark-factory/scenarios/${SPEC}" "$STAGING_DIR"
go test "./${STAGING_DIR}/..." -v 2>&1
exit_code=$?
rm -rf "$STAGING_DIR"
exit $exit_code
```

Config: `SCENARIO_TEST_CMD="bash .dark-factory/run-go-scenarios.sh {SCENARIOS_DIR}"`

**Rust example** (`.dark-factory/run-rust-scenarios.sh`):
```bash
#!/usr/bin/env bash
set -euo pipefail
SPEC="$1"
STAGING_DIR="tests/scenarios_${SPEC}"
rm -rf "$STAGING_DIR"
cp -r ".dark-factory/scenarios/${SPEC}" "$STAGING_DIR"
cargo test --test "scenarios_${SPEC}" 2>&1
exit_code=$?
rm -rf "$STAGING_DIR"
exit $exit_code
```

Config: `SCENARIO_TEST_CMD="bash .dark-factory/run-rust-scenarios.sh {SCENARIOS_DIR}"`

---

## Pipeline Metrics

The pipeline records per-run metrics to `.dark-factory/metrics.jsonl` (JSON Lines format, one object per line). Each completed run records:

| Field | Description |
|-------|-------------|
| `spec` | Spec name |
| `run_id` | Unique run identifier |
| `wall_s` | Wall-clock seconds |
| `iters` | Iterations used |
| `outcome` | `shipped`, `escalated`, `failed`, etc. |
| `agent_failures` | Count of agent failures across the run |
| `pass_rates` | Pipe-delimited pass rates per iteration (e.g., `80|100`) |

**View metrics:**
```bash
bash scripts/metrics-summary.sh
```
