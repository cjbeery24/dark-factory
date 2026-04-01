# Dark Factory Workflow Guide

> **See also:** [CONFIGURATION.md](CONFIGURATION.md) — complete config reference

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

### Step 2b: Architecture Lock (Automatic)

**Who:** Architect agent
**Time:** 2-5 minutes
**Output:** YAML manifest at `.dark-factory/architecture/<spec>-manifest.yaml`

When `[architecture] enabled = true` (default), the pipeline invokes an Architect agent after spec validation. The Architect reads the spec and existing codebase, then produces a structured manifest with:

- **File map** — every file to create/modify, with exact paths and FR traceability
- **Data models** — schema definitions with field types and constraints
- **API contracts** — endpoint paths, methods, request/response shapes
- **Dependencies** — required packages and target manifest files
- **Environment** — tools/binaries the container needs

Both the Scenario Architect and Implementer receive this manifest as context, ensuring they agree on structure before writing code or tests. This eliminates the most common cause of wasted iterations: structural divergence between blind tests and implementation.

The manifest is validated deterministically (FR traceability, path validity, no duplicates, modify-targets-must-exist) before the pipeline proceeds.

**Resume:** Skipped if the manifest exists and `spec_hash` matches the current spec. Regenerated on `--fresh` or `--regen-scenarios`.

### Step 3: Generate Blind Test Scenarios (or Declare Not Applicable)

**Who:** Scenario Architect agent (in a SEPARATE Claude Code session)
**Time:** 3-10 minutes
**Output:** Test files in `.dark-factory/scenarios/my-feature/` — OR a sentinel file if no testable runtime behavior exists

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

**Normal output:** Executable test files in one or more formats (A/B/C/D).

**Sentinel output:** For specs that define *only* infrastructure with no testable runtime behavior (project scaffolding, CI/CD config, build system setup), the Scenario Architect may instead write a `SCENARIOS-NOT-APPLICABLE.md` sentinel file to `.dark-factory/scenarios/my-feature/`. The sentinel must justify why each scenario format is inapplicable and map every functional requirement to a build/lint/typecheck gate.

The pipeline **validates the sentinel before implementation runs**. A missing section, trivial justification, or uncovered functional requirement is rejected immediately — the pipeline halts with specific objections. If in doubt about whether a sentinel is appropriate, write real Format C integration tests instead.

### Step 3b: Scenario Quality Validation (Automatic)

**Who:** Automated validation (`dark-factory validate-scenarios`)
**Time:** <1 second
**Output:** Validation report in `.dark-factory/reports/[spec]-scenario-validation.md`

After scenario generation, the pipeline automatically validates that the generated scenarios contain real, non-trivial tests (`dark-factory validate-scenarios <spec>`). This prevents a Scenario Architect that produces `assert(true)` or empty test bodies from passing all quality gates.

**Six checks performed:**

| Check | Severity | What It Catches |
|-------|----------|----------------|
| File count | Hard fail | Too few scenario files generated (default minimum: 3) |
| Assertion presence | Hard fail | Files with zero real assertion calls |
| Trivial assertions | Hard fail | Files where ALL assertions are trivial (e.g., `expect(true)`, `assert True`) |
| Empty test bodies | Hard fail | Test functions with no logic in the body |
| Build-tool spawn detection | Warning | Files that spawn build tools (pnpm, tsc, etc.) — fragile in scenarios |
| stderr convention | Warning | Assertions on `result.stderr` for tools that write diagnostics to stdout (tsc, eslint, prettier) |

Checks 5-6 produce warnings in the validation report but do not block the pipeline. They inform the smoke test (Step 3c) and the Oracle.

**Configuration** (in `.dark-factory/config.toml`):

| Key | Default | Description |
|-----|---------|-------------|
| `[gates] validate_scenarios` | `"strict"` | `strict` = halt on failure, `warn` = log and continue, `off` = skip |
| `[gates] scenario_min_files` | `3` | Minimum number of scenario test files required |
| `[gates] scenario_min_assertions_per_file` | `1` | Minimum real assertion calls per file |

**Failure behavior:**
- **strict mode (default):** Pipeline halts with exit code 1. Fix the scenarios and re-run.
- **warn mode:** Issues are logged as warnings, pipeline continues to implementation.
- **off:** Validation is skipped entirely.

### Step 3c: Scenario Smoke Test (Automatic)

**Who:** Automated guard (`scenario_smoke_test`)
**Time:** Typically <1 minute
**Output:** `.dark-factory/reports/[spec]-smoke-test.env`, optional `.dark-factory/reports/[spec]-smoke-test.md`

After validation, the pipeline runs the generated scenarios against the project in its **current state** (before implementation). This pre-implementation guard classifies each result:

