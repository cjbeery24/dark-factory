# Performance Specification: [Optimization Name]

<!-- dark-factory-metadata
feature-id: F-X
depends-on: []
plan:
profile:
-->

**Author:** [name]
**Date:** [YYYY-MM-DD]
**Bottleneck Type:** CPU | Memory | I/O | Network
**Current p95:** [X ms]
**Target p95:** [X ms]
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Current Baseline

**What is slow or resource-intensive?**
[Describe the operation, endpoint, or process that is underperforming]

**Where was this measured?**
[Environment: production / staging / local. Tool: APM, profiler, benchmark suite]

**Current Metrics:**
| Metric | Value | Measured At |
|--------|-------|-------------|
| p50 latency | [X ms] | [date, environment] |
| p95 latency | [X ms] | [date, environment] |
| p99 latency | [X ms] | [date, environment] |
| Throughput | [X req/s or ops/s] | [date, environment] |
| CPU usage | [X% per core] | [date, environment] |
| Memory usage | [X MB/GB peak] | [date, environment] |
| I/O wait | [X ms or %] | [date, environment] |
| Error rate under load | [X%] | [date, environment] |

**Load Profile:**
- Typical concurrency: [X concurrent users/requests]
- Peak concurrency: [X concurrent users/requests]
- Payload size: [typical and maximum]

---

## 2. Targets

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| p95 latency | [X ms] | [X ms] | [X% reduction] |
| Throughput | [X req/s] | [X req/s] | [X% increase] |
| CPU usage | [X%] | [X%] | [X% reduction] |
| Memory usage | [X MB] | [X MB] | [X% reduction] |
| [Custom metric] | [current] | [target] | [improvement] |

**Minimum acceptable improvement:** [Define the threshold below which the optimization is not worth shipping]

---

## 3. Root Cause Analysis

**What is causing the bottleneck?**
[Explain the root cause with evidence: profiler output, flame graphs, query plans, etc.]

**Evidence:**
```
[Paste relevant profiler output, EXPLAIN ANALYZE, flame graph summary, or benchmark data]
```

**Hot path:**
1. [Step 1 in the hot path — time spent: X ms]
2. [Step 2 in the hot path — time spent: X ms]
3. [Step 3 in the hot path — time spent: X ms (THIS IS THE BOTTLENECK)]
4. [Step 4 in the hot path — time spent: X ms]

**Why this specific approach?**
[Explain why you chose this optimization strategy over alternatives]

---

## 4. Optimization Strategy

### Change 1: [Description of optimization]
- **File(s):** [exact file paths]
- **What changes:** [describe the code change]
- **Why it helps:** [explain the performance mechanism]
- **Expected impact:** [X ms reduction / X% throughput increase]

### Change 2: [Description of optimization]
- **File(s):** [exact file paths]
- **What changes:** [describe the code change]
- **Why it helps:** [explain the performance mechanism]
- **Expected impact:** [X ms reduction / X% throughput increase]

### Change 3: [Description of optimization]
- **File(s):** [exact file paths]
- **What changes:** [describe the code change]
- **Why it helps:** [explain the performance mechanism]
- **Expected impact:** [X ms reduction / X% throughput increase]

[Add more as needed]

---

## 5. Constraints

- **Zero behavior change:** All existing tests must pass without modification
- **No new dependencies:** Unless explicitly listed: [list or "none"]
- **Memory budget:** [Optimization must not increase peak memory by more than X MB]
- **Code readability:** [Optimization must not reduce code clarity without justification]
- **Backward compatibility:** [API contracts, data formats must remain unchanged]
- **Rollback plan:** [Optimization can be reverted by reverting the commit / toggling a feature flag]

---

## 6. Benchmarking Plan

### Benchmark Setup
- **Environment:** [Dedicated benchmark machine / CI / staging — specify hardware]
- **Tool:** [e.g., wrk, k6, JMH, hyperfine, go bench, pytest-benchmark]
- **Dataset:** [Describe the test data: size, shape, how it reflects production]
- **Warm-up:** [Number of warm-up iterations before measurement]
- **Iterations:** [Number of measured iterations]
- **Concurrency:** [Simulated concurrent load]

### Benchmark Scenarios
| Scenario | Description | Key Metric |
|----------|-------------|------------|
| BS-1 | [e.g., Single request, small payload] | p95 latency |
| BS-2 | [e.g., 100 concurrent requests, mixed payloads] | Throughput |
| BS-3 | [e.g., Sustained load for 5 minutes] | Error rate, memory growth |
| BS-4 | [e.g., Maximum payload size] | p99 latency |

### Before/After Protocol
1. Run benchmarks on the current code (commit: [hash]) — record results
2. Apply optimization
3. Run identical benchmarks on the optimized code — record results
4. Compare using [tool: e.g., benchstat, custom script]
5. Verify statistical significance: [e.g., at least 3 runs, p-value < 0.05]

---

## 7. Edge Cases

| ID | Condition | Expected Behavior |
|----|-----------|-------------------|
| EC-1 | [Optimization under zero load (single request)] | [Performance does not regress for single-request case] |
| EC-2 | [Optimization under extreme load (10x normal peak)] | [Degrades gracefully, no crashes or data corruption] |
| EC-3 | [Cold start / empty cache scenario] | [First request latency within acceptable bounds: X ms] |
| EC-4 | [Maximum payload or dataset size] | [Completes within timeout, does not exhaust memory] |
| EC-5 | [Concurrent access to shared resource after optimization] | [No race conditions or deadlocks introduced] |

---

## 8. Acceptance Criteria

