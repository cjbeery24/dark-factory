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

---

## 8. Bounded Iteration

**What:** Maximum 3 iterations before human intervention.
**Why:** Prevents infinite loops where the agent keeps trying the same approach. If 3 attempts don't converge, the problem is likely in the spec or architecture, not the implementation.
**How:** The Oracle tracks iteration count and escalates at iteration 3.

---

## 9. Feedback Without Leakage

**What:** When implementation fails blind tests, the implementer receives error messages and test results, but never the test source code.
**Why:** Maintains the information barrier across iterations. If the implementer saw the test code, they'd optimize for those specific tests.
**How:** The Oracle's feedback format includes test names and error messages, but never test implementation details.

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
