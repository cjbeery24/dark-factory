# Plan: Add Test Engineer Agent (DEFERRED — Try Prompt Fix First)

**Priority:** DEFERRED (revisit after 10 pipeline runs with measurement data)
**Status:** Replaced by zero-cost prompt enhancement; build agent only if measured bottleneck
**Reviewed by:** Liotta, Neo

## Problem Statement

The original Dark Factory architecture specifies two parallel test generation phases:
1. **Test Engineer** — visible tests from spec (Implementer CAN see)
2. **QA-Holdout / Scenario Architect** — blind holdout tests (Implementer CANNOT see)

Our implementation only has the Scenario Architect. The Implementer writes their own self-tests.

## Why This Is Deferred

**Review consensus (Liotta + Neo):** The problem is theoretical. We have no data showing that weak self-tests are the bottleneck. Before adding an entire agent (Docker container, barrier config, resume/state complexity, API cost), try the zero-cost fix and measure.

Key concerns from review:
- **No measurement of the problem.** What percentage of NO-SHIP decisions are caused by weak Implementer self-tests vs. actual implementation bugs vs. spec ambiguity?
- **Cost.** Each agent invocation costs API dollars. If the Test Engineer produces tests that overlap 60% with what the Implementer writes anyway, you're paying double for 40% marginal coverage.
- **Resume/state complexity.** The orchestrator uses integer step numbers for resume. Adding step 2b requires either a non-integer scheme or making the Test Engineer idempotent on re-run. This is a real gap.
- **Test runner discovery.** Assumes the project's test runner auto-discovers `spec-tests/` — will silently fail in monorepo setups with explicit globs.

## Phase 1: Zero-Cost Prompt Enhancement (DO THIS NOW)

**File:** `agents/implementer.md`

Add one paragraph to Phase 4 (Self-Testing) of the Implementer's protocol:

```markdown
### Phase 4: Self-Testing

**Spec-derived test requirement:** Your self-tests MUST be derived from the spec's
Functional Requirements and Error Conditions, not from your implementation. For each
FR-N and EC-N in the spec, write at least one test that would pass for ANY correct
implementation, not just yours. If you find yourself testing implementation details
(internal function behavior, private state, specific data structures), you are testing
the wrong thing. Test the contract described in the spec.

Minimum test coverage per spec section:
- Each FR-N: at least one happy-path test using the spec's own examples
- Each EC-N: at least one test for the documented edge case
- Each ERR-N: at least one test for the documented error condition

After writing tests, mentally verify: "Would these tests catch a bug in a completely
different implementation that also follows this spec?" If not, rewrite them.
```

**Estimated effort:** 15 minutes. Zero new files, zero Docker containers, zero API cost.

## Phase 2: Measure After 10 Pipeline Runs

After running 10 real specs through the pipeline (deferred Priority 1 from the gap analysis), collect:

1. **Self-test coverage** — How many FR-N/EC-N sections have corresponding self-tests?
2. **Self-test vs. blind scenario overlap** — What percentage of blind scenario failures are also caught by self-tests?
3. **Root cause distribution** — Of NO-SHIP decisions, how many are caused by:
   - Weak/missing self-tests?
   - Implementation bugs the self-tests should have caught?
   - Spec ambiguity?
   - Scenario issues?

**Decision gate:** Build the Test Engineer agent ONLY if:
- Self-test coverage is consistently below 80% of FR-N/EC-N sections
- AND >30% of blind scenario failures would have been caught by better visible tests
- AND the prompt enhancement from Phase 1 did not close the gap

## Phase 3: Full Agent Implementation (IF NEEDED)

If Phase 2 data shows the prompt fix is insufficient, the full implementation plan is preserved here for reference:

### Summary of what would be built:
1. `AgentRole.TEST_ENGINEER` enum member
2. Barrier config: source dirs masked (same as Scenario Architect), write access to test dir
3. Agent definition at `agents/test-engineer.md` — constructive (not adversarial) tests
4. `configure_barriers()` signature updated to accept `test_dir` parameter
5. Pipeline integration: Step 2b in orchestrator, after scenario generation
6. Implementer prompt updated to reference visible tests
7. Config fields: `test_engineer_enabled`, `test_engineer_test_subdir`
8. Resume/state: resolve integer step numbering (either fractional scheme or idempotent re-run)
9. Quality gate: verify spec-test files are discovered by test runner
10. Tests and documentation updates

### Key design decisions (preserved from review):
- Test Engineer runs ONCE before iteration loop (not per-iteration)
- Output goes to `{test_dir}/spec-tests/{spec-name}/` (separate from Implementer self-tests)
- Coverage scope: contract, happy path, error conditions (NOT adversarial — that's the SA's job)
- Sequential execution first; parallel with SA as fast-follow optimization
- Must resolve `completed_step` scheme before implementing (Neo review)
- Must verify test runner discovers spec-tests dir for all project types (Neo review)
