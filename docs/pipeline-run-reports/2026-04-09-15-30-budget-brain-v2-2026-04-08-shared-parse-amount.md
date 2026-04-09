# Pipeline Run Report — 2026-04-09 15:30 UTC

**Repo:** budget-brain-v2
**Spec:** 2026-04-08-shared-parse-amount
**Framework commit:** 790221f (ship-record automation)
**Run ID:** f0619ab149894b8a

Trigger: validate pipeline on a simple standalone spec after Run 12 SHIP.
Second spec to run against budget-brain-v2.

## Run 13 — SHIPPED (iter 1, clean)

**Outcome:** Clean SHIP on first iteration with 100% pass rate. 32/32
blind scenarios passed. Oracle correctly overruled a reviewer false
critical finding. Total pipeline wall time ~23 minutes.

### Timeline
| Phase | Status |
|---|---|
| spec-validator | PASS — 99% score, LOW divergence, 0 blockers |
| architect | PASS — single file_map entry + barrel export modify |
| qa-holdout | PASS — 6 holdout test files generated |
| test-engineer | PASS — 4 scenario files, 32 total scenarios |
| smoke | PASS — all scenarios fail cleanly pre-implementation |
| iter 1 | pre-gate PASS, smoke PASS, gate 100%, review complete, Oracle **SHIP** |

### Framework validations

**Ship-record automation — VALIDATED.**
`shipped-scenarios/2026-04-08-shared-parse-amount/` contains:
- 4 scenario files + `.registry-meta` (copied by pipeline)
- `review.md` (copied by ship-record automation, 790221f)
- No `defect-registry.md` (correct — 100% pass rate, no defects)

Ship ledger updated with both api-bootstrap and shared-parse-amount entries.

**Environment synthesizer — VALIDATED.**
After wiping `environment.toml` and `environment.deltas/`, the pipeline:
1. Synthesized bootstrap delta from config.toml (warning emitted)
2. Architect generated new environment.toml with shared-parse-amount manifest
3. Environment contract includes both api-bootstrap (committed) and
   shared-parse-amount manifests

### Findings

**Finding 1 (P2 bug): atexit handler overwrites SHIP state with interrupted.**
After the pipeline writes `status=shipped` to the state file (line 2498
in orchestrator.py), the process exits normally (return 0). This triggers
the `atexit` handler registered by `CleanupManager.register()` (cleanup.py
line 50), which calls `_cleanup_all()` → `on_interrupt()` →
`_write_interrupted_state()`, unconditionally overwriting the state file's
status from `shipped` to `interrupted`.

The state file for this run reads `status=interrupted` despite a
successful SHIP. The pipeline output (`SHIPPED: 2026-04-08-shared-parse-amount`)
and the ship ledger (`shipped.jsonl`) are correct — only the state file
is wrong.

Root cause: `_cleanup_all` doesn't check whether the pipeline has
already reached a terminal state (shipped/escalated) before calling
`on_interrupt()`. The atexit handler fires on ALL process exits,
including normal completion.

Fix candidates:
- Check `spec_run.status` inside the `on_interrupt` lambda — skip write
  if already SHIPPED/ESCALATED/DEPLOYED
- Set `cleanup.on_interrupt = None` after SHIP write
- Add a `completed` flag to `CleanupManager` that gates the interrupt write

Impact: low. The state file is only read on `--resume`, and a shipped
spec wouldn't be resumed. But it's misleading for debugging.

**Finding 2 (non-blocking): test-engineer barrier violation warnings.**
```
WARNING: barrier violations detected in test-engineer output: ['packages/shared/src']
```
Two warnings emitted. The test-engineer's output contained references to
source paths (likely import paths or file references in scenario code).
This is a defense-in-depth audit warning — the primary barrier (Docker
tmpfs masking `SOURCE_DIRS` in the scenario architect container) is intact.
The scenario architect cannot read the source files.

This is a known pattern: scenario test files naturally reference import
paths (e.g., `import { parseAmount } from '../../src/parse-amount'`) which
triggers the path-string audit. The audit correctly flags this for
review, but it's expected behavior, not a barrier breach.

**Finding 3 (positive): Oracle correctly overruled false critical.**
The reviewer flagged a "critical rounding logic violation" claiming
`Math.round()` contradicts the spec's no-rounding requirement. The
Oracle correctly identified this as a misunderstanding: `Math.round()`
only applies to inputs that have already passed the ≤2 decimal places
check — it handles floating-point precision in cent conversion, not
rounding of user-visible decimals. Inputs with >2 decimals are rejected
as `TOO_MANY_DECIMALS` before reaching the arithmetic.

**Finding 4 (positive): clean first-try SHIP validates pipeline stability.**
This is the first clean SHIP (no ship-with-gaps). Combined with Run 12's
ship-with-gaps SHIP, the pipeline has now validated both paths:
- 100% pass → clean SHIP on iter 1
- 97% plateau → ship-with-gaps on iter 2

### Status summary

| Concern | Status |
|---|---|
| Ship-record automation (790221f) | VALIDATED — review.md copied, no defect-registry (correct) |
| Environment synthesizer | VALIDATED — bootstrap delta + fresh environment.toml |
| All prior fixes (4.6b.1d-1h) | no regressions observed |
| New: atexit overwrites shipped state | P2 bug, document only |
| New: test-engineer barrier audit warnings | known pattern, non-blocking |
| 4.6b.1e deferred control-flow fix | still pending |
