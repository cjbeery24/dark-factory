# Dark Factory Best Practices

## Industry-Leading Practices Incorporated

This framework incorporates the following proven software engineering practices, adapted for AI-agent-driven development.

---

## 1. Specification-Driven Development (SDD)

**What:** Write a complete specification before any code is written.
**Why:** Eliminates the "build first, fix later" anti-pattern. When the spec is complete, first-pass implementation success rates exceed 60%.
**How:** Use the templates in `templates/` and the spec engineering guide in `docs/SPECIFICATION-GUIDE.md`.

**Key insight:** The spec is the most valuable artifact in the pipeline. A 30-minute spec saves hours of iteration.

---

## 2. Adversarial Blind Testing

**What:** Test scenarios are generated independently from the implementation, with neither side seeing the other's work.
**Why:** Prevents "teaching to the test" — the #1 failure mode of AI-written code. When an agent knows what the tests check, it writes code to pass those specific tests rather than genuinely correct code.
**How:** Separate Claude Code sessions enforce the information barrier. See `AGENT.md` for the architecture.

**Key insight:** The information barrier is what makes Dark Factory fundamentally different from "write code then write tests." The blindness is the feature.

---

## 3. Property-Based Testing

**What:** Instead of testing specific input/output pairs, test that properties (invariants) hold for all possible inputs.
**Why:** Catches bugs that example-based tests miss. A few well-chosen properties can replace hundreds of individual test cases.
**How:** The Scenario Architect generates property-based tests at Layer 7. Use tools like fast-check (JS), Hypothesis (Python), or gopter (Go).

**Examples of properties:**
- "For any valid user, `serialize(deserialize(user))` equals the original"
- "The length of the output array always equals the length of the input array"
- "The sum of all account balances is always zero (double-entry accounting)"

---

## 4. Mutation Testing

**What:** Automatically inject small bugs into the code and verify that the test suite catches them.
**Why:** Measures the actual quality of your test suite. A test suite with 100% code coverage but 50% mutation score is only catching half the bugs it should.
**How:** Tools like Stryker (JS), mutmut (Python), or cargo-mutants (Rust). Target >80% mutation score.

**Key insight:** Code coverage tells you what code was executed. Mutation testing tells you what code was actually verified.

**Note:** Mutation testing is opt-in and not part of the default pipeline. To enable it, set `mutation_cmd` in `[gates]` in your `.dark-factory/config.toml`. When configured, it runs as Gate 6 after Blind Scenarios.

---

## 5. Contract-Driven Development

**What:** Define the exact interface contract (types, shapes, constraints) before implementation.
**Why:** Eliminates integration bugs and misunderstandings between components.
**How:** The spec's Interface Contract section (Section 5 in every template) defines the contract. Both the Implementer and Scenario Architect work from the same contract.

---

## 6. Defense in Depth (Testing Layers)

**What:** Multiple layers of testing, each catching different classes of bugs.
**Why:** No single testing approach catches everything. Layered testing provides comprehensive coverage.
**How:** The Scenario Architect's 8 layers:

| Layer | Catches |
|-------|---------|
| Contract | Wrong function signatures, missing endpoints |
| Happy Path | Basic logic errors |
| Edge Cases | Off-by-one, empty input, boundary bugs |
| Error Conditions | Missing error handling, wrong error codes |
| State & Sequence | Race conditions, invalid state transitions |
| Security | Injection, auth bypass, data leakage |
| Property-Based | Subtle invariant violations across all inputs |
| Adversarial | Creative failure modes, unusual combinations |

---

## 7. Quality Gates

**What:** Automated checkpoints that code must pass before proceeding to the next stage.
**Why:** Catches issues early when they're cheap to fix. A lint error caught at gate 2 is cheaper than a bug found in production.
**How:** The `scripts/quality-gate.sh` script runs gates in order: Build → Lint → Type Check → Self-Tests → Blind Scenarios.

**Key insight:** Gates run in order of increasing cost. Fast, cheap checks first. Slow, expensive checks last.

### Deterministic Review Pre-Pass

After quality gates pass, a deterministic pre-pass computes objective scores for 3 of 5 review dimensions (Test Results Analysis, Code Quality, Security) using gate results, complexity metrics, and regex-based security pattern matching. This runs in <10 seconds with no external tools or network calls. The LLM reviewer receives these scores as baselines and can focus its token budget on Spec Compliance and Robustness — the dimensions that genuinely require judgment. The reviewer retains authority to override any pre-pass score.

---

## 8. Bounded Iteration

