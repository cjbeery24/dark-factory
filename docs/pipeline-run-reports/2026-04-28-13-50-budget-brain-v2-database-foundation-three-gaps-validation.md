# 2026-04-28 13:50 — budget-brain-v2 database-foundation (three-gaps discriminating experiment)

## Outcome

**ESCALATED** after 3 iterations. Wall clock ~83 min (under 90-min cap).

This was the discriminating experiment for the three framework gaps closed
in commits `5d7fb29` (Gap 3), `1892d42` (Gap 1), `e2b77a6` (Gap 2). The
spec did not ship. **The framework changes themselves produced weaker
evidence than I initially framed** — Neo's belated review caught me
overclaiming. This rewrite reflects the honest read.

## Iteration trajectory

| Iter | Pass rate | Gate | Substrate | Trajectory | Terminal path |
|-----:|----------:|------|-----------|------------|---------------|
| 1 | 80% | FAIL | FAIL (2 findings) | — | classifier → reviewer → oracle (NO-SHIP) |
| 2 | 68% | FAIL | FAIL (same 2) | — | `GATE_REGRESSION` escape-hatch (skipped classifier+oracle) |
| 3 | 68% | FAIL | FAIL (same 2) | DEGRADING | `TRAJECTORY_PLATEAU` escape-hatch → escalation agent |

Substrate findings were identical across all three iterations:
- `substrate-guc-unregistered`: GUC `app.tenant_id` not registered after setup
- `substrate-bootstrap-not-applied`: `__drizzle_migrations` tracking table missing

**The implementer received iter-1's cascade-aggregated Oracle fix-list
three times** (inlined verbatim into iter-2's escape-hatch directive, then
nested into iter-3's via iter-2 → iter-3 chaining), plus a `git reset` to
iter-1's source tree at the iter-2 boundary, and **still did not address
either upstream cause**. Gap 2's signal landed; the implementer didn't act.

## State file (terminal)

```
spec=2026-03-28-database-foundation
run_id=6a3a69e80b584bd9
spec_hash=32d0cd09257f72bc5252ad2e19fcf380929a352af926c20d30610e6746cb8a88
last_decision=NO-SHIP
iteration=3
smoke_result=passed
phase=review
pass_rate=68
gate_exit_code=1
status=escalated
```

## Three-gap evaluation (the point of this run)

### Gap 1 (substrate-prep schema-level non-claim) — ⚠️ NOT EXERCISED

**Initial framing (wrong)**: "CLEAN WIN — the schema-level non-claim
survived contact with the live LLM."

**Neo's correction**: the substrate-owner agent's iter-1 emission
showed `commands_emitted: []` because the project already had every
prerequisite in place (drizzle journal present, drizzle config
present, migration directories present, package declared). The
agent's `considered` list at the substrate-prep JSON shows 4
preconditions all decided `skip` for evidence-based reasons. With
nothing to emit, `claim: "none"` was the *only* legal output. The
schema-level non-claim was **not** tested against an emission that
required commands or files_seeded.

The honest conclusion: this run shows the v2 schema is shape-correct
on a no-op input. It does NOT discriminate against the v1
verification-claim failure mode (`no_op: true` while substrate
verification was about to FAIL) — because there were no commands
to elide and no claim to mistakenly assert in either direction.

**To actually exercise Gap 1**: re-run on a spec where substrate-prep
MUST emit ≥1 command (e.g. a clean checkout where the journal does
NOT pre-exist). The schema enforcement matters when the agent has
something it COULD claim about. We didn't get that here.

### Gap 2 (upstream_cause schema + cascade detection) — ⚠️ N=1, SIGNAL CORRECT, IMPLEMENTER NON-RESPONSIVE

**Initial framing (wrong)**: "CLEAN WIN — 18 → 2 cascade entries,
exactly the design contract."

**Neo's correction**: Gap 2 was tested **exactly once** in this run.
iter-2 and iter-3 both fired escape-hatch (`GATE_REGRESSION` and
`TRAJECTORY_PLATEAU` respectively) and bypassed classifier + oracle
entirely. The archive directories for iter-2 and iter-3 contain NO
`defects-iter*.json` files (confirmed via `ls reports/`). One run of
one iteration is not "CLEAN WIN" — it's n=1.

**What the n=1 sample showed (still meaningful, but qualified)**:

| Metric | 5b iter-2 baseline (pre-Gap 2) | iter-1 of this run (post-Gap 2) |
|--------|-------------------------------|----------------------------------|
| Total defects | 18 | 18 |
| SCENARIO_CRASH | 0 | 8 |
| ENV_ISSUE | 0 | 7 |
| SPEC_AMBIGUOUS | **18** | 3 |
| With structured `upstream_cause` | 0 | **18** |
| Distinct upstream primitives | (in prose only) | 2 |

iter-1 Oracle fix-list (verbatim from feedback-iter1.md):

> 1. **Fix upstream substrate/substrate-bootstrap-not-applied** — Cascades to 11 tests. ...
> 2. **Fix upstream substrate/substrate-guc-unregistered** — Cascades to 7 tests. ...
> 3. **Security** — Remove all Object.assign calls ...
> 4. **FR-2** — Replace regex UUID validation ...

The signal was structurally correct on this single sample. The Oracle's
phrasing matches the `agents/oracle.md` "Upstream Cascades (Gap 2
contract)" section verbatim.

