# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware (Attempt 3)

**Date:** 2026-03-31
**Spec:** `2026-03-30-auth-schemas-middleware.md`
**Target repo:** budget-brain-v2
**Pipeline version:** Current main
**Previous runs:** 2 sessions (2026-03-30: ESCALATED 61%, 2026-03-31 01:40: 4 runs — 2 INTERRUPTED at SA, 1 ESCALATED at 88%, 1 ESCALATED at 79%)

---

## Context

Third session attempting the auth-schemas-middleware spec (F-2a). Previous sessions showed:
- SA validation is the #1 blocker (50% of runs interrupted before implementation)
- SA oscillates between `no_source_reads` and `assertion_upper_bound` violations
- Best pass rate achieved: 88% (Run 2 of session 2), but with all build/lint/TC failing
- Best build quality: Run 3 of session 2 (all PASS, 79% scenarios)
- Pattern-match brittleness in Format D scenarios is the dominant implementation failure mode

Goal: observe pipeline behavior and track improvement/regression.

---

## Run 1

**Start time:** 2026-03-31 ~05:08 UTC
**Outcome:** PENDING
**Pipeline stage reached:** PENDING

### Pre-run state
- budget-brain-v2 repo: reset (tracked files restored, untracked cleaned, .dark-factory artifacts cleared)
- Docker images: rebuilt
- Agents/templates: synced from dark-factory

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 05:08 | Pre-run setup begins |