**What:** Maximum 3 iterations before human intervention.
**Why:** Prevents infinite loops where the agent keeps trying the same approach. If 3 attempts don't converge, the problem is likely in the spec or architecture, not the implementation.
**How:** The Oracle tracks iteration count and escalates at iteration 3.

---

## 9. Progressive Adoption

**What:** Start with human-in-the-loop oversight and gradually increase autonomy as confidence grows.
**Why:** Dark Factory is a significant workflow change. Binary "trust everything" adoption fails because teams don't yet know if their specs and scenarios are good enough.
**How:** Use `[autonomy] level` in `.dark-factory/config.toml`:

| Phase | Level | When |
|-------|-------|------|
| Learning | 1 (Assisted) | First 5-10 specs — review every SHIP decision |
| Building confidence | 2 (Supervised) | Track metrics — if first-pass rate >60%, move up |
| Mature | 3 (Autonomous) | Spec quality is consistently high, scenarios catch real bugs |

**Key insight:** The progressive trust model mirrors how you'd onboard a human developer — start with code review on every PR, then reduce oversight as they demonstrate competence.

---

## 10. Feedback Without Leakage

**What:** When implementation fails blind tests, the implementer receives error messages and test results, but never the test source code.
**Why:** Maintains the information barrier across iterations. If the implementer saw the test code, they'd optimize for those specific tests.
**How:** The Oracle's feedback format uses spec-section-anchored references (FR-N, EC-N + behavioral category) and error messages, but never test implementation details or scenario names.

---

## 10. Specification Completeness Scoring

**What:** Quantitative scoring of spec completeness before any implementation begins.
**Why:** "80% complete" specs produce 50% first-pass success. "100% complete" specs produce 80%+ first-pass success.
**How:** The Spec Validator's 10-section checklist, each scored 0-10, requiring 100/100 to proceed.

---

## Anti-Patterns to Avoid

### 1. Skipping the Spec
"It's a simple feature, I don't need a spec." This always leads to iteration cycles that cost more time than the spec would have taken.

### 2. Vague Specs
"It should handle errors gracefully." This is not a spec. This is a wish.

### 3. Peeking at Scenarios
"Let me just check what the scenarios test so I can make sure the implementation covers them." This defeats the entire purpose.

### 4. Skipping Quality Gates
"The blind tests pass, so we don't need lint/type checking." Quality gates catch different categories of issues. Skipping any gate leaves a gap.

### 5. Ignoring Escalation
"Let me try iteration 4, 5, 6..." If 3 iterations didn't work, the problem is not in the implementation — it's in the spec, the architecture, or the approach.

### 6. Single-Session Pipeline
"I'll just run everything in one Claude Code session." This breaks information barriers because the session context accumulates.

### 7. Gold Plating
"While I'm implementing, let me also add caching, logging, and a CLI tool." Implement the spec. Nothing more, nothing less.

---

## 11. Linter Messages as Agent Instructions

**What:** Configure linter rules with descriptive messages that guide the AI agent toward correct fixes during iteration feedback.

**Why:** When the quality gate runs lint checks, the output is fed to the Implementer agent in iteration feedback. Clear, instruction-style lint messages help the agent fix issues faster, reducing iteration count.

**How:** Write lint rule messages as direct instructions, not just descriptions.

**ESLint example** (`.eslintrc.js`):
```js
rules: {
  'no-console': ['error', { message: 'Use the structured logger from lib/logger.ts instead of console.*' }],
  'import/no-cycle': ['error', { message: 'Circular imports break hot reload. Move shared types to types/ directory.' }],
}
```

**Ruff example** (`pyproject.toml`):
```toml
[tool.ruff.lint.per-file-ignores]
# Ruff messages are fixed, but you can add comments in pyproject.toml
# and document patterns in AGENTS.md for the Implementer:
# E501: Line too long → Use line continuation or extract helper function
# F401: Unused import → Remove it; don't comment it out
```

**golangci-lint example** (`.golangci.yml`):
```yaml
linters-settings:
  revive:
    rules:
      - name: exported
        arguments: ["checkPrivateReceivers"]
        # Exported functions must have doc comments explaining behavior, not just restating the name.
```

**Key insight:** Lint output is consumed by agents in iteration feedback. The more specific and actionable the message, the fewer iterations needed to converge.

---

## 12. Writing Effective AGENTS.md

**What:** A project-level `AGENTS.md` file that gives AI agents architectural context about your codebase.
**Why:** Agents make better implementation decisions when they understand your project's conventions, directory structure, and constraints. This reduces iteration count by avoiding convention violations.
**How:** Create `AGENTS.md` in your project root (generated automatically by `dark-factory init`). Keep it concise (~100 lines) and focus on:

1. **Architecture overview** — how components fit together
2. **Conventions** — naming, error handling, logging patterns
3. **File structure** — where things live and why
4. **Do/Don't rules** — project-specific constraints agents should follow

The Implementer and Scenario Architect automatically read `AGENTS.md` when it exists. Update it as your project evolves.

**Template:** See `templates/agents-md.md` for a starting point.

---

---

## 13. Cost Management

**What:** Set a USD budget to prevent runaway pipeline runs.
**Why:** Complex specs with multiple iterations can accumulate significant API costs. A budget ceiling catches outliers without blocking normal runs.
**How:**

```toml
[cost]
budget_usd = 10.0               # 0.0 = unlimited — NOT zero budget
per_input_token = 0.000015      # Update these if you switch models
per_output_token = 0.000075
alert_thresholds = [50, 80, 100]
```

**Recommendation:** Set `budget_usd` for all production pipelines. A typical spec costs $0.50–$3.00 with Claude Sonnet; set a ceiling at 5–10× that to catch outliers. Monitor the 50% and 80% alerts — they appear in notifications and logs.

**Model cost rates:** The defaults match Claude Sonnet pricing. If you configure per-role model overrides (e.g., Haiku for spec validation), update `per_input_token` and `per_output_token` to reflect your blended cost, or use `cost_correction_factor` to calibrate after running a few specs with `dark-factory calibrate-cost`.

---

## 14. Model Selection

**What:** Choose which Claude model each agent uses.
**Why:** Not all pipeline stages require the same model capability. Spec validation and review pre-passes are structured tasks that a smaller model handles well; implementation requires deep reasoning.
**How:**

```toml
[model]
id = "claude-sonnet-4-20250514"   # Default for all agents

[model.ids]
spec-validator = "claude-haiku-4-5-20251001"
reviewer = "claude-haiku-4-5-20251001"
implementer = "claude-sonnet-4-20250514"
oracle = "claude-sonnet-4-20250514"
```

**Concrete recommendation:** Haiku for `spec-validator` and `reviewer` (structured scoring); Sonnet for `implementer`, `scenario-architect`, and `oracle` (complex reasoning). This reduces cost by 30–50% with minimal impact on quality.

Valid role names: `spec-validator`, `scenario-architect`, `implementer`, `quality-gate`, `reviewer`, `oracle`.

---

## 15. Dynamic Autonomy

**What:** Let the pipeline adjust autonomy level and iteration budget automatically based on trust history.
**Why:** High-trust mature specs don't need as many iterations or as much oversight as untested specs.
**How:**

```toml
[autonomy]
dynamic_autonomy = true       # Adjust level based on spec trust score
dynamic_iterations = true     # Adjust iteration budget based on history
```

**Warning: Do not enable before 20+ pipeline runs.** The trust scoring system needs history to produce reliable scores. On a new pipeline, dynamic modes produce noisy, unreliable adjustments. Enable only when you have at least 20 completed runs in `.dark-factory/metrics.jsonl`.

---

## 16. Multi-Run Testing for Flaky Tests

**What:** Run each scenario file N times and require K successes instead of a single pass/fail.
**Why:** Flaky tests cause false NO-SHIP decisions. K-of-N execution filters out flakiness while still catching genuine failures.
**How:**

```toml
[satisfaction]
multi_run_n = 3     # Run each file 3 times
multi_run_k = 2     # Require 2 successes out of 3
```

**Recommendation for critical paths:** `n = 3, k = 2`. This tolerates one flaky failure per file without masking genuine bugs.

Note: `multi_run_n > 1` multiplies pipeline execution time by up to N×. Use selectively on specs with known flaky tests.

---

## Continuous Improvement

### Track These Metrics Weekly
1. **First-pass rate:** % of features that SHIP on iteration 1
2. **Spec rejection rate:** % of specs that fail validation
3. **Average iterations:** Mean iterations to SHIP
4. **Scenario catch rate:** % of bugs caught by blind scenarios
5. **Escalation rate:** % that need human intervention

### Retrospective Questions
- Which spec sections are most often incomplete? (Improve templates)
- What types of bugs do blind scenarios miss? (Improve scenario architect)
- Where do implementations most often fail? (Improve implementer agent)
- Are escalations caused by spec issues or implementation issues?

### Framework Evolution
- Update agent definitions based on failure patterns
- Add new scenario layers for domain-specific testing needs
- Create pre-filled templates for common patterns in your domain
- Build a library of example specs that demonstrate quality