**The actionability gap (separate from Gap 2's mandate, but worth
recording)**: iter-2's escape-hatch directive INLINED iter-1's full
fix-list verbatim plus rolled the source tree back to iter-1's
state — the implementer received the cascade signal and a clean
slate together. iter-3's directive nested iter-2's (which contained
iter-1's). Across two more iterations the implementer did not act
on either upstream-cascade fix-list entry. The signal landed three
times. Whether that's a sonnet-4-under-temp=0 actionability ceiling,
a spec-comprehension gap, or a directive-format issue is undetermined
from a single run — but it's NOT a Gap 2 failure. Gap 2's mandate
was producing the right signal; it did. What happens downstream of
the signal is out of scope for this gap.

**Notable but not a Gap 2 win**: at iter-1 the classifier's first
attempt marked all 18 cascading defects as SCENARIO_CRASH and was
rejected by the pre-existing `crash_split_mismatch` cross-validation
(the per-test results JSON has separate counts for crashed-vs-asserted
failures; classifier's count must match). The retry produced the
8/7/3 split shown above. This is a pre-existing safety net catching
the new agent's over-eager interpretation of Gap 2's "prefer
SCENARIO_CRASH" directive — a positive interaction, but credit goes
to the pre-existing rule, not Gap 2.

**To actually exercise Gap 2 across multiple iterations**: run a spec
where iter-2 produces NEW failures with NEW upstream causes, so the
classifier is challenged to re-detect cascades on changed evidence,
not to re-emit iter-1's stable 18.

### Gap 3 (per-iteration diff capture) — ❌ NET-NEGATIVE

**Initial framing (wrong)**: "PARTIAL WIN with two failure modes."

**Neo's correction**: 1 of 3 iterations partially captured, and the
captured one is *misleading* about the implementer's work. iter-2
and iter-3 patches are missing entirely. **A wrong artifact is
worse than a missing one** for a post-mortem reader who doesn't
know to discount it.

| Iteration | Patch present? | Patch contents | Implementer actually wrote |
|-----------|---------------|----------------|----------------------------|
| iter-1 | ✅ Yes (13 lines) | `apps/api/src/common/config.ts` mods only — adds `DATABASE_URL` and `DB_POOL_MAX` env vars | Whole `apps/api/src/db/` tree (8 files), `drizzle.config.ts`, `scripts/df-setup.sh` (all UNTRACKED new files) |
| iter-2 | ❌ MISSING | — | Implementer made changes (caused `GATE_REGRESSION`); no patch captured |
| iter-3 | ❌ MISSING | — | Implementer made changes; no patch captured |

A post-mortem reader of the iter-1 patch would conclude "the
implementer added 2 env vars" — entirely wrong. The actual work was
authoring an entire DB layer plus migration baseline plus tenant
context. The post-`5d7fb29` archive is **net negative** for diagnosis
of THIS class of run (foundation specs), and missing-vs-present
samples mean the archive's coverage on this run is effectively 0/3.

**Two failure modes** (one Neo-flagged mid-run, one I caught
post-mortem):

1. **Untracked-file skew.** `git stash create -u` on git 2.50.1
   silently drops untracked files. `_capture_iteration_end_sha`
   documented this as a known skew and accepted it because its
   first consumer (B1 idle-iteration check at iteration.py:600-657)
   tolerates under-reporting. Gap 3 is a NEW consumer with the
   OPPOSITE tolerance — under-reporting writes a misleading
   artifact. The producer's contract was negotiated against B1's
   tolerance; reusing the SHA pair without renegotiation is the
   inverted persisted-but-not-consumed shape.

2. **Escape-hatch bypass.** `_capture_iteration_end_sha` runs inside
   `_step_oracle` (iteration.py:4407). When a run fires
   `GATE_REGRESSION` or `TRAJECTORY_PLATEAU` escape-hatch after the
   gate, `_step_oracle` is skipped. No end-SHA is captured.
   `diff_sha_pair_for_completed` returns `("","")` and
   `snapshot_iteration_diff_for_completed` silently skips. iter-2
   and iter-3 archive directories contain reviews/, reports/,
   audit/, etc. but NO `implementer-changes.patch`.

**Surgical fix for both** (per Neo's design + my extension):
- Move `_capture_iteration_end_sha` invocation to `_step_quality_gate`
  (post-gate, pre-escape-hatch) so the SHA captures regardless of
  terminal path.
- In `capture_iteration_diff`, after `git diff` succeeds, run
  `git ls-files --others --exclude-standard -- <pathspecs>` and
  append synthetic `diff --git /dev/null b/<path>` blocks for each
  untracked file. Bound per-file size before truncating.
- Tests: untracked-only iter, mixed iter, escape-hatch iter, binary
  untracked file.

**Until that ships, the Gap 3 archive is actively misleading and
should be treated as un-trustworthy by any post-mortem reader.**

## Quality gate summary (final iteration)

```
| Gate                          | Status |
|-------------------------------|--------|
| Install                       | PASS   |
| Setup                         | PASS   |
| Build                         | PASS   |
| Lint                          | PASS   |
| Type Check                    | PASS   |
| Substrate Verification        | FAIL   |
| Import Contract               | PASS   |
| Self-Tests                    | PASS   |
| Scenario Lint                 | FAIL   |
| Blind Scenarios (executable)  | FAIL   |
| Mutation Testing              | SKIP   |
```

Substrate findings (unchanged across all 3 iterations):

```json
{
    "status": "fail",
    "findings": [
        {
            "code": "substrate-guc-unregistered",
            "message": "GUC 'app.tenant_id' is not registered after setup; check `SELECT current_setting('app.tenant_id', true)` returned NULL"
        },
        {
            "code": "substrate-bootstrap-not-applied",
            "message": "bootstrap_check 'drizzle-migrations-tracking-table' did not observe expected post-condition: SQL `SELECT 1 FROM information_schema.tables WHERE table_name='__drizzle_migrations'` returned no rows. Setup may have exited 0 without applying anything (e.g. drizzle-kit migrate against a comment-only baseline never creates the tracking table)."
        }
    ]
}
```

The `[[bootstrap_check]]` substrate primitive (commit `24a3dd9`) is
working — observed the post-condition, reported FAIL when the table
wasn't created, AND included the workaround mechanism in the message
("Setup may have exited 0 without applying anything (e.g.
drizzle-kit migrate against a comment-only baseline never creates
the tracking table)"). **The finding's message contains a
near-instruction for the fix.** A literal reading of "comment-only
baseline never creates the tracking table" suggests adding a
non-comment SQL statement to the baseline (e.g. `SELECT 1;`). The
implementer received this finding three times and did not try this
fix.

## On why the spec didn't ship

Two non-exclusive interpretations:

1. **Spec-level ambiguity** (deferred bug #1 territory): the spec
   says `pnpm db:migrate` should bootstrap the tracking table but
   doesn't say HOW given that drizzle-kit's behavior depends on
   baseline migration content. A more prescriptive spec would
   resolve this.

2. **Implementer reading-comprehension gap**: the bootstrap_check
   finding's message LITERALLY describes the failure mechanism
   ("comment-only baseline never creates the tracking table"). The
   fix is to make the baseline non-comment. Three iterations did
   not try this. This may be a sonnet-4-under-temp=0 actionability
   limit on cascade-aggregated directives, OR a prompt structure
   issue (the finding is in the gate-summary table; the
   classifier's rationale and Oracle's fix-list both reference
   "the migration system" but neither quotes the bootstrap_check
   message verbatim into the implementer's prompt).

The honest framing: **the spec didn't ship, and the framework
changes are not at fault for that, but neither did the framework
demonstrate end-to-end shipping under the new contract**. We can't
distinguish (1) from (2) without either re-running with a more
prescriptive spec OR re-running with the bootstrap_check rationale
inlined verbatim into the implementer prompt.

## Notable events

- **`COVERAGE_PASS`** at iter-1 after 2 coverage_regen attempts —
  H2 loop closed cleanly. 13 visible scenarios produced.
- **`ITERATION_REGRESSION`** at iter-2 (80% → 68%). Implementer
  attempted to fix the cascading failures but introduced new
  failures and got rolled back to iter-1's tree.
- **`TRAJECTORY_CLASSIFIED iteration=3 trajectory=DEGRADING`**.
  Same pass rate as iter-2 = no recovery from the regression.
- **`ESCAPE_HATCH_FEEDBACK`** fired at both iter-2 and iter-3,
  bypassing the normal classifier→reviewer→oracle path. iter-3
  went straight to the escalation agent.
- **No `BARRIER_PATH_ECHO_DETECTED`** events — barriers held throughout.
- **No smoke errors** — `test_server` worked across all iterations.

## Audit log spot-check

Both barrier-sensitive agents enforced filesystem masks correctly via
Docker tmpfs:

```
qa-holdout: --tmpfs /project/.dark-factory/scenarios:ro,size=0,mode=755
            --tmpfs /project/apps/api/src:ro,size=0,mode=755

test-engineer: --tmpfs /project/.dark-factory/holdouts:ro,size=0,mode=755
               --tmpfs /project/apps/api/src:ro,size=0,mode=755

implementer: --tmpfs /project/.dark-factory/scenarios:ro,size=0,mode=755
             --tmpfs /project/.dark-factory/holdouts:ro,size=0,mode=755
             --tmpfs /project/.git:ro,size=0,mode=755
```

`--disallowedTools` flags + `--append-system-prompt` barriers also
present as defense-in-depth.

## On the escalation report's "26+ previous attempts"

The pipeline-emitted `reviews/escalation.md` says: "Analysis of
archive data shows **26+ previous attempts** dating back to
2026-04-20." This figure mixes infrastructure-aborted runs (where
the pipeline never reached implementer) with substantive
ship-attempts. **The "26+" should not be read as 26 sequential SHIP
failures.** Of those, at most 6-8 were genuine completed runs of
this spec; the rest were aborts on prior framework bugs (the
bootstrap_check primitive didn't exist before commit `24a3dd9`,
substrate findings weren't inlined before `c68b3f8`, etc.). A future
escalation surface should distinguish "iterations that ran" from
"runs that completed."

## What this run did NOT prove

- **End-to-end ship**: the spec did not ship. Disambiguating
  spec-ambiguity (1) from implementer-comprehension (2) requires
  follow-up runs.
- **Gap 1 against an emission requiring commands**: the substrate-
  owner had nothing to seed. A spec where prerequisites are absent
  on entry would test the schema-level non-claim against actual
  pressure.
- **Gap 2 across multiple distinct cascade events**: only iter-1
  ran the classifier. iter-2 and iter-3 escape-hatched.
- **Gap 2 produces ACTIONABLE signal under temp=0**: the signal
  was structurally correct but the implementer didn't act on it
  across two more inlined re-presentations. Whether tighter prompt
  framing (e.g. quoting bootstrap_check message verbatim) closes
  this is undetermined.
- **Gap 3 captures anything under escape-hatch paths**: 0/2
  escape-hatch iterations produced patches.

## Action items

### Must fix before next discriminating run

1. **Gap 3 follow-up commit (untracked-file capture).** In
   `capture_iteration_diff`, append synthetic `diff --git /dev/null
   b/<path>` blocks for untracked files. ~40-60 LOC + tests. Don't
   touch `_capture_iteration_end_sha` (3 other consumers).

2. **Gap 3 follow-up commit (escape-hatch path).** Capture
   `iteration_end_sha` in `_step_quality_gate` post-gate so the
   SHA exists regardless of which terminal path fires. Or:
   `snapshot_iteration_diff_for_completed` falls back to a
   gate-time SHA when the oracle SHA is empty. Investigation
   needed.

These two follow-ups can land in one commit. Until they do, the
Gap 3 archive is misleading and any framework-changes-validation
run report should explicitly disclaim the Gap 3 partial captures.

### Should fix soon

3. **Quote bootstrap_check rationale verbatim in implementer prompt.**
   The finding message contains the fix mechanism. Currently the
   classifier paraphrases ("the migration tracking table was never
   created") but loses the WHY ("comment-only baseline never creates
   the tracking table"). This is a Gap 2 follow-up — the cascade
   directive could include "preserve the upstream finding's `message`
   field verbatim in the rationale."

4. **Re-run database-foundation with prescriptive baseline guidance.**
   Either amend the spec to mandate non-comment baseline content,
   or add a precondition test that fails fast on comment-only
   baselines. This disambiguates spec-vs-implementer in the
   non-shipping cause.

### Deferred bugs (still open from prior session — unchanged by this run)

1. **spec_validator wire-language FP on foot-gun docs**.
2. **holdouts.md never consumed by iteration loop**.

## Comparison summary (corrected)

| Question | Pre-three-gaps (5b iter-2 baseline) | This run |
|----------|-------------------------------------|----------|
| Substrate-prep schema enforces non-claim under live LLM? | n/a | Shape-correct on no-op input; not exercised under pressure |
| Cascade detection produces structured tags? | No | Yes on n=1 (iter-1 only; iter-2+3 escape-hatched) |
| Oracle aggregates cascades? | No | Yes on n=1 (same caveat) |
| Cascade signal is actionable by implementer? | n/a | No, across 2 inlined re-presentations |
| Per-iteration diff archived correctly? | No (zero capture) | Net negative: 1/3 captured, captured one is misleading |
| Spec shipped? | No | No |

The framework changes from `5d7fb29` / `1892d42` / `e2b77a6` are
not regressed by this run — but the discriminating evidence each
was meant to produce is weaker than I initially framed it. Two
clean follow-up bugs identified (Gap 3, both modes). One open
question on Gap 2 actionability that this run cannot resolve. Gap 1
needs a different spec to actually be tested.

The next-run plan: ship the Gap 3 follow-up commit first, then
re-run database-foundation against either a prescriptive baseline
spec or with the bootstrap_check rationale inlined into the
implementer prompt. That's two changes, but they probe different
hypotheses (one structural to the framework, one structural to the
prompt) and shouldn't be conflated.
