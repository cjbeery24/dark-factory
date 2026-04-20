# Pipeline Run: 2026-03-28-database-foundation (budget-brain-v2)

- **Date:** 2026-04-20 13:07:05 → 14:09:06 UTC-local
- **Target project:** `../budget-brain-v2`
- **run_id:** `d8268a4a75ed4848`
- **Outcome:** **SHIPPED** (via `SHIP_WITH_GAPS`; Oracle original decision was `NO-SHIP`)
- **Iterations:** 2 of 3
- **Wall time:** ~62 min (3717 s from first event to `SPEC_SHIPPED`)
- **Cost:** 600 turns, ~$126.00 (USD estimate from `COST_SUMMARY`)
- **Satisfaction / pass rate:** 89 % both iterations (35 / 39 scenario pass, 4 stable failures)

## State file (post-run)

```
spec=2026-03-28-database-foundation
run_id=d8268a4a75ed4848
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
last_decision=NO-SHIP
iteration=2
server_error=
smoke_result=passed
pass_rate=89
gate_exit_code=1
phase=oracle_complete
status=shipped
completed_step=99
```

`SHIP_WITH_GAPS` event body (why an Oracle NO-SHIP still shipped):

```json
{
  "stable_failures": 4,
  "stable_failure_ratio": 1.0,
  "pass_rate": 89,
  "oracle_original": "NO-SHIP"
}
```

All 4 remaining blind-scenario failures persisted unchanged across iter 1 → iter 2 → stable-failure ratio 1.0 → ship-with-gaps triggered.

## Phase timing (events.jsonl)

| Start (UTC-local) | Agent / phase | Duration |
|---|---|---|
| 20:07:10 | `spec-validator` | 4 m 09 s |
| 20:11:24 | `qa-holdout` (generate holdouts) | 6 m 03 s |
| 20:17:29 | `test-engineer` (generate visible scenarios) | 4 m 07 s |
| 20:21:37 | `test-engineer` (validate pass 1) | 6 m 14 s |
| 20:27:55 | `test-engineer` (validate pass 2) | 5 m 46 s |
| 20:33:49 | `test-engineer` (coverage regen, after `COVERAGE_GAP`) | 3 m 03 s |
| 20:36:53 | `implementer` iter 1 attempt 1 | 8 m 12 s |
| 20:45:05 | `implementer` iter 1 attempt 2 (self-correct) | 2 m 27 s |
| 20:48:03 | smoke (advisory) — PASS | — |
| 20:48:16–20:48:58 | quality-gate iter 1 → FAIL 89 % | 42 s |
| 20:48:59 | `defect-classifier` iter 1 | 2 m 27 s |
| 20:51:27 | `reviewer` iter 1 | 3 m 07 s |
| 20:54:34 | `oracle` iter 1 (→ NO-SHIP, feedback for iter 2) | 1 m 35 s |
| 20:56:09 | `implementer` iter 2 attempt 1 | 6 m 13 s |
| 21:02:36 | smoke (fatal) — PASS | — |
| 21:02:38–21:02:44 | quality-gate iter 2 → FAIL 89 %, `TRAJECTORY_CLASSIFIED=PLATEAU` | 6 s |
| 21:02:44 | `defect-classifier` iter 2 | 1 m 33 s |
| 21:04:17 | `reviewer` iter 2 | ~3 m |
| 21:07:20 | `oracle` iter 2 (→ NO-SHIP, promoted to SHIP_WITH_GAPS) | 1 m 46 s |
| 21:09:06 | `SPEC_SHIPPED` | — |

## Quality gate summary (iter 2, the shipping iteration)

```
# Quality Gate Summary: 2026-03-28-database-foundation
Date: 2026-04-20T21:02:38Z

| Gate | Status |
|------|--------|
| Install | PASS |
| Setup | PASS |
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Self-Tests | PASS |
| Blind Scenarios (executable) | FAIL |
| Mutation Testing | SKIP |
```

Iter 1 summary is identical (both result FAIL at 89 %).

## Blind-scenario failures (stable across both iters)

From the gate report detail section:

- `[AC-7]` strict RLS policy allows unauthorized rows — received 1 row instead of 0
- `[AC-8]` nullable RLS policy returns matching tenant rows AND null-tenant rows — received 3 instead of 2
- `[AC-12]` unset `app.tenant_id` GUC returns zero rows (fail-closed) — received 2 instead of 0
- (one additional failure — total 4 failed of 39)

