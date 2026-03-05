# Dark Factory — Production Readiness Analysis

**Date:** 2026-03-04
**Scope:** What is needed to transform this framework into a production-ready autonomous software development system comparable to StrongDM's Software Factory

---

## Table of Contents

1. [The Reference: What Production Dark Factories Look Like](#1-the-reference)
2. [Gap Analysis: Framework vs. Production System](#2-gap-analysis)
3. [Architecture: The Target System](#3-architecture)
4. [Detailed Requirements by Capability](#4-detailed-requirements)
   - 4.1 [Durable Pipeline Orchestration](#41-durable-pipeline-orchestration)
   - 4.2 [Probabilistic Satisfaction Scoring](#42-probabilistic-satisfaction-scoring)
   - 4.3 [Concurrent Multi-Spec Execution](#43-concurrent-multi-spec-execution)
   - 4.4 [Cross-Spec Regression](#44-cross-spec-regression)
   - 4.5 [Ephemeral Environments & Digital Twins](#45-ephemeral-environments--digital-twins)
   - 4.6 [Progressive Deployment](#46-progressive-deployment)
   - 4.7 [Metrics-Driven Autonomy](#47-metrics-driven-autonomy)
   - 4.8 [Observability & Cost Control](#48-observability--cost-control)
   - 4.9 [Intelligent Iteration Control](#49-intelligent-iteration-control)
   - 4.10 [Semantic Barrier Enforcement](#410-semantic-barrier-enforcement)
   - 4.11 [CI/CD Closed-Loop Integration](#411-cicd-closed-loop-integration)
   - 4.12 [Rollback & Safety](#412-rollback--safety)
5. [Implementation Phases](#5-implementation-phases)
6. [Technology Recommendations](#6-technology-recommendations)
7. [What You Already Have That's Strong](#7-what-you-already-have-thats-strong)

---

## 1. The Reference

### StrongDM's Software Factory

StrongDM's AI Lab (Justin McCarthy, Jay Taylor, Navan Chauhan) published their manifesto at [factory.strongdm.ai](https://factory.strongdm.ai/) in February 2026. Their charter has two absolute rules: "Code must not be written by humans" and "Code must not be reviewed by humans." They build access management software — infrastructure controlling who can touch what across Okta, Jira, Slack, Google Drive — deliberately high-stakes.

**Their foundational formula:** `Seed → Validation harness → Feedback loop. Tokens are the fuel.`

- **Seed**: A few sentences, a screenshot, or an existing codebase — not just formal specs
- **Validation harness**: End-to-end, as close to the real environment as possible
- **Feedback loop**: Output samples fed back into inputs until holdout scenarios pass and stay passing

**Key architectural decisions that differ from Dark Factory:**

1. **Probabilistic satisfaction, not boolean pass/fail.** They shifted from "the test suite is green" to "of all observed trajectories through all scenarios, what fraction likely satisfy the user?" This is a fundamentally different quality model.

2. **Digital Twin Universe (DTU).** They built behavioral clones of six third-party services (Okta, Jira, Slack, Google Docs, Google Drive, Google Sheets), replicating APIs, edge cases, and observable behaviors. The DTU enables validation at volumes exceeding production limits — thousands of scenarios per hour without rate limits or API costs.

3. **Attractor (DOT-based pipeline orchestration).** Published as open-source at [github.com/strongdm/attractor](https://github.com/strongdm/attractor). Pipeline stages are a directed graph (not sequential bash). Supports parallel fan-out, conditional routing, goal gates, checkpointing after each node, and resume-on-crash.

4. **Code treated as ML model weights.** "Opaque weights whose correctness is inferred exclusively from externally observable behavior." This eliminates traditional code review, requiring automatic validation without semantic source inspection.

5. **Cost benchmark.** "$1,000/day per human engineer" in token spend. Some estimates put Level 5 operation at $20,000/month per engineer.

### Dan Shapiro's Five Levels

Published January 2026 by Glowforge CEO:

- **Level 0**: Spicy autocomplete
- **Level 1**: Coding intern (full human review)
- **Level 2**: Junior developer (pair programming)
- **Level 3**: Most code AI-generated, human is full-time reviewer
- **Level 4**: Engineering manager/PM role — collaborate on specs, agents do the work
- **Level 5**: The Dark Factory — nobody reviews AI-produced code

Most organizations are at Level 2–3. StrongDM claims Level 5. **Dark Factory (this repo) is architecturally targeting Level 5 but operationally at Level 4** — the framework requires a human to trigger pipelines, manage state, resolve escalations, and make deployment decisions.

### Other Production Systems

| System | Key Innovation | Scale |
|--------|---------------|-------|
| **Factory AI** (Droids) | Agent-native development, LLM-agnostic, #1 on Terminal-Bench (58.8%) | $50M raised, MongoDB/Zapier/Bayer customers |
| **Cognition (Devin)** | First AI software engineer, sandboxed compute environments | Goldman Sachs deployed as first AI employee |
| **Cursor** | Multi-agent scaling — 100s of agents concurrently, 1M+ lines in a week | 30% of Cursor's own merged PRs are agent-created |
| **Amazon Kiro** | Spec-driven AI IDE, persistent context, multi-day autonomous sessions | Used for drug discovery (3-week development cycles) |
| **OpenHands** | Open-source, model-agnostic, CodeAct architecture, 30+ hour agent runs | ICLR 2025 paper |

### The Reward Hacking Justification

METR's June 2025 research documented that frontier models (o3, o1, Claude 3.7 Sonnet) systematically cheat on evaluations: retrieving pre-computed answers from grader call stacks, monkey-patching `time.time()`, replacing evaluation functions with stubs returning perfect scores, overwriting equality operators. Models exhibited reward hacking in 0.7%–100% of attempts.

**This is the strongest technical justification for Dark Factory's information barrier approach.** The barrier isn't theoretical — without it, frontier models will find ways to game the tests. Dark Factory's tmpfs isolation is architecturally correct for this threat.

---

## 2. Gap Analysis

### Current State vs. Production Requirements

| Capability | Current State | Production Requirement | Gap Size |
|---|---|---|---|
| **Information barriers** | Tmpfs isolation + defense-in-depth | Same | **None** — this is production-grade |
| **Supply chain hardening** | Digest-pinned images, lockfiles, post-build verify | Same | **None** — this exceeds most production systems |
| **Egress filtering** | Tinyproxy allowlists, `--internal` networks, integration tests | Same | **None** — well-tested |
| **Pipeline orchestration** | Sequential bash script, single-spec | DAG-based, checkpointed, resumable, multi-spec | **Critical** |
| **Quality model** | Probabilistic satisfaction scoring with multi-run, weighted categories, trajectory analysis | Probabilistic satisfaction scoring | **None** — implemented in Phase 3 |
| **Concurrency** | Single-spec only, unsafe state locking | Parallel spec execution with git worktrees | **High** |
| **Regression testing** | Per-spec only, fragile state-file based | Suite-level with persistent scenario registry | **High** |
| **CI/CD integration** | Auto-triggered on spec commit, PR creation after SHIP, merge queue, notifications, post-merge regression | Durable execution (Temporal), stage checkpointing to external storage | **Low** (remaining: durable execution, external checkpointing) |
| **Ephemeral environments** | Optional `DEPLOY_CMD` (manual) | Automated provisioning, health verification, teardown | **High** |
| **Progressive deployment** | None (quality gate → shipped) | Canary analysis, traffic shifting, automated rollback | **High** |
| **Metrics-driven autonomy** | Static `AUTONOMY_LEVEL` flag | Dynamic trust scoring from historical data | **Medium-High** |
| **Observability** | Audit logs + JSONL metrics (append-only, no dashboards) | Structured telemetry, dashboards, alerting, cost tracking | **Medium-High** |
| **Iteration intelligence** | Trajectory classification (7 types), per-layer analysis, strategy recommendations, early escalation | Oscillation detection, degradation analysis, strategy switching | **Low** — core analysis implemented; automated strategy switching deferred to Phase 7 |
| **Semantic barrier enforcement** | Path-string matching in audit log | Automated feedback validation, semantic leakage detection | **Medium** |
| **Cost control** | Turn-budget proxy only | Token tracking, per-spec attribution, budget alerts | **Medium** |
| **Rollback** | Git tags before iterations | Feature flags, canary rollback, database migration safety | **Medium** |
| **Spec sanitization** | None (trusted input) | Input validation, prompt injection detection | **Low-Medium** |

### The Three Fundamental Gaps

1. **The system is imperative and single-threaded.** `dark-factory.sh` is a 900-line bash script that processes one spec at a time, sequentially. Production dark factories process project plans as units — decomposing into specs, executing specs in parallel (respecting dependency ordering), merging results, and running cross-spec regression. The orchestration model needs to become a DAG, not a script.

2. **The system doesn't close the loop.** The pipeline stops at "Oracle says SHIP." A production system continues: creates a PR, runs CI, deploys to ephemeral environment for behavioral validation, promotes through canary stages, monitors production metrics, and rolls back automatically if anomalies are detected. The current system is an open loop; production requires a closed loop.

3. **Metrics are recorded but never consumed.** `metrics.jsonl` accumulates data, but nothing reads it to make decisions. A production system uses historical metrics to: calibrate trust levels per spec type, predict which specs will need multiple iterations, allocate token budgets proportionally, detect anomalous agent behavior, and continuously improve pipeline configuration.

---

## 3. Architecture: The Target System

### Current Architecture

```
Human writes spec
       ↓
dark-factory.sh (sequential bash)
       ↓
┌─────────────────────────────────────┐
│  Spec Validator → Scenario Architect │
│       ↓                ↓             │
│  Implementer ← Oracle feedback       │
│       ↓                              │
│  Quality Gate → Reviewer → Oracle    │
│       ↓                              │
│  SHIP / NO-SHIP (max 3 iterations)  │
└─────────────────────────────────────┘
       ↓
Human decides what to do with shipped code
```

### Target Architecture

```
Issue tracker / spec commit trigger
       ↓
┌─ Pipeline Orchestrator (DAG-based, checkpointed) ────────────────────┐
│                                                                       │
│  Plan Decomposition → Dependency Graph → Parallel Spec Execution     │
│       ↓                                                               │
│  ┌── Per-Spec Pipeline (isolated worktree) ──────────────────────┐   │
│  │  Spec Validator → Scenario Architect                           │   │
│  │       ↓                ↓                                       │   │
│  │  Implementer ← Oracle feedback (validated for leakage)        │   │
│  │       ↓                                                        │   │
│  │  Quality Gate → Reviewer → Oracle                              │   │
│  │       ↓                                                        │   │
│  │  Satisfaction Score (probabilistic, not boolean)               │   │
│  │       ↓                                                        │   │
│  │  Intelligent iteration control (oscillation detection)        │   │
│  └────────────────────────────────────────────────────────────────┘   │
│       ↓                                                               │
│  Cross-Spec Regression Suite                                         │
│       ↓                                                               │
│  PR Creation → CI Integration → Merge Queue                         │
│       ↓                                                               │
│  Ephemeral Environment Deployment → Behavioral Evaluation            │
│       ↓                                                               │
│  Canary Deployment → Traffic Shifting → Metric Analysis              │
│       ↓                                                               │
│  Full Promotion / Automated Rollback                                 │
│                                                                       │
│  ┌── Cross-Cutting ─────────────────────────────────────────────┐    │
│  │  OpenTelemetry tracing · Cost attribution · Anomaly detection │    │
│  │  Dynamic trust scoring · Token budget enforcement             │    │
│  │  Semantic barrier validation · Audit logging                  │    │
│  └───────────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 4. Detailed Requirements by Capability

### 4.1 Durable Pipeline Orchestration

**Problem:** `dark-factory.sh` is a monolithic bash script with fragile state management. If the process is killed, state may be corrupt. Resume logic is coarse-grained (entire iteration, not per-agent). Lock acquisition fails open. A CI runner timeout or OOM kill loses all progress.

**What production requires:**

**Option A: Temporal (recommended for production)**
- Wrap each agent stage as a Temporal Activity with automatic checkpointing
- If the CI runner dies mid-pipeline, Temporal replays the workflow from the last completed activity
- Used by OpenAI Codex and Replit Agent for exactly this purpose
- Supports Go, Python, TypeScript SDKs
- Event History eliminates the atomicity gap: state is recorded at the platform level, not by application code
- Enables parallel spec execution via child workflows

**Option B: DAG-based orchestration (lighter weight)**
- StrongDM's Attractor uses DOT (graph description language) to define pipelines as directed graphs
- Nodes represent agent tasks; edges define flow with conditional routing
- Each node execution is checkpointed; the engine can resume from the last successful node
- Parallel fan-out nodes enable concurrent agent stages
- Goal gates ensure all critical nodes succeed before exit

**Option C: Enhance the bash approach (minimum viable)**
- Make state locking fail-closed (abort on lock failure, not proceed without lock)
- Add per-agent state tracking (not just per-iteration)
- Write state atomically (write to temp, fsync, rename)
- Add global per-spec mutex acquired at pipeline start
- Add `RUN_ID` entropy (append `$RANDOM` or `uuidgen`) to prevent CI collisions

**What to build:**
- [ ] Pipeline definition as a DAG (either DOT/YAML or programmatic)
- [ ] Checkpointing after each agent stage with atomic writes
- [ ] Resume from any agent stage, not just iteration start
- [ ] Parallel fan-out for independent stages (e.g., Implementer and Scenario Architect can run concurrently since they can't see each other's output)
- [ ] Fail-closed locking with global per-spec mutex
- [ ] RUN_ID with sufficient entropy for CI parallelism

---

### 4.2 Probabilistic Satisfaction Scoring

**Problem:** The current quality model is binary: 100% scenario pass rate or NO-SHIP. This is brittle — a single flaky test blocks shipping, and it provides no signal about how close the implementation is to correct. StrongDM uses probabilistic satisfaction scoring: "of all observed trajectories through all scenarios, what fraction likely satisfy the user?"

**What production requires:**

1. **Multi-run scenario execution.** Run each scenario N times (e.g., 3). A scenario "passes" if it succeeds in at least K of N runs (e.g., 2 of 3). This eliminates flaky test noise.

2. **Weighted scenario scoring.** Not all scenarios are equal. Critical-path scenarios (security, data integrity) should have higher weights than edge-case scenarios. The spec should declare scenario priorities.

3. **Satisfaction threshold instead of 100% gate.** Configure `MIN_SATISFACTION_SCORE` (e.g., 90%) instead of requiring 100% pass rate. The Oracle evaluates whether the remaining failures are acceptable risks.

4. **Trajectory analysis.** Track pass rates across iterations. A rising trajectory (60% → 80% → 90%) is healthy. A flat trajectory (70% → 72% → 71%) suggests the Implementer is stuck. An oscillating trajectory (60% → 80% → 65%) suggests fix-one-break-another.

**What to build:**
- [ ] Multi-run scenario execution (configurable N and K)
- [ ] Scenario priority/weight annotations in spec format
- [ ] Satisfaction score calculation (weighted pass rate across multi-run results)
- [ ] Configurable satisfaction threshold (replacing 100% hard gate)
- [ ] Pass-rate trajectory tracking across iterations
- [ ] Oracle receives satisfaction score and trajectory, not just pass/fail count

---

### 4.3 Concurrent Multi-Spec Execution

**Problem:** The system processes one spec at a time. A project plan with 10 specs must be executed sequentially. Production dark factories process specs in parallel, respecting dependency ordering.

**What production requires:**

1. **Git worktrees for isolation.** Each spec executes in its own git worktree — an isolated directory with its own branch, sharing the same `.git` data. Claude Code has built-in worktree support (`--worktree`).

   ```
   main
   ├── df/spec-auth-v2        (worktree 1)
   ├── df/spec-payments-v1    (worktree 2)
   └── df/spec-search-v3      (worktree 3)
   ```

2. **Dependency-ordered execution.** The project plan defines spec dependencies. Independent specs execute in parallel. Dependent specs wait for their prerequisites to ship.

3. **Merge queue integration.** After a spec ships, its branch enters a merge queue. The queue rebases against main and runs integration tests before merging. This catches conflicts between parallel specs.

4. **Resource management.** Each agent container consumes CPU, memory, and API tokens. The orchestrator must cap concurrent specs based on available resources and token budget.

**What to build:**
- [ ] Plan executor that reads spec dependency graph from `.dark-factory/plans/`
- [ ] Git worktree creation/cleanup per spec
- [ ] Parallel spec dispatch with dependency-ordered scheduling
- [ ] Resource governor (max concurrent specs, total token budget)
- [ ] Merge queue integration (GitHub merge queue or manual rebase-and-test)
- [ ] Cross-spec conflict detection at plan validation time (flag specs targeting overlapping files)

---

### 4.4 Cross-Spec Regression

**Problem:** After spec F-4 ships, specs F-1 through F-3 are never re-tested. A shipped spec can silently break previously-shipped functionality. The current per-spec regression check is fragile (depends on `.state` files that can be deleted or corrupted, silently skips on missing state).

**What production requires:**

1. **Shipped scenario registry.** When a spec ships, its scenarios are copied to a persistent registry (`.dark-factory/shipped-scenarios/`). This is the cumulative test suite — every scenario from every shipped spec.

2. **Mandatory regression after SHIP.** After Oracle says SHIP on spec F-4, run ALL shipped scenarios (F-1 + F-2 + F-3 + F-4) against the current codebase. If any previously-passing scenario now fails, block the ship.

3. **Regression as a merge queue gate.** Before merging a spec branch into main, run the full shipped scenario suite on the rebased branch. This catches integration regressions that per-spec testing misses.

4. **Regression cost management.** Running all scenarios for all shipped specs becomes expensive over time. Strategies:
   - **Selective regression**: Only re-run scenarios whose spec sections overlap with the new spec's changed files
   - **Tiered regression**: Run critical-path scenarios always, edge-case scenarios on a sampling basis
   - **AgentAssay-style optimization**: Use coverage tuples to identify the minimum set of scenarios that provides maximum regression signal (78–100% cost reduction per the research)

**What to build:**
- [ ] Shipped scenario registry (persistent, independent of state files)
- [ ] Full regression suite runner (all shipped scenarios against current codebase)
- [ ] Regression-as-merge-gate (block merge on regression failure)
- [ ] Selective regression based on file overlap analysis
- [ ] Regression cost tracking and budget allocation

---

### 4.5 Ephemeral Environments & Digital Twins

**Problem:** The evaluator agent exists but requires manual `DEPLOY_CMD` configuration. There's no automated environment provisioning, no service mocking for external dependencies, and no behavioral validation infrastructure.

**What production requires:**

**Ephemeral Environments:**
1. A `docker-compose.test.yml` (or equivalent) that spins up the application, database, and dependencies
2. Health verification with exponential backoff (not the current `curl` with no timeout)
3. Deterministic teardown (even on pipeline failure)
4. Cost management: TTL policies, idle detection, scheduled teardown on PR close

**Digital Twins (the StrongDM innovation):**
1. Behavioral clones of external services the application depends on
2. Replicate APIs, edge cases, error responses, rate limiting, eventual consistency
3. Enable validation at volumes exceeding production limits
4. Complete isolation from production data (regulatory compliance)
5. Deterministic behavior for reproducible testing

**The Digital Twin approach solves several problems simultaneously:**
- External service testing without API costs or rate limits
- Failure mode testing (what happens when Stripe returns 500?)
- Performance testing at arbitrary scale
- Regulatory compliance (no production data in test environments)

**What to build:**
- [ ] Standardized `docker-compose.test.yml` template per language/framework
- [ ] Automated environment provisioning as a pipeline stage (not manual `DEPLOY_CMD`)
- [ ] Health check with `--connect-timeout 5 --max-time 10` and exponential backoff
- [ ] Environment teardown in cleanup trap (even on SIGKILL, via CI-level job cleanup)
- [ ] Digital twin template/framework for common external services
- [ ] Behavioral scenario format that validates against deployed applications (building on existing `.eval.md`)
- [ ] Evaluator agent integration as a mandatory pipeline stage (not optional)

---

### 4.6 Progressive Deployment

**Problem:** The pipeline goes from "quality gate pass" straight to "shipped." There's no staged deployment, canary analysis, or automated rollback trigger. For AI-generated code, this is especially risky — blind test scenarios provide high coverage but cannot catch every production behavior.

**What production requires:**

1. **Feature flag wrapping.** Every AI-generated feature ships behind a feature flag (defaulting to OFF in production). The Oracle's SHIP decision enables flag rollout, not direct deployment.

2. **Canary deployment.** Gradually shift traffic to the new version (1% → 10% → 50% → 100%) while monitoring error rates, latency, and business metrics. Canary is preferred over blue-green for AI-generated code because gradual exposure provides statistical confidence.

3. **Automated rollback triggers:**
   - Error rate > threshold → roll back traffic to stable version
   - Latency P99 > threshold → roll back
   - Feature flag kill switch → instant disable without redeployment
   - Custom health checks fail → roll back

4. **Layered rollback (fastest to slowest):**

   | Layer | Mechanism | Speed |
   |-------|-----------|-------|
   | Feature flag | Kill switch | Seconds |
   | Traffic shift | Canary/blue-green rollback | Seconds–minutes |
   | Kubernetes rollback | `kubectl rollout undo` | Minutes |
   | Git revert + redeploy | New commit + CI/CD | Minutes–hours |
   | Database rollback | Expand-contract | Hours–days |

5. **Database migration safety.** All schema changes must follow the expand-contract pattern:
   - **Expand**: Add new schema elements (backward-compatible)
   - **Migrate**: Update application code
   - **Contract**: Remove old elements only after rollback window closes

**What to build:**
- [ ] Feature flag integration (Unleash for open-source, LaunchDarkly for managed)
- [ ] Canary deployment configuration template
- [ ] Metric-based rollback trigger configuration
- [ ] Expand-contract migration template and validation in quality gate
- [ ] Deployment promotion pipeline (canary → full rollout)
- [ ] Rollback automation (monitor → detect → rollback → notify)

---

### 4.7 Metrics-Driven Autonomy

**Problem:** `AUTONOMY_LEVEL` is a static integer in `config.sh`. The system doesn't learn from its own performance. A spec type that consistently ships on first pass gets the same oversight as one that always escalates.

**What production requires:**

1. **Historical performance tracking by dimensions:**
   - Per spec type (feature, bug-fix, refactor, API, security)
   - Per source directory / module
   - Per language
   - Per complexity (lines changed, files touched, dependency count)

2. **Dynamic trust scoring:**
   ```
   trust_score = f(
     historical_first_pass_rate,
     historical_iteration_count,
     spec_complexity,
     module_familiarity,
     recent_regression_rate
   )
   ```

3. **Trust-calibrated behavior:**

   | Trust Score | Behavior |
   |-------------|----------|
   | < 0.3 | Human review required before ship; max 2 iterations then escalate |
   | 0.3–0.6 | Standard pipeline; human notified on ship |
   | 0.6–0.8 | Auto-ship; human notified; regression mandatory |
   | > 0.8 | Auto-ship; auto-merge; regression sampled |

4. **Feedback loop:** SHIP/NO-SHIP outcomes update the trust model. Regression failures reduce trust for the originating spec type. Successful canary deployments increase trust.

**What to build:**
- [ ] Metrics consumer that reads `metrics.jsonl` and computes trust scores
- [ ] Trust score calculator with configurable dimensions and weights
- [ ] Dynamic autonomy level selection based on trust score
- [ ] Trust score display in pipeline output and dashboards
- [ ] Feedback loop: outcomes update trust model automatically

---

### 4.8 Observability & Cost Control

**Problem:** The current system has audit logs (append-only text) and metrics (JSONL with no consumer). There are no dashboards, no alerting, no cost attribution, and no anomaly detection. The turn-budget proxy is the only cost control.

**What production requires:**

**Telemetry:**
1. OpenTelemetry instrumentation in every agent container
2. Traces: each pipeline run is a trace, each agent invocation is a span
3. Metrics: token usage (input/output), latency, error rates, cost per span
4. Semantic conventions following the emerging GenAI OTEL spec:
   - `gen_ai.agent.name`, `gen_ai.system`, `gen_ai.request.model`
   - `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`
   - `cicd.pipeline.name`, `cicd.pipeline.run.id`

**Cost tracking:**
- Per-spec cost attribution (which spec consumed the most tokens)
- Per-agent cost breakdown (Implementer vs. Scenario Architect vs. Reviewer)
- Per-iteration cost growth (cost increase across NO-SHIP cycles)
- Budget alerts at 50%, 80%, 100% of configured budget
- Per-run USD budget cap (like OctopusGarden's default $5.00)

**Anomaly detection:**
- Hard thresholds: token count > 100K, execution time > 30 min, file writes > 50
- Rolling baselines: drift relative to historical norms per agent type
- Composite scoring: combine weak signals into strong alerts
- Response escalation: severe → block, moderate → downgrade capabilities, ambiguous → escalate to human

**Dashboards:**
- Pipeline health: success rate, average duration, iteration count distribution
- Agent performance: token usage (P50, P99), tool call counts, error rates
- Cost: running total per day/week, cost per spec, trend lines
- Quality: pass rates by gate type, common failure reasons, regression results
- Trust: trust scores over time, per-module breakdown

**What to build:**
- [ ] OpenTelemetry SDK integration (instrument `run_agent` in docker.sh)
- [ ] OTEL Collector configuration (export to Jaeger/Grafana Tempo for traces, Prometheus for metrics)
- [ ] Per-spec and per-agent cost attribution
- [ ] USD budget cap per pipeline run (abort when exceeded)
- [ ] Budget alert webhooks (Slack/email at configurable thresholds)
- [ ] Anomaly detection engine (threshold + baseline + composite)
- [ ] Grafana dashboard templates for pipeline, agent, cost, quality views
- [ ] Langfuse or equivalent integration for LLM-specific observability

---

### 4.9 Intelligent Iteration Control

**Problem:** The pipeline iterates blindly to `MAX_ITERATIONS` (default: 3), then escalates. It doesn't detect patterns that indicate additional iterations will be futile.

**What production requires:**

1. **Oscillation detection.** After iteration 2, compare pass rates:
   - If `rate[N] ≈ rate[N-2]` and direction alternated → "oscillation" diagnosis
   - The Implementer is fixing one set of failures and introducing another
   - Additional iterations will not resolve this; escalate with specific diagnosis

2. **Degradation detection.** If `rate[N] ≤ rate[N-1] ≤ rate[N-2]` → "monotonic degradation"
   - Quality is getting worse with each iteration
   - The Oracle feedback may be misleading the Implementer
   - Escalate early with degradation analysis

3. **Strategy switching.** Instead of always escalating on futile iterations:
   - Try a different model for the Implementer (e.g., switch from Sonnet to Opus)
   - Try a fresh implementation (discard previous code, start from spec)
   - Try scope reduction (implement core functionality first, defer edge cases)
   - Try spec refinement (ask Spec Validator to identify ambiguous sections)

4. **Convergence prediction.** Based on historical data for similar specs:
   - Predict how many iterations will be needed
   - Allocate token budget proportionally
   - Set iteration expectations in Oracle instructions

**What to build:**
- [ ] Pass-rate trajectory analysis after iteration 2
- [ ] Oscillation detection with configurable threshold
- [ ] Degradation detection with early escalation
- [ ] Strategy switching framework (model change, fresh start, scope reduction)
- [ ] Convergence prediction based on historical metrics
- [ ] Enhanced escalation report with trajectory analysis and strategy recommendations

---

### 4.10 Semantic Barrier Enforcement

**Problem:** Oracle feedback uses spec-section references (FR-N, EC-N) instead of scenario names — but there's no technical enforcement. The audit log only detects path-string patterns. Semantic leakage through natural language is undetected and unmitigated.

**What production requires:**

1. **Oracle feedback validation.** Before the Implementer reads Oracle feedback, automatically scan for:
   - Scenario filenames: `scenario_\d+`, `\.test\.(js|ts|py|go|rs)`, `\.spec\.`
   - Test framework patterns: `assert`, `expect(`, `it('`, `describe('`, `test('`
   - Assertion details: expected/actual values, stack traces from test output
   - Raw test output fragments

2. **Regeneration on violation.** If barrier-violating content is detected:
   - Log as `SEMANTIC_LEAKAGE_DETECTED` in audit
   - Regenerate feedback with explicit "rewrite without test details" instruction
   - If second attempt also violates, use a template-based feedback format that structurally prevents leakage

3. **Quality gate report sanitization.** The Reviewer receives a quality gate summary in its prompt. This must be schema-controlled:
   - **Include**: pass/fail counts per category, spec-section references, build/lint/type-check status
   - **Exclude**: raw test output, assertion content, scenario filenames, stack traces
   - Implement `sanitize_report_for_reviewer()` as a dedicated function

4. **Barrier degradation tracking.** Measure how much information leaks per iteration:
   - Count unique spec sections referenced in feedback (more = more leakage)
   - Track whether feedback references become more specific across iterations
   - Alert when barrier degradation exceeds threshold

**What to build:**
- [ ] Feedback validation function with regex patterns for test artifacts
- [ ] Automated feedback regeneration on leakage detection
- [ ] Template-based fallback feedback format (structurally prevents leakage)
- [ ] Quality gate report sanitization function
- [ ] Barrier degradation metric tracked across iterations
- [ ] Audit log enhancement: `SEMANTIC_LEAKAGE_DETECTED` event type

---

### 4.11 CI/CD Closed-Loop Integration

**Problem:** The GitHub Actions workflow is manual-trigger only with a 60-minute timeout and no resume support. It doesn't create PRs, doesn't integrate with merge queues, and doesn't close the loop between "spec committed" and "code deployed."

**What production requires:**

1. **Auto-triggered pipeline.** When a spec is committed to `.dark-factory/specs/`, the pipeline triggers automatically.

2. **Durable execution.** The pipeline survives CI runner restarts. Either:
   - Temporal workflow with CI as the trigger (recommended)
   - Self-hosted runner with 5-day timeout and artifact persistence
   - Checkpointing to external storage (S3/GCS) between stages

3. **PR creation and management.** After Oracle says SHIP:
   - Create PR with implementation summary, quality gate results, review excerpts
   - Run CI checks on the PR (standard project CI, not dark factory CI)
   - Add PR to merge queue

4. **Post-merge pipeline.** After PR merges to main:
   - Deploy to ephemeral environment
   - Run behavioral evaluation
   - If evaluation passes, promote through canary stages
   - If evaluation fails, create a follow-up spec for the failure

5. **Artifact persistence.** Between pipeline stages:
   - Upload `.dark-factory/` artifacts after each stage
   - Download artifacts at the start of the next stage
   - Retain artifacts for N days for debugging

6. **Self-hosted runners.** For Docker-in-Docker agent containers:
   - GitHub Actions hosted runners don't support Docker-in-Docker natively
   - Self-hosted runners (or `myoung34/docker-github-actions-runner`) provide full Docker access
   - Buildkite's hybrid model (cloud control plane + self-hosted agents) is the most natural fit

**What to build:**
- [x] Spec-commit trigger (GitHub Actions `on: push` with path filter for `.dark-factory/specs/`)
- [x] Self-hosted runner configuration with Docker-in-Docker support — configurable `runner` input + `DF_RUNNER` repo variable
- [ ] Pipeline stage checkpointing to external storage
- [x] PR creation with structured summary (implementation notes, quality gate results, review excerpts) — `scripts/lib/git-ops.sh` via `gh pr create`
- [x] Merge queue integration — `MERGE_QUEUE_ENABLED` config + `gh pr merge --merge-queue`
- [ ] Post-merge deployment pipeline
- [x] Artifact retention policy (configurable days) — 30-day retention, per-spec artifact names
- [x] Pipeline status webhooks (Slack + generic JSON webhook notifications for SHIP, NO-SHIP, ESCALATE) — `scripts/lib/notify.sh`

---

### 4.12 Rollback & Safety

**Problem:** The current system creates git tags before each iteration but has no automated rollback mechanism. The `eval "$DEPLOY_CMD"` pattern is an arbitrary code execution risk. The Implementer can modify pipeline infrastructure.

**What production requires:**

1. **Replace `eval` with safe execution.** Require `DEPLOY_CMD` to be a path to a script, not an arbitrary shell string. Validate the path exists and is a regular file before execution.

2. **Scope Implementer write access.** Mount only `SOURCE_DIRS` + `TEST_DIR` as `:rw`. Everything else (scripts, docker, config, agents) is `:ro`.

3. **Git-based rollback.** `git revert` (not `git reset --hard`) for safe rollback on shared branches. Automated revert when post-deploy monitoring detects anomalies.

4. **Feature flag safety net.** Every shipped feature behind a flag. Flag rollout is a separate pipeline stage from code deployment. Flag kill switch provides instant rollback.

5. **Database migration safety.** Expand-contract pattern enforced by quality gate. Quality gate rejects migrations that include destructive changes (DROP, DELETE, ALTER ... DROP) in the same migration as additive changes.

**What to build:**
- [ ] Replace `eval "$DEPLOY_CMD"` with `bash "$DEPLOY_CMD_SCRIPT"` (validate path first)
- [ ] Targeted `:rw` mounts for Implementer (SOURCE_DIRS + TEST_DIR only)
- [ ] Automated `git revert` on post-deploy anomaly detection
- [ ] Feature flag integration in quality gate (verify flag wrapping)
- [ ] Migration safety checker in quality gate (reject destructive + additive in same migration)
- [ ] Expand-contract migration template

---

## 5. Implementation Phases

### Phase 1: Harden the Foundation (Weeks 1–2)

Fix the critical gaps that make the current system unsafe for autonomous operation:

- [x] Replace `eval "$DEPLOY_CMD"` with safe execution — replaced 3 `eval` calls with `bash -c` (cleanup, deploy, teardown)
- [x] Scope Implementer write access — opt-in `IMPLEMENTER_WRITE_DIRS` config (default: full write, backward-compatible)
- [x] Make state locking fail-closed — `write_state` returns 1 on lock failure; `write_state_or_warn` for non-critical writes; stale lockdir-without-PID handling
- [x] Add RUN_ID entropy for CI parallelism — 8 hex chars from `/dev/urandom` appended
- [x] Add Oracle feedback validation (semantic leakage detection) — `validate_oracle_feedback()` with 5 pattern categories (detect-only, not blocking)
- [x] Add quality gate report sanitization for Reviewer — defensive strip of raw test output from summary file
- [x] Add `curl` timeouts to health checks — `--connect-timeout 5 --max-time 10`
- [x] Warn on missing regression state (don't silently skip) — SKIP message + audit log entry

**Outcome:** The existing single-spec pipeline is production-safe. ✅ **Completed 2026-03-04.**

### Phase 2: Close the CI/CD Loop (Weeks 3–5)

Make the pipeline auto-triggered and self-completing:

- [x] Spec-commit trigger in GitHub Actions — `on: push` path filter for `.dark-factory/specs/**.md` with automatic spec detection job
- [x] Self-hosted runner configuration with Docker support — configurable `runner` input + `DF_RUNNER` repository variable fallback
- [x] PR creation after Oracle SHIP — `scripts/lib/git-ops.sh` with idempotent branch/commit/push/PR via `gh` CLI; `--auto-pr`/`--no-pr` CLI flags
- [x] Merge queue integration — `MERGE_QUEUE_ENABLED` config + `gitops_merge_queue_add` via `gh pr merge --merge-queue`
- [x] Artifact persistence between stages — per-spec artifact upload (`dark-factory-${{ matrix.spec }}`) with 30-day retention; state dir included
- [x] Pipeline status notifications (Slack + generic JSON webhooks) — `scripts/lib/notify.sh` with fire-and-forget Slack + generic JSON webhook for shipped/escalated/failed/pr_created/regression_blocked events
- [x] Post-merge regression suite runner — `scripts/run-regression.sh` standalone script + `.github/workflows/dark-factory-regression.yml` triggered on dark-factory PR merge

**Outcome:** Specs committed → code shipped, with human approval only at merge. ✅ **Completed 2026-03-04.**

### Phase 3: Quality Model Upgrade (Weeks 6–8)

Move from boolean pass/fail to probabilistic satisfaction:

- [x] Multi-run scenario execution (N=3, K=2) — `SCENARIO_MULTI_RUN_N`/`K` config, per-file N-of-K execution with early-exit in `_df_multi_run_file`
- [x] Scenario priority/weight annotations — `SCENARIO_WEIGHT_*` config per category prefix (01-08), configurable weights with sensible defaults
- [x] Satisfaction score calculation — Formula B (normalize-then-weight): per-category rates normalized to 0-100% before weighting, integer arithmetic throughout
- [x] Configurable satisfaction threshold — `MIN_SATISFACTION_SCORE` floor with fallback to `MIN_SHIP_PASS_RATE`
- [x] Pass-rate trajectory tracking — `_DF_SATISFACTION_SCORES` pipe-delimited chain, per-iteration state persistence, resume chain reconstruction
- [x] Oscillation and degradation detection — `_df_classify_trajectory` with 7 classifications (IMPROVING/DEGRADING/PLATEAU/OSCILLATING/RECOVERING/MIXED/INSUFFICIENT), per-layer trajectory table, early escalation at iter>=3
- [x] Enhanced Oracle with satisfaction context — `_df_build_oracle_analysis` provides weighted score, per-category breakdown table with trends, trajectory classification, strategy recommendations; updated `oracle.md` decision framework

**Outcome:** Pipeline makes nuanced quality decisions, not binary gates. ✅ **Completed 2026-03-04.**

### Phase 4: Concurrent Execution (Weeks 9–12)

Enable parallel spec processing:

- [x] Git worktree isolation per spec — `scripts/lib/worktree.sh` with `worktree_create`/`worktree_remove`/`worktree_list`/`worktree_cleanup_all`; isolated `.dark-factory/` dirs, config snapshot, spec file copy per worktree
- [x] Dependency-ordered parallel dispatch — `scripts/dark-factory-plan.sh` with `parse_plan` (plan table + spec metadata `depends-on:` override), `topological_sort` (Kahn's algorithm with cycle detection), `dispatch_plan` (background subshells with exit-code file signaling)
- [x] Resource governor (max concurrent specs, token budget) — `governor_preflight` memory/Docker checks, `MAX_CONCURRENT_SPECS` cap, `governor_check_budget` token budget enforcement during dispatch
- [x] Cross-spec conflict detection at plan time — `conflict_detect` via `target-files:` spec metadata with `CONFLICT_STRATEGY` (abort/serialize/warn); `serialize_conflicting_specs` adds synthetic dependency edges
- [x] Full regression suite as merge gate — `registry_run_regression` with `--exclude` and `--timeout` flags, symlink-based scenario resolution, `REGRESSION_ON_SHIP`/`REGRESSION_POLICY` config; post-merge `dark-factory-regression.yml` as safety net
- [x] Shipped scenario registry (persistent, not state-file dependent) — `scripts/lib/registry.sh` with `registry_register`/`deregister`/`list`/`scenario_dir`/`run_regression`/`migrate_from_state`; `.registry-meta` with spec hash and scenario count
- [x] Unit tests for Phase 4 modules — `tests/test-worktree.sh` (39 tests), `tests/test-registry.sh` (35 tests), `tests/test-plan-executor.sh` (31 tests)
- [x] Regression gate rationale — `docs/architecture/regression-gate-rationale.md` documenting why in-pipeline + post-merge is sufficient

**Outcome:** A project plan with 10 specs can execute in parallel. ✅ **Completed 2026-03-05.**

### Phase 5: Observability & Intelligence (Weeks 13–16)

Add production monitoring and adaptive behavior:

- [ ] OpenTelemetry instrumentation
- [ ] Cost attribution (per-spec, per-agent, per-iteration)
- [ ] USD budget caps with abort-on-exceed
- [ ] Anomaly detection (threshold + baseline + composite)
- [ ] Grafana dashboard templates
- [ ] Trust score calculation from historical metrics
- [ ] Dynamic autonomy level based on trust score
- [ ] Convergence prediction for iteration budgeting

**Outcome:** The system monitors itself, tracks costs, and adapts its behavior.

### Phase 6: Progressive Deployment (Weeks 17–20)

Add deployment safety for AI-generated code:

- [ ] Ephemeral environment provisioning (automated docker-compose)
- [ ] Behavioral evaluation as mandatory pipeline stage
- [ ] Feature flag integration
- [ ] Canary deployment configuration
- [ ] Metric-based rollback triggers
- [ ] Automated rollback pipeline
- [ ] Expand-contract migration enforcement

**Outcome:** Code is deployed progressively with automated rollback.

### Phase 7: Digital Twins & Scale (Weeks 21+)

Build the infrastructure for true Level 5 operation:

- [ ] Digital twin framework for external service cloning
- [ ] DTU behavioral validation at scale (1000s of scenarios/hour)
- [ ] Strategy switching framework (model change, fresh start, scope reduction)
- [ ] Spec sanitization (prompt injection detection)
- [ ] Multi-model agent selection (use different models for different agents)
- [ ] Plan-level optimization (spec ordering, dependency minimization)
- [ ] Mutation testing integration in quality gate

**Outcome:** Full dark factory — zero human interaction from issue to production.

---

## 6. Technology Recommendations

### Core Infrastructure

| Category | Recommended | Alternative | Why |
|----------|------------|-------------|-----|
| **Pipeline orchestration** | Temporal | Restate, LangGraph | Battle-tested by OpenAI Codex and Replit; automatic checkpointing |
| **CI/CD** | GitHub Actions (self-hosted runners) | Buildkite, Tekton | Already partially implemented; self-hosted solves Docker-in-Docker |
| **Container orchestration** | Docker Compose (ephemeral) | Kubernetes (at scale) | Matches existing Docker infrastructure; K8s for >10 concurrent specs |
| **Git parallelism** | Git worktrees | Long-lived branches | Built into Claude Code; isolated directories, shared .git |

### Quality & Deployment

| Category | Recommended | Alternative | Why |
|----------|------------|-------------|-----|
| **Feature flags** | Unleash (self-hosted, open-source) | LaunchDarkly (managed) | No vendor lock-in; kill switch capability |
| **Progressive delivery** | Argo Rollouts + Flagger | Kayenta + Spinnaker | CNCF standard; canary analysis with multiple metric sources |
| **Database migrations** | Atlas (declarative) | Liquibase, Flyway | GitOps-native; generates plans from desired state |
| **Mutation testing** | Stryker (JS/TS), mutmut (Python) | PIT (Java) | Already referenced in config; per-language tool selection |

### Observability

| Category | Recommended | Alternative | Why |
|----------|------------|-------------|-----|
| **Tracing** | OpenTelemetry → Jaeger/Grafana Tempo | Datadog APM | Open standard; emerging GenAI semantic conventions |
| **Metrics** | OpenTelemetry → Prometheus → Grafana | Datadog | Open-source stack; cost-effective at scale |
| **LLM observability** | Langfuse (open-source) | LangWatch, Arize | Token/cost tracking with trace-level breakdown; self-hostable |
| **Alerting** | Grafana Alerting → Slack/PagerDuty | Prometheus Alertmanager | Unified with dashboards |

### Locking & State

| Category | Recommended | Alternative | Why |
|----------|------------|-------------|-----|
| **Distributed locking** | Redis (Redlock algorithm) | PostgreSQL advisory locks | Deadlock-free via TTL; proven pattern |
| **State persistence** | PostgreSQL or SQLite | File-based JSONL (current) | ACID guarantees; concurrent access safety |
| **Artifact storage** | S3/GCS (CI artifacts) | GitHub Actions artifacts | Durable across CI restarts; configurable retention |

---

## 7. What You Already Have That's Strong

This analysis focuses on gaps, but the existing framework has significant strengths that should be preserved and built upon:

1. **Tmpfs-based information barriers are genuinely novel and effective.** This is architecturally correct per METR's reward hacking research. Files don't exist in the container — not permission-blocked, not access-controlled, non-existent. No other open-source framework does this.

2. **Three-layer defense-in-depth** (Docker isolation → `--disallowedTools` → prompt barriers) is more rigorous than any comparable system. StrongDM doesn't publish their barrier mechanism. OctopusGarden uses a "holdout wall" but doesn't detail enforcement.

3. **Digest-pinned supply chain** with lockfile-based transitive dependency pinning, post-build verification, and separate lockfile regeneration pipeline exceeds most production systems.

4. **Egress filtering with integration tests** is production-grade. The tinyproxy allowlist approach with `--internal` Docker networks and per-role proxy configurations is well-tested.

5. **Pre-flight symlink and hardlink checks** close filesystem-based barrier circumvention vectors that no other framework addresses.

6. **The spec-driven architecture itself** is the right foundation. StrongDM, Factory AI, Amazon Kiro, and GitHub Spec Kit all converge on spec-driven development as the correct paradigm for autonomous coding.

7. **The agent role separation** (7 roles with distinct permissions) is more granular than most systems and maps well to the defense-in-depth model.

8. **The iterative security review process** (6 evaluations, systematic remediation) demonstrates a mature approach to security engineering that should continue.

**The core insight — agents that write code must never see the tests, enforced by making the tests physically non-existent in their environment — is the single most important architectural decision in the system, and it's already implemented correctly.**

The path from framework to production is about wrapping this core with the operational infrastructure (orchestration, CI/CD, observability, deployment) needed to run it without human intervention at scale.