| Result | Meaning | Action |
|--------|---------|--------|
| ASSERTION_FAIL | Healthy — scenarios should fail before implementation | None (expected) |
| CRASH | Scenario bug — TypeError, ReferenceError, SyntaxError, import errors | Triggers SA repair |
| PASS | Suspicious — may be tautological (always-true assertions) | Warning logged |

**If crashes are found**, the Scenario Architect is re-invoked for one repair pass with sanitized crash output (error type + scenario-file line number only — no implementation paths leak through). Post-repair mechanical validation ensures the SA did not weaken assertions or delete tests.

**Configuration:**

| Key | Default | Description |
|-----|---------|-------------|
| `[gates] scenario_smoke_test` | `true` | Enable/disable the pre-implementation smoke test. Respects `validate_scenarios` — if `"off"`, smoke test is also skipped. |

**Failure behavior:** Infrastructure failures (container won't start, test runner crashes) are soft — they log a warning and skip the smoke test. A broken smoke test never blocks the pipeline.

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

### Step 4.5: Pre-Gate (automatic, before full quality gate)

**Who:** Pipeline orchestrator (automatic)
**Time:** 15–60 seconds
**Output:** `.dark-factory/reports/<spec>-pre-gate.md`

Before running the full quality gate, the pipeline runs a lightweight **pre-gate** that
checks only Gates 1–3 (Build + Lint + Typecheck). Pre-gate failures loop directly back to
the implementer with the error output — **they do not consume an iteration slot**.

The implementer gets up to `pre_gate_retries` (default: 2) attempts to fix build/lint/type
errors before the full gate runs. This prevents avoidable iteration waste from mechanical
failures the implementer can self-correct.

To disable the pre-gate: set `pre_gate_retries = 0` in `[gates]`.

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
# 5. Blind scenarios (all formats: executable, HTTP, and browser journey)
```

**Gate 5 — Blind Scenarios** runs all scenario formats found in `.dark-factory/scenarios/<spec>/`:

| Format | File pattern | Runner container |
|--------|-------------|-----------------|
| A — HTTP behavioral | `*.scenarios.md` | `dark-factory-python` |
| B — Browser journey | `*.journey.md` | `dark-factory-playwright` |
| C/D — Executable tests | `*.test.ts`, `test_*.py`, etc. | `dark-factory-{language}` |
| Sentinel | `SCENARIOS-NOT-APPLICABLE.md` | — (SKIP reported) |

If `SCENARIOS-NOT-APPLICABLE.md` is present, Gate 5 reports **SKIP** (not 0%) and embeds the sentinel justification in the review prepass for the Reviewer to evaluate semantically. A 0% pass rate is never recorded for a sentinel spec — `min_ship_pass_rate` enforcement is waived.

Format C/D runs inside the language container (same as Gates 1–4). Format A and B are
**orchestrated by the Python pipeline after the language container exits** — each runs in
its own Docker container on the same execution network. This is why journey scenarios work
correctly: `journey-runner.py` runs inside `dark-factory-playwright`, which has Playwright
installed, rather than inside the language container.

`dark-factory-python` is required for Format A even in non-Python projects. It is built automatically with `dark-factory build`.

If a spec uses Format A or B, configure `[test_server]` in `.dark-factory/config.toml`:

```toml
[test_server]
# HTTP runner (Format A)
base_url = "http://localhost:8000"
server_cmd = "uvicorn myapp:app --port 8000"   # optional: omit for external servers
startup_timeout = 30
health_path = "/health"

# Browser runner (Format B)
browser_base_url = "http://localhost:3000"
browser_server_cmd = "npm start"
browser_startup_timeout = 60
```

If the project needs backing services (databases, caches), configure `[[services]]`:

```toml
[[services]]
name = "postgres"
image = "postgres:16-alpine"
health_cmd = "pg_isready -U postgres"
env = { POSTGRES_PASSWORD = "postgres", POSTGRES_DB = "testdb" }
inject_env = { DATABASE_URL = "postgres://postgres:postgres@postgres:5432/testdb" }
```

The Python orchestrator starts service sidecars first (waiting for each health check),
then starts the configured server(s) with `inject_env` variables, polls the health
endpoint, runs all applicable runner containers, then stops everything in reverse order.
If `test_server_cmd` and `browser_server_cmd` are identical, one server container is
reused for both formats. If journey scenarios are present and the `dark-factory-playwright`
image is missing, the pipeline errors with a clear message before any container is launched.

### Step 5.5: Ephemeral Deployment & Evaluation (Optional)

**Who:** Automated deployment + Evaluator agent
**Time:** 1-5 minutes
**Output:** Evaluation report in `.dark-factory/reports/`
**Requires:** `[deploy] cmd` configured in `.dark-factory/config.toml`

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
1. If `auto_pr = true` (or `--auto-pr` flag): create branch, commit changes, push, and open PR via `gh` CLI
2. If `merge_queue = true`: add PR to merge queue
3. Send notifications (Slack/webhook) if configured
4. Otherwise: code is ready locally for manual commit/push

**If NO-SHIP (iteration < 3):**
1. The Oracle writes structured feedback to `.dark-factory/reviews/[spec]-feedback-iter[N].md`
2. Start a new Implementer session (Step 4) pointing to the feedback file
3. The Implementer gets: structured feedback with spec-section-anchored references (FR-N, EC-N + behavioral category), error messages, and priority-ordered fix list (never test source code)
4. Rerun from Step 5

**If NO-SHIP (iteration = 3):** The pipeline generates an escalation report at `.dark-factory/reviews/[spec]-escalation.md`. See `docs/ESCALATION.md` for the decision tree and re-entry procedures.

**Early escalation (ENV_ISSUE):** When Oracle feedback classifies all failures as `ENV_ISSUE` (iteration 1) or a majority as `ENV_ISSUE` (iteration 2+), the pipeline escalates immediately instead of burning remaining iterations. This also triggers when `server_setup_cmd` fails on 2 consecutive iterations. ENV_ISSUE indicates a config or infrastructure problem outside the Implementer's writable scope — retrying won't help.

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
dark-factory run my-feature --debug
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
dark-factory run my-feature

# With options
dark-factory run my-feature --max-iterations 3

# Debug mode: keep containers alive on failure
dark-factory run my-feature --debug
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

Control how much human oversight the pipeline requires via `[autonomy] level` in `.dark-factory/config.toml`:

```toml
[autonomy]
level = 1   # 1=Assisted, 2=Supervised, 3=Autonomous (default)
```

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
dark-factory run my-feature --regression
```

```toml
# Via config (always-on)
[regression]
on_ship = true
timeout = 300
```

After the Oracle says SHIP but before the autonomy-level gate, the pipeline runs `dark-factory scenarios` for each previously-shipped spec. Results are logged to audit. Any failures produce a WARNING but do not block SHIP (the human at Level 1 sees the results before confirming).

### Probabilistic Satisfaction Scoring

By default, the pipeline uses binary pass/fail counting: each scenario either passes or fails, and the pass rate is `passed / total`. Probabilistic satisfaction scoring extends this with two capabilities:

1. **Multi-run execution** — Run each scenario file N times and require K successes (handles flaky tests)
2. **Weighted scoring** — Weight scenario categories differently (e.g., security tests count more than adversarial tests)

#### Enabling Satisfaction Scoring

Set these values in `.dark-factory/config.toml`:

```toml
[satisfaction]
# Run each scenario 5 times, require 3 successes
multi_run_n = 5
multi_run_k = 3

# Require a minimum weighted satisfaction score of 70%
min_score = 70
```

Setting `SCENARIO_MULTI_RUN_N > 1` enables multi-run execution. Setting `MIN_SATISFACTION_SCORE > 0` enables weighted scoring and the hard override floor (Oracle SHIP is rejected if satisfaction is below this floor).

#### How the Score Is Computed

1. Each scenario file is run N times. It passes if >= K runs succeed (early exit on K successes or when K becomes mathematically impossible).
2. Files are grouped by their `NN-` filename prefix into categories (01-Contract, 02-Happy Path, etc.).
3. Per-category pass rates are computed: `category_passed / category_total`.
4. The satisfaction score is a weighted average: `Σ(weight × category_rate) / Σ(weight)`, normalized to 0-100%.

#### Configuration Reference

```toml
[satisfaction]
multi_run_n = 1         # Times to run each scenario file (1 = single-run)
multi_run_k = 1         # Minimum successes required per file
min_score = 0           # Minimum weighted score for SHIP (0 = disabled)
plateau_threshold = 5   # Percentage points for plateau detection

[satisfaction.weights]
contract = 100          # Weight for 01- Contract scenarios
happy_path = 100        # Weight for 02- Happy Path scenarios
edge_cases = 80         # Weight for 03- Edge Case scenarios
error_conditions = 90   # Weight for 04- Error Condition scenarios
state_sequence = 80     # Weight for 05- State & Sequence scenarios
security = 120          # Weight for 06- Security scenarios
property_based = 70     # Weight for 07- Property-Based scenarios
adversarial = 60        # Weight for 08- Adversarial scenarios
```

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

#### Stable Failure Detection

In addition to trajectory classification, the pipeline tracks **per-test failure names** across iterations. When the same tests fail across multiple iterations (invariant failure set), it suggests the plateau is caused by scenario bugs rather than implementation deficiency. This is reported to the Oracle as `stable_failure_ratio`.

The detection triggers when either:
- The invariant ratio exceeds the threshold (default 0.7), OR
- At least 2 invariant failures AND the ratio exceeds 0.5

Configure the threshold via `[gates] trajectory_stable_failure_threshold` (default: `0.7`).

#### Hard Override Behavior

When `MIN_SATISFACTION_SCORE` is non-zero and the satisfaction score is below that floor, the pipeline **rejects an Oracle SHIP decision**. This is logged to the audit trail. The Oracle cannot override the floor — it is an orchestrator-level enforcement, similar to `MIN_SHIP_PASS_RATE`.

#### Interaction with `MIN_SHIP_PASS_RATE`

Both thresholds can coexist. `MIN_SHIP_PASS_RATE` uses unweighted binary pass rate; `MIN_SATISFACTION_SCORE` uses weighted category scoring. When `MIN_SATISFACTION_SCORE > 0`, the weighted score is the primary quality signal displayed to the Oracle. Both floors are enforced independently — failing either one blocks SHIP.

### Canary Deployments

Canary mode replaces the direct post-SHIP deployment with a gradual traffic-shifting rollout. The pipeline evaluates metrics at each stage and automatically rolls back on threshold breach.

**Enabling canary:**

```toml
[canary]
enabled = true
shift_cmd = "scripts/shift-traffic.sh"
rollback_cmd = "scripts/rollback-traffic.sh"  # required

[[canary.stages]]
percent = 10
soak_seconds = 300

[[canary.stages]]
percent = 50
soak_seconds = 600

[[canary.stages]]
percent = 100
soak_seconds = 0

[[canary.metrics]]
name = "error_rate"
max_value = 0.01
metric_cmd = "scripts/get-error-rate.sh"
```

**Required fields:** `canary_rollback_cmd` is required when `canary_enabled = true` — the pipeline will not start without it. `canary_stages` must be in strictly increasing `percent` order.

**What happens on metric failure:** If any metric breaches its threshold during a soak period, the pipeline immediately invokes `rollback_cmd` and marks the deployment as ROLLED_BACK. The spec run does not count as failed — the canary worked as intended.

See [CONFIGURATION.md §canary](CONFIGURATION.md#canary--canary-deployments) for the full field reference.

---

### Feature Flags

Wire up any feature flag system to integrate flag lifecycle with the pipeline. The pipeline creates the flag before the spec begins, enables it after Oracle SHIP, and disables it on failure.

```toml
[flags]
create_cmd = "scripts/flags/create.sh {flag_name}"
enable_cmd = "scripts/flags/enable.sh {flag_name}"
disable_cmd = "scripts/flags/disable.sh {flag_name}"
check_cmd = "scripts/flags/check.sh {flag_name}"
```

**Lifecycle:**
1. Pipeline start → `create_cmd` (idempotent — OK if flag already exists)
2. Oracle SHIP → `enable_cmd`
3. Oracle NO-SHIP or pipeline failure → `disable_cmd`

All four fields are validated as safe script paths at startup. If your commands require shell features (pipes, environment variable expansion), wrap them in a script.

---

### Migration Safety Checks

Detect potentially destructive database migrations before they ship. When enabled, the pipeline scans migration files for DROP, TRUNCATE, DELETE-without-WHERE, and similar destructive operations.

```toml
[migration]
check_enabled = true
check_policy = "warn"   # or "block" to halt the pipeline
dirs = ["migrations/", "db/migrate/", "alembic/versions/"]
```

**Policies:**
- `warn` — logs destructive operations detected, pipeline continues
- `block` — halts the pipeline if destructive operations are found

**Custom patterns:**

```toml
[migration.extra_patterns]
unsafe_alter = ["ALTER TABLE .* DROP COLUMN", "ALTER TABLE .* MODIFY.*NOT NULL"]
```

---

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
- The automated pipeline (`dark-factory run`) runs them sequentially
- Scenarios are generated once and reused across iteration cycles

### Go and Rust Scenario Runners

Go (`go test`) and Rust (`cargo test`) ignore dotted directories by convention. Since blind scenarios live under `.dark-factory/scenarios/`, these toolchains cannot discover them natively.

**Workaround:** Set `[core] scenario_test_cmd` in `.dark-factory/config.toml` to a custom shell script that copies or symlinks scenario files into a toolchain-visible path before running tests.

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

Config (`config.toml`): `scenario_test_cmd = "bash .dark-factory/run-go-scenarios.sh {SCENARIOS_DIR}"`

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

Config (`config.toml`): `scenario_test_cmd = "bash .dark-factory/run-rust-scenarios.sh {SCENARIOS_DIR}"`

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
dark-factory metrics
```