All four persisted unchanged between iter 1 and iter 2 (same test names, same assertion deltas), which is why the stable-failure detector promoted NO-SHIP → SHIP_WITH_GAPS.

## Substrate verification

`.dark-factory/reports/2026-03-28-database-foundation-substrate-findings.json`:

```json
{"status": "skip", "findings": []}
```

Substrate phase skipped — see blocker note below (python3 missing in gate container); it is unclear whether this is an orchestrator-side legitimate skip or a silently failed render. No substrate row appears in the gate summary either way.

## Notable events

- **`BARRIER_VIOLATION` (test-engineer → `apps/api/src`)** at 20:36:52. Behavior-correct: the masked tmpfs made the path unreachable, the audit-side path-string match caught the reference in the agent's output. One event, no containment failure.
- **`SMOKE_COMPLETED`**: both iters `success=true`; iter 1 `policy=advisory`, iter 2 `policy=fatal` — policy promotion works as designed.
- **`TRAJECTORY_CLASSIFIED` iter 2**: `PLATEAU` / "Fundamentally different approach needed — same strategy is not working." Consistent with 89 % → 89 % no movement.
- **`COVERAGE_GAP` + `COVERAGE_REGEN` + `COVERAGE_PASS`**: test-engineer regen cycle fired once (20:33:45–20:36:53) and resolved — no gap escaped into the implementer phase.

## Audit-log spot-check (barrier mask argv)

`qa-holdout` docker argv (20:11:24) — masks all present and correct:

```
--tmpfs /project/apps/api/src:ro,size=0
--tmpfs /project/.dark-factory/scenarios:ro,size=0
--tmpfs /project/.git:ro,size=0
-v .../.dark-factory/holdouts:/project/.dark-factory/holdouts:rw
-v .../.dark-factory/reports:/project/.dark-factory/reports:rw
```

`test-engineer` docker argv (20:17:29 and later phases) — masks all present and correct:

```
--tmpfs /project/apps/api/src:ro,size=0
--tmpfs /project/.dark-factory/holdouts:ro,size=0
--tmpfs /project/.git:ro,size=0
-v .../.dark-factory/scenarios/.../_contracts:...:ro
```

Source dirs, opposite-visible-test-dir, and `.git/` are all masked on both analysis agents.

---

# Change Validation

Four changes were in scope for this run. Two fired and behaved correctly; one correctly did not fire (by design); one did not fire due to a deployment blocker; one did not fire because it lives in a user-invoked skill that the automated pipeline never calls.

## 1. Import Contract gate phase — **DID NOT FIRE (deployment blocker)**

Expected: `scripts/lib/render_import_contract_phase.py` invoked from `scripts/quality-gate.sh` between Substrate phase and Self-Tests, producing a PASS/FAIL/SKIP row in the gate summary.

**Observed:** No `Import Contract` row in either iter's gate summary or gate report. Every grep for the phase name across `.dark-factory/reports/`, `.dark-factory/audit/`, events.jsonl, and gate-telemetry returned empty.

**Root cause** — the `dark-factory-node` gate image has no `python3`:

```
$ docker run --rm --entrypoint /bin/sh dark-factory-node -c "command -v python3 && python3 --version"
# exit 127 (command not found)
```

`docker/Dockerfile.base` installs only `bash`, `coreutils`, `git`, and `pnpm`:

```
RUN apk add --no-cache \
    bash=${BASH_VERSION} \
    coreutils=${COREUTILS_VERSION} \
    git=${GIT_VERSION} \
 && npm install -g pnpm@${PNPM_VERSION} --no-fund --no-update-notifier
```

But `scripts/quality-gate.sh:1102` invokes:

```
python3 /opt/dark-factory/scripts/lib/render_import_contract_phase.py \
    "$REPORT_FILE" "$SUMMARY_FILE" "$SPEC_NAME" "$TOOLCHAIN" \
    || GATE_FAILED=true
```

With no `python3`, the shell raises `command not found`, `GATE_FAILED=true` is set (already `true` from blind-scenarios anyway), and nothing is appended to `$REPORT_FILE` / `$SUMMARY_FILE`. The same failure hits `render_substrate_phase.py` one block earlier — which is why the substrate row is also missing from the gate summary even though `substrate-findings.json` was written.