### Tier 1: Test Contracts (compiled deterministically)

These contracts are compiled to test code mechanically by the pipeline. They
produce identical tests every run. Use for verifiable performance artifacts.

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: command
    given: "optimized code is built"
    when:
      cmd: "[test suite command]"
    then:
      exit_code: 0
    invariant: true    # already verified by self-tests gate

  - id: TC-2
    fr: FR-1
    type: file_exists
    given: "after implementation"
    when:
      check: "[benchmark script path]"
    then:
      exists: true
```

**Contract types and `when`/`then` schemas:**
- `file_exists` — `when: { check: "path" }` → `then: { exists: true }`
- `file_contains` — two modes:
  - Structured: `when: { file: "path", parse: "json"|"yaml", path: "dot.path" }` → `then: { exists: true }`
  - Pattern: `when: { file: "path", parse: "text", pattern: "regex" }` → `then: { matches: true }`
- `command` — `when: { cmd: "..." }` → `then: { exit_code: 0 }`
- `http` — `when: { method, path, body (request) }` → `then: { status, body (assertions) }`
  - `then.body` values use operators: `{ type: "string" }`, `{ equals: "value" }`, `{ contains: "sub" }`
- `import` — `when: { module: "path", symbols: ["name", ...] }` → `then: { exports: true }`

`invariant` (optional, default `false`): when `true`, the pipeline skips this contract —
it documents that the build/lint/test gate already covers the check.
`given` is freeform text used as the test description in compiled output.

### Tier 2: Adversarial Criteria (SA-generated)

These criteria guide the SA's creative adversarial testing (layers 3-8: edge
cases, error conditions, state, security, property-based, adversarial). The SA
does not re-test what Tier 1 contracts already cover.

| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the optimized code WHEN running benchmark BS-1 THEN p95 latency is below [target] ms |
| AC-2 | GIVEN the optimized code WHEN profiling memory under load THEN peak memory does not exceed [X MB] |

---

## 9. Scenario Intent

Declare what scenarios should verify for each requirement. The Scenario Architect
compiles these intents into executable test code — it does not invent scenarios
beyond what is declared here plus a small cross-cutting set.

### Performance Targets
| # | Intent | Layer |
|---|--------|-------|
| 1 | p95 latency under target after optimization | happy_path |
| 2 | Throughput meets or exceeds target under typical load | happy_path |
| 3 | Memory usage stays within budget under load | property |
| 4 | Improvement meets minimum acceptable threshold | property |
| 5 | Performance gains are statistically significant across runs | property |

### Functional Preservation
| # | Intent | Layer |
|---|--------|-------|
| 1 | No functional regressions — all existing tests pass | contract |
| 2 | Output is identical for all input types before and after | contract |
| 3 | Error handling behavior is unchanged | error |
| 4 | API contracts remain identical | contract |

### Stress & Edge Conditions
| # | Intent | Layer |
|---|--------|-------|
| 1 | Single-request latency does not regress (zero-load case) | edge_case |
| 2 | System degrades gracefully under extreme load (10x peak) | edge_case |
| 3 | Cold start latency is within acceptable bounds | edge_case |
| 4 | Maximum payload size completes within timeout | edge_case |
| 5 | No race conditions or deadlocks under concurrent access | state |

Layer values: `contract`, `happy_path`, `edge_case`, `error`, `state`, `security`, `property`, `adversarial`

---

## 10. Examples

### Example 1: Happy path — optimization under typical load
```
Before:
  p95 latency: [X ms]
  Throughput: [X req/s]
  CPU usage: [X%]

After:
  p95 latency: [X ms] (improved by X%)
  Throughput: [X req/s] (improved by X%)
  CPU usage: [X%] (reduced by X%)

Load: [X concurrent users], [X req/s], [typical payload size]
```

### Example 2: Error case — optimization does not help under specific conditions
```
Scenario: [Describe condition where optimization has minimal effect]
Before:
  p95 latency: [X ms]

After:
  p95 latency: [X ms] (no significant change)

Reason: [Explain why — e.g., bottleneck shifts to a different component]
Action: [Document as known limitation, not a regression]
```

### Example 3: Edge case — cold start performance
```
Scenario: First request after application restart (empty caches)
Before:
  First request latency: [X ms]

After:
  First request latency: [X ms]

Expectation: Cold start latency may not improve (caches are empty regardless).
  Subsequent requests must meet p95 target within [X] requests.
```

---

## 11. Dependencies

### Existing Code
- [File/module path] -- [What it provides, why it is relevant to the optimization]

### Infrastructure
- [Benchmark environment requirements: CPU, memory, disk type]
- [Monitoring/APM tool for production validation]

### Libraries
- [Library name] -- [Version, purpose, already installed?]

---

## 12. Out of Scope

The following are explicitly NOT part of this optimization:
- [Algorithmic rewrites beyond the identified hot path]
- [Infrastructure scaling (horizontal/vertical) as an alternative to code optimization]
- [Optimizing unrelated endpoints or operations]
- [Database query optimization (if not the identified bottleneck)]

---

## 13. Free Variables (implementation choices — DO NOT assert in scenarios)

List implementation decisions this spec intentionally leaves unconstrained.
The Scenario Architect will treat these as DO-NOT-ASSERT directives.

| ID | Variable | What Is Constrained | What Is Free |
|----|----------|---------------------|--------------|
| FV-1 | [e.g., Caching strategy] | [e.g., Must meet latency target in Section 4] | [e.g., Cache implementation (in-memory, Redis, etc.)] |

If no implementation choices are free, write "None — all observable behaviors are fully constrained."