**Deployment blocker:** add `python3` (stdlib-only per the phase renderer's own header) to `docker/Dockerfile.base`'s `apk add` list, rebuild `dark-factory-base` + `dark-factory-node`, and rerun. Until that ships, both the Import Contract gate and the Substrate rendering step are silent no-ops in node runs.

## 2. Manifest contract context in Oracle feedback — **DID NOT FIRE (out of scope for `dark-factory run`)**

Expected: priority-ordered fix items in the iter-N feedback file include a "Contract reminder" bullet with `specifier`, named exports, and `module_system` when the cited spec section maps to a `test_integration.entry_points` entry.

**Observed:** Neither `feedback-iter1.md` (mtime 13:56:00) nor `feedback-iter2.md` (mtime 14:08:55) contains "Contract reminder", `specifier`, named exports, or `module_system` — despite the architect manifest having an `entry_points` block that covers FR-1, FR-2, FR-3, FR-4:

```yaml
test_integration:
  module_system: esm
  import_extension: .js
  entry_points:
  - path: apps/api/src/db/index.ts
    specifier: ../../../apps/api/src/db/index.js
    exports: [db, connection, withTenantDb, DrizzleTx]
  - path: apps/api/src/db/tenant-context.ts
    specifier: ../../../apps/api/src/db/tenant-context.js
    exports: [tenantContext]
```

iter-1 feedback's fix list cites FR-2, FR-3, EC-4 — at least FR-2 and FR-3 should have matched entry points, but no injection occurred. Same for iter 2.

**Root cause:** Change 2 modifies `/df-write-feedback` (`~/.claude/skills/df-write-feedback/SKILL.md`, Step 3.5 "Inject Manifest Contract Context"), which is a **user-invoked skill** that runs in a separate Claude Code session and writes a feedback file. `dark-factory run` does not invoke `/df-write-feedback`; its feedback is produced by the `oracle` agent running inside a Docker container from `agents/oracle.md`. A `grep -E "specifier|named export|module_system|entry_point|test_integration|manifest contract" agents/oracle.md` returns zero matches — the Oracle's prompt has no corresponding injection logic.

**Implication:** the skill-level change is live and available to anyone invoking `/df-write-feedback` manually, but the automated pipeline cannot exercise it. If the intent is for every pipeline iteration to carry manifest-contract context into the implementer's feedback, the change also has to be ported into `agents/oracle.md` (or into the Python-side feedback assembler that calls the Oracle).

## 3. MEDIUM precision auto-hint — **CORRECTLY SKIPPED (divergence = LOW)**

Expected: `darkfactory/architecture/precision_hint.py` writes `.dark-factory/architecture/<spec>-precision-hint.md` when spec validation reports `divergence=MEDIUM`; stale hints are cleared on `LOW`.

**Observed:** The spec validation status file says:

```
$ cat .dark-factory/reviews/2026-03-28-database-foundation-validation.status
verdict=PASS
score=95
divergence=LOW
blockers=0
```

Therefore the hint should not be generated. Confirmed:

```
$ ls .dark-factory/architecture/*precision*
(no match — zsh: no matches found)
```

Architect prompt ran without any precision-hint attachment. **Behavior is correct**: change 3 only triggers on MEDIUM, and on LOW the hint path is empty. This run does not exercise the positive path (hint-write on MEDIUM) and cannot confirm the staleness-clear branch directly, but it does confirm the negative path (LOW produces no file).

To exercise the positive path, we'd need a spec whose validator scores it as `divergence=MEDIUM`.

## 4. Per-run artifact archive — **FIRED AND BEHAVED CORRECTLY**

Expected: `darkfactory/pipeline/archive.py` snapshots each iteration's `reviews/`, `reports/`, `audit/`, `architecture/`, `state/` into `.dark-factory/archive/<YYYYMMDD-HHMMSS>-<spec>-<short-run-id>/iter-N/`, and the ship handler writes a `final/meta.json` with terminal status. Observational only.

**Archive root matches run_id:**

```
$ ls ../budget-brain-v2/.dark-factory/archive/
20260420-130705-2026-03-28-database-foundation-d8268a4a
```

Exactly one new run directory, with short run-id `d8268a4a` matching the state file's `run_id=d8268a4a75ed4848` (first 8 chars).

**run.json:**

```json
{
  "spec": "2026-03-28-database-foundation",
  "run_id": "d8268a4a75ed4848",
  "started_at": "20260420-130705"
}
```

**Structure:**

```
archive/20260420-130705-2026-03-28-database-foundation-d8268a4a/
├── run.json
├── iter-1/   (architecture/ audit/ precheck/ reports/ reviews/ state/)
├── iter-2/   (architecture/ audit/ precheck/ reports/ reviews/ state/)
└── final/    (events.jsonl metrics.jsonl meta.json)
```

Two `iter-N/` directories — matches `state.iteration=2`.

**final/meta.json:**

```json
{
  "spec": "2026-03-28-database-foundation",
  "run_id": "d8268a4a75ed4848",
  "status": "shipped",
  "final_iteration": 2,
  "completed_at": "2026-04-20 14:09:06"
}
```

`status=shipped` — correct terminal status routed through the SHIP_WITH_GAPS handler.

**Spec-scoped filtering check** — iter-1/reviews/:

```
$ find archive/.../iter-1/ -type f ! -name '2026-03-28-database-foundation*' \
    ! -name 'run-telemetry*' ! -name 'gate-telemetry*'
(empty)
```

No files from other specs (e.g. `2026-03-28-api-bootstrap-*`, `2026-03-28-monorepo-tooling-*`, `2026-04-08-shared-parse-amount-*` that live in the live `.dark-factory/reviews/` directory) leaked into the archive. Spec-scoped filtering works.

**Archive warnings in pipeline log:**

```
$ grep -i "archive:" /tmp/df-run-2026-03-28-database-foundation.log
(none)
```

No best-effort failures logged.

---

# Findings summary

- ✅ **Change 4 (per-run archive)** is working end-to-end — path, structure, filtering, terminal-status meta all correct.
- ⚠️ **Change 3 (MEDIUM precision hint)** is in the negative branch (LOW) and correctly silent. Positive path not exercised this run.
- 🔴 **Change 1 (Import Contract gate)** is **blocked by a missing runtime dep (`python3`) in the `dark-factory-node` base image.** Same issue silently disables the substrate-phase renderer. Fix `docker/Dockerfile.base` to `apk add python3` and rebuild before the Import Contract gate has any chance of firing in node-language runs.
- ⚪ **Change 2 (manifest contract in Oracle feedback)** is scoped to the `/df-write-feedback` skill only — the automated pipeline's Oracle agent never invokes that Step 3.5 logic, so this change cannot be validated via `dark-factory run`. To land this inside the pipeline, port the Step 3.5 injection logic into `agents/oracle.md` (or the Python feedback assembler).

# Incidental issues observed (not part of the four changes)

- The `df-pipeline-run` skill's Monitor script at Step 5 extracts `role` / `iteration` / etc. from the event top level, but events.jsonl nests them under `ev["data"]`. The monitor output therefore shows only `AGENT_START` / `AGENT_COMPLETED` with no role — which primed an early misread of the phase sequence during this run (I thought the Implementer was running ~14 min in when it was actually still `test-engineer`). Low-effort fix: `d = ev.get('data', {}); role = ev.get('role') or d.get('role')`.
- Step 6 of the skill describes phases using stale role names ("architect", "scenario-architect") — the current pipeline uses `spec-validator`, `qa-holdout`, `test-engineer`, `implementer`, `defect-classifier`, `reviewer`, `oracle`.
- The previous-run files under `.dark-factory/reviews/` (`-feedback-iter2.md`, `-oracle-output-iter2.md`, `-oracle-output-iter3.md`, `-escalation.md` dated 11:24–11:38) were **not** wiped by Step 1 of the skill — the skill only wipes `scenarios/ reports/ audit/ state/ failures/ events.jsonl`, which leaves `reviews/` with zombie files from prior runs. Current run's iter-2 feedback overwrote `feedback-iter2.md`, but `oracle-output-iter3.md` and `escalation.md` from the aborted prior run still live in the archive's iter-N/reviews/ snapshots. Consider either wiping spec-scoped review files alongside the other runtime artifacts at Step 1b, or scoping the archive copy by mtime.
