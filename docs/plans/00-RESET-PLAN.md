# 00 — Reset Plan: Get Back On Track

> Canonical plan as of 2026-06-08. Supersedes the three prior "Phase N"
> numbering schemes (git-branch ladder, `docs/ROADMAP.md`,
> `docs/HIGHER-PRIORITY-QUEUE-PLAN.md` — all now marked superseded).
> Reviewed by: Claude (assistant) + Neo (independent, APPROVE-WITH-CHANGES,
> all changes folded in). Code-review gate: Linus + Codex in parallel.
> Owner: Caleb.

## ▶ RESUME HERE — state at 2026-06-19 session (LATEST — spec-4 SHIPPED but WEAK evidence; diagnosis-variance is the real wall)

> **SHIPPED + MERGED MAIN (all Neo design + Linus + Codex gated, no Co-Authored-By):**
> - **merge `05261c9`:** RULE A pre-impl scenario path-escape lint (clamps a `../` run that
>   escapes the project root — fixes bb spec-4 AC-25, host-side, deterministic) + **#22** mask
>   `.dark-factory/reports/` from the TEST_ENGINEER (latent d2fecbf-class read leak; QA-Holdout
>   untouched) + **#18** Setup observability (capture the Setup command's stdout/stderr to the
>   Oracle when a substrate bootstrap_check fails after exit-0 — the silent-no-op case). This merge
>   also carried the prior-session **#11 `--from-phase gate`** + **#20 substrate-setup-before-verify**
>   + coverage-floor commits onto `main` (they were committed-but-not-on-the-main-ref).
> - **merge `0941491`:** substrate-verifier status keys on **error-severity**, not findings-presence
>   (latent fix: the first info/warning finding would have spuriously failed the substrate gate;
>   discovered via the #29 snapshot work). 4434 tests pass.
> - **RULE B (manifest-discriminated unqualified-table lint): BLOCKED by Neo** as d2fecbf-class
>   barrier escalation (routes architect/source-derived schema knowledge to the barrier'd TE). Not built.
>
> **bb spec-4 (`2026-03-28-database-foundation`) SHIPPED (run `88870b6c`) — but treat as WEAK
> evidence the pipeline "works".** It shipped via `--from-phase gate` (skipped spec-validate /
> architect / TE / from-scratch implementer — exactly the stages all ~8 prior attempts died on,
> TE-timeouts) + a **manual** AC-25 pre-clamp. And the NO-SHIP→SHIP flip was **REVIEWER DIAGNOSIS
> VARIANCE**: MEASURED 1/3 — 3 independent runs from byte-identical AC-24-failing state → 1 correct
> "pin `migrations.schema`" diagnosis (→ship), 2 *different* wrong theories ("table absent",
> "per-test reset wiped it") (→escalate/dead-end). Diagnosis↔outcome perfectly correlated. AAR +
> the variance experiment are the spine of this session.
>
> **THE REAL WALL (strategic):** the pipeline's reliability for **semantic** spec defects (AC-24
> asserts a tool-internal table) is gated on **LLM diagnosis variance**, and there is **no clean
> deterministic fix** — semantic badness needs *understanding* (LLM → variance). **Structural**
> defects (AC-25's `../` path) ARE deterministically catchable (RULE A is proof). The reliable-vs-
> variance axis is **guess-vs-observe**, not up-front-vs-late: up front there's only spec text
> (→guess); late-stage the failure actually happened + the DB is real (→observe).
>
> **#29 (HELD, UNPROVEN):** a per-ecosystem **DB-state snapshot** (dump table→schema map +
> `search_path` as an info finding, inline into the Oracle prompt) so the Oracle *correlates* a
> failure against ground truth instead of guessing. Non-curation (names no tool). Built + VALIDATED
> to capture correctly (`drizzle.__drizzle_migrations` + `search_path: public`), but **could NOT
> measure** 1/3→? because the ad-hoc restore harness kept breaking the test bed (terminal: stale
> `src` restore missing `global-setup.ts` → vitest crashed at *collection* → AC-24 never asserted).
> Reverted from the tree, saved at `/tmp/db-state-snapshot-mechanism.patch`. **Measure during the
> campaign (clean failing states for free); only land if it beats 1/3.**
>
> **CURATION DISCIPLINE reinforced (Caleb):** the framework mentioning `__drizzle_migrations` at
> all is a smell. **#26 REVERTED** (manifest-discriminated schema probe deepened the tool-internal-
> table catalogue). The de-curation fix is **#28**: verify a migration applied via its **declared
> effects** (GUC / declared tables — names from the spec), NOT the tool's internal tracking-table
> ledger; drop the per-tool catalogue (`__drizzle_migrations; alembic_version; etc.`) from the
> architect addendum. Policy banked: an adapter probe earns its place only if **ground-truth (no
> hardcoded tool facts) AND a proven-recurring class AND empirically validated** — never a one-off.
>
> **NEXT (the #24 campaign — and the most important open question):** run a **FRESH** bb spec (no
> `--from-phase gate`, no manual clamps) with **spec-by-spec instrumentation** to (a) answer the
> thing spec-4 did NOT — does a fresh pipeline ship anything end-to-end; (b) generate the *frequency*
> data that decides whether #28/#29 are worth landing; (c) measure diagnosis variance on fresh
> failing states. bb is reset to clean spec-3 (HEAD `54a91f2`); the corrupted spec-4 tree is in
> `stash@{0}` (recoverable). Branch `from-scratch-campaign`.
>
> **META-lessons (Caleb caught most):** verify a mechanism against the PRODUCED RUN ARTIFACT, not a
> side-reproduction (mis-attributed AC-24's cause twice this session); don't ASSERT reliability —
> MEASURE it (the 1/3 experiment is the model); the gate `pass_rate` event is misleading — read the
> Oracle's feedback artifact; measurement harnesses need a COMPLETE git-clean restore, not a partial
> snapshot. New backlog: #24, #25 (diagnosis-localization metric), #27 (implementer-changes.patch
> misses repo-root config edits), #28, #29.

## RESUME HERE — state at 2026-06-18 session (S0 SHIPPED, S1 re-scoped by trace+Neo)

> **S0 SHIPPED + MERGED MAIN (both halves, each Neo design + Linus + Codex gated):**
> - **S0(b) (`827a443`→merge `5712eb1`): Check 9 tsc @types/node no-op fix.** Reproduced
>   firsthand with real `npx tsc`: validator Check 9 silently NO-OP'd for node-importing TS
>   specs in pnpm monorepos (`types:[...,"node"]` + @types/node unwalkable → single TS2688 bail
>   → bucketed import-error → soft "skipped"). Fix: `_resolve_project_node_types` finds the
>   project's OWN @types/node (flat/app/`.pnpm` store, semver-highest) + check-local symlink (NO
>   typeRoots — it breaks vitest/globals) + explicit target/lib es2022 + TS1xxx syntax-hard-fail.
>   NEW structured `CheckResult.compile_status` ("passed"|"skipped"|"failed"|"syntax") RETIRES the
>   fragile `"skipped" in detail` substring the S41 salvage backstop used → backstop now ships only
>   on `compile_status=="passed"`. Stub generator emits bare+.ts+.js variants. AUDIT: bb spec-3
>   (SHIPPED clean) rested on this no-op too (`type definitions not available (skipped)`); same
>   layout as notes-crud-ts/pg-ts — blind gate was the real arbiter, not Check 9. End-to-end
>   verified (real tsc, pnpm layout: no-op→skipped without fix, 32 real TS7006/2698/2352→failed
>   with fix). 4356 tests pass.
> - **S0(a) (`6ea51e3`→merge `291fbe1`): real per-invocation stream-metric observability.** On a
>   SIGKILL timeout there's no result envelope → telemetry recorded IN=0/OUT=0 (blind).
>   `_parse_stream_metrics` parses the partial stream → tool_use_counts histogram, num_assistant_
>   messages, **first_write_turn / wrote_any_scenario / scenario_write_count** (the exploration-vs-
>   generation discriminator), output_tokens_stream_lowerbound. Telemetry schema v1→v2 (additive;
>   billed output_tokens NEVER overwritten — cost path stays honest; usage_source field). Failure
>   dump embeds the metrics summary + writes the FULL stream to a `.stream.jsonl` sidecar under
>   failures/ (dir-masked) when the embedded stdout was truncated. Verified on the REAL spec-4
>   dumps. 4371 tests pass.
>
> **⚠ RETRACTED finding (Caleb caught it by checking disk, 2026-06-18):** I briefly concluded the
> spec-4 TE was "EXPLORATION-bound, never reached generation" (from parsing the 4 timeout dumps) and
> Neo re-scoped S1 around exploration-reduction. **BOTH WRONG.** The TE DOES generate — run-telemetry
> shows many `exit=0` TE invocations with 41–55k output_tokens (wall 116–836s), and the 15 on-disk
> scenario files were written 07:12–07:17 by validate-att2. The dumps under-capture: on SIGKILL,
> `subprocess.run` keeps only what the agent FLUSHED; claude block-buffers stdout when piped, so the
> unflushed tail (the writes) is LOST — the captured stream is a biased exploration PREFIX. So the
> real shape is explore → WRITE (uncaptured) → hit the wall = **wall-tightness under rate latency**,
> the ORIGINAL diagnosis the post-mortem reversed. Detail + retraction: memory
> `project_bb_spec4_te_exploration_bound`.
>
> **S1 = ORIGINAL plan (Caleb's), exploration-reduction DEMOTED to optional:**
> - **(a) per-role TE timeout (~1800s)** via generic `agent_timeout_overrides` dict + per-call-site
>   `timeout_override` (the explore+generate cycle that fits in ~836s under good latency overruns
>   900s when rate-throttled — give it room). Per-phase is fine (generate big, validate/floor smaller).
> - **(b) warm the cold validate/floor regens** — barrier-SAFE (confirmed: TE container masks identical
>   across phases; validate_scenarios runs host-side). LANDMINES (Neo, still valid): spanning-`finally`
>   container leak across the ~280-line try-body's raise paths; `_enforce_coverage_floor` signature
>   threading; **`--continue` into a SIGKILL'd session on the SALVAGE path → force COLD there**;
>   cold-degradation parity at all 3 sites.
> - **(optional) exploration-reduction** — injecting a precomputed inventory would TRIM the explore
>   prefix so the cycle fits the wall; a secondary speedup, NOT the fix. Defer unless cheap.
> - **SUCCESS METRIC — NOT first_write_turn (unreliable on timeouts — see the observability gap), and
>   NOT "didn't time out" alone:** measure by DISK STATE / outcome — did the TE write a full coherent
>   scenario set and did the run progress past generation. Whether spec-4 reaches the gate/ships.
>
> **NEW observability gap to fix (S0(a) under-delivered):** the failure-dump under-capture is at the
> AGENT-BUFFERING level, not dump truncation — my `.stream.jsonl` sidecar preserves an INCOMPLETE
> stream. `first_write_turn`/`wrote_any_scenario` can't be trusted on timeouts. To fix: line-buffer
> the agent stream (`stdbuf -oL` / real-time incremental pipe read so the tail survives SIGKILL).
> This is the genuine "fix the failure-dump UNDER-capture" item from S0(a) — addressed dump-level
> truncation, NOT the source-level buffering. Worth a follow-up before trusting the write-conversion
> signal.
>
> **S0(c) SHIPPED + MERGED (`5e7649a`→merge `5ea7c9b`; Neo design + Linus SHIP-WITH-NITS + Codex
> BLOCK→resolved):** wall-time-attribution streaming observability. After the buffering hypothesis was
> DISPROVEN by experiment (claude streams live, docker forwards live, subprocess.run keeps pre-kill
> events), the REAL gaps were: (1) no per-event timestamps → wall-time unattributable; (2) buffered
> subprocess.run doesn't drain the pipe after SIGKILL. Fix: `run_streaming` (hand-rolled Popen,
> concurrent stdout/stderr readers + stdin writer thread for the 3-pipe deadlock, per-line
> arrival-timestamped sidecar under masked failures/, post-kill pipe-close so a docker grandchild
> can't hang the readers — verified real-docker timeout returns 2.0s not 21s, exit 124) +
> `compute_wall_attribution` (model vs tools vs idle_tail vs longest_gap, in telemetry + dump). Agents
> opt in; general run() untouched. Verified against REAL subprocesses + end-to-end real docker. 4379
> tests pass. NOTE: captures only what reaches the HOST pipe — the validate-att2 upstream loss (writes
> absent from a complete untruncated dump) is NOT claimed fixed; if the next spec-4 run is still
> incomplete, a time-boxed in-container tee experiment localizes claude-emission vs transport.
>
> **DIAGNOSTIC HONESTY (this session, important):** THREE successive spec-4 timeout hypotheses were
> each DISPROVEN — (1) "TE exploration-bound" (RETRACTED — telemetry shows 9 exit=0 TE invocations at
> 18–55k out_tok; the TE generates fine; the dumps were biased prefixes); (2) "stdout block-buffering"
> (DISPROVEN — all layers capture live); (3) "rate-limiting stalls" (UNPROVEN — the rate_limit_events
> all say `status:allowed_warning` at 0.84 util, i.e. ALLOWED not throttled; no per-event timestamps
> existed to prove where the 900s went; same false-signal Caleb's spec-3 screenshot already ruled out).
> Caleb caught each by checking ground truth (disk/telemetry). **We do NOT currently know why spec-4
> times out** — S0(c) exists precisely so the NEXT run is diagnosable. Do not assert a cause until the
> timestamped trace proves one.
>
> **RE-RUN DONE + POST-MORTEM SETTLED (run 465c1e39, S0c live; full data in
> `docs/plans/bb-spec4-postmortem-465c1e39.md`; Neo + Codex critically reviewed, both REVISE→folded):**
> The MEASURED cause (from S0c's complete arrival-timestamped streams) is **intrinsic per-turn
> extended-THINKING latency** — every multi-minute inter-event gap is a `thinking` block emitting
> ~20-40 tokens, SYSTEMIC across ALL agents (architect 181s / qa-holdout 324s / spec-validator 347s
> max gaps, at LOW utilization). The **test-engineer** times out because it needs **69-109 turns** vs
> analysis agents' 33-62 → turns × intrinsic-thinking-per-turn > 900s. NOT exploration (tools 1-10s),
> NOT generation-volume (≤3.4k out-tok/agent), NOT buffering (S0c captured complete streams incl. late
> writes). **Rate-limiting DROPPED** (Caleb + data: the LARGEST thinking gaps were at FULL budget /
> zero utilization — rate didn't inflate per-turn latency; it only crashed the final attempt via a
> service hang → signal-15). The run was killed by the 5-hour usage limit at TE#5; never reached a
> verdict. S0c + S0b both VALIDATED in production (S0b's Check 9 actually FAILed on real TS errors in
> the salvaged files vs the old no-op skip).
>
> **SHIPPED (`e2464b0`→merge `90a847a`; Neo post-mortem review + Linus + Codex gated):** L2 per-role
> timeout override (`agent_timeout_overrides` config, evidence-justified BRIDGE) + the S0c
> sidecar-collision fix (both reviewers caught it). Warm-regen DROPPED (Neo: warming optimizes a
> non-bottleneck — exploration is 1-10s).
>
> **NEXT (the durable fix): S2 — parallel anchor-batched TE (= lever L3).** N TE containers, disjoint
> anchor subsets → fewer turns/invocation → fits the wall. Both reviewers rank it the top structural
> lever; Neo: the "burns rate faster" worry is OVERSTATED (thinking is wall-clock-dominated, ≤73
> out-tok/turn). To VALIDATE L2 first: re-run spec-4 with `[agents.timeout_overrides] test-engineer =
> 1800` on a FRESH 5-hour budget (the data predicts the TE may finish in one ~1500-1800s attempt).
> DEFERRED: L1 (cap MAX_THINKING_TOKENS) — data-supported but a quality footgun on a barrier-isolated
> correctness agent; needs a controlled quality comparison + gate, not a blind cap.

## ▶ RESUME HERE — state at 2026-06-18 session (LATE — spec-4 saga + HEAVY-SPEC strategy)

> **bb SPEC-4 (database-foundation, F-1c, Postgres) FAILED TWICE — the campaign is BLOCKED on it.
> S41 shipped; the headline finding is a STRATEGY REVERSAL (decompose ❌ → raise pipeline
> capacity ✅) + a latent TS-fleet correctness hole.** Full detail: memory
> `project_bb_spec4_postmortem_s41` (the post-mortem was CORRECTED after Neo+Codex independent
> review inverted my first read — read the corrected version).
>
> **S41 SHIPPED (`2e9164f`, Neo design + Linus + Codex 5-round dual-gate): TE timeout-salvage.**
> On a TE *timeout* (exit 124) that left scenario files, break into the existing SA-validate +
> coverage-floor machinery instead of the unconditional SPEC_FAILED raise (which incinerated
> valid work). Hardened with: salvage gated on `sa_max_retries>0`; `timed_out` reads the runner's
> REAL TimeoutExpired flag (not `exit_code==124` inference); a post-loop backstop that refuses to
> ship salvaged scenarios whose tsc-validation didn't genuinely run. **Salvage CORE = validated
> (it fired correctly on the spec-4 re-run, file_count=7).**
>
> **THE SPEC-4 FAILURE — corrected diagnosis (my first post-mortem was INVERTED; Neo + Codex +
> firsthand `tsc` repro overturned it):**
> 1. **My S41 backstop fired on a TRUE POSITIVE, not a false one.** The skip
>    `"type definitions not available (skipped)"` is NOT benign TDD-red — it's `validator.py`'s
>    Check 9 (`_check_typescript_compilation`) BAILING on `error TS2688: Cannot find type
>    definition file for 'node'` (the generated tsconfig declares `"types":[...,"node"]` but
>    `@types/node` doesn't resolve in the pnpm monorepo) → tsc quits BEFORE type-checking the
>    bodies. Running real tsc surfaced **33 genuine code errors** (29× implicit-any, spreads,
>    unsafe casts) that were masked. The backstop correctly refused to ship them. **DO NOT
>    "narrow the backstop" (my original wrong fix) — that re-opens the unvalidated-ship hole.**
> 2. **REAL BUG (P0, independent of heaviness): Check 9 is a SILENT NO-OP for node-importing TS
>    specs in monorepo layouts** — latent since the check was added. Prior "clean ships"
>    (notes-crud-ts, notes-crud-pg-ts) may have rested on a no-op tsc gate. **Fix Check 9 (resolve
>    `@types/node` via minimal ambient stub / typeRoots; fix the entry-point `declare module`
>    extension mismatch; consider a syntax-only hard-fail pass) + AUDIT prior TS ships.** This is
>    the most urgent item and is independent of the heavy-spec debate.
> 3. **The deeper blocker: systemic TE-timeout.** EVERY TE invocation walled at exactly 900s
>    (generate ×2, validate-regen ×2). The TE used only 34–67 captured turns (<< the 200 cap;
>    dumps UNDER-capture so true count is higher) — so the binding constraint is the per-invocation
>    900s WALL (turn-count × high per-turn latency, partly the 7-day rate budget at ~82%), NOT
>    turns or the 64k output cap. Anchor counts: api-bootstrap 23 (shipped) vs database-foundation
>    **55** (4 FR / 38 AC / 13 EC, failed). FR count is the WRONG heaviness metric (both specs = 4
>    FR); total anchors / scenario-intent volume is the predictor.
>
> **STRATEGY REVERSAL — "raise pipeline capacity" BEATS "decompose" (Neo reversed its own ranking
> under the data; I concur). SUPERSEDES my earlier 2026-06-18 "decompose + heaviness gate"
> proposal below.** For a spec heavy because ONE capability is intrinsically rich (a DB foundation
> IS one coherent thing), decomposition is STRICTLY DOMINATED: it needs a new `/df-write-plan
> --decompose` engine, semantic boundary-drawing, depends-on rewiring of all downstream specs, and
> 3× per-run overhead (tripling the spec-validator+architect+qa-holdout+gate that WEREN'T failing),
> and it fragments a cohesive spec. The binding constraint is a TUNABLE config wall, not physics —
> so RAISE the ceiling. A hard heaviness GATE that blocks cohesive specs is anti-robustness +
> contamination-adjacent (forces authors to route around tooling) — keep heaviness ADVISORY, as a
> signal that ROUTES to parallel batching, never a hard block. Honest caveat: "the TE got 54/55,
> it was close" is partly an ARTIFACT — those files were unvalidated (33 type errors) + the
> file-count was inflated by timed-out validate-regen partial writes; the win condition is "covered
> AND tsc-clean AND survives validate+floor", which is why Check-9 + warm-regens ride with the
> timeout bump (it's not a one-liner).
>
> **CORRECTED SPRINT SEQUENCE (the heavy-spec answer — each piece its own Neo→Linus+Codex gate):**
> - **S0 — observability + Check 9 (P0, do FIRST):** (a) fix the failure-dump UNDER-capture (we keep
>   going to disk for turn counts/writes — we're tuning blind) + add real turn/wall/output telemetry
>   so the binding constraint is MEASURED not inferred; (b) fix Check 9's `@types/node` tsc no-op +
>   audit prior TS ships. (b) is the latent correctness hole; (a) de-risks every downstream choice.
> - **S1 — raise capacity (load-bearing; likely ships spec-4):** (a) per-role TE timeout (~1800s)
>   applied to ALL THREE phases (generate + validate-regen + floor-regen); (b) WARM the cold
>   validate/floor regens (orchestrator.py ~3942, ~5087 invoke the TE COLD — no session/continue,
>   unlike generate ~3350). Without (b), (a) only fixes one of three walls.
> - **S2 — parallel anchor-batched TE (the DURABLE ceiling-remover; a real sprint, build AFTER S1
>   unblocks spec-4):** N TE containers each own a DISJOINT anchor subset (~18-27, fits budget),
>   run CONCURRENTLY (wall = slowest batch, NOT sum), disjoint-by-construction coherence + a union
>   merge → one validate/floor pass. Decomposition's upside (smaller anchor sets per TE window)
>   without its cost. Same machinery precedent as F Move 2b. NON-trivial: shared architect
>   manifest / contract staging (`_contracts/`,`_df_setup/`) / per-test DB reset / the merge /
>   barrier integrity for 2 concurrent TE containers.
> - **Heaviness DETECTION = advisory routing signal, NOT a gate.** precheck already collects
>   fr_ids/ac_ids/ec_ids/declared_intents; surface total_anchors + total_intents (instrument both,
>   gate on neither yet — intents is the better predictor mechanistically but UNMEASURED). A heavy
>   spec AUTO-ENABLES parallel batching / the heavier timeout; it does NOT get blocked. Fix the
>   `/df-write-spec` FR-count guard to an anchor-aware WARN (advisory).
> - **DECOMPOSITION = demoted to a spec-authoring SMELL** — reserve for specs that bundle genuinely
>   INDEPENDENT capabilities (auth+billing), NOT a cohesive intrinsically-rich one. Do NOT build a
>   `--decompose` engine to solve the TE wall (parallel batching dominates it). A one-off MANUAL
>   split of spec-4 is only a fallback unblock if S1 fails, not the strategy.
>
> **bb STATE:** impl on disk = still spec-3 (`54a91f2`); spec-4 left only scenario artifacts
> (clear on next reset). Decontaminated spec-4 + the postgres `[[services]]` config addition are
> UNCOMMITTED in bb (inputs — KEEP). Open follow-ups that were gated on spec-4 passing and DID NOT
> happen: spec-4 AAR, spec-4 code commit, review+merge `sprint/te-qa-parallelization` (worktree
> `~/sites/.df-worktrees/te-qa-parallel`, commit `59694c2` — F Move 2b, still
> unmerged), bb spec-5. The TE/qa-holdout parallelization worktree (F Move 2b) is closely related
> to S2 (parallel batching) — reconcile them.

## ▶ RESUME HERE — state at 2026-06-18 session (EARLIER — backlog re-prioritization, pre-spec-4)

> **bb-FROM-SCRATCH ON TRACK — 3 specs shipped, framework materially healthier; this is a
> backlog re-prioritization, not a firefight.** Nothing on the backlog is BLOCKING. bb specs
> 1 (monorepo-tooling), 2 (shared-parse-amount), and 3 (api-bootstrap) have all SHIPPED;
> spec-3 shipped CLEAN (iter 1, 51/51 scenarios). Currently between spec-3 and spec-4.
>
> **SHIPPED THIS ARC (S33–S40, all Neo→Linus+Codex gated, merged main):** S33 stream-json
> observability + full timeout dumps; S34 output-token cap (model-derived) + non-retryable
> cap-overflow short-circuit; S35 ARCHITECT added to `_AGENT_DEF_INJECT_ROLES` (section-only
> injection) + D2 manifest schema-lint backstop; S36/P1 dump-every-timeout-in-full; S37/P2
> architect-only scenarios barrier-echo suppression; S38/P3 vitest import-crash classifier;
> S39/P4 substrate `files_seeded` tolerance; **S40 (`fbc84cf`) — CRITICAL gate fix.**
>
> **KEY STATE CHANGES vs the 2026-06-16 block below (these re-rank the backlog):**
> - **S35 RESOLVED the TS-architect schema drift (Finding mode-B/C / the empty-`file_map`
>   TE-starvation chain).** The architect produced a CANONICAL `file_map` manifest on EVERY
>   draw observed across the spec-3 re-runs (no `MANIFEST_VALIDATION_RETRY`). The TE no longer
>   reverse-engineers the filesystem for import paths → that root cause is closed.
> - **S40 fixed a CRITICAL gate false-negative the spec-3 re-run caught.** S38's `readonly
>   DF_CRASH_REGEX` hoist was re-exported to the python classifiers via the command-prefix form
>   `DF_CRASH_REGEX="$DF_CRASH_REGEX" python3 ...`; under `set -euo pipefail` that aborts the
>   gate AFTER the "Blind Scenarios PASS" row but BEFORE the count fragment is written → zero
>   fragments → false "0 scenarios executed" NO-SHIP on a fully-green gate. **Broke every
>   scenario gate since S38** (only a live full-gate exercises it; prior P3 validation was
>   offline). Fix = `export` once + drop the prefixes; Test 14 zero-tolerance static guard
>   (8 vectors) + Test 15 dynamic control. (Detail: memory `project_bb_spec3_aar_and_priorities`.)
> - **P5 SETTLED on complete P1 dumps (the 900s-timeout cause, was Finding G):** TWO distinct,
>   agent-specific causes. **TE timeout = latency/wall-tightness** — it finishes the work (all
>   files written) and is killed on a trivial final step; warm-retry (write-target persists
>   across attempts) rescues it. **Implementer timeout = genuine non-convergence** reverse-
>   engineering `@fastify/rate-limit` internals because the rate-limit AC behaviorally tests the
>   PLUGIN (AC-9 "send 101 requests → 429" — a deliverable-scope violation), but it converges by
>   retry and the code is CORRECT (51–56/56 pass). Both amplified by the EXOGENOUS 7-day rate
>   budget (76–77% `allowed_warning`, not rejected). Warm-retry makes both non-blocking.
>
> **RE-PRIORITIZED BACKLOG (2026-06-18 triage — supersedes the 2026-06-16 "SEQUENCE FROM HERE"
> + "Backlog (unchanged)" lines and Finding G's lever ranking):**
>
> **DEFAULT NEXT = continue the campaign → bb spec 4** (it is the measurement that matters and
> surfaces framework gaps for free), pulling in item 1 first.
>
> **WORK ON NOW (highest leverage):**
> 1. **Finish-or-kill F Move 2a (deterministic contract-resolution).** Branch
>    `sprint/f2-deterministic-contract-resolution` (`5c3ecf2`), detection done + corpus-measured
>    (0 HARD / 5 advisory over 25 specs, 0 false positives), **UNMERGED**. Closes the
>    standalone-`validate` vs in-run-gate false-green gap. Remaining: wire into precheck + move
>    spec-bytes checks ahead of the `dry_run` short-circuit + AUTHORIZATION-MODEL review (grants
>    a corpus-backed hard gate) + Linus+Codex. A rotting half-built branch is debt — finish it or
>    formally drop it.
> 2. **`/df-write-spec` deliverable-scope hardening for behavioral-plugin-enforcement ACs** (the
>    AC-9 class: "send N requests → observe the plugin's 429/enforcement" tests a third-party
>    tool, not our config). It was the ONLY genuine non-convergence this session, is breadth-
>    general, and prevents implementer plugin-spelunking across the remaining ~20 specs. Cheap
>    curation fix; the deliverable-scope rule exists but this shape slipped through.
>
> **DROPPED:**
> - **Incremental TE writes** (memory `project_incremental_te_writes_design_options`) — targeted
>   the output-token-cap/hang class; S34 (cap raise) + S35 (TE-starvation fix) mitigated it, and
>   this session's TE timeouts were NOT cap-hangs (TE wrote all files; killed by latency on a
>   final `ls`). Keep the design note only for a genuine >model-max single-turn recurrence.
> - **Real-time mid-stream timeout kill** (Finding G / cap-timeout deferred-a) — high-risk
>   streaming-`Popen` rewrite with `docker stop` kill semantics for a now-rare residual the cap
>   raise + warm-retry already cover. Only-if-it-becomes-common.
> - **P1 "bound TE output" (Finding G)** — subsumed: the cap is set (S34) and the cold-regen
>   thrash it worried about was fixed by F Move 2c (shipped, 2026-06-16 block).
>
> **DOWNGRADED:**
> - **Architect-manifest non-determinism (N≥3) (was P6 / Finding F6)** — S35 made the architect
>   canonical on every observed draw; concern appears RESOLVED. Spot-check opportunistically;
>   re-open only on a recurrence (don't spend a dedicated N≥3 measurement).
> - **Cost-accounting-on-timeout (P6)** — real usage is already in `run-telemetry.jsonl`; the
>   estimate-at-cap is a known display artifact (memory `project_cost_accounting_estimate_at_cap`).
>   Fold into a single "scorecard consumes run-telemetry" task when convenient.
> - **Per-role / heavier agent timeout (Finding G's "most-supported lever")** — DOWNGRADED to a
>   latency optimization, not a fix: warm-retry already rescues correctness, and a higher wall
>   makes a genuinely-stuck agent burn longer. Do only if campaign run-latency becomes painful.
> - **F Move 2b (parallelize TE + qa-holdout, ~10 min/run)** — real but dwarfed by the
>   ~30–45 min of timeout-warm-retry waste per run; behind the campaign + item 1.
> - **P0 transient-classification (Finding G)** — stdout-aware `_is_transient` + don't count
>   transient/timeout as a validation attempt. Sound and "correct regardless," but hasn't bitten
>   since warm-retry + S40; keep as low-priority correctness debt, not a now-item.
>
> **SHIPPED SINCE THE 2026-06-16 PLAN (remove from "pending"):** F Move 1 (advisory demotion,
> `c326645`), F Move 2c (TE warm-session self-correction / SCENARIO_MISNAMED_RETRY, on main per
> the 2026-06-16 block), B′ (eligible-NO-SHIP terminal, `c06c9de`), S33–S40.
>
> **STANDING DEBT (unchanged, not now):** integration suite `run_regression` patch-target (48
> pre-existing failures — stash-verify new ones), warm pnpm cache volume / cache-volume-ownership
> (rust/go user-site, refuted as a timeout bottleneck — install is ~11s), periodic curation
> re-audit, model-pin resilience, reviewer-authored structural-gate feedback (C), scenario-lint
> darkfactory-import barrier (D). The injection flip (SAST step-2) still waits on bb generating
> finding-bearing owned-surface runs as a byproduct → then rust breadth.

## ▶ RESUME HERE — state at 2026-06-16 session

> **bb-FROM-SCRATCH SPEC 1 (monorepo-tooling) — first shippable-stage saga + 2 framework
> sprints shipped + a 3rd in progress.** The blank-state bb run on spec 1 surfaced 1 spec
> defect + 3 framework gaps + a validator policy gap (full post-mortem: memory
> `project_bb_monorepo_stable_failure_postmortem`, dual-reviewed by Neo + Codex which corrected
> my mechanism twice).
>
> **SHIPPED TO MAIN (both full Neo→Linus+Codex dual-gate):**
> - **B′** (`c06c9de`) — the eligible-NO-SHIP **stable-failure terminal** (`iteration.py` ~6002)
>   marked a run terminal whenever the Oracle declined a ship-with-gaps-eligible run, regardless
>   of WHY; now it RE-ITERATES when an implementer-actionable structural gate (Import Contract /
>   Substrate Verification — not in `_HARD_GATE_NAMES`) is still FAIL and iterations remain
>   (pure predicate `_stable_failure_terminal_suppressed_by_actionable_gate`; ceiling via
>   `_effective_max_iterations` so it matches the loop bound under dynamic-iterations — Linus
>   catch). Only flips terminal→iterate, never no-ship→ship.
> - **F Move 1** (`c326645`) — the in-run LLM `unresolved_contracts` gate was non-deterministic
>   and emitted FALSE POSITIVES contradicting the spec bytes (hallucinated already-resolved
>   TC-23/TC-32 divergences, aborting 3 runs, blocking the campaign). Demoted to ADVISORY behind
>   `unresolved_contracts_hard_gate` (default False); legacy machinery extracted to
>   `_enforce_unresolved_contracts_gate` (opt-in). Deterministic error-message / Free-Variables /
>   consistency gates stay hard. De-escalation (safe direction). **VERIFIED END-TO-END: run #6
>   cleared the validation gate** (advisory fired, logged-not-blocked) — campaign unblocked.
>
> **F MOVE 2 — the durable replacement, IN PROGRESS, two sub-sprints under one block:**
> - **F Move 2a — deterministic contract-resolution check.** DETECTION PHASE DONE (f2 branch
>   `sprint/f2-deterministic-contract-resolution`, commit `5c3ecf2`, NOT merged):
>   `darkfactory/spec/contract_resolution.py` compares each Tier-1 `file_contains` contract's
>   assertion to the literal a Wire Contract pins at the same `(file, path)`. HARD only on a
>   genuine value contradiction; weaker-than-wire = ADVISORY; fail-open. **Corpus-measured over
>   25 bb specs: 0 HARD / 5 advisory** (zero false positives where the LLM hallucinated 2). The
>   measure-first pass caught + fixed 2 would-be false-positive bugs (YAML `on:`→bool coercion;
>   string `'true'` vs JSON bool `True`). REMAINING (authorization-sensitive): wire into precheck
>   so standalone `validate` and the in-run gate AGREE (kills the false-green gap), move
>   spec-bytes checks ahead of the `dry_run` short-circuit, AUTHORIZATION-MODEL review (grants a
>   new hard gate, now corpus-backed), then Linus+Codex. Design: Neo ruling, this session.
> - **F Move 2b — parallelize test-engineer + qa-holdout (NEW PERFORMANCE TRACK, slotted
>   2026-06-16, Caleb).** Measured on run #6 telemetry: TE (11.5 min) + qa-holdout (10.5 min)
>   run SERIALLY but are INDEPENDENT (both read only the spec, neither reads the other, write to
>   different dirs `scenarios/` vs `holdouts/`) → running them concurrently saves ~10-11 min on a
>   ~60 min run. RATIONALE for adding a perf track now (the roadmap had none): the campaign just
>   proved it is RUN-BOUND, and bb is **spec 1 of ~25** with multiple ~60-min runs each ahead, so
>   run-latency investment amortizes at the FRONT of a long run-heavy road. SCOPE NOTE: barrier-
>   safe (different output dirs) but it is an ORCHESTRATION-CONCURRENCY change (2×4GB containers,
>   concurrent writes to `events.jsonl`/`run-telemetry.jsonl`/state, `step_begin` enforcement,
>   forked error/timeout handling) — its own Neo→Linus+Codex sprint, NOT bundled into 2a's PR.
>   Do AFTER 2a + after run #6 lands (don't destabilize the serial pipeline mid-measurement).
> - **F Move 2c — TE warm-session self-correction (slotted AHEAD of 2b, 2026-06-16, Caleb).**
>   Generalize the IMPLEMENTER's existing persistent-container `claude --continue` pre-gate
>   retry loop (primitive: `docker/containers.py:120` "S1 session continuation") to the
>   test-engineer. Today the TE cold-restarts on retry (both the basic retry AND the opt-in
>   `te_pre_acceptance_check` re-`invoke_agent` a FRESH container — `_step_te_pre_acceptance`
>   docstring: cold-restart was a deliberate simplification "disjoint from barriers.py
>   restructuring"), re-deriving every scenario from the spec blind to attempt 1. Instead: keep
>   the TE container warm; the ORCHESTRATOR runs the deterministic check on the host
>   (authoritative — don't let the agent run its own checker) and pipes the specific failure into
>   the warm session via `--continue`; the agent fixes in-context; host re-checks; loop to
>   pass-or-budget. Barrier-clean (TE self-corrects against its OWN scenarios + spec/manifest;
>   no src/holdouts). Wins BOTH: speed (kills the cold re-derivation) AND convergence (in-context
>   targeted fix beats blind regen — directly attacks the TE-retry failures seen in runs #2 + #6).
>   Edges out 2b because it helps the failure path we keep hitting, not just the happy path.
>   Its own Neo→Linus+Codex sprint. **DECISION (2026-06-16, Caleb): full-2c (warm-session) over
>   A (cold-retry-with-signal). Neo's first pass favored A (sufficient for CONVERGENCE — the
>   corrective string does that cold or warm); Caleb's efficiency point flipped it: A still cold
>   RE-DERIVES all 13 scenarios per retry (run #6 burned ~44 min / 4 invocations, one 900s
>   timeout) and clear-and-regenerate re-rolls the whole output (can inject a NEW defect); warm
>   preserve-and-rename keeps the known-good files. The corrective signal is a SHARED component
>   (warm-alone has the same zero-signal problem), so building full-2c subsumes A. Neo 2nd pass
>   delivered a complete, barrier-VERIFIED design (TE `scenarios/` write-overlay survives a
>   persistent container; NO BarrierConfig change; mirrors the implementer S1 loop
>   iteration.py:2957-3034 via start_agent_session/stop_agent_session + invoke_agent(use_continue);
>   contracts_ro_args MUST be baked at start_agent_session; flag-gated `scenario_warm_session`;
>   composes with SA-validation/te_pre_acceptance WITHOUT budget nesting; dry_run preserved).**
>   STATUS: branch `sprint/f2c-te-warm-session`. (1) DETECTOR DONE+TESTED (commit e145ead):
>   `find_misnamed_scenario_files` in detection.py (shared `_TEST_PATTERNS` complement, scoped to
>   code exts + excludes tier1/_contracts/_df_setup/sentinel/COVERAGE-GAPS.md/dotfiles →
>   false-positive-free; I corrected Neo's P0 block which dropped its own Q3 code-ext filter and
>   would have false-positived on COVERAGE-GAPS.md; 9 unit tests). REMAINING (the orchestration
>   piece): restructure the empty-retry loop orchestrator.py:3199-3231 (start persistent TE
>   session → initial generate → host check → on miss feed deterministic corrective via
>   use_continue=True, PRESERVE-and-rename not clear-and-regen → loop to pass-or-budget → finally
>   teardown; graceful cold fallback if session start fails) + `_format_misnamed_corrective` +
>   `_dump_misnamed_failure` (MISNAMED_SCENARIOS terminal, failures/ artifact) + emit_scenario_
>   misnamed_retry event + `scenario_warm_session` config knob + a REAL-LOOP test (mock only the
>   container boundary, assert `--continue` on attempt>0, no _clear, finally-teardown) → Linus+Codex.
>   **SHIPPED to main (merged 9c42e22 + detector e145ead; Neo 2 design passes + Linus
>   ACCEPT-WITH-NITS + Codex SAFE 8/8). Loop restructured to a warm persistent TE session
>   (mirrors implementer S1): misnamed→deterministic corrective via use_continue + PRESERVE-and-
>   rename (no clear), graceful cold fallback, finally-teardown, precise MISNAMED_SCENARIOS
>   terminal + failures/ artifact, flag-gated scenario_warm_session (default True). 30 new tests;
>   full suite 4222. NITS DONE (sentinel dedup, advisory-prose comment). Test-harness: Linus ruled
>   source-inspection + pure-execution OK for merge (no extracted harness required), BUT
>   REQUIRED-before-claiming-validated: a live run must show SCENARIO_MISNAMED_RETRY fires + NO
>   _clear between warm retries — that is run #7 (in flight). All THREE campaign fixes (B′ + F Move
>   1 + F Move 2c) now on main; run #7 is the end-to-end proof the campaign can advance past spec 1.**
>
> **FINDING G — the 900s agent_timeout is too tight for heavy agents (NOT TE-specific; warm-cache hypothesis REFUTED by measurement). 2026-06-16, runs #8/#9, Neo+Codex diagnosed:** Runs #8/#9 (clean, post-fix) showed the TE AND the implementer both hitting the 900s SIGKILL (exit 124) doing LEGITIMATE work (not hang/thrash). Run #9 reached the implementer for the first time (a validate-timeout does NOT abort — it proceeds; the implementer wrote the full scaffold then timed out). So it's a GENERAL agent_timeout=900 boundary problem with TWO root causes for the same symptom: TE = GENERATION-bound (many dense .test.ts files; the run#8 validate attempt hit the 32K `max_output_tokens` cap — `CLAUDE_CODE_MAX_OUTPUT_TOKENS` is UNSET, args.py:191); implementer = AGENT-LOOP/CONTEXT-bound (~30 turns × high per-turn latency from millions of cache-read tokens on claude-sonnet-4-6). **MEASURED + REFUTED:** I hypothesized cold `pnpm install` dominated the implementer's 900s and would raise the warm-cache (`cache-volume-ownership`) item's priority — but a cold install of the actual scaffold (empty store, no node_modules) is **~11s** (host; the gate Install step also passed within budget earlier). So install is NOT the bottleneck and **the warm-cache item does NOT get a priority bump** (rationale refuted by measurement — the pull-trace lesson again; I nearly recorded a wrong bump). Implementer slowness is turn-count/context, not toolchain. `in=0 out=0` telemetry on timed-out attempts = the parse-on-SIGKILL artifact, NOT zero work (scaffold files prove productivity). API incident (500/503/529) aggravated run #8; `_is_transient` (agents.py:228) only inspects STDERR, so API-5xx (in stdout) + exit-124 timeouts are misclassified as real failures and burn the SA-validate budget (orchestrator.py:3418 counts them as validation attempts). FIX LEVERS (Linus+Codex-gated, not yet built): **P0 transient-classification** (stdout-aware `_is_transient` + don't count transient/timeout as a validation attempt — correct regardless); **per-role timeout bump** (now the most-supported lever; heavy agents legitimately need ~30 turns / >900s); **P1 bound TE output** (split / fix-only-failing-files / don't-re-read-everything + explicit `MAX_OUTPUT_TOKENS`); P0.5 drop the dead-end `Read agents/test-engineer.md` (404s in-container, wastes a turn) + fix the failure-dump mid-truncation (observability). NOT a blind raise; NOT spec-edit remediation (spec is fine). DEEPER lever: reduce TE/implementer turn-count + context bloat (agent efficiency). Run #9 = the clean repro; fix-implementation decision pending.
>
> **bb run #6 ABORTED (2026-06-16) — F Move 1 WORKED, died later.** Validation CLEARED (advisory
> fired, logged-not-blocked — the F Move 1 end-to-end proof) → architect → qa-holdout (7 holdout
> files) → **test-engineer ran 4× incl. a 900s exit-124 TIMEOUT** → terminal ABORT: "scenario
> generation succeeded but no scenario files were written" — DESPITE 13 visible + 7 holdout files
> existing on disk (run #6 mtimes). ROOT CAUSE CONFIRMED (code-grounded, not the "convergence" red herring): the
> test-engineer wrote scenario files as plain `.js` (`fr-1-workspace-install.js` etc.) with NO
> `.test.` infix; `has_scenario_files` (detection.py:20-37 `_TEST_PATTERNS`) correctly rejects
> them (they wouldn't be collected by vitest either), so the empty-retry loop (orchestrator.py
> 3213-3231) deleted them via `_clear_tier2_scenarios` and re-invoked the TE 4× (one 900s
> timeout), then exhausted → abort, DESPITE valid-content files on disk. NOT TE non-convergence —
> a TE output-NAMING-conformance bug whose valid work the cold clear-and-retry loop threw away.
> (The qa-holdout got naming right: `.test.mjs`.) FIX = F Move 2c is the robust answer (warm-
> session: orchestrator pipes "rename to *.test.ts so the runner collects them" into the warm TE
> session → fix in-context, vs cold-regenerating the same mistake 4×). Plus consider tightening
> the TE agent-def naming mandate / a deterministic naming-conformance pre-acceptance check.
> Needs Neo+Codex on the fix design.
>
> **SEQUENCE FROM HERE:** root-cause the run #6 scenario-gen abort (trace → Neo+Codex → fix → re-run)
> → F Move 2a gate-wiring (Linus+Codex) → F Move 2c TE warm-session (Linus+Codex) → F Move 2b
> parallelization (Linus+Codex) → resume bb-from-scratch spec 2 (shared-parse-amount).
> Then the prior roadmap resumes: injection flip (bb generates the evidence) → rust breadth.
> Backlog (unchanged): warm pnpm cache volume, agent-boundary timeout/retry fix (Sprint 33
> observability was the prerequisite), C (reviewer-authored structural-gate feedback), D
> (scenario-lint darkfactory-import barrier), periodic curation re-audit, model-pin resilience.

## ▶ RESUME HERE — state at 2026-06-13 session

> **SPRINT 30 SHIPPED (2026-06-15, merged main `860a0e8`; Neo design APPROVE-WITH-CHANGES
> + Linus ACCEPT + Codex 2-round CLEAN).** Curation-hardening step 2 = **SAST swap, step 2a
> (compare-mode).** A vendored, externally-maintained Semgrep ruleset
> (`darkfactory/review/sast_rules/`, official `semgrep-rules` py91+node31 security packs,
> pinned by SHA `d4143379`) now runs ALONGSIDE the 15 in-house security regexes and writes a
> per-iteration regex-vs-Semgrep **compare artifact** (`{spec}-sast-compare-iter{N}.jsonl`) for
> the offline demote analysis. **ADVISORY ONLY** — gated on `sast_compare_enabled` (default
> off), does NOT feed `security_high_count`/score/`GATE_FAILED`, never inlined into an agent
> prompt; the regexes remain the sole security authority. Key facts the next session needs:
> (1) **The scan is HOST-SIDE** (the prepass is host-in-process, `iteration.py` → `prepass.py`,
> NOT in the agent images) — so Semgrep is a host pip extra `dark-factory[sast]` (sibling of
> the `pglast` SQL-lint), NOT an agent-image dep; **no image rebuild / tinyproxy egress** (the
> original prompt's framing was wrong — corrected by pulling the code; Neo chose host-extra
> "Option H"). Offline/hermetic (`--config` local dir, `--metrics=off`, zero network).
> (2) **py+node only**; go/rust rulesets DEFERRED to the 3rd-stack breadth work (no active
> coverage; unsupported lang → `ran=False`, honest non-scan, never false-clean).
> (3) Findings join the 7 regex categories via **CWE** metadata (standard taxonomy, not bespoke
> per-rule curation); the vendored ruleset is maintained-external → NOT counted by
> `test_curation_budget.py` (`SECURITY_PATTERN_BUDGET` stays 15 — nothing demoted yet).
> (4) `_maybe_run_sast_compare` takes the iteration NUMBER not `iter_state` → structurally can't
> touch the gate (locked by a test); fake-binary wiring test exercises the REAL subprocess (no
> mock = no Sprint-28 bypass). Codex caught + I fixed 2 real safety gaps (rc≥2 engine-error→
> `ran=False`; malformed-JSON-shape→never-raise). **EARLY SIGNAL (toy fixtures only, NOT a
> measurement):** Semgrep OSS under-fires on snippet-level patterns vs the blunt regexes
> (node agree=0/regex_only=6) — exactly what compare-mode exists to quantify on REAL runs.
> **NEXT (step 2b, NOT YET):** turn on `sast_compare_enabled` for real benchmark runs, collect
> ≥30 finding-bearing runs, then per the **demote criteria written down in
> `docs/curation-audit.md`** decide which regexes Semgrep subsumes → the flip to
> Semgrep-authoritative is a SEPARATE authorization-gated step (changes what feeds
> `security_high_count`; needs its own `Audience:` line + at the flip `ran=False`→`-1` not
> silent-clean). Then: periodic curation re-audit; model-pin resilience; python conftest
> DB-reset; THEN breadth (3rd stack). Neo agent: a5d303c4fc7a13b99.

> **NEAR-TERM SEQUENCE (decided 2026-06-15, Caleb) — depth-of-real-project BEFORE breadth.**
> The reframe: sprints 19–30 hardened the autonomous half but were validated only on
> TINY isolated specs (parse-amount, notes-crud, single Tier-A/B/C increments). Two axes
> remain UNPROVEN post-reset: (i) the spec-authoring half (the highest-leverage input,
> least hardened; spec contamination has TWICE invalidated breadth claims — the
> notes-crud-ts §5 import-mandate + the tenant_isolation phrasing); (ii) MULTI-SPEC
> PROJECT ACCUMULATION (every run this campaign was an isolated spec with source reset
> between; the REAL product workflow — build an app spec-by-spec, each building on the
> last — has NOT been exercised post-reset; the benchmark harness resets between specs by
> design, so it can't test this). budget-brain-v2 IS that real multi-spec app, and its
> flagship specs (database-foundation ~37 reports/never-cleanly-shipped, api-bootstrap
> override-only) are the EXACT cases the reset plan opened with. So the honest "move the
> number" is to prove bb on the existing stacks before adding a 3rd language. SEQUENCE:
> 1. **SAST swap finish (step 1 = offline corpus harness → step 2 = demote flip). [STEP 1 DONE +
>    FIRST DEMOTE SHIPPED.]** Corpus harness built (`scripts/sast_corpus_compare.py`, v2 code-only
>    staging + safe/targets split); offline sweep over 7 OSS repos (~480 files) produced the
>    measured per-regex demote evidence in `docs/curation-audit.md` ("Measured demote evidence").
>    **Sprint 31 SHIPPED (merged a659bac, Neo APPROVE + Linus+Codex CLEAN):** deleted the
>    `prototype_pollution` regex (measured 22/22 FP / 0 TP / 0 Semgrep-corroboration — pure noise
>    generator, the S27 confound), budget 15→14, a PURE DE-ESCALATION (no auth-escalation; proto-
>    pollution stays advisory-covered by the vendored Semgrep rules). VERDICTS: KEEP
>    `insecure_deserialization` + `hardcoded_secret` (Semgrep doesn't subsume); the INJECTION
>    categories (sql/command/path/xss) = DEMOTE→Semgrep but those make Semgrep AUTHORITATIVE
>    (audience-escalating) → their own full AUTHORIZATION-MODEL review + `ran=False→-1` guard +
>    owned-surface scoping. **INJECTION FLIP RE-SEQUENCED → DEFERRED PAST bb (Neo ruling DEFER
>    2026-06-15, assistant concurred independently): the corpus measured the SCANNER not the
>    GATE; the flip's gating paths have NEVER fired on a finding-bearing owned-surface run (all
>    4 real runs 0/0/0); the injection regexes are NOT harmful (no urgency) → don't delete a
>    working backstop + add gating power on unmeasured-gate evidence (the S28 bet). bb-from-
>    scratch generates the finding-bearing owned-surface runs as a FREE byproduct = the
>    real-run evidence the original design wanted. Design PRE-BUILT + mechanical RE-EVAL
>    conditions recorded in docs/curation-audit.md flip-sequencing §2.** REVISED ORDER:
>    prototype_pollution demote (DONE) → spec-skill audit → bb-from-scratch (generates evidence)
>    → injection flip (conditions met) → rust breadth. The
>    in-pipeline compare WIRING is validated (4 real runs incl. Tier-C notes-crud-pg, all
>    sast_ran=1 / 0/0/0 — confirming clean impls trip neither scanner, why offline was right). The demote EVIDENCE
>    is a property of the scanners on code, NOT the pipeline → gather it OFFLINE at corpus
>    scale (run analyze_security + run_semgrep + build_compare over a real-code corpus:
>    semgrep-rules' own vuln/safe test targets as ground truth + real OSS py/ts repos +
>    the df repo itself), zero API cost, orders of magnitude more finding-bearing data than
>    ≥30 pipeline runs. Then apply the demote criteria (docs/curation-audit.md) → the FLIP
>    (authorization-gated: Neo + Linus/Codex, Audience line, ran=False→security_high_count=-1)
>    demotes subsumed regexes + lowers SECURITY_PATTERN_BUDGET. (Why offline: clean impls
>    produce 0/0/0 — the 3 validation runs all did — so the regex-FP signal is rare per
>    live run; a static corpus sweep is the right instrument.)
> 2. **Spec-skill audit (read-only, cheap) = the pre-bb AND pre-breadth gate.** Audit
>    /df-write-spec /df-write-plan /df-densify-spec /df-validate-spec /df-write-feedback:
>    (a) contamination guard — do they produce framework-/stack-AGNOSTIC specs, and is the
>    §5-style-leak catch real? (load-bearing); (b) is the 100%-completeness + Precision-
>    Analysis standard ENFORCED by the skill or just advisory prose? (c) is /df-densify-spec
>    (Phase-4 Yassine pivot) load-bearing or vestigial cruft like SCENARIO_ARCHITECT →
>    deprecate if not pulling weight; (d) deprecation hygiene on /df-implement +
>    /df-generate-scenarios (redirect-only, no stale behavior). Refactor ONLY what the audit
>    flags. The REAL validation of the skills is then USING them to clean bb's spec chain (3).
>    **AUDIT DONE (2026-06-15, agent a7d146a84e4094e48; key claims verified independently):**
>    (a) CONTAMINATION GUARD = **ABSENT** (the load-bearing finding). df-write-spec's "no impl
>    details" prose targets TEST-mechanism leakage, not dark-factory-tooling contamination
>    (import mandates, agent roles, pipeline history); the ONLY deterministic spec-text scanner
>    (`darkfactory/audit/sanitizer.py`) catches prompt-injection/jailbreak only (role_injection,
>    barrier_circumvention) — it would NOT flag the `notes-crud-ts §5` import-mandate or the
>    "tenant isolation" Out-of-Scope phrase. Contamination prevention rests entirely on LLM
>    author discipline, and **df-densify-spec's prose pushes the OTHER way** (mandates
>    mechanism-full detail: exact tools/paths/commands). → FIX (pre-bb, highest value): add an
>    explicit framework-/stack-agnostic checklist rule to df-write-spec + a DETERMINISTIC
>    ADVISORY precheck regex (flags spec BODY referencing pipeline internals: `implementer|test
>    engineer|qa holdout|\.dark-factory/|import (must|mandate)|barrier|scenario architect`) —
>    would have caught BOTH prior incidents at authoring time. (b) COMPLETENESS/PRECISION =
>    **MIXED**: precision-HIGH blocks but ONLY via the LLM validator's verdict (precision.py:13
>    + precheck.py:1669 "advisory only, never blocks"; non-deterministic = the meta-agent
>    variance the campaign distrusts); the "100% completeness" bar is ADVISORY (spec-validator.md:682:
>    a 90/100 spec proceeds). CLAUDE.md's "must score 100%" claim is INACCURATE → fix the doc to
>    match the real gate (blockers/divergence/consistency, not score). (c) df-densify-spec =
>    **VESTIGIAL / NEEDS-REWORK**: NOT chained from df-cycle/df-pipeline-run/df-write-spec
>    (parallel, not required), untested across stacks, premised on the disabled
>    p4_mechanism_token_gate, and its 30-50-AC mechanism-full mandate + auto-injected postgres
>    §4a is the LARGEST contamination surface in the skill set → **for bb, use df-write-spec NOT
>    df-densify-spec**; deprecate-or-rework densify separately. (d) DEPRECATION HYGIENE = **CLEAN**
>    (df-implement + df-generate-scenarios are pure redirect stubs, no stale behavior).
>    PRE-BB ACTION = the (a) contamination guard (checklist rule + advisory precheck regex) —
>    a small framework change (Neo + Linus/Codex); (b) is a CLAUDE.md doc fix.
>    **SKILL-SURFACE CLEANUP (Sprint 32, 2026-06-15 — Caleb-confirmed): 11→7 ACTIVE skills.**
>    DELETED 3 (no consumers, verified): `df-implement` + `df-generate-scenarios` (pure CLI-redirect
>    tombstones), `df-cycle` (redundant with df-pipeline-run + `dark-factory run`; its "multi-session"
>    premise is stale — the CLI orchestrates the separate containers itself). **DEPRECATED-IN-PLACE
>    (not deleted): `df-densify-spec`** — the dual-gate (Linus C1 + Codex P2) caught that a hard
>    delete broke `tests/reproducibility/` (3 non-llm tests): it's not pure cruft, it's a
>    contradiction-typology reproducibility MEASUREMENT SURFACE in a Neo-designed harness
>    (`build_densifier_prompt`/`parse_densifier_output`). So per the audit's actual recommendation
>    (vestigial-DEPRECATE), it's banner-deprecated (out of the AUTHORING path — `[DEPRECATED]`
>    description + a do-not-use note redirecting to df-write-spec) but retained for the harness. KEPT
>    ACTIVE (7, the real workflow): df-write-hints, df-write-plan, df-write-spec (+ contamination
>    guard), df-validate-spec, df-pipeline-run, df-run-quality-gate, df-write-feedback (MARGINAL —
>    superseded by the autonomous loop; later "is manual iteration real?" check). **T1–T5 typology
>    is NOT lost regardless:** canonical SSOT at `darkfactory/contracts/contradiction_typology.md`,
>    inlined into the spec-validator agent (production validation-time consumer), guarded by
>    `test_consistency_status.py::TestTypologySSOTSync`. LESSON (re-learned): deleting a skill has
>    wider blast radius than the unit suite shows — `tests/reproducibility/` is a separate test root
>    not under `tests/unit/`; run BOTH. Full hard-delete of densify would need a separate
>    harness-rework (excise the densifier surface) — deferred, low priority.
> 3. **bb FROM SCRATCH, incrementally — the campaign's integration test / climax.** Reset bb
>    source to initial, clean each spec with the new skills, run the HARDENED pipeline
>    spec-by-spec in dependency order (monorepo-tooling → api-bootstrap → database-foundation
>    → transaction-management → …). NOT big-bang: stop at the FIRST hard failure and convert
>    it to a sprint (fresh trace → fix), don't burn budget pushing through. This is the
>    DIFFERENT mode the benchmark can't test (accumulation, not isolation) and the REAL
>    product workflow. Success on database-foundation = the single biggest proof of the whole
>    reset; re-failure = the next real bottleneck, honestly surfaced. Substrate already proven
>    on bb (pg/drizzle/S28-29 DB reset via notes-crud-pg-ts) → cheapest possible real-project
>    test, no new infra. COST-GATE: spec-by-spec, stop-at-first-failure.
> 4. **THEN breadth (rust/go)** — now with a real multi-spec project ship behind us, clean
>    spec skills, and the curation-count metric ready to grade the new stack.
> Housekeeping: sast_compare_enabled is COMMITTED ON in both fixtures (bb e0c6fc3, py
> 3deb3a6) so every future benchmark run passively accumulates compare artifacts free; the
> framework default stays off (only fixtures opt in).

**🎯 RATE MOVED 86.7% → 100% (15/15) via Sprints 26+27 — VALIDATED by re-measure.** Post-26/27 TS
re-measure (`docs/benchmark/scorecard-ts-26-27.jsonl`, 2-point N=3): **ts-A shared-parse-amount 3/3 +
ts-B notes-crud-ts 3/3, ALL six clean on iteration 1** (was ts-A 2/3 w/ a 3-iter escalation, ts-B 2/3
w/ a 1-iter abort). Combined with python 9/9 (TS-only fixes don't touch py paths → the full-matrix py
result stands): **honest clean-ship rate = 15/15 = 100% across 2 stacks × 3 tiers.** Sprint 27 ALSO
lifted ts-B (its escalation had carried the same health.ts scope-leak, SECURITY_HIGH_COUNT=1) — confirmed:
ts-B now 3/3 all iter-1. Bonus: faster convergence (iter-1 vs iter-3/4 before) — the implementer no
longer burns iterations on un-fixable out-of-scope feedback. NEXT FRONTIER = BREADTH (Tier-C on TS, then
a 3rd stack), then the speed backlog. Honesty caveat: py 9/9 is from the 2026-06-13 full matrix (not
re-run post-26/27, but those changes are TS-only); a clean full-matrix N=3 would make the 15/15 a single
artifact if desired.

**RE-MEASURE DONE (2026-06-13) — the north-star number is now MEASURED on the hardened framework.**
Full-matrix N=3 (5 points, SHIP_WITH_GAPS/escalated/aborted = non-ship), scorecard at
`docs/benchmark/scorecard-2026-06-12.jsonl`, battery `/tmp/remeasure-2026-06-12-battery.toml`:
- **13/15 = 86.7% clean-ship.** PYTHON = **9/9** across all three tiers (parse-amount-py 3/3,
  notes-crud 3/3, notes-crud-pg 3/3 — Tier-C Postgres now confirmed at N=3, was N=1). The S19–25
  hardening HELD aggregate.
- The only 2 non-clean were BOTH TypeScript, both intermittent, INDEPENDENTLY rooted (fresh traces
  named the bottlenecks — exactly what the re-measure was for):
  - **ts-B notes-crud-ts 2/3 — 1 ABORT, deterministic framework bug → FIXED (Sprint 26).**
    `_generate_declare_module_stubs` emitted invalid `export const default` (TS1389) when the
    architect declared a `default` export (idiomatic Fastify `export default fp(...)`) → scenario
    typecheck failed 3× → abort. Intermittent only because the architect (LLM) declares `default`
    ~1/3 of the time. Archive `d6af5fb9`.
  - **ts-A shared-parse-amount 2/3 — 1 ESCALATED, reviewer scope-leak → FIXED (Sprint 27).** 40/40
    scenarios passed every iter; Oracle NO-SHIPped on a HIGH prototype-pollution finding in
    `apps/api/src/common/health.ts:15`. INVESTIGATION (all 3 runs): the DETERMINISTIC review-prepass
    (`prepass.py: analyze_security`) scans the WHOLE repo and flags that PRE-EXISTING scaffold (verified:
    untouched by the spec's patch) in ALL 3 runs; the LLM reviewer itself calls it "unrelated/out-of-
    scope", but the deterministic HIGH + docked score still reach the Oracle, which NON-deterministically
    escalates (1/3). So detection is deterministic; the variance is the ORACLE's response to an
    out-of-scope finding. DISTINCT from the 0c14a85e reframe (implementer-INTRODUCED vuln = correct
    block). Archive `cb1eb82a`.

**Shipped 2026-06-13:**
- **Sprint 26 (faad9fb) — valid TS for default-export entry-point stubs.** Neo design
  APPROVE-WITH-CHANGES (both applied: regression test + value-shaped-default comment); Linus + Codex
  code gate BOTH CLEAN (Linus verified FAIL→PASS against real tsc 5.9.3). Node-gated only, no
  auth-model trigger (de-escalation, stub already Implementer-readable). `default` is the only
  reserved word legal as an export name → special-case set is complete.
- **Sprint 27 (d8bf71b) — scope prepass ship-gating findings to the run's owned surface.** Fixes the
  ts-A escalation: the prepass now demotes findings OUTSIDE the run's owned surface (architect file_map
  create/modify ∪ implementer diff) to ADVISORY — reported, but excluded from the score AND from
  `security_high_count` (the Oracle gate input). So pre-existing untouched code can't ship-gate a run.
  Both security AND code-quality scoped. Load-bearing invariant: a vuln the implementer INTRODUCES is
  always in the diff → always gating (`changed_paths` FAILS CLOSED: any git failure → owned surface
  None → everything gating, never a partial surface). Fail-safe: missing/unparseable manifest → None →
  everything gating. Neo APPROVE-WITH-CHANGES (8 items applied); Linus+Codex caught a partial-surface
  hole round 1 (changed_paths must fail closed) → fixed → BOTH CLEAN round 2. `Audience:` line in commit
  (de-escalation, gating set shrinks). NOT yet full-pipeline re-validated (unit + dual-gate-traced; a
  shared-parse-amount ×N re-run would confirm the escalation is gone + give a fresh ts-A rate).
- VALIDATION (Sprint 26): notes-crud-ts ×N re-run confirmed NO abort (Sprint 26 working); the architect
  picked NAMED exports (not `default`) so the fix's branch wasn't exercised end-to-end — but the unit +
  real-tsc proof stands. Runs grind 2–3 iters on the known TS-B implementer-CRUD variance.

**Shipped previous session (7 sprints, all Neo design + Linus+Codex dual-gate, merged main; full
records in the dated entries below):**
- Sprint 19 (eab6fef) hermetic env-contract reset · Sprint 20 (467bf73) oracle ship-gate parser
  hardening · Sprint 21 (5d7be87) deterministic DB driver↔URL-scheme preflight · Sprint 22
  (eb58398) python dep-install at the Install stage (pre-Setup) · Sprint 23 (1f32bfc) non-root
  user-site-bin on PATH (python/playwright; rust/go deferred) · Sprint 24/B2 (55fb8fd) setup
  failures routed OUT of the code trajectory + setup-directed feedback · Sprint 25 A0 (d11cb8a)
  capture pytest collection errors + A1 (c119891) route mass-collection-drop to import-surface
  feedback (top-of-loop snapshot counter).
- BARRIER AUDIT of the whole session: CLEAN (independent review). 2 non-blocking hardening notes.
- Tier-C python DB (notes-crud-pg) reached CLEAN SHIP end-to-end.

**IN PROGRESS — TIER-C ON TS (breadth de-risk #1):** authored + validated (100/100) + committed
`notes-crud-pg-ts` (Postgres notes CRUD, TS/Fastify/drizzle; bb fixture pin `0e02654`) — the TS analogue
of py `notes-crud-pg`. De-risk run #1 → **ESCALATED (3 iters, pass oscillated 59→50→63%)**, BUT the
substrate WORKS (postgres up, drizzle migration applied, 22 scenarios ran, 0 crashes) and Sprint 27 holds
(SECURITY_HIGH_COUNT=0 all iters). ROOT CAUSE (trace, archive `6abd8674`): **TE-generated brittle id
assertions on a DB spec.** The TE's `beforeEach` clears rows with `db.delete(notesTable)` — which does NOT
reset Postgres's identity sequence — yet asserts LITERAL ids (`id:1` on a "fresh" table, `toEqual([{id:1},
{id:2}])`). Sequence climbs across tests → every absolute-id assertion fails; the implementer CAN'T fix it
(tests are the TE's barrier-isolated code). Why py shipped same spec: **py's TE wrote ROBUST RELATIVE id
assertions** (captures returned id, compares relatively; literal `id:1` only in docstrings) — same
plain-DELETE isolation, but assertion style differs. So this is **TE scenario-generation reliability on
DB-backed specs** (no guidance that absolute-id assertions are unsafe unless isolation does TRUNCATE …
RESTART IDENTITY). DETERMINISM CHECK DONE: **3/3 ESCALATED** (tee-up 3 iters 59→50→63%; det#1 2 iters 61→61%; det#2 3 iters
50→13→50%) — all crashed=0, all high_count=0. DETERMINISTIC DB-test-isolation gap (NOT variance, despite
py shipping — see below). FIX DESIGN — Neo APPROVE-WITH-CHANGES (general cross-stack contract, NOT a TS one-off; barrier-sensitive):
a HYBRID — adapter-staged reset helper (mechanism) AUTO-INVOKED via the framework-generated test-runner
config (determinism, no TE reliance). Pure "steer the TE to call it" is rejected (that's the surface that
failed 3/3). Concrete shape Neo specified:
  • Helper lives in the postgres adapter `contracts_dir()` (staged to `_contracts/postgres/`); does a SINGLE
    `TRUNCATE <all> RESTART IDENTITY CASCADE`.
  • TABLE LIST from the LIVE pg catalog at reset time (`pg_tables WHERE schemaname='public'`, EXCLUDE
    migration-tracking tables `__drizzle*`/`alembic_version`), NOT the manifest — `DataModel.name`
    (manifest.py:100) is a LOGICAL name, not the physical relname, so manifest-derivation silently no-ops.
  • AUTO-INVOKE via the ALREADY-EXISTING vitest-wrapper generator `scripts/lib/scenarios.sh`
    (`_df_resolve_vitest_config`, ~226-267 mergeConfig) → add `setupFiles` pointing at the staged reset
    module; python gets a staged pytest autouse conftest. FACTOR reset-SQL (postgres adapter) SEPARATELY
    from setupFiles-wiring (language adapter/gate harness) — neither learns the other's domain.
  • GATE on the existing `spec_triggers` postgres-active condition (adapter_staging.py:111) so non-DB specs
    get NO reset injection. Reuse, don't add a parallel gate.
  • CONNECTION: framework-owned raw from `DATABASE_URL` (postgres.js for the TS setupFile, psycopg for the
    py conftest — same pattern as verify_substrate adapter.py:505); open once / close afterAll (pool).
  • Correctness must-haves: single-statement CASCADE truncate (FK cycles); exclude tracking tables (else
    re-break migration state); `lock_timeout` so a TE-held tx can't trade the oscillation for a HANG.
  • Generalizes: makes PYTHON deterministic too (not TE-luck) + future MySQL/SQLite adapters (mechanism only).
  • Barrier: no audience escalation (catalog-derived, no holdout/reviewer strings); add `Audience:` line
    (changed-surface: new artifact under .dark-factory/). Governance: no ABC change → no deprecation clock.
Why py shipped same spec: py's TE wrote robust RELATIVE id assertions so plain-DELETE sufficed; the fix
removes that TE-luck dependency. Scorecard `scorecard-tier-c-ts-determinism.jsonl`; archives 6abd8674 /
75a4b5db / a9bcc9b4; Neo agent ae3419eafb59d7f86.

⚠️ IMPLEMENTATION BLOCKER FOUND (2026-06-14, pre-coding reality check — NOT yet resolved): Neo's "node
vitest setupFile opens its OWN pg client from DATABASE_URL" has NO GUARANTEED RUNTIME DRIVER in the node
gate. Evidence: (1) the notes-crud-pg-ts impl used `drizzle-orm/node-postgres` + `import pg from 'pg'` —
the runtime driver is a SPEC FREE VARIABLE (`pg` vs `postgres.js` vs …); (2) the existing postgres
contract helpers import `postgres` (postgres.js) but TYPE-ONLY (erased) — they never needed a runtime
client; (3) the postgres contracts package.json has NO deps and contract deps are NOT installed; (4) the
node Dockerfile bakes no psql/python3, and quality-gate.sh treats `psql` as best-effort/optional
(node+pg projects use a JS client, not psycopg). So a framework-injected node setupFile can't reliably
`import` a client to run TRUNCATE. RESOLUTION OPTIONS (need Neo round-2 + decision): (A) framework ENSURES
a known driver is installed for postgres-TS specs (Sprint-15 test-extras precedent: detect.py installed
httpx for py) and the reset setupFile imports THAT (e.g. always-install `pg`, reset opens `new pg.Client`
— independent of impl ORM); (B) bundle `pg` in the postgres contracts package.json + install contract
deps into `_contracts/` (node_modules-resolution fiddly); (C) different mechanism entirely. (A) is the
cleanest/precedented but adds a framework-managed test dep (pnpm interaction).

✅ RESOLVED — SPRINT 28 SHIPPED (merged main dddba3e). Neo round-2 REJECTED (A)-as-default in favor of
**(E) driver-ADAPTIVE setupFile, ZERO project-dep mutation**: the impl must have installed SOME pg driver
(the app needs it), so the framework setupFile resolves whichever (`postgres.js` OR `pg`) via
`createRequire(DF_RESET_RESOLVE_BASE).resolve()` + `import(fileURL)` (ESM/CJS-safe) from a gate-provided
base. SHIPPED: catalog-driven `_df_reset.sql` (TRUNCATE public RESTART IDENTITY CASCADE, excl migration
tables, lock_timeout, single source); `_df_reset.setup.ts` (vitest beforeEach, one conn, afterAll close);
scripts/lib/scenarios.sh injects it into the generated wrapper's setupFiles + `fileParallelism=false`
(serialize files — the shared-DB race) for postgres-active specs ONLY (non-postgres byte-identical);
pyproject ships `*.sql`. DUAL-GATE earned its keep (2 rounds): Linus B1 (the .ts was globbed by the
contracts tsconfig → tsc-in-Docker break; excluded it, verified via npx tsc) + Codex 2× P1 (string-
setupFiles shatter on spread → normalize; vitest parallel test FILES race the shared-DB TRUNCATE →
fileParallelism=false) — all fixed → both CLEAN round 2. Reset-SQL factored in the postgres adapter,
setupFiles wiring in the gate harness (cross-stack-clean). Tests: 45 shell + postgres-adapter/smoke/
packaging. FOLLOW-UP (noted, not blocking): python conftest path to make python deterministic vs TE-luck
(reuses the same .sql). VALIDATION run #1 ESCALATED but is CONFOUNDED — NOT a clean Sprint-28 test; stopped the battery (runs 2&3
same confounds). Trace (archive 2e5586ab): THREE tangled issues, none cleanly Sprint-28:
  (1) SPEC CONFOUND I introduced — the out-of-scope phrase "tenant isolation" FALSE-triggers the postgres
      `tenant_isolation` substrate rule (`triggered_rules` regex), activating RLS/tenant machinery + likely
      the tenant contract-helper-usage requirement. FIX: reword the spec's Out-of-Scope (avoid "tenant
      isolation"/"multi-tenant") so it doesn't false-trigger. (Also worth: spec_triggers matching an
      OUT-OF-SCOPE mention is itself a framework FP — backlog.)
  (2) SUBSTRATE friction (separate Tier-C-TS bottleneck) — iters 1&2 Setup FAIL `error: unknown command
      'migrate'`: the architect/impl ran `drizzle-kit migrate` but the installed drizzle-kit version lacks
      that subcommand (newer = generate+migrate; older = push/up). Ate the iteration budget before
      scenarios ran (analogous to python's alembic-ordering Tier-C friction). NEEDS its own fix.
  (3) Sprint-28 EFFECTIVENESS UNVERIFIED — only iter-3 ran scenarios (27, 55%); failures were a BROAD mix
      (basic AC-1 create + FR-5 input-validation, not just id-state), and the TE ALSO wrote its own
      `db.delete` beforeEach (so two reset hooks). Could not confirm the reset's TRUNCATE executed end-to-
      end. Need ISOLATED ground truth (does the staged setupFile load, resolve the driver, run the SQL
      against the live sidecar) — not buried in a confounded full run.
⚠️→✅ ROOT CAUSE of the confound FOUND + SPRINT 29 SHIPPED (merged 3f68288). The "Sprint 28 reset never
fired" mystery (vitest `setup 0ms`, id-24/28 contamination) was NOT the barrier and NOT a structural wall:
the reset shipped via `stage_adapter_contracts`, gated at orchestrator.py on `if substrate_manifest.is_file()`
— the architect's RLS/tenant manifest, which a plain Postgres CRUD spec NEVER emits. So staging was skipped
→ reset never staged/ran. My S28 tests passed because they called staging directly, BYPASSING that
orchestrator gate (the coverage gap). [This was surfaced by having neo:neo + codex review my barrier-friction
analysis — both independently found `setup 0ms` + the wrong gate; my "barriers convert one-agent-fixes into
mechanisms" conclusion was OVERSTATED (durable finding in [[project_barrier_friction_analysis]]).] SPRINT 29 (Neo r1 abbba5a/r2
design; Linus+Codex 2-round dual-gate, Codex P1 + Linus wiring-test caught & fixed → both CLEAN): DECOUPLE
the DB reset (gate test-infra) from the RLS-contract staging — moved `_df_reset.*` to a dedicated
`adapters/postgres/setup/ts` + `db_reset_setup_dir()` hook; new `stage_db_reset` stages under `_df_setup/`
(sibling of `_contracts/`, invisible to Check 18) gated on `db_reset_active(config)` (a service injects
DATABASE_URL — symmetric with the gate's own DATABASE_URL preflight, deterministic, NOT the substrate
manifest); scenarios.sh dual-guards on `-f reset && -n DATABASE_URL`; `_clear_tier2_scenarios` preserves
`_df_setup` across TE retries (Codex P1); structural wiring test locks the gate. COVERAGE GAP CLOSED (tests
now exercise the orchestrator activation decision, not direct staging).

🎉 TIER-C-TS CLEAN SHIP (2026-06-15): notes-crud-pg-ts → clean_ship (exit 0, iter 2, model
claude-sonnet-4-6, $10.31, scorecard-tier-c-ts-verify.jsonl). SPRINT 29 VALIDATED END-TO-END: blind
scenarios 100% (26/26, 0 crashed) with the per-test `TRUNCATE … RESTART IDENTITY` reset firing (`_df_setup`
staged, no id contamination) — the DB-isolation blocker is solved. Substrate works (Setup PASS — the
`drizzle-kit migrate` friction was CONVERGENCE COST, impl fixed it; no lockfile fail), de-confounded spec
gave no tenant noise, build/lint/typecheck/import-contract all PASS; the only multi-iter friction was the
implementer's own Self-Tests (ordinary impl-quality convergence, fixed by iter 2 — NOT a framework gap).
**TIER-C now clean-ships on BOTH stacks (py + TS) → depth-without-breadth discharged through Tier-C.** The
fix chain that got here: barrier-friction analysis → root-caused "reset never fired" = wrong staging gate
(substrate-manifest) → Sprint 29 decoupled it onto the DATABASE_URL signal → model-retirement hotfix
(claude-sonnet-4-20250514 → claude-sonnet-4-6, 01e90c5) → de-confound spec + resolve the non-integer-:id
contract the new stricter validator caught. The drizzle-migrate/lockfile frictions self-resolved (impl
convergence), not needing the catalogue-vs-rehearsal decision after all.

RATE PROVENANCE (Caleb decision 2026-06-15): full re-measure on the new model is SKIPPED for now. The
15/15 (86.7%→100%) was measured on the RETIRED claude-sonnet-4-20250514; we now treat it as the ASSUMED
working baseline on claude-sonnet-4-6, backed by ONE live-model confirmation (the Tier-C-TS clean ship
above) — NOT a fresh measurement. Honesty rule: cite it as "assumed (1 live-model point)", not "measured",
until a spot/full re-measure refreshes it. Trigger a full re-measure if any spot test (or normal run)
regresses, or before any external claim. (Cost/time tradeoff: ~18-run full matrix deferred.)

NEXT MOVE = breadth + hardening, re-measure deferred. Sequence (Neo+Codex consensus, minus re-measure):
PREP (cheap): model-pin RESILIENCE (fail-fast/track-current so a dated ID can't silently break every agent
again — the staleness liability that ate this session). THEN choose between: (A) BREADTH — a 3rd stack
(rust/go; gated on cache-volume-ownership infra) = the north-star move + the real test of the generalization
thesis; (B) CURATION HARDENING — SAST swap (advisory-first) + curation-count metric + labeled rule audit =
de-risks future stacks, addresses the curation drift, makes the 3rd stack's litmus measurable. Doing (B)
before (A) lets the curation-count metric grade the 3rd stack; doing (A) first chases the number. Spot-check
existing stacks opportunistically (a single Tier-A run per stack) rather than the full matrix. Neo agents:
ae3419eafb59d7f86, a54ebf38f4ebaf99b, abbba5a95f7b27204 (Sprint 29); aa6fbd4dfc0dbcc12 (scalability policy).

**NEXT MOVE:** both named ts bottlenecks FIXED (S26 abort, S27 scope-leak); RE-MEASURE confirmed rate
86.7%→100% (15/15). Current frontier = the Tier-C-on-TS de-risk above (determinism check → then fix or
accept). After Tier-C TS lands: a 3rd stack, then the speed backlog. The benchmark battery is a real,
version-controlled asset (notes-crud-pg `fed9741`; notes-crud-pg-ts `0e02654`; bb baseline `2e5e0d7`).

**BACKLOG (low priority, defense-in-depth — do NOT prioritize over the named bottleneck / breadth):**
- **Pipeline speed (investigated 2026-06-13, grounded in real per-agent wall-times; LOWER priority
  than TS-A reviewer-scope + breadth).** A run is ~45–52min, almost all LLM agent wall-time (setup
  overhead minor); per-iter cost ~15–20min, dominated by implementer (~210s/call, 1011s on a 2-iter
  py ship) + the gate's test execution (180–360s/iter). Ranked levers, each a full-gate change:
  (1) **Parallelize the pre-iteration test-gen fan-out** test-engineer ∥ qa-holdout ∥
  proposal-rehearsal — all consume only the architect manifest, write disjoint dirs
  (scenarios/holdouts/reports), mutually independent, each already its own isolated container
  (parallelism PRESERVES the barrier). Serial ~640s → concurrent ~max(346s) ≈ **−5min EVERY run**.
  Needs a host resource-contention check (concurrent docker runs). (2) **Skip/cache substrate-owner**
  — it ran ALL 3 iters on the substrate-LESS pure-function TS-A producing empty prep (375s pure
  waste); reuse the Sprint-8 "architect modeled no substrate → SKIP" gate on the AGENT invocation +
  run-once-reuse across iters unless the manifest changed (≈ −4–6min multi-iter). (3) Container/
  session reuse for per-iter agents (cold-start ~10–23s × ~19 calls ≈ −3–5min; impl pre-gate already
  reuses). NOTE already-optimal (don't touch): agents already on Sonnet-4 (no cheap downgrade left),
  prompt caching active, early-exit guards comprehensive. BIGGEST lever is FEWER ITERATIONS i.e.
  reliability == speed (each avoided NO-SHIP cycle ≈ −15–20min) — already covered by the TS-A work.
- Sprint 25 B (testability): extend spec-validator + df-write-spec anti-pattern table to flag
  module-scope import-time resource construction (create_engine/connect/client/file at module
  scope) + a one-line architect constraint. NEO CAVEAT: structurally INCOMPLETE — the validator
  only sees spec-described side effects; the a13bfc6f create_engine was an implementer choice
  invisible to the spec. A0+A1 are the real net; B is cheap-but-partial.
- Cache-volume-ownership (Sprint 23 deferral): pre-own df-cargo-cache/df-go-cache (df-pnpm-store
  pattern: Dockerfile.base chown + copy-on-first-mount) so rust/go non-root user-site installs work.
  No active rust/go breadth coverage today.
- Orphan-cleanup gap (found 2026-06-13): the benchmark runner's orphan cleanup filters
  `docker ps -a --filter label=df-run-id`, which MISSES the egress-proxy containers (`df-<id>-proxy-*`)
  and their networks (`df-<id>-{analysis,execution,external}`) — they accumulate across runs (found 17
  proxies + 18 networks lingering after a killed run). Fix: cleanup should also target `name=df-` proxy
  containers (rm -f) before removing the networks. Trivial; do alongside other runner hardening.
- 2 barrier-audit hardening notes: (1) run_db_driver_preflight is the one summary writer skipping
  _sanitize_output (safe today — closed content); (2) audit_history_for_barrier_leak's scenarios_dir
  param is unused.
- Standing debt (pre-session): env-contract/agent-def sync, spec-authoring skill battery,
  cost-from-telemetry ([[project_cost_accounting_estimate_at_cap]]), integration-suite repair
  ([[project_integration_suite_broken_run_regression]]).

**Meta-agent reliability REFRAME (durable finding):** "green gate but escalated" runs are NOT
meta-agent bugs — they're the reviewer/oracle CORRECTLY blocking real defects the blind scenarios
miss (e.g. 0c14a85e: a real prototype-pollution vuln); the system holds. The deterministic levers
were B2 (setup-failure misrouting, Sprint 24) and the scenario-count variance (Sprint 25 A0/A1) —
both shipped. A softer, harder residual: convergence gap on reviewer-found NON-scenario defects
(implementer can't fix security/state-isolation the scenarios miss → honest escalation).

## Why this plan exists

dark-factory is a spec-driven, information-barrier autonomous-SDLC framework
(~62K LOC Python, 11 wired agents, 12-step pipeline, ~3,800 tests). The
**goal** (owner-confirmed) is a **reliable framework that ships real features
across projects and languages**. The Yassine 11-agent/9-phase pipeline in
`docs/goals/` is *inspiration*, not a spec to match. Current **purpose** is
personal R&D — SaaS/GTM gaps in `docs/business-plan.md` are out of scope.

### The diagnosis this plan responds to

Evidence from ~130 run reports in `docs/pipeline-run-reports/`:

- Exactly **one** clean autonomous ship, ever: `shared-parse-amount` (a single
  pure function, 100% on iter 1).
- `api-bootstrap` shipped only via the `SHIP_WITH_GAPS` override (85–97%;
  Oracle said NO-SHIP).
- `database-foundation` (flagship): ~37 reports over weeks, **never cleanly
  shipped**. Its final "shipped" status was `SHIP_WITH_GAPS` with substrate
  verification *permanently failing*, against a **self-contradicting spec**
  (FR-3 vs FR-1/AC-5/AC-24). The report retracts its own "win" framing.
- **Every** run report targets **one** project (`budget-brain-v2`) on **one**
  stack (TypeScript + Postgres/drizzle). The `python_fastapi` adapter has zero
  run-report evidence it has ever completed a pipeline.
- Recurring failure modes: `SHIP_WITH_GAPS` masks failure as success; the
  escalation agent **hallucinates** (fabricated scores/AC-IDs across runs); the
  barrier audit **false-positives** on legitimate import strings; the
  implementer ignored feedback inlined 3×; the spec-validator emits 0
  contradictions on 2/5 runs at temperature 0.

**Core thesis:** the framework has been hardened in **depth** against **one**
fixture while never establishing it generalizes to a **second**. Depth without
breadth. And the success metric (`SHIP_WITH_GAPS`) cannot distinguish "shipped
working" from "gave up cleanly."

## North star

A single, honest **reliability number**: clean-ship rate across a fixed,
version-controlled benchmark battery spanning **≥2 stacks**, each spec run
**≥3×** for determinism, with `SHIP_WITH_GAPS` counted as a **non-ship**.
"Back on track" = the battery exists, a baseline is measured, and the number
moves up *honestly*.

**The spine:** honest outcome → measure → breadth → spec gate → trustworthy
meta-agents → move the number.

---

## Generalization thesis & curation policy (2026-06-15, Neo+Codex consensus)

> Recorded after Caleb asked whether the per-stack substrate/lint/security rules
> require us to catalogue every stack's gotchas (= unscalable). neo:neo +
> codex:codex independently reviewed and CONVERGED. This section is the standing
> answer + policy. (Self-correction logged: I'd mis-attributed the campaign's
> prototype-pollution catch to the LLM reviewer — it is a CURATED REGEX
> (`darkfactory/review/patterns.py`) with the LLM EXPLICITLY MUZZLED on security
> at the ship gate (`iteration.py:5655`, "AUTHORITATIVE Deterministic Security
> Verdict"). So "lean on the LLM" is the wrong lesson — see thesis point 3.)

**THE THESIS (what "generalized framework" means here).** Generality does NOT
come from the framework *knowing* every ecosystem's footguns. It comes from
**orchestrating each ecosystem's own externally-maintained verification tools
(compiler, type-checker, linter, test-runner, SAST) as ground-truth gates inside
the autonomy spec→test→impl loop.** The stack knowledge scales because it lives
in those tools, maintained by their communities and amortized across all their
users — exactly why we use the stack's compiler instead of writing our own. So:
adapters stay THIN (detect + wire the stack's tools, NOT re-encode its gotchas),
and our irreducible hand-curation should asymptote to the AUTONOMY-PROBLEM
mechanics (barriers, the loop, per-test substrate isolation, gate orchestration)
— not stack gotchas. The generality is in the orchestration + autonomy machinery,
not in an encoded catalogue.

**THREE DETECTION LAYERS** (decreasing scalability / increasing curation):
1. Generic ground-truth gates (build/lint/typecheck/blind-scenarios-vs-live-
   sidecar) — stack-parameterized, ZERO gotcha-knowledge. Catches most bugs. Scales.
2. LLM verifiers (reviewer/oracle) — broad latent knowledge, but NON-DETERMINISTIC,
   so they are SENSORS/EXPLAINERS, never FINAL AUTHORITIES at a ship gate (the
   no-human pipeline can't tolerate non-determinism at gates; the framework has
   repeatedly demoted LLM judgment — Sprints 16/20/27). Use them to DISCOVER
   candidate issues; convert findings into deterministic checks before they gate.
3. Curated deterministic rules — the backstop for the narrow class that PASSES
   the generic gates + is INVISIBLE to app-level tests (e.g. RLS owner-bypass) +
   is LLM-unreliable. NB: maintained external SAST and bespoke in-house regex are
   BOTH layer 3 but have opposite scalability — the axis to minimize is
   **bespoke CURATION (hand-encoded stack knowledge)**, NOT deterministic-check
   count; and some "rules" (substrate-rule adapter contracts) feed live
   verification and are legitimate adapter semantics, not denylist bloat.

**STANDING DECISION RULE (for any new friction).** Prefer, in order:
(1) make a live ground-truth gate catch it → (2) use a maintained AST/SAST tool →
(3) convert an LLM finding into a deterministic repro/check → (4) add a bespoke
curated rule ONLY when none of the above can. Treat **bespoke curation as the
budgeted scarce resource.** LLM stays advisory at ship gates by design; promoting
it to authority requires an explicit `docs/AUTHORIZATION-MODEL.md` review.

**COURSE OF ACTION (sequenced).**
- NOW (cheap, doesn't displace breadth): this section (thesis+rule recorded);
  the narrow `tenant_isolation` trigger FP (stop scanning Out-of-Scope/non-
  normative spec sections for triggers) is a backlog item; **stay focused on
  Tier-C-TS** — N=2 substrate is currently PASSING its own litmus (3 rules, not
  dozens), so this is a standing policy, not an active fire.
- FIRST APPLICATION / ALIGNMENT TEST: the Tier-C-TS `drizzle-kit migrate`
  friction MUST be fixed the ground-truth/rehearsal way (rehearse the setup
  command, let the tool's own error surface + feed back) — NOT by cataloguing
  the drizzle version quirk. Reaching for a catalogue entry violates rule (1)
  on the first try.
- AFTER Tier-C-TS is clean (CORRECTING accumulated curation drift): swap the
  in-house security regexes (`review/patterns.py`) → maintained SAST (Semgrep
  first, CodeQL later), advisory/compare-mode first → authoritative once noise
  measured, KEEP the owned-surface scoping; add a CURATION-COUNT metric to the
  scorecard (count bespoke SECURITY_PATTERNS + substrate rules + mechanism
  tokens, per stack/tier — goal isn't zero, it's catching SILENT GROWTH);
  label-audit curated rules (ground_truth_proxy / adapter_contract /
  security_sast_candidate / temporary_workaround / incident_specific_backstop)
  → delete/demote/replace the uncategorized.
- DEFERRED until a 3rd stack: do NOT generalize the substrate-rule architecture
  — wait for a third stack to reveal what's universal vs Postgres-only.

Neo agents: aa6fbd4dfc0dbcc12. [[project_barrier_friction_analysis]].

---

## Version control — no fork

A fork would fragment history for no benefit. `main` is a strict ancestor of
`experiment/phase-4-product-owner` (83 behind, 0 ahead), so the fast-forward is
clean. Tag for safety, promote phase-4 to `main`, prune the sprawl.

```bash
# 1. Safety checkpoint (recoverable anchor)
git tag checkpoint/pre-reset-2026-06-08 experiment/phase-4-product-owner

# 2. Land in-flight Phase 3.5 Session 1.5 work so the tree is clean
git add -A && git commit -m "wip(phase-3.5): session 1.5 checkpoint before reset"

# 3. Promote phase-4 to the single source of truth
git checkout main
git merge --ff-only experiment/phase-4-product-owner

# 4. Archive branches with unique commits, then prune the rest
git tag archive/phase-0p5-frozen experiment/phase-0p5-frozen
git tag archive/phase-3-architect-preconditions experiment/phase-3-architect-preconditions
git branch -D experiment/phase-0-baseline experiment/phase-1-track-a experiment/phase-2-mechanism-gate
git branch -D experiment/phase-0p5-frozen experiment/phase-3-architect-preconditions experiment/phase-4-product-owner
git branch -D worktree-agent-a0d07f43 worktree-agent-a5722d3d worktree-agent-a5b495e1 worktree-agent-a87ce1c4

# 5. Work on short-lived sprint branches off main, merged at each exit gate
git checkout -b sprint/00-ground-truth
```

**Standing constraint (Neo):** `iteration.py` (5,615 lines) must not grow. Run
`gitnexus_detect_changes` before each merge; keep it flat or smaller.

---

## Review gate (canonical procedure)

**Design review (first):** Neo (`neo:neo`) reviews the design before
implementation begins.

**Code-review gate (before each merge to `main`):**

1. After the phase's code is written, **open the parallel review gate in one
   assistant message containing BOTH tool calls** (both backgrounded so they
   run concurrently):
   - `Agent(subagent_type: "Linus", run_in_background: true, prompt: "...")` —
     kernel-grade review
   - `Bash(command: 'codex review "<prompt>"', run_in_background: true)` —
     independent second opinion via the local Codex CLI
2. Each reviewer is told: **the plan path, the phase scope, and the exact files
   touched.** Each is explicitly told **NOT to flag the absence of unwritten
   future phases.**
3. **Wait for both task notifications.**
4. Apply all **BLOCKING + FIX-IT** findings; **reject only with a documented
   reason.** NITs may be batched or deferred.
5. If changes are substantive, **re-trigger the gate.** Only advance to the
   next phase after **both reviewers return clean.**

Rationale: Linus and Codex are different models with different blind spots. On
a correctness-critical framework whose whole thesis is "independent
verification beats self-assessment," dual code review applies that principle to
our own changes.

---

## Sprint 0 — Ground truth & clear the decks *(small, low-risk)*

Neo: "ship as-is."

- **T0.1** Execute the VC steps above. `main` = truth; sprawl gone; checkpoint
  tagged.
- **T0.2** Safe hygiene (folded in, not a separate phase): fix the stale
  `CLAUDE.md` agent table to match the actually-wired roles + correct the
  `skills/` → `.claude/skills/` reference + the `SCENARIO_ARCHITECT` rule-#2
  wording; remove `repomix-output.xml` (6.6 MB) and gitignore it.
- **T0.2a (DESCOPED → tracked follow-up, not Sprint 0):** delete the
  `SCENARIO_ARCHITECT` role. Investigation (2026-06-08) showed it is **not**
  the trivial "dead enum" earlier believed: it has **no production
  invocation** but is wired into `barriers.py` (`BarrierConfig`), the
  hardlink-safety check (`agents.py:739`), the source-barrier role set
  (`agents.py:147`), adapter references, and ~15 **barrier-security** test
  files. Removing it touches `barriers.py` and is therefore
  **authorization-sensitive** (see `docs/AUTHORIZATION-MODEL.md`). Cramming an
  auth-sensitive barrier refactor into the "small, low-risk" housekeeping
  sprint would itself repeat the scope-underestimation failure mode this plan
  exists to fight. Until removed, `CLAUDE.md` documents it as vestigial. Do it
  as a standalone change through the full review gate.
- **T0.3** Collapse the three "Phase N" numbering schemes into this sprint
  numbering. This file is the canonical pointer; mark `ROADMAP.md` and
  `HIGHER-PRIORITY-QUEUE-PLAN.md` superseded.

**Exit:** `main` canonical; tests still green (~3,800); one numbering scheme;
cruft gone.

---

## Sprint 1 — Honest outcomes + the measurement linchpin *(heaviest sprint)*

> **Status (2026-06-08): Sprint 1 DONE.** Part A (T1.1–T1.4, STABLE_FAILURE)
> merged `1acd982`; Part B (T1.5–T1.7, benchmark runner + scorecard) merged
> `f6418a3`. Designs + review trails: `sprint-1-stable-failure-design.md`,
> `sprint-1b-benchmark-runner-design.md` (both Neo-approved; Linus + Codex
> round-2 CLEAN). NOTE: Part B delivers the *tested tool*; producing a real
> baseline number is Sprint 2 (T2.3), run on a machine with Docker.

### A. `SHIP_WITH_GAPS` reclassification is control-flow surgery, not a label

Neo grounded this in `iteration.py`: `SHIP_WITH_GAPS` overrides the Oracle
(`5465–5477`), biases the Oracle's prompt (`5354–5366`), suppresses escalation
in three places (`3448`, `3420`, `3599`), and triggers post-ship side effects
(`shipped.jsonl`, defect registry, archive via `post_ship_archive.py:159`).

- **T1.1** Introduce a terminal **`STABLE_FAILURE`** state with its own exit
  code (proposed 4) — **not** an escalation route (deliberately decouples
  Sprint 1 from the unfixed escalation agent in Sprint 4). **Verify the chosen
  exit code is unused in the current CLI exit-code table before claiming it**
  (existing map: 0=shipped, 1=failed, 2=escalated, 3=aborted).
- **T1.2** Keep `should_ship_with_gaps` as the **eligibility predicate**
  (boolean at `iteration.py:151` — a useful signal). Route an eligible run to
  `STABLE_FAILURE` **at the decision site**, not to forced SHIP (the decision
  site is the `5465–5477` override being stripped in T1.3 — T1.2 and T1.3 touch
  the same site, not the predicate). It must write **no** SHIPPED status and
  **no** `shipped.jsonl` row.
- **T1.3** **Strip the Oracle override (`5465–5477`) and the prompt-biasing
  (`5354–5366`) now.** While the orchestrator can force SHIP, "Oracle judgment"
  is theater and all downstream attribution is contaminated.
- **T1.4** Integrity test (this *is* the linchpin's guarantee): a
  stable-failure run produces exit code ≠ 0 and writes zero `shipped.jsonl`
  rows.
- Run `gitnexus_impact` on `should_ship_with_gaps` first — ~8 files reference
  `ship_with_gaps` (models/pipeline.py, events/types.py, precheck.py, …);
  multi-file change with state-file/event-schema implications.

### B. The benchmark runner — the right tool, minimal

Neo's category-error catch: `tests/reproducibility/harness.py` is
**LLM-procedure-isolated** (its own docstring says its numbers "do not transfer
to the production pipeline"). It is **not** a pipeline runner. The real linchpin
is a **from-clean-state, multi-stack, full-pipeline batch runner** — the
heaviest item in the plan.

- **T1.5** Build the batch runner: input = list of `(spec, stack)`; for each,
  reset target project → rebuild image → `dark-factory run <spec>` N times →
  capture **exit code + terminal `events.jsonl` event**. Productize the manual
  reset/rebuild/monitor dance that `/df-pipeline-run` does for one stack.
- **T1.6** Emit a **minimal** JSONL scorecard — one row per
  `(spec, stack, run, outcome, cost, iters)` and nothing more. Determinism,
  cost-means, and **spec-vs-impl attribution are post-hoc queries over the
  JSONL, not harness features** (attribution before Sprint 4 is circular — it
  depends on the not-yet-trusted defect classifier).
- **T1.7** Anchors: `shared-parse-amount` (known-good, must be 100%),
  self-contradicting `database-foundation` (known-bad), **plus one trivial
  `python_fastapi` spec** — so the linchpin is cross-stack *from birth* (the
  single highest-leverage change against the depth trap).

**Exit:** JSONL scorecard runs end-to-end on ≥2 stacks; baseline number exists;
`SHIP_WITH_GAPS` reads as a non-ship everywhere; integrity test green.

---

## Sprint 2 — Breadth battery + falsifiable baseline

- **T2.0 (de-risk first):** confirm **one trivial spec completes a full
  pipeline run on `python_fastapi` at all.** The adapter is *unproven*, not
  just unmeasured. If it can't, that is the real finding — branch into a
  "make python_fastapi work" mini-sprint.
- **T2.1** Author a graduated battery, **each spec pre-validated internally
  consistent** (Phase 3.5 contradiction validator + human sanity check):
  - **Tier A** trivial (pure function) — sanity floor, must be 100%.
  - **Tier B** small (CRUD endpoint, single table).
  - **Tier C** medium — a **genuinely different domain** (not a corrected
    database-foundation; that's the depth-trap in disguise). Keep a *corrected*
    database-foundation as a **separate regression fixture**.
- **T2.2** Replicate Tier A+B on `python_fastapi`.
- **T2.3** Run the full battery through the Sprint 1 scorecard.
- **T2.4 — pre-register the kill-switch (in writing, before running):** the
  number that means "the framework does not generalize, change R&D direction" —
  e.g. *"Tier B < 50% clean-ship on python_fastapi after spec-gating."*
  Without a pre-committed threshold, a bad baseline gets rationalized as "one
  more sprint."

**Exit:** ≥5 specs, ≥2 stacks, baseline per stack, explicit go/no-go against
the pre-registered threshold. **Fallthrough:** if T2.0 fails (python_fastapi
cannot complete a single run), Sprint 2 instead exits on the python_fastapi
mini-sprint finding, and the cross-stack baseline defers until that lands.

> **Sequencing note (Neo):** breadth-before-core (Sprint 5) is correct —
> fixing the implementer first would optimize against a sample of one. **But**
> if S2's scorecard shows one dominant, obvious, cross-stack failure bucket,
> allow a single "smoke" implementer fix rather than religiously deferring all
> of S5. The spine stays breadth-first; momentum is not handcuffed. **A smoke
> fix must be re-baselined (full battery re-run) before the T2.4 kill-switch is
> evaluated** — otherwise the fix contaminates the very number the kill-switch
> reads.

---

## Sprint 3 — Spec-quality gate as the primary lever (finish Phase 3.5)

- **T3.1** Fix the validator's 2/5-zero-contradiction non-determinism
  (Session 1.5: structured-output sub-call; agent-split fallback).
- **T3.2** Make contradiction detection a **hard gate** before any code phase.
- **T3.3** Honest validation: test the validator on the **new Sprint 2 specs it
  was not designed against** — not only the database-foundation-derived
  `tests/fixtures/contradiction_typology/` (validating on fixtures from your
  one known bug is overfitting).

**Exit:** validator catches T1–T5 ≥4/5 reproducibly *and* flags a planted
contradiction in a fresh spec.

---

## Sprint 4 — Trustworthy meta-agents

- **T4.1** Make the escalation agent **extractive** — assemble reports
  deterministically from real artifacts (state, gate output, `defects.json`);
  no free-form fact generation. Or remove the LLM agent and keep a
  clearly-labeled optional summary.
- **T4.2** Reduce barrier-audit false positives — upgrade from naive
  path-string matching to detection that excludes legitimate `import`
  statements. Keep as defense-in-depth; Docker masking remains the real
  barrier.
- **T4.3 (now unblocked):** with the classifier/escalation trusted, turn on the
  deferred **spec-vs-impl attribution** post-hoc query from Sprint 1.

**Exit:** escalation reports contain zero fabricated facts on a replay of known
runs; barrier FP ≈ 0 on the battery.

---

## Sprint 5 — Move the number

Iterate on implementer/architect reliability using the now-trustworthy loop:
take the largest failure bucket from the scorecard, attack it, re-measure,
repeat. Value accrues here — and it is safe to do *only* now, because before
this you could not tell a real implementer bug from a fixture artifact.

**Exit:** ongoing — no terminal gate. Each cycle is measure → fix → re-measure;
the reliability number is the running scoreboard.

---

## Deferred / out of scope

- Consumer-audit registry (`docs/CONSUMER-AUDIT-REGISTRY-PLAN.md`) — Neo
  rejected the prior attempt on 3 blockers; do not re-attempt without
  addressing them.
- All SaaS/GTM work in `docs/business-plan.md` — out of scope for R&D.
- New pipeline phases — the plan is subtractive where possible; do not add
  mechanism.

## Known issues surfaced during the reset (tracked, not yet fixed)

- **`tests/integration` `run_regression` patch-target — FIXED** (`7ed6105`,
  Linus+Codex CLEAN). `conftest.py` now patches the source module
  `darkfactory.scenarios.regression.run_regression`. 48 AttributeErrors → 0;
  suite 398→400 pass, 23→0 errors, zero regressions.
- **`tests/integration` mechanical fixture drift — FIXED** (`8d8603f`,
  2026-06-09): `pipeline_env` now writes `active_adapters` and stubs
  `precheck_result.fatal`/`.summary`. Full suite (Docker up): **406 pass / 71
  fail** (was 400/77). Remaining 71 are **behavioral-expectation drift** — stale
  assertions about agent-invocation order, event sequences, and exit codes that
  encode an OLD pipeline shape (e.g. `ARCHITECT != IMPLEMENTER`, `ITER_COST` vs
  `SPEC_FAILED`, `3 == 0`). That's a per-test modernization effort (no API cost,
  but real judgment per test) — a dedicated task, NOT mechanical drift. Process
  note: run BOTH `tests/unit` and `tests/integration` before claiming green.

---

## Progress Log

> Append-only, step-level record for interruption recovery. Newest at the
> bottom. Each entry: date · what landed · commit(s) · verification · next.
> A fresh session can resume from the last entry. Commit this file after each
> meaningful step.

- **2026-06-08 · Sprint 0 DONE** · commits `e8994b7`, `f3d6a57` (merged to main).
  VC consolidation (main fast-forwarded to old phase-4; `checkpoint/pre-reset-
  2026-06-08` + `archive/*` tags; experiment/worktree branches deleted);
  hygiene (CLAUDE.md agent table corrected to wired roles, `skills/` →
  `.claude/skills/`, repomix dump removed); legacy roadmaps banner-marked
  SUPERSEDED. Descoped: `SCENARIO_ARCHITECT` deletion → T0.2a (auth-sensitive,
  ~25 files incl. barrier-security tests). Tests green (doc-only).
- **2026-06-08 · Sprint 1 Part A (T1.1–T1.4) DONE** · commits `1acd982`,
  `1bfc10e` (merged to main). `SHIP_WITH_GAPS` override → terminal
  `STABLE_FAILURE` (exit 5). Removed Oracle force-SHIP override AND prompt-bias
  (round-1 BLOCKING caught by both Codex+Linus; round-2 CLEAN). Composite-
  blocked still escalates. New PipelineStatus/EventType/SpecDispatchStatus;
  `_handle_stable_failure` writes no SHIPPED status / no shipped.jsonl. Design +
  review trail: `sprint-1-stable-failure-design.md`. Verify: 3818 unit tests
  pass (only failure = pre-existing Docker-daemon env test); zero new lint.
  **Next: Sprint 1 Part B (T1.5–T1.7)** — from-clean-state multi-stack
  full-pipeline batch runner + minimal JSONL scorecard {spec, stack, run,
  outcome, cost, iters}, seeded with one python_fastapi spec. Heaviest item;
  needs Docker/target-project infra decisions; may surface that python_fastapi
  has never completed a run.
- **2026-06-08 · Sprint 1 Part B (T1.5–T1.7) DONE** · commit `f6418a3` (merged
  to main). `dark-factory benchmark <battery.toml>` + `darkfactory/benchmark/`
  (scorecard/battery/runner). From-clean-state batch runner over
  `dark-factory run --fresh`; minimal JSONL scorecard; CLEAN_SHIP vs
  SHIP_WITH_GAPS vs STABLE_FAILURE distinguished honestly. Design +
  review trail: `sprint-1b-benchmark-runner-design.md` (Neo-approved; Linus +
  Codex round-2 CLEAN). Verify: 46 benchmark unit tests pass; ruff clean.
  **Sprint 1 complete.** Tool is built+tested; the real baseline run is Sprint 2
  (needs Docker + external target projects).
  **Discovery during review:** the `tests/integration` suite has 48 pre-existing
  failures (`orchestrator.run_regression` patch-target drift) — see "Known
  issues" above. I had been running only `tests/unit`; corrected going forward.
  Recommended to fix BEFORE Sprint 2.
  **Next: Sprint 2 (T2.0–T2.4)** — but consider the integration-suite fix first.
- **2026-06-08 · Integration-suite `run_regression` patch-target FIXED** ·
  commit `7ed6105` (merged to main; Linus + Codex CLEAN). Repointed the
  `pipeline_env` fixture patch to `darkfactory.scenarios.regression.run_regression`
  (the documented "separate follow-up"). 48 AttributeErrors → 0; suite 398→400
  pass, 23→0 errors, zero regressions. Unmasked two deeper PRE-EXISTING layers
  (active_adapters fixture drift ~21 tests; broad Docker-daemon dependence) —
  deferred to a Docker host (see "Known issues"). Stopped here deliberately
  (can't verify Docker-dependent fixes in this env). **Next: Sprint 2**, ideally
  on a Docker-capable machine (where the integration rehab + real benchmark
  baseline both belong).
- **2026-06-09 · Integration mechanical fixture drift FIXED** · commit `8d8603f`
  (test-only). Once Docker came up, fixed the two remaining mechanical drifts
  unmasked by the run_regression fix: `pipeline_env` now declares
  `active_adapters` + stubs `precheck_result.fatal`/`.summary`. Full integration
  suite 400→406 pass / 77→71 fail. The remaining 71 are behavioral-expectation
  drift (stale agent-order/event/exit-code assertions) — a separate per-test
  modernization, deferred. NOTE: committed without a fresh review gate (test-
  only, same class as the reviewed run_regression fix, mirrors the reviewed
  _write_active_adapters_hints helper, verified by test progression).
- **2026-06-09 · Sprint 2 kickoff: single-spec benchmark RUN** · validating the
  Part B tool on the known-good anchor `2026-04-08-shared-parse-amount` against
  `~/sites/budget-brain-v2` (Docker up, images built). Battery
  `/tmp/spa-battery.toml`, scorecard `/tmp/spa-scorecard.jsonl`.
  **RESULT: tool PROVEN; outcome ABORTED (exit 3), not CLEAN_SHIP.** The runner
  reset cleanly, ran the pipeline, captured exit 3, classified ABORTED, and
  wrote a valid scorecard row (run_id 88d5036751504ee4, wall 146s) without
  wedging — the Part B linchpin works end-to-end. The abort is a real finding:
  the spec-validator returned **REVISE (score 100/100, divergence LOW,
  consistency PASS, blockers=1)**; the sole blocker is **MISSING_SECTION:
  Non-Negotiable Acceptance Criteria (§8)** — a mandatory section added to the
  framework AFTER this 2026-04-08 spec was written. So the historically
  "known-good" anchor now fails validation purely on framework-evolution drift
  (same class as the integration-test drift), and likely so do all old
  budget-brain-v2 specs. Sub-findings: (a) spec-validator burned 200 turns
  (max) / ~400K output tokens / 142s on a trivial spec — runaway/cost smell;
  (b) scorecard shows cost_usd=null on aborts because COST_SUMMARY only fires
  on ship/stable-failure/escalation — the runner should fall back to summing
  AGENT_COMPLETED turns so aborts aren't shown as free (Part B follow-up).
- **2026-06-09 · Item #1 (validator turn-runaway) INVESTIGATED — DEBUNKED + bigger finding.**
  Real spec-validator usage was **7,276 output tokens** (the "400K" was an
  estimate-at-cap; ~55× over); 142s wall is normal. The actual finding:
  **pipeline turn/cost accounting is cap-based fiction** — `total_turns +=
  agent_max_turns` (200/agent), COST_SUMMARY derives $ from that with no cache
  modeling, while real usage sits UNUSED in
  `.dark-factory/reports/run-telemetry.jsonl` (persisted-but-not-consumed).
  Historical "$/feature" figures are overestimates; the aborted run cost <$1
  (cache-dominated). Memory: `project_cost_accounting_estimate_at_cap`.
  **Reshapes item #2:** the scorecard cost fix must consume real run-telemetry
  tokens with cache-aware pricing, not sum cap-based turns. Item #1 done
  (investigation; no validator fix warranted — it's behaving fine).
- **2026-06-09 · Item #2 (scorecard real-cost) DONE** · commit `f13cfb4`
  (merged; Linus + Codex round-2 CLEAN). Scorecard now consumes real per-agent
  tokens from run-telemetry.jsonl with cache-aware, PER-LINE (mixed-model-safe)
  pricing — works on aborts. Review caught a 3×-too-high Opus rate ($15/$75 →
  $5/$25, verified against the authoritative claude-api reference) and
  mixed-model mispricing. cost_usd is None when any line's model is unknown.
  60 benchmark unit tests; full suite 3879. Pipeline total_turns/COST_SUMMARY
  cap-fiction still stands (tracked: project_cost_accounting_estimate_at_cap).
  **Next: Item #3** — re-run for a real CLEAN_SHIP. Candidate `echo-endpoint`
  already has §8 (no project edit); `shared-parse-amount` needs §8 added.
- **2026-06-09 · Item #3 attempt 1 (echo-endpoint): TIMED OUT, not a clean ship.**
  Ran 45 min (run_timeout_s=2700), killed at iter-2 implementer. The tool
  handled the timeout perfectly: ERROR row + **real cost $8.76** (per-line,
  cache-aware: 12 agents, in 12.5K / out 129K / cache_read 15.3M / cache_create
  580K, model sonnet) + stderr sidecar — validating the timeout + real-cost
  paths live. Root issue (not the tool): blind scenarios never executed
  (pass_rate 0 → forced NO-SHIP), barrier false-positive on apps/api/src, HIGH
  prototype-pollution finding. echo-endpoint is not CLEAN_SHIP-viable as-is.
  Per the "Both" plan → fall back to shared-parse-amount (+§8).
- **2026-06-09 · Item #3 attempt 2 (shared-parse-amount +§8): ABORTED at architect.**
  Added §8a to the spec (committed to budget-brain-v2 `efc29d7`, empty/justified
  form). §8 fixed the validator gate — got 4 agents deep (was 1) — but the
  ARCHITECT produced a bad manifest (MONOREPO_SCOPING_VIOLATION on
  commands.install + `data_models[1].fields[0]` parse error), retried 3×,
  exhausted → SPEC_FAILED exit 3. Real cost $1.27, 9.5 min.
  **HEADLINE: 3 real runs, 0 clean ships, each failing at a DIFFERENT layer**
  (validator-gate → scenario-execution → architect-manifest). This is the
  reset's first honest baseline and it empirically reconfirms the core
  diagnosis: the pipeline cannot currently clean-ship even trivial specs. The
  benchmark tool itself worked perfectly on all 3 (correct outcome + real
  per-line cost every time) — CLEAN_SHIP/STABLE_FAILURE remain unit-tested, not
  yet seen live because the pipeline doesn't produce them. Getting a live
  CLEAN_SHIP now requires fixing real pipeline bugs (architect non-determinism /
  monorepo scoping / scenario execution) = Sprint 5 "move the number", not a
  quick demo. Real per-run costs observed: $1.27 (abort@architect),
  &lt;$1 (abort@validator), $8.76 (45-min timeout) — all FAR below the historical
  cap-fiction "$15-170/feature".
- **2026-06-09 · Sprint 5 architect-scoping fix IMPLEMENTED + merged** · commit
  `e3e4dbf` (Linus + Codex parallel gate, round 2 CLEAN). M1 command-scope now
  anchors on `detect_workspaces()` (drift-proof, external, non-circular) instead
  of the drift-prone source_dirs; ancestor matching gated (`allow_ancestor`) so
  workspace anchors reject parent-spanning targets while source_dirs fallback
  keeps ancestor. Round-1 review caught a P1 (my ancestor over-generalization
  re-opened the cross-workspace hole) — fixed. 14 scope tests; full unit suite
  3893. **Verification IN FLIGHT:** shared-parse-amount ×3 against still-drifted
  budget-brain-v2, measuring clean-architect / clean-ship RATE (data_models
  LLM-variance is a separate dice roll — a single abort there ≠ failed fix).
  **RESULT: 3/3 aborted UPSTREAM at the spec-validator (agents=1), never reached
  the architect — so the scoping fix is UNVERIFIED e2e** (sound + unit-tested +
  reviewed CLEAN, just not exercised end-to-end). Two findings: (1) a REAL spec
  contradiction — §5 normative TOO_MANY_DECIMALS message
  `"Amount must have at most 2 decimal places"` (spec line 140) vs §10 Example 2
  `"At most 2 decimal places are allowed"` (line 351); (2) **validator
  non-determinism** — the SAME spec PASSED validation in the earlier §8 run
  (reached architect, agents=4) but fails 3/3 now (this issue was a *warning* in
  run 1, *non-blocking* in run 2, *blocking* in runs 4-6). The dominant blocker
  is now UPSTREAM of M1: the validator gate is a coin-flip (reset-plan Sprint 3,
  validator determinism). Real costs ~$0.29-0.46/run. To verify Sprint 5 e2e we
  must first get past the validator (fix the §5/§10 contradiction AND/OR address
  validator determinism). NEXT: user decision — fix §5/§10 + rerun, pivot to
  Sprint 3 (validator determinism), or stop.
- **2026-06-09 · Sprint 3 (validator determinism) STARTED** — user pivoted here.
  Design: `docs/plans/sprint-3-validator-determinism-design.md`. Investigation
  found: `blockers` is an LLM SELF-COUNT (spec-validator.md:675-680), no code
  derives it; temp=0 pinned but un-instrumented and its own commit named verdict
  instability as the falsifier (now observed); the orchestrator ALREADY trusts
  the typed consistency-ledger over the status field (orchestrator.py:1287-1297)
  → deriving the gate from typed artifacts is an incremental extension. Design
  review APPROVE-WITH-CHANGES (folded), but the **neo:neo tool CRASHED** (output
  post-processor / structured_parser bug) — code-grounded analysis, NOT a real
  Neo ruling; retry Neo before the Step-2 gate change. Crux reframed: not
  classification-vs-detection (no severity knob exists) but **is the
  contradictions-list stable across runs** — derivation fixes transcription
  variance (cause 2), not detection variance (cause 1/3). **PRELIMINARY SIGNAL
  (cause 2 likely):** surviving run-#3 ledger DETECTED the §5/§10 contradiction
  (typed T1, severity:blocking) yet its status file showed blockers=0/PASS/no-
  consistency — the typed artifact is right, the LLM self-count flipped.
  **MEASUREMENT DONE (N=4):** the contradictions-list is UNSTABLE and the
  variance is DETECTION, not transcription. The validator detected the real
  §5/§10 contradiction only **2/4 runs** (runs 1&4 missed it entirely → empty
  ledger → verdict PASS → proceeded to architect; runs 2&3 caught it → REVISE/
  FAIL; typed T1 once, T4 once). The status file faithfully tracked the ledger
  in all 4 — **NO transcription gap**, so my earlier "cause 2" read (from an
  OVERWRITTEN artifact) was wrong. **Consequence: Step 2 (derive gate from
  ledger) is a NO-OP on this evidence** — status already matches ledger.
  **The real fix is DETECTION determinism (axis a)** — make the validator
  reliably surface the contradiction every run (Session 1.5 structured-output
  Pass-1 extraction, never implemented; and/or agent-split). Bigger lift; needs
  a real Neo ruling (the tool crashed). Secondary wall confirmed: runs 1&4
  passed validation and still failed at the architect. Capture data:
  /tmp/sprint3-capture/. NEXT: user decision on the detection-determinism design.
- **2026-06-09 · Sprint 3b (detection determinism) IMPLEMENTED + merged** ·
  commit `21906da`. Neo ruled CLEAN this retry (APPROVE-WITH-CHANGES, folded):
  the fix is a DETERMINISTIC code-lint, not tightened LLM extraction (spending an
  LLM on byte-equality is inverse leverage; the LLM's FR-centric Pass-1
  structurally never reads the §5 table/§10 examples, so the lint covers a
  disjoint surface). New `darkfactory/spec/error_message_consistency.py`:
  `validate_error_message_consistency(spec_text)` flags any error code mapped to
  ≥2 distinct literal messages, from object-literal `code/message` + header-
  qualified message tables; fail-open on unparseable, fail-closed on confident
  hit. Wired as a true backstop in `_step_spec_validation` (reads only the spec
  file, independent of the LLM ledger). Catches the §5/§10 `TOO_MANY_DECIMALS`
  contradiction N/N where the LLM was 2/4. Scope: error-code→message ONLY
  (structured-error-contract SSOT + class taxonomy deferred). Linus+Codex round-2
  CLEAN (round-1 FIX-IT = single-word codes EMPTY/TIMEOUT). 12 unit tests; suite
  3905; integration 406/71 (= baseline). Audience: pipeline-halt only.
  **Verification DONE (honestly):** N=3 live runs → 3/3 aborted at the validator,
  BUT via the LLM `blockers=1` self-count gate, not the lint — the LLM *caught*
  the contradiction all 3 this round (a lucky streak vs the measured 2/4), so the
  early blockers>0 gate fired before the lint was reached. So the live runs did
  NOT exercise the lint-backstop path. Closed that gap with 2 targeted tests
  (`test_error_message_lint_fires_when_llm_passes` /
  `_silent_on_consistent_spec` in test_consistency_status.py) that drive
  `_step_spec_validation` with a mocked LLM-PASS (the miss case) + a contradictory
  spec → proves the lint raises in the wired flow, and doesn't false-abort when
  consistent. NET: the spec now aborts deterministically (= LLM-catch OR
  lint-catch; both paths proven — pure fn 12 tests, wired backstop 2 tests, live
  3/3). The lint adds determinism precisely on the LLM-miss runs the live streak
  didn't surface. Tests committed.
- **2026-06-09 · FIRST-CLEAN-SHIP attempt** — confirmed (1 post-3b run) that
  §5/§10 is the SOLE validator blocker (spec scores 100, divergence LOW, only 1
  Critical Issue = the §5/§10 message mismatch). Fixed it surgically (user
  approved manual one-string reconcile over heavyweight /df-densify-spec, since
  densify risks diverging from the already-shipped impl): bb-v2 line 351 example
  message → §5 normative "Amount must have at most 2 decimal places". Committed to
  budget-brain-v2 `b72d833`; lint now 0 violations. source_dirs stays drifted
  (Sprint 5 handles it). **N=3 ship attempt IN FLIGHT** (run_timeout_s=2400):
  validator should now pass → reaches the architect (next wall; data_models
  hallucination is a separate dice roll, so report the clean-ship RATE not a
  binary). **RESULT (N=3): 0/3 clean ship — but the old walls are GONE.**
  Validator 3/3 pass (§5/§10 + 3b worked); architect 2/3 pass (Sprint 5 scoping
  fix worked — no MONOREPO_SCOPING, no data_models hallucination this round).
  Two NEW deeper walls: (1) run 3 aborted at the ARCHITECT on a type-only-export
  mistake — manifest declared `ParseAmountResult`/`ParseAmountErrorCode` (TS
  type-only, erased at runtime) in entry_points exports; the Import Contract gate
  rejects them (intermittent LLM variance, 1/3). (2) runs 1&2 reached the
  TEST-ENGINEER and aborted: "TE scenarios failed validation after 3 attempts"
  (consistent, 2/2 — the deepest + most reliable blocker now). Runs ~$1.26-3.09,
  7/7/4 agents, 7-22 min. Forensics: per-run archive final/events.jsonl has full
  agent sequence + ts + abort reason (live events.jsonl is wiped between runs;
  scorecard lacks abort_stage/reason inline — enhancement candidate). NEXT: the
  TE scenario-validation wall is the consistent blocker to investigate.
- **2026-06-09 · (earlier) Sprint 5 architect-scoping fix DESIGNED, then PAUSED.** Root-caused the deterministic
  blocker: M1 command-scope validator false-positives because budget-brain-v2's
  environment.toml source_dirs drifted to apps/api/src while the spec is in
  packages/shared/src, and feature specs can't fix source_dirs. Design +
  Neo review: `docs/plans/sprint-5-architect-scoping-fix-design.md`. Neo REWORKED
  my first idea (file_map anchor = circular) → corrected fix: anchor the
  scope check on `detect_workspaces()` (reads pnpm-workspace.yaml — drift-proof,
  external, non-circular), source_dirs as fallback. ABC-signature change →
  ADAPTER-GOVERNANCE. Keep budget-brain-v2 drifted (test oracle). Verify over
  N≥3 runs (data_models LLM-variance is a separate dice roll). **NEXT (on
  resume): implement → Linus+Codex gate → re-run shared-parse-amount ×3 to
  measure clean-architect rate.**

> NOTE: a few mid-session Progress Log inserts silently no-op'd (anchor spanned a
> line break). The authoritative record for those milestones is the git history +
> the per-sprint design docs. Consolidated tail below (chronological):

- **2026-06-09 · Scorecard forensics (quick win) merged** · `3864609`. Benchmark
  scorecard now records `last_stage` + `abort_reason` per run (where/why a run
  died), legible inline instead of archive-spelunking. Linus+Codex CLEAN. 34
  scorecard tests.
- **2026-06-09 · Sprint 6 (TE wall) DESIGNED + Neo-ruled (clean) + IMPLEMENTED +
  merged** · `4cc600b`, design `docs/plans/sprint-6-te-wall-design.md`. The TE
  abort was mostly a FALSE-POSITIVE CASCADE: the pure-function spec DENIES
  substrate ("...no tenant-isolation...") but the postgres `tenant_isolation`
  trigger regex (`tenant.*isolat`) matched the substring with no negation-
  awareness → activated postgres → staged DB contract helpers → validator
  hard-failed because the TE correctly won't import irrelevant helpers (same
  "wire-language FP on foot-gun docs" family). Neo APPROVE-WITH-CHANGES → fix at
  the STAGING seam: gate `stage_adapter_contracts` on `substrate_manifest.is_file()`
  (A3 — the architect's manifest is the substrate authority, NOT a spec-text
  regex; extends the already-blessed is_file gate on the SA addendum). Plus B1:
  TE prompt + retry feedback now teach SPLITTING >30-assertion files. Linus+Codex
  CLEAN. suite 3914; integration 406/71.
- **2026-06-09 · 🎉 FIRST CLEAN SHIP — the number moved 0→1.** Ship attempt #2
  (post 3b+5+6), N=3: **run 3 = CLEAN_SHIP** (exit 0, iters=1, oracle SHIP,
  satisfaction 100, **41 blind scenarios @ 100% pass**, no defect registry → a
  genuine CLEAN_SHIP, NOT SHIP_WITH_GAPS; $5.25, ~33 min). The TE wall FELL
  (Sprint 6 worked — TE→implementer→gate→reviewer→oracle all passed). The full
  reset arc paid off: honest outcome → measure → fix the REAL deterministic
  blockers (Sprint 5 scoping, Sprint 3b error-message determinism, Sprint 6
  staging-manifest gate) → first honest autonomous clean ship.
  **Rate: 1/3.** Runs 1&2 TIMED OUT at the 40-min cap at the reviewer stage
  ($7.56/$7.22) — diagnosed as SLOW-but-progressing (every stage advanced, no
  hang), NOT failures. They'd likely ship with a longer timeout. Time sink: the
  substrate-owner ran 4× (retries, ~9 min) even on this pure-function spec with
  NO substrate manifest — a reliability/efficiency finding worth chasing
  (substrate-owner shouldn't churn on a substrate-less spec). NEXT options:
  (a) raise run_timeout_s + re-measure the rate; (b) investigate the
  substrate-owner-retries-on-substrate-less-spec inefficiency; (c) Sprint 2
  breadth (prove it on a 2nd stack — the real north star).
- **2026-06-09 · Sprint 7 (substrate-owner churn) DESIGNED → Neo REWORK → PAUSED**
  · `docs/plans/sprint-7-substrate-owner-churn-design.md`. Root cause:
  `run_substrate_prep` (substrate_prep.py:460) invoked UNCONDITIONALLY
  (iteration.py:3064, no gate); loop runs 4× (1+DEFAULT_MAX_RETRIES=3); a no-gap
  spec's empty payload trips the strict schema-v2 parser → re-prompts 3×, each
  re-reading the whole project (~9 min = the timeout cause). Neo REWORKed my fix:
  bare `substrate_manifest.is_file()` gating (Sprint-6 mirror) would
  false-negative the substrate-owner's FOUNDING case (drizzle journal). My
  follow-up verification refined it: gap-detection keys on the architect
  `commands.setup` tool refs + spec §4a + lockfile, NOT the TOML; the founding
  drizzle case (database-foundation) DOES have a TOML, but a spec COULD have a
  setup-tool without one — so bare-manifest gating is still theoretically unsafe,
  and "tooling-in-project" (Neo's predicate b) is coarse (project-level, not
  per-spec). Correct per-spec predicate ≈ "architect commands.setup references a
  known prep-gap tool" → needs a deterministic manifest parse. Subtle +
  regression-risky → PAUSED post-headline rather than rush. NEXT: re-engage Neo
  (round 2) with the verification to settle the predicate → implement → gate →
  N=3 for the rate. Cheap interim alt: raise run_timeout_s to measure the true
  rate without touching the churn.
- **2026-06-09 · Sprint 7 GROUND TRUTH — both prior hypotheses INVALIDATED by the
  trace.** Pulled the captured substrate-prep trace (which I should have done
  FIRST — see [[feedback_pull_trace_before_theorizing]]): substrate-prep is
  genuinely NEEDED for shared-parse-amount (the GLOBAL environment.toml setup runs
  `drizzle-kit migrate` for every spec → the pure function still needs the drizzle
  journal/dirs seeded or Setup hangs) → GATING THE INVOCATION (my Option A + Neo's
  P-predicates) WOULD REGRESS. And the 4× churn is ONE rejected command
  (`SUBSTRATE_PREP_VALIDATOR_REJECT step=create_drizzle_config`): the agent
  re-emits a `drizzle.config.ts` config-AUTHORING command the validator forbids
  (agent.md:46) across all 4 attempts (`unresolved_rejections:1`) instead of
  dropping it and converging on its 2 valid seeding commands → NOT Neo round-1's
  stage_exit hypothesis either. REAL fix space (re-design on this data): (R1) make
  the retry loop converge by DROPPING rejected commands; (R2) prompt guard against
  emitting config/schema/source-writing commands; (R3) deeper budget-brain-v2
  config issue (global setup runs DB migrate for non-DB specs — edits the oracle).
  Sprint 7 design doc corrected. PAUSED — needs a fresh design pass on the real
  cause. The first CLEAN SHIP (the headline) stands regardless.
- **2026-06-09 · Sprint 7 churn fix ATTEMPTED → BOTH reviewers BLOCKING → set
  aside (NOT merged).** Implemented a prompt-only fix, but Codex + Linus both
  blocked it. DEFINITIVE cause (finally read the rejection `rule`): the agent
  emits a MULTI-LINE command to write drizzle.config.ts; the real rule is
  `"contains newline (pre_setup entries must be one-liners)"` — a SHELL-SAFETY
  rule, NOT "config authoring" (no such validator rule exists;
  validate_pre_setup_entry, environment.py:204, is shell-safety only). My fix
  keyed the drop on a non-existent rule, and Linus showed it would likely
  CONVERGE BY LETTING THE CONFIG WRITE THROUGH as a one-liner = a silent
  scope-barrier escape (worse than the churn). Retry test was a tautology
  (fabricated rule string). I theorized the cause FOUR times and only nailed it by
  reading the `rule` field — see [[feedback_pull_trace_before_theorizing]].
  Correct rework (design doc): add a real scope rule to validate_pre_setup_entry
  (reject config/schema/source-authoring by TARGET — enforces agent.md:46 which
  is currently prompt-only), drop-by-target not rule-text, thread surviving
  commands into the retry prompt, fix the test → Neo (new reject rule on a
  barrier-sensitive path) + Linus/Codex → N=3. Code attempt preserved on branch
  `sprint/07-substrate-churn-wip-do-not-merge` (7401ecb); `stage_exit`-removal
  half is salvageable. main clean @ 49fad5e. **First CLEAN SHIP stands.**
- **2026-06-09 · Sprint 7 REWORK — substrate-owner churn fix MERGED** · `7099c98`.
  Grounded in the real trace this time. Neo design ruling caught the P0 I'd
  missed: substrate-owner.md:143 LITERALLY told the agent to emit config files as
  a gap class (contradicting :46/:136) — the agent was obeying, not lapsing.
  Fix: (1) reframed :143 (config authoring = implementer's domain → considered:
  skip); (2) ENFORCED scope rule in substrate_prep.validate_command_scope (called
  in _validate_commands after the role-agnostic shell-safety validate_pre_setup_
  entry) — rejects config/schema/source authoring by basename, allow-list-first
  (seeds _journal.json/mkdir/data.json PASS; *.config.*/*.sql/*.ts/etc FAIL),
  targets from command-string regex (redirect/tee-all-operands/dd/cp/mv/sed-i).
  Closes the silent scope-barrier escape (a shell-safe one-liner config write is
  now rejected by scope). (3) retry prompt shows survivors + drops-by-what-the-
  command-does + stage_exit removed. Round-1 BLOCKED by Codex (output-wide
  files_seeded poisoned survivors; tee first-operand-only) → fixed: regex-only
  per-command scope, tee captures all operands. Round-2 Linus+Codex CLEAN. 35
  substrate tests; full unit 3926; integration 406/71. PROCESS: nailed the cause
  only after reading the rejection `rule` field ([[feedback_pull_trace_before_
  theorizing]]). **N=3 ship battery (post-Sprint-7) IN FLIGHT** — tests whether
  killing the ~9min churn turns 1/3 → ~3/3 without timeouts. **RESULT: churn
  fixed (substrate-owner now 1×/iteration, confirmed), but at the 40-min cap runs
  still timed out — because they ITERATE (oracle NO-SHIP → re-implement), not
  churn. Re-ran N=3 at a 90-min cap (user's call) to get the true rate +
  iteration-cost comparison.**
- **2026-06-09 · N=3 @ 90-min cap — THE CLOCK WAS NEVER THE LIMITER. Rate 0/3
  clean; all 3 FINISHED (no timeouts).** All three honest terminal outcomes fired
  live for the first time: run1 STABLE_FAILURE (exit5, 2 iters, 44min, gate stuck
  at 94% — genuine partial), run2 FAILED (exit3, TE scenario-validation ×3, 22min
  — TE gen variance reappeared, LLM-dependent), run3 ESCALATED (exit2, 3 iters,
  44min). Wall-time ∝ iteration count (run2 died early at TE = 22min; iterating
  runs = 44min, ~15-18min/back-half-iteration). **KEY FINDING (run3): a
  FUNCTIONALLY COMPLETE impl (44/44 tests, 100% scenarios / 100% satisfaction /
  46 scenarios on ALL 3 iterations) CANNOT SHIP** — gate exit=1 on a NON-scenario
  check (escalation report: "type export contract violation — types exported from
  wrong module"; same TS type-only-export root cause that bit the architect in an
  earlier battery) PLUS the defect-classifier output file was MISSING ("pipeline
  infrastructure issue"). delta=0 / identical failure-set-hash across iters → the
  implementer can't fix a NON-functional blocker → escalate. So the remaining
  unreliability is NOT timeout and NOT substrate — it's: (a) the gate's
  TS-type-only-export contract failing a 100%-passing run [recurring, systemic],
  (b) a defect-classifier infra gap, (c) TE scenario-gen variance, (d) implementer
  reaching only 94% sometimes. NEXT lever candidates (data-grounded): the
  type-only-export gate handling (highest — blocks a perfect impl), the
  defect-classifier missing-output infra bug.
- **2026-06-10 · Issue #1 (substrate-trigger FP) FIXED + merged** · `749febc`.
  The run3 escalation's REAL gate failure (not the escalation report's "type
  export" hallucination) was Substrate Verification: the `tenant_isolation` prose
  regex (`tenant.*isolat`) matched the spec's DENIAL → verify_substrate emitted
  `substrate-manifest-missing` → blocked a functionally-complete impl. THREE
  lexical regex fixes were tried and ALL blocked by Codex+Linus (negation is
  positionally intractable; real-corpus echo-endpoint:28 "requires no tenant
  context" FP'd). Neo round-2: stop fixing the trigger — make the GATE
  architect-authoritative (host-side `substrate_phase.py`: substrate-TOML absent +
  architect `<spec>-manifest.yaml` present → architect modeled no substrate →
  SKIP; YAML absent → fall through to the real missing-architecture failure).
  Finishes the Sprint-6 A3 migration; trigger regex left untouched (harmless for
  activation/cache). Reverted the 3 regex attempts. Linus+Codex CLEAN. Foot-gun
  (RLS-spec-forgot-the-Substrate-Primitives-block) deferred to a spec-validator
  addendum. **Validation re-run IN FLIGHT** (N=1): expect Substrate Verification
  to SKIP and the Scenario Lint failure (issue #2) to surface next. **VALIDATED:
  CLEAN SHIP (exit 0, iters=1, oracle SHIP, $4.66, 33min).** Gate summary:
  Substrate Verification = SKIP (issue #1 fixed — was the FAIL that escalated
  run3), Scenario Lint = FAIL, **Result = PASS → SHIPPED**. So issue #1's fix
  directly converted the run3 escalation class into a clean ship = SECOND clean
  ship, deterministic (removed the blocker, not luck). AND "issue #2" (Scenario
  Lint FAIL) is NON-BLOCKING BY DESIGN (quality-gate.sh:1318: "writes a FAIL row
  for observability but does NOT set GATE_FAILED"; ROADMAP B2 count-only) — I'd
  misread the advisory FAIL row as a ship-blocker. It is NOT a real issue. The
  REAL remaining ship-blockers (90-min battery, variance-dominated, now that #1 is
  fixed): (a) implementer reaching only 94% scenarios → oracle no-ship →
  stable_failure (run1), (b) TE pre-gate scenario-validation failing 3× → abort
  (run2). NEXT: re-measure the rate (N=3) post-#1 to see how much it moved + which
  back-half variance dominates, then take that one.
- **2026-06-10 · 🎉 RATE 0/3 → 3/3 on shared-parse-amount (post issue #1).** N=3 @
  90-min cap, ALL THREE CLEAN_SHIP on iteration 1 (oracle SHIP, $4.36-4.92,
  28-37min). +the validation run = 4 consecutive clean ships. Issue #1 (the
  substrate-verification false-positive) was THE dominant ship-blocker — it
  escalated functionally-complete runs; removing it made the fixture ship
  reliably. The back-half variance (implementer-94%, TE-gen-failure) did NOT recur
  in this batch — occasional noise, not systemic. "Issue #2" (Scenario Lint) was a
  non-issue (advisory by design). So the per-fixture deterministic ship-blockers
  are CLEARED; shared-parse-amount clean-ships deterministically. Remaining concrete
  (non-variance) item from the battery list: the defect-classifier missing-output
  infra note (run3) — only surfaces on NO-SHIP iterations (a clean ship has no
  defects to classify), so it doesn't bite the 3/3. Investigating it next, then the
  reset's north star = Sprint 2 breadth (prove the rate on a 2nd stack).
- **2026-06-10 · Defect-classifier "infra gap" = CONFIRMED NON-ISSUE.** The
  defect-classifier step is guarded `if gate_result.scenario_failed > 0`
  (iteration.py:3507, "only runs when there are failing tests to classify").
  run3 had 0 failing scenarios (100% pass; it failed the gate on
  substrate-verification = issue #1), so the classifier was correctly skipped →
  no defects file → the ORACLE prompt mis-narrated the EXPECTED absence as a
  "pipeline infrastructure issue." Symptom of issue #1's now-fixed path, not a
  bug. NET: the entire battery-analysis issue list is RESOLVED — #1 fixed (0/3→
  3/3); Scenario Lint advisory-by-design; defect-classifier expected-skip; TE-gen
  & implementer-94% variance non-recurring in 3/3. shared-parse-amount clean-ships
  DETERMINISTICALLY. The per-fixture deterministic ship-blockers are cleared.
  NEXT PHASE: Sprint 2 breadth (the north star) — prove the rate on a SECOND
  stack, since depth-on-one-fixture is exactly the diagnosis this reset exists to
  fix.
- **2026-06-10 · Sprint 2 T2.0 (breadth de-risk) IN FLIGHT — does python-fastapi
  complete a run at all?** Scaffolded an isolated python-fastapi target
  (~/sites/py-fastapi-sample); `dark-factory init` detected
  Python/FastAPI (src/app, pytest, active_adapters=["python-fastapi"]); the
  dark-factory-python image is already built. Authored a trivial Tier-A
  pure-function spec (parse_amount, trimmed port of shared-parse-amount). The
  cheap `dark-factory validate` loop caught FOUR of my own spec bugs pre-run:
  embedded raw NUL byte; a whitespace-vs-null-byte behavioral contradiction;
  missing dark-factory-metadata frontmatter; a T5 regex contradiction (FR-1 shape
  `\d{1,2}` rejects "1.234" as NOT_A_NUMBER, contradicting the TOO_MANY_DECIMALS
  classification — same class as the budget-brain §5/§10 bug) + missing §11/thin
  §9/§8a. Fixed all → spec PASSES (110/100). N=1 run launched (python-fastapi,
  45min). Completes (ship/fail/escalate, not crash) → adapter works, breadth
  viable; else → "make python_fastapi work" mini-sprint. [result pending]
- **2026-06-10 · T2.0 VERDICT: python-fastapi BREADTH IS VIABLE; first cross-stack
  blocker = JS-coupled anchor-coverage.** The python-fastapi run COMPLETED the
  front half (spec-validator → architect → qa-holdout → test-engineer; no crash,
  no hang) — so the adapter is NOT fundamentally broken (no "make python_fastapi
  work" disaster). It aborted at the TE on a coverage-floor violation: ALL anchors
  (FR-1..4/AC-1..7/EC-1..10) reported zero-covered DESPITE the TE generating 8
  scenario files. Root cause (confirmed): `_extract_anchors_from_text`
  (scenarios/coverage.py:131) credits anchors ONLY via JS idioms — `@covers FR-1`
  (JSDoc) and `[FR-N]` inside `describe()/it()/test()` literals. The python TE
  tagged correctly in PYTEST idiom — `@pytest.mark.covers("FR-1")` + `[FR-1]`
  docstrings on `def test_*` — neither matched → zero credit → false coverage-floor
  abort. The exact JS-coupling breadth exists to surface. NEXT ISSUE (the breadth
  blocker): teach the anchor-coverage extractor python conventions (broaden the
  @covers match to `.covers("...")` pytest markers; consider `[FR-N]` in pytest
  test docstrings). plan→Neo→Linus/Codex→re-run T2.0.
- **2026-06-10 · Sprint 9 (first breadth blocker) FIXED + merged** · `1cecbc5`.
  The T2.0 coverage-floor false-abort: the anchor-coverage extractor was
  TypeScript-coupled (credited `@covers`/describe-it-test only); taught it the
  python pytest idiom (`@pytest.mark.covers(...)` decorator + `[FR-N]` in
  `def test_*()` docstrings). Neo Option C. Dual gate caught a ship-gate
  false-credit in round 1 (bare `.covers(` credited any method call named covers,
  e.g. `query.covers("FR-1")` / shapely `poly.covers`) — re-anchored to the
  `@`-decorator form; round 2 CLEAN. Full unit 3934; 21/21 real anchors credited,
  0 false-credits. NEXT: re-run T2.0 to validate the floor clears (PAUSED — the
  pipeline re-run consumes the subscription; awaiting the subscription decision).
- **2026-06-10 · Sprint 9 VALIDATED + breadth advanced 5 stages on python-fastapi.**
  T2.0 re-run (on the switched subscription): the python pipeline now runs
  spec-validator → architect → qa-holdout → test-engineer ×2 → implementer ×2.
  THE TE COVERAGE FLOOR CLEARED (was the Sprint-9 abort point) → Sprint 9's python
  anchor-coverage fix is validated end-to-end. Two spec-authoring issues fixed en
  route (my hand-authored spec, not framework): (a) the precheck §9 parser wants
  per-FR intent TABLES `| # | Intent | Layer | AC |` (≥5 rows, AC column maps
  anchors), not bullet lists — my bullets parsed as 0 intents → validator blocker;
  reformatted to the proven table form. NOTE: the first re-run aborted at the
  VALIDATOR on this (the earlier `dark-factory validate` 110/100 had waved the
  unparseable §9 through as advisory — LLM-validator leniency/non-determinism;
  the deterministic precheck is the reliable signal). NEW breadth blocker
  (root-caused, the implementer abort): forbidden-pattern preflight matches inside
  COMMENTS — the implementer correctly AVOIDED `round()` and wrote
  `# Manual rounding to avoid using round() function`; the comment's `round()`
  tripped FR-3's forbidden `round(` (forbidden_patterns.py:25-27 deliberately does
  NOT strip comments, with a documented "revisit if false positives in practice"
  — this is that case). Same recurring "lexical match in non-code context" family
  as Sprints 8/9. NEXT: strip comments (lang-aware) before the forbidden-pattern
  match. plan→Neo→Linus/Codex→re-run.
- **2026-06-10 · Sprint 10 (forbidden-pattern matches comments) FIXED + merged** ·
  `90733c4`. The implementer-preflight abort from T2.0: the forbidden-pattern check
  matched `round(` inside the implementer's COMMENT explaining it AVOIDED round().
  Neo HYBRID: strip the line-comment tail before matching (comments-only, by file
  ext, double-quote-parity; NOT tokenize, NOT strings). Dual gate caught an
  escaped-quote false-negative in round 1 (Codex; Linus round-1 missed it, owned it
  round 2) → fixed with an escape-aware unescaped-quote count; round 2 both CLEAN.
  full unit 3945. DEFERRED (tracked, Sprint-8-shaped declaration-layer move): loose
  bare-identifier pattern declarations (`round(` matches `surround(`); single-quote
  + block-comment lexing (needs a tokenizer) — softened by the downstream
  blind-scenario/QA gate. Re-running T2.0 to validate the implementer clears
  preflight → reaches gate/oracle.
- **2026-06-10 · Sprint 10 VALIDATED — python-fastapi runs the FULL pipeline (all
  9 roles).** T2.0 re-run: ZERO preflight events (implementer cleared preflight →
  Sprint 10 works), and the run reached validator→architect→qa→TE→implementer×4→
  substrate-owner→defect-classifier→reviewer→oracle×2, oracle said NO-SHIP, and it
  was into a 2nd iteration (implementer×3→substrate-owner) when it hit the 45-min
  TIMEOUT. So python-fastapi is DEFINITIVELY fully viable end-to-end (T2.0's core
  question answered) — every agent role fired. The limiter is now the 45-min cap +
  back-half iteration slowness (7 implementer invocations, 2 pipeline iterations),
  NOT a deterministic blocker — same profile as the TS runs that needed >40min.
  NEXT: re-run at 90-min cap to get the honest terminal outcome (ship /
  stable_failure / escalate) + the oracle's no-ship reasoning.
- **2026-06-10 · T2.0 COMPLETE: python-fastapi VIABLE; clean-ship blocked by a
  deterministic PEP 668 build failure.** 90-min re-run → honest terminal ESCALATED
  (3 iters, 23 agents, $11.77, 61min — full pipeline incl. escalation). The impl is
  CORRECT (ORACLE_METRIC_DELTA: actual_pass_rate=100, blind scenarios 100%); the
  oracle no-shipped because the GATE failed on BUILD, not the implementation. Real
  gate reports (NOT the escalation prose): iter1 Build FAIL + Scenario Lint FAIL
  (advisory); iter2/3 FAIL(setup). Root cause: `pip install -e . -q` (the python
  build cmd) hits PEP 668 `externally-managed-environment` in the
  dark-factory-python container. Framework INCONSISTENCY: Dockerfile.python:12
  already uses `--break-system-packages` at IMAGE-BUILD time, but the RUNTIME cmds
  (setup/detect.py:342 generated build_cmd; scripts/quality-gate.sh:861-871
  install_cmd; quality_gate.py:52 server install) do NOT. Exactly the class of
  never-exercised-before blocker breadth exists to surface. NEXT (breadth issue,
  one at a time): make runtime python pip honor PEP 668 — cleanest is a single
  `ENV PIP_BREAK_SYSTEM_PACKAGES=1` in Dockerfile.python (covers build/install/
  server uniformly). plan→Neo→Linus/Codex→rebuild image→re-run.
- **2026-06-10 · Sprint 11 (PEP 668 python build) FIXED + merged** · `8364618`.
  The T2.0 clean-ship blocker: `pip install -e .` refused by PEP 668 in the
  dark-factory-python container. Neo A+B: `ENV PIP_BREAK_SYSTEM_PACKAGES=1` in
  Dockerfile.python (load-bearing — all runtime pip inherits it; Linus verified it
  survives the --user=1000:1000 switch) + `--break-system-packages` on the 3
  generated build_cmds (self-doc). Linus+Codex CLEAN. full unit 3946; image
  rebuilt + env verified baked in. Re-running T2.0 (90-min) — build should clear →
  real gate verdict (ship, or next blocker).
- **2026-06-10 · PEP 668 re-run hit intermittent TE variance (not the build).**
  T2.0 re-run on the fixed image aborted EARLY at the test-engineer (12min, 4
  agents): "scenario generation succeeded but no scenario files were written"
  (orchestrator.py:3111 — TE exited 0 but wrote nothing). NOT the PEP 668 build
  (never reached), NOT a regression (my change is build-only), NO API/rate-limit
  errors in stderr (switched subscription healthy; turns:200 is the cap-estimate
  fiction, not real spin). This is the known TE scenario-gen non-determinism —
  3 prior python runs cleared the TE fine. So the deterministic breadth blockers
  (§9 / Sprint 9 / Sprint 10 / Sprint 11) are CLEARED; what remains between python
  and a clean ship is the SAME back-half LLM variance as TS (TS hit 3/3 only after
  its deterministic blockers were fixed). POSSIBLE deterministic improvement if it
  recurs: retry the TE on empty-output instead of aborting (orchestrator.py:3111).
  Re-running once to clear the flake + validate the PEP 668 build end-to-end.
- **2026-06-10 · 5th breadth blocker root-caused: assertion-cap is TS-tuned
  (3rd of the family).** PEP668-validation re-run aborted at the TE again — this
  time `assertion_upper_bound FAIL: 3 files exceed 30 assertions` (50/36/45) after
  3 attempts. Root cause: `scenario_max_assertions_per_file=30` (config.py:80) is
  GLOBAL, but assertion-counting is per-language (validator.py:43-44): node counts
  `expect(`/`assert\w*(` (vitest uses ~1 `toEqual` per check); python counts every
  bare `assert ` (idiomatic pytest asserts each property separately → ~2-3 asserts
  per logical check). So 30 is ~2-3× stricter for python; the TE's reasonably-
  organized 3 files (by FR-group) blow the cap on counting alone, not cramming.
  Same family as Sprint 9 (anchor-coverage was JS-coupled). NOTE the prior re-run
  hit TE no-files variance; this one hit the assertion cap — the TE is the current
  python choke point (mix of a TS-tuned cap + scenario-gen variance). FIX (next):
  per-language assertion cap (scale python's ~2-3×) or per-language counting.
  plan→Neo→Linus/Codex→re-run.
- **2026-06-10 · Sprint 12 (per-language assertion cap) FIXED + merged** ·
  `5dfbaa7`. The TE assertion_upper_bound false-reject: `_ASSERTION_CAP_MULTIPLIER
  = {"python": 2}` → python effective cap 60 (Neo Option A; node-tuned 30 was
  ~2-3× stricter for pytest's bare-assert idiom). Linus+Codex CLEAN. full unit
  3947; T2.0 files (50/36/45) pass at 60. Re-running T2.0 (90min) — assertion cap
  cleared; whether the TE then sails through to the PEP668-fixed build + a real
  gate verdict is the open question (TE also has a no-files variance face).
- **2026-06-10 · Sprints 11+12 VALIDATED end-to-end; 2 new blockers, 1 may be the
  LAST deterministic one.** Post-Sprint-12 T2.0 re-run: BUILD PASS (PEP 668 fix
  validated), TE clean in 1 invocation (assertion-cap fix validated), blind
  scenarios 100%. Still ESCALATED (3 iters) on: (a) iter-1 Setup FAIL —
  implementer wrote scripts/df-setup.sh with `source .venv/bin/activate` but no
  venv (quality-gate.sh:1046 uses an implementer-authored df-setup.sh); SELF-
  CORRECTED by iter-2 (transient, implementer behavior, not a framework blocker).
  (b) iter-2/3 PERSISTENT: Type Check FAIL — `mypy .` (detect.py:309, generated
  because pyproject dev-deps list mypy) scans the implementer's setup.py →
  `import setuptools` has no stubs in the image (ships mypy, no types-*) →
  `[import-untyped]` hard error. Same class as PEP 668 (python-toolchain gap).
  KEY: if Type Check passes, the gate is all-PASS except advisory Scenario Lint
  (non-blocking, quality-gate.sh:1318) → Result PASS → python clean-ship in reach.
  So the mypy-robustness fix may be the LAST deterministic breadth blocker.
  Options: mypy --ignore-missing-imports / --disable-error-code import-untyped /
  add types-setuptools to image / scope to src. plan→Neo→Linus/Codex→re-run.
- **2026-06-10 · Sprint 13 (mypy import-untyped) FIXED + merged** · all 3
  python-typecheck producers now emit `mypy . --disable-error-code=import-untyped`
  (detect.py/integrator.py/quality-gate.sh). Neo 0.95; Linus caught the 3rd
  producer (integrate path) in round 1, both CLEAN round 2. Keeps hard-fail;
  suppresses only the un-stubbed-dep class. May be the LAST deterministic breadth
  blocker. Re-running T2.0 — possible python clean-ship.

## 🎉 2026-06-10 — BREADTH PROVEN: python-fastapi CLEAN-SHIPS (the north star)
T2.0 post-Sprint-13: **parse-amount-py CLEAN_SHIP** (exit 0, SPEC_SHIPPED, iters=2,
15 agents, $5.66, 38min; ORACLE_METRIC_DELTA actual_pass_rate=100). Gate iter-2:
Install/Build/Lint/Type-Check/Self-Tests/Blind-Scenarios ALL PASS; Scenario Lint
FAIL is advisory (non-blocking) → Result PASS → oracle SHIP. iter-1 failed
HONESTLY (Self-Tests + Blind Scenarios FAIL — real implementer miss), iter-2 fixed
it → shipped on merit. This is the FIRST python-fastapi clean ship EVER and
answers the reset's core "depth without breadth" diagnosis: the framework
GENERALIZES to a 2nd stack.

Got there by fixing 6 deterministic breadth blockers (each design→Neo→Linus+Codex→
merge), surfaced ONE AT A TIME by re-running T2.0 — every one a never-before-
exercised cross-stack coupling the TS-only history had hidden:
1. §9 spec format (precheck wants per-FR intent TABLES not bullets) — spec authoring.
2. Sprint 9 anchor-coverage: extractor was JS-only → credit pytest @covers/docstrings.
3. Sprint 10 forbidden-pattern matched COMMENTS → strip line-comments (lang-aware).
4. Sprint 11 PEP 668: runtime pip refused → ENV PIP_BREAK_SYSTEM_PACKAGES + flag.
5. Sprint 12 assertion-cap was node-tuned (30) → per-language ×2 for pytest density.
6. Sprint 13 mypy hard-failed on un-stubbed deps → --disable-error-code=import-untyped.
Three of six are the same family: a node-tuned mechanism / python-toolchain gap
that needed per-language treatment.

NEXT: establish the RATE (north star = clean-ship rate across ≥2 stacks, each ≥3×).
python is 1/1; run N=3 to confirm the python rate like TS's 3/3. Then the battery
spans 2 stacks with measured rates — "back on track" achieved.

## 2026-06-10 — NORTH STAR ACHIEVED: measured clean-ship rate across 2 stacks
python-fastapi N=3 (post all 6 breadth fixes): **2/3 CLEAN_SHIP** (runs 2&3 shipped
clean on ITER 1, oracle, ~26min; run 1 aborted at the TE on the intermittent
no-files flake "scenario generation succeeded but no scenario files written" —
NOT a deterministic blocker). So the honest battery now spans TWO stacks with
measured rates: **TS shared-parse-amount 3/3, python-fastapi parse-amount-py 2/3**
(SHIP_WITH_GAPS counted as non-ship; these are genuine clean ships, 100% blind
scenarios). The reset is demonstrably "back on track" — depth-without-breadth is
answered. The ONE python miss is the TE no-files variance, which has a clean
DETERMINISTIC fix: retry the TE on empty-output (orchestrator.py:3111 currently
RAISES/aborts instead of re-invoking). That fix would likely take python to 3/3.
NEXT (optional, toward 3/3): TE retry-on-empty-output; then breadth Tier-B (CRUD)
on both stacks per Sprint 2 T2.1.
- **2026-06-10 · Sprint 14 (TE retry-on-empty) FIXED + merged** · the python 2/3
  miss was the TE no-files flake (exits 0, writes nothing → orchestrator.py:3111
  ABORTED). Now bounded-retries (config.scenario_empty_retry_attempts=2, same
  prompt, SCENARIO_EMPTY_RETRY event, dry_run single-shot, fingerprints-once).
  Neo + Linus + Codex CLEAN. full unit 3950. Re-running python N=3 — expect →3/3.

## ✅ 2026-06-10 — NORTH STAR COMPLETE: python-fastapi 3/3 (two-stack rate honest)
Post-Sprint-14 python N=3: **3/3 CLEAN_SHIP** (run1 iter2, runs2&3 iter1; oracle;
24-39min; 100% blind scenarios). Final battery rate: **TS shared-parse-amount 3/3,
python-fastapi parse-amount-py 3/3** — measured, honest (SHIP_WITH_GAPS=non-ship),
across 2 stacks. The reset's "depth without breadth" diagnosis is DISCHARGED.
Caveat: the Sprint-14 SCENARIO_EMPTY_RETRY guard did NOT fire this batch (the
~1-in-3 TE no-files flake didn't recur), so 3/3 = the 7 breadth fixes + the flake
not hitting; the guard is insurance (test+review-validated) for when it does.
CAMPAIGN SUMMARY — python-fastapi: 0-ever → 3/3 via 7 merged fixes, each
design→Neo→Linus+Codex→merge, surfaced one-at-a-time by re-running T2.0:
§9-intent-tables, Sprint9 anchor-coverage(JS-only), Sprint10 forbidden-pattern-
comments, Sprint11 PEP668-build, Sprint12 assertion-cap(node-tuned), Sprint13
mypy-import-untyped, Sprint14 TE-retry-on-empty. NEXT (Sprint 2 T2.1, optional):
Tier-B (CRUD/single-table) on both stacks — widens beyond Tier-A pure functions.

## TRACKED FOLLOW-UP (2026-06-10, surfaced by Caleb) — spec-AUTHORING skill regression battery
GAP: the reset plan tests the PIPELINE (validator + agents) on a battery of specs,
but the SPEC-AUTHORING skills that PRODUCE specs — /df-write-spec, /df-write-plan,
/df-densify-spec (session-side, upstream of the pipeline) — are NOT tested for
robustness across stacks/domains anywhere. The plan's "spec gate" = the mechanical
validator; "meta-agents" = the judging agents (oracle/validator/classifier) — NOT
these authoring skills. Evidence it's a real gap: the breadth specs (parse-amount-py,
notes-crud) were hand-authored and still tripped format issues (bullet-vs-table §9,
missing frontmatter) the cheap validate-loop caught; the skills' cross-stack output
is unverified and could have rotted (e.g. still emitting the old bullet §9 the
precheck rejects, or TS-centric specs ignorant of python conventions).
PROPOSED TEST: a skill-output regression battery — run each authoring skill on a
small matrix (>=2 stacks x >=2 domains) and assert each output passes
`dark-factory validate` (same mechanical gate, ~3min/spec). Catches skill rot the
way the pipeline battery catches framework rot. Fits alongside Sprint 2 (breadth)
/ Sprint 3 (spec gate). [tracked; not yet scheduled]

## TRACKED FOLLOW-UP (2026-06-10) — detect.py command fixes don't propagate to existing env contracts
GAP found scaffolding Tier-B: the Sprint-11 (PEP668 --break-system-packages) and
Sprint-13 (mypy --disable-error-code=import-untyped) fixes live in
darkfactory/setup/detect.py, which only runs at `dark-factory init`. An
ALREADY-init'd project's `architecture/environment.toml` [commands] keeps the
stale `pip install -e . -q` / `mypy .` — and `dark-factory init --update` does NOT
refresh the environment contract (it rewrote config.toml + project-hints only).
So framework command-default fixes silently don't reach existing projects; their
gate uses the stale command (PEP668 is masked by the Dockerfile ENV, but stale
`mypy .` is a live Type-Check risk). Worked around for py-fastapi-sample by editing
environment.toml directly. PROPER FIX (tracked): `init --update` (or a dedicated
refresh) should re-materialize the environment contract's detected commands from
the current detect.py, or emit a delta — so default fixes propagate. [tracked]

## 2026-06-10 — Tier-B (T2.1) de-risk #1: notes-crud on python-fastapi → ESCALATED; root cause = TestClient dep (httpx) not installed
First Tier-B run (in-memory CRUD, TestClient/in-process — the live-HTTP-server
model is retired): ESCALATED (3 iters, $10.54, 55min). Build/Lint/Type-Check ALL
PASS every iter (the architect modeled a uvicorn server + the app, TE wrote
TestClient scenarios, implementer wrote the FastAPI CRUD, stale `mypy .` didn't
bite — no setup.py). The blocker: Self-Tests + Blind Scenarios FAIL at 0%,
`RuntimeError: starlette.testclient requires the httpx package` — every test does
`from fastapi.testclient import TestClient` → needs httpx → NOT installed. ROOT
CAUSE: build cmd `pip install -e . -q` installs MAIN deps only (fastapi/uvicorn),
NOT the project's `[dev]` extras where httpx/pytest live; the dark-factory-python
image ships pytest (universal) but not httpx (FastAPI-specific). So the FastAPI
TestClient transport dep never installs → 0% collection error. The Tier-B steps
themselves (architect/TE/implementer for a stateful multi-endpoint app) all
WORKED — this is purely a test-dependency-install gap. NEXT FIX (Neo): where does
httpx come from — (a) image (add httpx to python-tools, like pytest — it's the
standard FastAPI TestClient transport), (b) build installs dev extras
`pip install -e ".[dev]"`, or (c) httpx as a main dep? plan→Neo→Linus/Codex→
rebuild→re-run. (Also confirms the env-contract-staleness gap is real but didn't
bite here — mypy passed because no setup.py.)

## 2026-06-10 — Sprint 15 (python test-extras install) FIXED + merged · 6e16248
The Tier-B httpx blocker: detect.py now installs the project's detected test-extras
group — `pip install -e '.[dev]' -q --break-system-packages` (Neo Option B; solves
the class "declared test deps never installed", not just httpx). Hardened against
malformed/adversarial pyproject (isinstance ladder → bare, never crash; returned
extra name validated via re.fullmatch + alnum start/end → no shell injection / no
trailing-\n bypass). Neo + Linus + Codex — FIVE review rounds, each closed a real
edge (crash / priority-path / injection / \n-bypass); exhaustively verified (27
unsafe rejected, 11 valid accepted). Backstop: architect_addendum prose recommends
'.[dev]' + names httpx. LESSON: splicing a project-derived string into a shell
command has a long edge tail — validate exhaustively up front. ALSO: regenerated
py-fastapi-sample's env contract (deleted environment.toml + deltas, re-ran init
--update) so its bootstrap delta now carries '.[dev]' + the mypy flag — the
env-contract-staleness gap (tracked) required this manual regen. Re-running Tier-B.

## 🎉 2026-06-10 — TIER-B CLEAN-SHIPS on python-fastapi (breadth beyond pure functions)
notes-crud (in-memory CRUD, multi-endpoint FastAPI, TestClient scenarios):
**CLEAN_SHIP on iter 1** (exit 0, SPEC_SHIPPED, 8 agents, $3.14, 24min,
ORACLE_METRIC_DELTA actual_pass_rate=100). Gate: Install/Build/Lint/Type-Check/
Self-Tests/Blind-Scenarios ALL PASS; Scenario Lint FAIL = advisory → Result PASS.
After the httpx/test-extras fix (Sprint 15), the de-risk-#1 0%-collection error
cleared and the implementer wrote a correct stateful CRUD first try. So
python-fastapi now clean-ships BOTH Tier-A (parse-amount-py pure function, 3/3)
AND Tier-B (notes-crud CRUD, 1/1 iter-1). Breadth is proven across TWO tiers on
the 2nd stack — the depth-without-breadth diagnosis is discharged at Tier-B too.
Tier-B de-risk took 1 framework fix (Sprint 15 test-extras) vs Tier-A's 7 — the
Tier-A campaign hardened most of the shared cross-stack coupling.
NEXT (optional, per T2.1/T2.2): Tier-B N=3 for a rate; replicate Tier-B on TS;
Tier-C (DB substrate — the harder frontier).

## 2026-06-10 — Tier-B on TS: ESCALATED on a barrier-audit false-positive (deep, authorization-sensitive finding)
notes-crud-ts (fastify in-memory CRUD on budget-brain-v2) ESCALATED (3 iters,
$7.26, 44min) DESPITE a fully-green gate every iter (Install/Build/Lint/TypeCheck/
Self-Tests/Blind-Scenarios PASS; pass_rate=100, satisfaction=100, scenario_total
=44; Scenario Lint advisory). Root cause (trace, not escalation prose):
BARRIER_VIOLATION{role=test-engineer, path=apps/api/src}. The TE's vitest inject
scenarios import the app entrypoint — `import { createServer } from
'../../../apps/api/src/server.js'` — and that RELATIVE PATH contains the masked
source-dir string `apps/api/src`, so audit/checker.py flags the TE as touching
source. It persists every iter → ITERATION_TEMPORAL_SIGNATURE unchanged → TRAJECTORY
PLATEAU → ESCAPE_HATCH → escalate, even though the gate passes.
WHY TS not python: python notes-crud shipped because `from app.main import app` is
a MODULE path (no `src/app` filesystem string); TS relative imports embed the
source-dir path. This is the reset's known "barrier audit false-positives on
legitimate import strings", concrete + cross-stack.
THE TENSION (real, not just a bug): in-process inject/TestClient testing REQUIRES
the scenario to import the app entrypoint; the TE is barred from source (to write
unbiased tests). The TE wrote a path STRING (per the spec) without READING source
(masked) — but the audit's path-string match can't tell "import-path string" from
"source read". 
FIX OPTIONS (authorization-sensitive — touches the barrier audit, the d2fecbf
pause-and-ask class; needs careful Neo design + AUTHORIZATION-MODEL review):
(a) import the app via a PACKAGE-NAME/workspace alias (e.g. @budget-brain/api)
    instead of a relative `apps/api/src/...` path — a spec/convention fix, no
    barrier change, mirrors why python's module import is clean; (b) refine the
    barrier audit to not flag an import-path-string that resolves to the source
    ENTRYPOINT for in-process testing (authorization-sensitive); (c) a barrier-safe
    app test-handle. (a) is the least-invasive + non-authorization-sensitive.
NOTE: python Tier-B clean-shipped; TS Tier-B is functionally correct (100% gate),
blocked only by this barrier false-positive. Distinct from python's httpx blocker.

## 2026-06-11 — TS Tier-B barrier FP FIXED (option a); re-run surfaced the NEXT blocker = REVIEWER false-positive
Implemented option (a) — the package-alias import — to clear the barrier-audit
false-positive (no framework/barrier code touched; spec+fixture only):
- Added a `./server` subpath export to budget-brain-v2 `apps/api/package.json`
  (`"@budget-brain/api/server"` → `./src/server.ts`) and rewrote notes-crud-ts §5
  to require `import { createServer } from '@budget-brain/api/server'` (NOT a
  relative `apps/api/src/...` path). Mirrors python's `from app.main import app`.
  Committed to budget-brain-v2 as `ecff0a0` (fixture+spec only).
- Pre-validated CHEAPLY before the run: a vitest probe resolved the package-name
  import to source via Node package self-referencing (works under apps/api's
  vitest root, NO build/symlink needed) and `app.inject('/health')`→200; confirmed
  `@budget-brain/api/server` matches NEITHER `checker.py:35`'s substring
  (`apps/api/src`) NOR `source_leak_scan.py`'s real-file-path matcher; spec
  re-validates 100/100; typecheck green; nothing else deep-imports apps/api.
- Neo APPROVE (A1 committed fixture + source-target). Both preconditions verified
  (package name exactly `@budget-brain/api`; no deep-imports of apps/api/*).
RE-RUN RESULT (run_id 10291d57): **barrier FP GONE** — zero BARRIER_VIOLATION,
44/44 scenarios pass every iter, gate Result PASS. But STILL ESCALATED (3 iters)
on a NEW blocker (classic one-at-a-time): the **REVIEWER returns REQUEST CHANGES
(74/100 < 80)**, driven primarily by a **FALSE-POSITIVE "Critical prototype
pollution"** on `apps/api/src/common/health.ts:15` — a benign static-key
`(module.exports as ...).__healthChecks = healthChecks` assignment in PRE-EXISTING
scaffolding (authored by api-bootstrap SHIP `645dec1`, OUTSIDE the notes-crud-ts
spec scope). Not prototype pollution; not the implementer's code. Plus two minor
findings: my `./src/server.ts` export (legit production nit — flip to
`./dist/server.js`, still resolves since gate builds before tests) and the
pre-existing no-op lint script (`echo "No lint configured"`). Oracle's checklist:
any Critical security finding → auto-NO-SHIP; 74 < 80 threshold. Test-failure set
is empty+identical across iters → TRAJECTORY PLATEAU → escalate.
CLASSIFIER NO-SHIP RULED OUT as an independent blocker (per Caleb's "investigate
classifier first"): the trace also logged "defects.json missing → Oracle required
to NO-SHIP per 4.6b.0.iv", but this is NOT causal. (1) The classifier only runs
when `scenario_failed > 0` (iteration.py:3505) → defects.json is ALWAYS absent on
a 100%-pass gate, by design. (2) `oracle.md:288` conditions "MUST NO-SHIP on
classifier error" on "if there are any failing scenarios" — zero here. (3) The
"required to NO-SHIP" log fires inside `apply_classification_routing`, which runs
only AFTER the Oracle already returned NO-SHIP (sets escalation category, doesn't
decide). (4) EMPIRICAL PROOF: python notes-crud clean-shipped with NO defects.json
and an empty `CLASSIFICATIONS: {}` block — oracle first line `SHIP`. The TS oracle
merely MISREAD the expected absence as a "pipeline classification failure" in its
rationale (non-causal noise; minor oracle-prompt clarity gap — could note in
oracle.md that absent defects.json with 0 failures is EXPECTED, not a failure).
SO: the reviewer FP is the TRUE + ONLY ship-blocker → a meta-agent reliability
problem on the "trustworthy meta-agents" spine. FORK for next session: (A) fix the
REVIEWER — don't mislabel static-key module.exports as prototype pollution
(security-severity calibration) and/or scope ship-blocking to the feature DIFF not
pre-existing shipped code; highest leverage (this FP blocks ANY feature with
imperfect surrounding code); authorization-adjacent (changes a meta-agent's
blocking behavior) → Neo design + dual gate. (B) clean the fixture (flip
export→dist, real lint, refactor health.ts) — cheaper but papers over reviewer
reliability + is out-of-scope target surgery. Recommend A + the cheap export→dist.

## 2026-06-11 — Sprint 16 (reviewer security authority) SHIPPED; but TS Tier-B re-run re-tripped the BARRIER FP → option (a) is NOT robust
Sprint 16 merged to main (2535065): deterministic security pre-pass is now
authoritative for security ship-gating (Oracle verdict-block + reconsideration
backstop; LLM security prose advisory). Neo APPROVE; Linus + Codex CLEAN (Linus
caught a real FIX-IT 2 regression — a context-free metrics re-prompt could revert
the reconsidered SHIP; fixed to validate-without-re-prompt). 3966 unit tests.
NOT YET VALIDATED end-to-end (see below).
RE-RUN (run_id 4774425a) to validate Sprint 16 RE-TRIPPED the barrier FP that
option (a) was supposed to fix: BARRIER_VIOLATION{test-engineer, apps/api/src}.
Pulled the trace — the scenario FILES are clean (all import @budget-brain/api/server,
§5 worked), so the violation is in the TE's TRANSCRIPT/output. ROOT CAUSE: the
FRAMEWORK AGENT DEFS hard-code the fixture's masked path as the canonical example —
agents/test-engineer.md:101 `specifier: "../../../apps/api/src/server.js"` and :112
`import { createServer } from '../../../apps/api/src/server.js';`; also
implementer.md:43,73 and spec/manifest.py:963. The TE echoes its own agent-def
example → checker.py:35 `check_output_for_barriers` blunt-substring-matches
`apps/api/src` → BARRIER_VIOLATION → composite_blocked → forces NO-SHIP regardless
of the gate/oracle (so Sprint 16 can't even be validated on this run). In the prior
run (10291d57) the TE happened NOT to echo it → no violation; this is probabilistic.
WHY option (a) is insufficient: the masked path is baked into the framework agent
defs AND the spec (§2 Patterns, §4 File Locations) as legitimate INPUT the TE reads;
checker.py flags ANY echo of given text. Unlike source_leak_scan.py (the
high-precision scanner, which subtracts `excluded_text`=spec and matches only real
file paths), checker.py does NOT subtract the agent's given-input → it false-positives
on legitimate echoes. This is the reset's known "barrier audit false-positives on
legitimate import/path strings", now root-caused.
PER CALEB'S PRE-AUTHORIZATION ("if the package-alias still trips the audit, escalate
to option b — refine the barrier audit, AUTHORIZATION-SENSITIVE"), this escalates to
option (b). Candidate fixes:
- B1 (cheap, NON-auth-sensitive): change the framework agent-def examples
  (test-engineer.md, implementer.md, manifest.py) to NOT hard-code a real source
  path (neutral placeholder / package-name form). Removes the dominant echo source;
  leaves the spec-echo latent (fragile).
- B2 (principled, AUTH-SENSITIVE): make checker.py::check_output_for_barriers
  subtract the agent's given-input text (prompt = agent def + spec + injected
  contracts) before flagging — mirror source_leak_scan.py's excluded_text. An echo of
  a path that was IN the agent's own prompt is not a source READ (source is
  tmpfs-masked); only flag paths NOT given to the agent. Shifts reliance for precise
  detection to source_leak_scan.py (by design the high-precision detector) + the
  primary Docker mask. De-escalating (fewer FPs) but WEAKENS checker.py's crude
  dir-match → AUTHORIZATION-MODEL review + Neo design + dual gate required.
RECOMMEND: B2 (root fix) + B1 (hygiene). Sprint 16 validation must wait until the
barrier FP is robustly fixed (else composite_blocked masks the oracle outcome).

## 2026-06-11 — TS Tier-B DETERMINISTIC clean-ship on a DE-CONTAMINATED spec; 3 framework fixes landed
Three framework fixes shipped to main this session, each Neo design + Linus+Codex
dual-gate CLEAN:
- **Sprint 16 (2535065): deterministic security authority.** The reviewer
  fabricated a "Critical prototype pollution" on benign pre-existing scaffolding,
  forcing NO-SHIP. Fix: the deterministic security pre-pass is authoritative for
  security ship-gating (Oracle verdict-block + reconsideration backstop); LLM
  security prose is advisory. Validated live (Security 15/15, no fabrication).
- **Sprint 17 (1b215ff B1 + 2f2848d B2): barrier-audit input-subtraction.** The
  framework agent defs hard-coded the fixture's masked path as the canonical
  import example; the TE echoed it and checker.py's blunt substring match
  false-positived. B1: neutralized the agent-def examples. B2: checker.py now
  subtracts the agent's given inputs (prompt+spec+manifest+agent-def) before
  flagging — an echo of given text is not a source READ; scoped to MULTI-segment
  source dirs (single-segment 'src' kept blunt to avoid over-suppression);
  holdouts/.git always fire. De-escalating; AUTHORIZATION-MODEL Audience:no-change.
- **Spec de-contamination (Caleb's holistic catch).** notes-crud-ts §5 "Tests MUST
  import by package name" + the apps/api package.json ./server export were a
  framework workaround (dodging the barrier audit) encoded as a PRODUCT REQUIREMENT
  — reverted (budget-brain 5345439). LESSON now in memory
  [[feedback_no_framework_workarounds_in_specs]]: fix the framework, never the
  spec; contamination invalidates breadth claims (the contaminated-spec run only
  shipped via reviewer VARIANCE declining to re-enforce §5 — iter-2, reconsideration
  fired). On the CLEAN spec the same run ships DETERMINISTICALLY at ITER 1
  (run 5ac28a92, satisfaction 100, no reconsideration). THAT is the honest TS
  Tier-B breadth proof.
SPEC-CONTAMINATION AUDIT (general-purpose sweep of all 26 specs): only notes-crud-ts
was introduced this session. PRE-EXISTING contamination found + ranked:
shared-parse-amount (Scenario-Architect harness directives — LOAD-BEARING, it's
the TS Tier-A 3/3 spec) → DE-CONTAMINATED (budget-brain 2e5e0d7; no framework
change needed, test-engineer.md:182 already enforces the constraint; re-validating
now); database-foundation (embeds pipeline-escalation history + applies_to:[scenarios,
holdouts] + enforcement:architect.amend_check — DEFERRED, not load-bearing);
borderline role-name leaks in api-bootstrap/echo/auth-schemas/py-notes-crud footnotes
(DEFERRED). Other ~20 specs CLEAN.
IN FLIGHT: shared-parse-amount (TS Tier-A, clean spec) + parse-amount-py (python
Tier-A regression) running concurrently. NEXT: python notes-crud (Tier-B regression)
to confirm Sprint-16 + Sprint-17 didn't break python breadth.

## 2026-06-11 — python regression check: NO regression (apparent one was cross-spec test pollution)
Validation sweep on the post-Sprint-16/17 framework: TS Tier-B (notes-crud-ts,
clean spec) iter-1 SHIP, TS Tier-A (shared-parse-amount, clean spec) iter-1 SHIP,
python Tier-A (parse-amount-py) iter-1 SHIP. python Tier-B (notes-crud) appeared to
ESCALATE — but the trace showed it was NOT a framework regression:
- Gate 100%, Security 15/15 (no fabrication), no barrier violation — all meta-agent
  changes behaved correctly.
- The Reviewer NO-SHIP was a LEGITIMATE scope violation: the project still contained
  `src/app/parse_amount.py` + `tests/test_parse_amount.py` — leftovers from the
  parse-amount-py run I'd done in the SAME py-fastapi-sample project. `dark-factory
  run --fresh` does NOT reset source, so spec-A's impl polluted spec-B's review.
- Sprint-16's reconsideration CORRECTLY did NOT override it (scope violation is a
  non-security blocker → preserved). The iter-2 implementer deleted the files, but
  the PLATEAU-on-empty-gate-failure-set trap was about to escalate it anyway
  (confirms that lurking structural issue).
METHODOLOGY LESSON (matters for the autonomous loop): running DIFFERENT specs in one
fixture project without resetting source → the later spec's Reviewer flags the
earlier spec's impl as an out-of-scope scope violation. Use a source reset between
different-spec runs (the benchmark battery's reset_cmd = git reset --hard <pin> +
clean does this; ad-hoc `dark-factory run --fresh` does not). N=3 of the SAME spec is
fine (no cross-spec impl). Re-running python notes-crud on a cleaned project now to
get the literal clean ship (expect iter-1).
CONCLUSION: Sprint-16 (oracle security authority) + Sprint-17 (barrier audit) do NOT
regress python breadth. The honest scorecard on the post-fix framework + clean specs:
TS Tier-A ✓, TS Tier-B ✓, python Tier-A ✓, python Tier-B (re-validating clean).

## 2026-06-11 — AUTONOMOUS LOOP started (Caleb /goal); Item 1 (validator non-determinism) DONE + 4/4 breadth validated
Caleb authorized an autonomous loop through the roadmap (plan→Neo→implement→Linus+
Codex→validate), re-ordered to start with spec-validator trustworthiness (it blocks
reliable rate measurement). LOOP STATE:
- **Item 1 — spec-validator FV-audit non-determinism: DONE + MERGED + VALIDATED.**
  Sprint 18 (1c749ca): the Free-Variables misclassification audit was keyed on the
  validator LLM's free-form report → hallucinated false abort (python notes-crud).
  Made it spec-authoritative. Neo APPROVE; Linus ACCEPT + Codex CLEAN (pin 1 verified
  to fail on old code). VALIDATED END-TO-END: post-fix, python notes-crud cleared
  spec-validation ("Spec validation passed") and SHIPPED clean iter-1 (run ca3ac419,
  100%, satisfaction 100) — recovering from connectivity-induced agent API failures
  via the framework's retry. Linus follow-up grep-sweep: the FV audit was the primary
  free-form-prose-keyed hard-abort; other orchestrator raises key on STRUCTURED
  channels (unresolved_contracts: YAML, status files) or deterministic checks — a full
  45-raise manual classification is a deeper Sprint-3 task but no other obvious
  prose-keyed aborts.
- **BREADTH 4/4 on the post-fix framework + DE-CONTAMINATED specs (clean iter-1
  ships):** TS Tier-A (shared-parse-amount) ✓, TS Tier-B (notes-crud-ts) ✓, python
  Tier-A (parse-amount-py) ✓, python Tier-B (notes-crud) ✓. This is the honest
  2-tier × 2-stack proof — no spec contamination, post-fix framework.
QUEUED (loop, run-heavy — deferred for a STABLE-CONNECTIVITY session; intermittent
internet caused architect+implementer API failures this session, though the framework
retried through them):
- **Item 2 — Tier-B N=3 rates.** Needs per-run source reset (cross-spec-pollution
  lesson). Baselines scoped: budget-brain-v2 HEAD is clean (notes route uncommitted);
  py-fastapi-sample needs a one-time clean-baseline commit (notes main.py IS committed
  → git rm + commit a scaffold-only baseline to reset to).
- **Item 3 — narrow the Sprint-16 reconsideration guard** (fires on any clean-gate
  NO-SHIP; LOW priority — observed preserving legitimate no-ships correctly, harm is
  an extra oracle invocation + occasional vague feedback; not breaking ships).
- **Item 4 — Tier-C frontier** (database-foundation de-contam + DB/substrate ship on
  python; multi-blocker, run-heavy).
- Standing infra: env-contract/agent-def sync command; spec-authoring skill battery
  (+contamination check); cost-from-telemetry; integration-suite repair; footnote
  de-contamination.

## 2026-06-11 — Item 2 (Tier-B N=3 rates) DONE: rate matrix complete; meta-agent variance is the rate-limiter
Tier-B N=3 via `dark-factory benchmark` with per-run source resets (clean baselines:
budget-brain HEAD 2e5e0d7; py-fastapi NEW baseline 03397c7 = scaffold-only, no notes
impl, dropped a hallucinated httpx2>=2.3.0 dep that broke clean rebuilds). RESULTS:
- **notes-crud-ts (TS Tier-B): 2/3 clean_ship** (run 2 ESCALATED — full 3 iters,
  last_stage=escalation, abort_reason=None → a NO-SHIP→iterate→escalate from
  reviewer/oracle NON-DETERMINISM, not a missing capability; runs 1&3 shipped iter-2).
  avg $7.36/run, ~40min.
- **notes-crud (python Tier-B): 3/3 clean_ship** (all iter-1, ~27min, $4.33/run avg).
FULL RATE MATRIX (post Sprint-16/17/18, DE-CONTAMINATED specs):
  TS:     Tier-A 3/3 | Tier-B 2/3
  python: Tier-A 3/3 | Tier-B 3/3
The ONE variance point is TS Tier-B. KEY FINDING: Tier-B is VARIANCE-LIMITED, not
capability-limited — the binding constraint on the rate is META-AGENT (reviewer/
oracle) non-determinism, concentrated on the TS stack's larger review surface
(package exports, lint, long-functions — vs python's simpler surface). Tier-A is
3/3 both stacks because a pure function gives the reviewer ~nothing to second-guess.

## RE-PRIORITIZED LOOP (the N=3 data changed the order)
1. **META-AGENT RELIABILITY — promote to TOP (was the deprioritized "Item 3").** It's
   the proven Tier-B rate-limiter. Cluster: reviewer non-determinism (flags findings
   inconsistently on identical code); PLATEAU-on-empty-gate-failure-set trap
   (reviewer-driven NO-SHIPs escalate even when addressable, because the gate
   test-failure set is empty at 100% pass); Sprint-16 reconsideration guard (fires on
   every clean-gate NO-SHIP). FIRST ACTION = MEASUREMENT not fix: capture a real
   escalation trace (the N=3 battery RESETS between runs and WIPES the trace — re-run
   notes-crud-ts WITHOUT reset, or add trace capture, to see exactly what the reviewer
   flagged on an escalating run). Design the fix off real data. Moving TS Tier-B
   2/3→3/3 is the highest-leverage remaining work; generalizes (Tier-C would inherit
   the same ceiling).
2. **Tier-C (DB/substrate on python)** — the breadth frontier; database-foundation
   de-contam (its applies_to:[scenarios,holdouts] + enforcement:architect.amend_check
   are framework-coupled, not a simple spec edit) + postgres adapter on python +
   substrate/migrations. Do AFTER #1.
3. **Standing debt:** env-contract/agent-def sync command (bit us — hand-synced agent
   defs this session); spec-authoring skill regression battery (+contamination check);
   cost-from-telemetry; integration-suite repair; validator consistency-gate
   non-determinism (Sprint 3); footnote de-contamination.

SESSION 2026-06-11 SHIPPED (all Neo design + Linus+Codex dual-gate CLEAN, merged main):
Sprint 16 (2535065, oracle deterministic security authority), Sprint 17 (1b215ff+
2f2848d, barrier-audit input-subtraction + agent-def example hygiene), Sprint 18
(1c749ca, spec-validator FV-audit spec-authoritative). Plus spec de-contamination
(notes-crud-ts §5 + shared-parse-amount SA-directives; budget-brain 5345439/2e5e0d7).

## 2026-06-11 — META-AGENT RELIABILITY item: the "2/3" was a CONTAMINATED MEASUREMENT, not reviewer non-determinism (Sprint 19)
Loop item #1 (the proven Tier-B rate-limiter). FIRST ACTION was measurement, not a
fix — and the trace FALSIFIED the hypothesis. Full evidence:
docs/plans/meta-agent-investigation.md. No new runs were needed: the runtime ARCHIVES
every run under .dark-factory/archive/ (NOT wiped by --fresh), so the escalated battery
run 2 (20260611-161212-notes-crud-ts-c9d47e15) was fully reconstructable.
WHAT ACTUALLY HAPPENED (NOT reviewer/oracle non-determinism — the reviewer NEVER RAN):
- All 3 iters: GATE exit_code=2, pass_rate=0, scenario_total=0. The gate died at the
  **Setup** stage before any scenario. Setup ran `npx drizzle-kit migrate` → no
  drizzle.config.json → exit 1.
- The drizzle setup + a [services.postgres] block came from database-foundation's env
  delta (0005, UNTRACKED). The Tier-B battery reset (`git reset --hard 2e5e0d7 &&
  git clean -fd -e .dark-factory`) resets TRACKED source but the `-e .dark-factory`
  exclusion + untracked-ness mean 0005 SURVIVES → the orchestrator re-folds it into
  environment.toml → the in-memory notes-crud-ts spec inherits a Postgres migration as
  its gate Setup. Env-contract sibling of [[project_reset_source_between_different_spec_runs]].
- Ship/escalate split = IMPLEMENTER LUCK on the df-setup.sh escape hatch
  (quality-gate.sh:1046; implementer.md:69 frames it as "if the spec REQUIRES
  migrations"). Shipping runs wrote a df-setup.sh shim → Setup PASS → ship. The
  escalated run correctly read "in-memory, no DB" and saw no reason to → stale drizzle
  failed all 3 iters. Sealed by the PLATEAU trap (scenario_total=0 across iters →
  trajectory.py score-only classifier → "fundamentally different approach" → escalate).
CONCLUSION: **"TS Tier-B 2/3" was a contaminated measurement, not a meta-agent variance
ceiling.** The plan's "meta-agent reliability is the Tier-B rate-limiter" conclusion
rested on this now-falsified run. Re-validates [[feedback_pull_trace_before_theorizing]]
(the scorecard last_stage=escalation/abort_reason=None inference was wrong) and
"measure before fixing."
SHIPPED — Sprint 19 (e3147a5, hermetic env-contract reset; Neo design + Linus+Codex
round-2 CLEAN, merged main): benchmark `_framework_reset` now runs
`git clean -fdx -- .dark-factory/architecture/` on a git target → env-contract resets to
the source pin (drops cross-spec untracked deltas/manifests). `-x` load-bearing
(gitignored env-contract path would inert a plain -fd); failed clean surfaced as a
runner error (a silent defeat re-contaminates the measurement); non-git targets skip.
4 new unit tests. Neo's key steer (against my draft): the deterministic generalizable
fix is NOT "gate skips a stale setup command" (an under-specified guess) — it's measure-
ment hygiene NOW + (separately) fixing the PLATEAU mislabel by routing the framework's
already-computed exit_code==2/SETUP_FAILURE signal into the escalation site
(iteration.py:3447 reads trajectory only). 
NEXT (validation, run-heavy): re-run TS Tier-B N=3 on the fixed runner for the HONEST
rate. Then the standing robustness item: the setup-failure→PLATEAU mislabel
(Neo's B2 — its own Neo+Linus sprint; exit_code==2 guard at iteration.py:3447 +
substrate-owner's structured "configuration mismatch" finding). Auto-neutralizing a
stale commands.setup via an orchestrator-emitted tracked amend (amend.py) is backlog,
NOT a silent gate skip.

## 2026-06-11 — Sprint 19 VALIDATION: contamination FIXED, but rate is STILL 2/3 (cause re-diagnosed — honest)
TS Tier-B N=3 on the fixed runner (scorecard /tmp/sprint19-ts-validate-scorecard.jsonl;
same over-excluding reset_cmd, framework clean compensates). RESULT — **2/3 clean_ship,
the rate did NOT move**, but the ONE escalation has a COMPLETELY different, genuine cause:
- Run 1: clean_ship iter-1 ($4.51). Run 3: clean_ship iter-2 ($6.08).
- Run 2: ESCALATED ($10.0) — but NOT contamination. Sprint 19 confirmed working: all 3
  runs got PAST Setup (no drizzle), ran scenarios (scenario_total=21). Live-verified the
  env-contract was clean (0005-0007 dropped, only tracked 0001-0004, no drizzle/postgres).
  Run 2's escalation = a GENUINE implementer state-isolation bug: the in-memory note store
  leaked state across requests/tests (violates "Map per server instance"), failing 6 blind
  scenarios (FR-1/2/5, EC-1/6, all classified IMPL_BUG). Gate exit_code=1 (self-tests +
  blind 71%→76%), NOT exit_code=2. The framework CORRECTLY no-shipped buggy code.
HONEST CONCLUSION: Sprint 19 fixed MEASUREMENT INTEGRITY (contamination gone), not the
rate. The plan's "reviewer non-determinism / meta-agent variance is the Tier-B rate-
limiter" is FULLY FALSIFIED on both counts — the original 2/3 was env-contract
contamination; the TRUE post-fix remaining variance is **IMPLEMENTER SOLUTION-QUALITY**
(state isolation on a stateful multi-endpoint CRUD), not the reviewer/oracle.
B1 EARLY-ESCALATION CHECKED AND EXONERATED (pulled the per-iter failing sets before
theorizing): run 2 iter-1 failed {EC-1,EC-6,FR-1,FR-2,FR-5}; iter-2 failed the SAME 5
PLUS {AC-2,AC-3,AC-8} — the core state-isolation cluster stayed failing AND the set GREW.
The implementer was FLAILING, not converging (the +5% scenario pass-rate was peripheral
noise). So the B1 iter-2 early-escalation (iteration.py:1238: PLATEAU + stable_failing_set
+ implementer-active) fired CORRECTLY and saved an iter-3 that would not have fixed a
structural bug the implementer couldn't locate. No premature-escalation framework defect.
HONEST ENDPOINT: TS Tier-B 2/3 is GENUINE IMPLEMENTER SOLUTION-QUALITY VARIANCE on
stateful multi-endpoint CRUD (state isolation) — a model-capability limit, NOT a framework
bug and NOT something a deterministic framework fix moves. The framework behaved correctly
end-to-end (shipped 2 good impls, no-shipped 1 buggy one). There is NO clean framework
lever to move this 2/3→3/3; doing so means implementer reliability on stateful logic
(prompt/guidance, uncertain) or more iteration budget (cost for marginal rate). The plan's
goal "move TS Tier-B 2/3→3/3" rested on the now-falsified reviewer-non-determinism premise;
with the contamination removed, the honest matrix is TS-A 3/3, TS-B 2/3 (genuine variance),
py-A 3/3, py-B 3/3. RECOMMEND: accept the honest 2/3 and advance to the genuine frontier —
Tier-C (DB/substrate on python, plan item #2) — rather than chase an uncertain 1/3.

DECISION (Caleb, 2026-06-11): advance to Tier-C. TS-B 3/3 is NOT dropped — TRACKED BACKLOG:
"re-measure TS-B after Tier-C (state-isolation is the same family as DB/substrate work, may
lift passively; cheap to fold a TS-B run into a later battery) AND pursue a dedicated sprint
ONLY if a DETERMINISTIC state-isolation lever emerges (not a prompt nudge)." Rationale also
on statistics: N=3 @ 2/3 has a ~30–90% CI — "stuck at 2/3" is not even established; more
samples (N=5+) post-Tier-C will tighten it.

## 2026-06-11 — TIER-C SCOPING (DB/substrate ship on python). Measure-first map before building.
Goal: get a DB-backed (postgres + migration) spec to CLEAN-SHIP on python (py-fastapi-sample).
KEY FINDING (Explore sweep): the postgres adapter (darkfactory/adapters/postgres/) is GENUINELY
STACK-AGNOSTIC — no TS/drizzle-specific code; it expects the language-aware ARCHITECT to emit
commands.setup (the migration cmd) and the manifest author to declare bootstrap_check SQL. So
Tier-C on python needs NO postgres-adapter changes. The gaps are python-side + wiring:
  GAP-1 python architect addendum (adapters/python_fastapi/architect_addendum.md, 61 lines) is
    SILENT on DB/ORM — no Alembic/SQLAlchemy/migration guidance, no services.postgres+inject_env
    DATABASE_URL pattern, no alembic_version bootstrap_check. (addendum has an ~85-line cap →
    may need a separate db addendum file.)
  GAP-2 substrate-prep has no Alembic equivalent of the drizzle _journal.json seeding (the
    alembic/versions/ dir must exist; substrate-owner has no python branch).
  GAP-3 docker/Dockerfile.python lacks psycopg (the substrate VERIFIER queries live postgres via
    psycopg) — and asyncpg/sqlalchemy for scenarios that hit the DB.
  GAP-4 DATABASE_URL → pytest-subprocess injection is UNTESTED on python (works for TS; gate
    passes inject_env into the gate container — needs a live check it reaches pytest).
  GAP-5 NO clean python DB spec/fixture exists. database-foundation (TS) is CONTAMINATED
    (embeds the drizzle-journal foot-gun + 2026-04-22 escalation history as §rationale +
    applies_to:[scenarios,holdouts] + enforcement:architect.amend_check) → must NOT be the basis
    [[feedback_no_framework_workarounds_in_specs]]. database-foundation also NEVER shipped
    [[project_database_foundation_deferred_bugs]] (blocked run-4 at spec-validator).
METHODOLOGY (same as the python Tier-A/B breadth wins): write a MINIMAL CLEAN python DB spec
(simplest substrate-exercising feature — e.g. notes persisted to postgres via SQLAlchemy +
one alembic migration, building on the shipped python notes-crud), validate cheaply
(`dark-factory validate`), then RUN and surface blockers ONE AT A TIME, each fixed
design→Neo→Linus+Codex→merge→re-run. Do NOT pre-fill the gaps above by theory — let the run
surface what actually blocks (the python breadth wins came from run-surfaced surprises:
PEP668, assertion-cap, mypy-import-untyped). Expect GAP-1/2/3 to surface early; GAP-4 mid.
FIRST STEP: author the minimal clean python DB spec via /df-write-spec (route through the
skill, [[feedback_prefer_skills_over_manual_edits]]); keep it framework-agnostic (WHAT to
build, no pipeline mechanics). Run-heavy campaign — multi-session.

## 2026-06-11 — TIER-C DE-RISK #1 RESULT: framework GENERALIZES to python DB (0 framework changes); escalation = a DETERMINISTIC oracle-parser bug (the REAL meta-agent rate-limiter)
Authored notes-crud-pg (clean python DB spec: postgres + SQLAlchemy + 1 Alembic migration;
precheck clean, validate PASS 100/100 LOW). Ran via 1-run battery (got Sprint-19 hermetic
reset). OUTCOME: escalated iter-3 — BUT this is a HUGELY positive Tier-C result + a precise
new finding. BREAKDOWN:
- **The python DB/substrate pipeline WORKS END-TO-END with ZERO framework changes.** iter-3
  gate was FULLY CLEAN: 30/30 blind scenarios against LIVE postgres, exit 0, satisfaction
  100, Setup(alembic upgrade head) PASS, security clean. The architect derived the full
  postgres env-contract (services.postgres + DATABASE_URL inject_env + alembic setup) from
  the spec + the STACK-AGNOSTIC postgres adapter addendum — the scoping's "12-19h of python
  DB framework work" was WRONG. The implementer self-resolved the only two frictions:
  (1) move alembic from [dev] to MAIN deps (the Setup-stage migration runs BEFORE build_cmd
  installs [dev] — detect.py:423 puts dep-install in build, gate order is Install→Setup→
  Build, so a [dev]-only migration tool is missing at Setup; iter-1 exit2 alembic-not-found,
  fixed iter-2); (2) add a conftest.py truncate fixture for DB test isolation (blind
  scenarios share the persistent table → state leaks; pytest applies the impl's conftest to
  blind tests too; iter-2 exit1 list-ordering leak, fixed iter-3).
- **THE ESCALATION = a deterministic ORACLE-PARSER bug (root-caused, airtight).** iter-3
  oracle DECIDED SHIP (bare `SHIP` on line 3 + full SHIP rationale). But its output's FIRST
  non-empty line was PROSE: "Based on my analysis... this is a clear **SHIP** decision."
  `_parse_oracle_decision` (iteration.py ~ def) reads the FIRST non-empty line, strips
  markdown, and if it isn't a standalone SHIP/NO-SHIP token hits a catch-all
  `return "NO-SHIP"` — it NEVER scans further. So oracle-SHIP → parsed NO-SHIP → iter-3
  (max) → escalate. Confirmed: composite_blocked is enterprise-only (off); satisfaction/
  pass floors not hit (100/100); reviewer REQUEST-CHANGES is NOT checked in the override
  path — the parser is the sole cause. The shipping runs happened to lead with the bare
  token; this one led with prose = pure oracle output-FORMAT variance the parser can't
  tolerate. The escalation REPORT prose hallucinated "blocked by infrastructure / dep-
  install failures" (false — everything installed + 30/30 passed); [[feedback_pull_trace_before_theorizing]]
  again — the parser code + oracle first-line were the truth.
- **This IS the genuine meta-agent reliability rate-limiter** (plan item #1) — and it's
  DETERMINISTIC, not deep variance. A clean-gate, oracle-APPROVED ship was thrown away by
  decision-parser fragility. Fix candidate (Neo + Linus+Codex, SHIP-GATE-SENSITIVE): make
  `_parse_oracle_decision` scan for the FIRST STANDALONE SHIP/NO-SHIP line (continue past a
  prose preamble) instead of defaulting NO-SHIP on line 1; NO-SHIP-precedence + standalone-
  only match to avoid misparsing "ship" mentioned inside prose. TENSION: the strict line-1
  rule was likely intentional (anti-misparse); relaxing needs care + the oracle-prompt's
  "start with exactly SHIP/NO-SHIP" instruction stays. AUTHORIZATION-MODEL: changes ship-
  gating logic but NOT audience (no reviewer-only string promoted) — no trigger, but full
  dual-gate required. This recovers oracle-approved clean ships across ALL stacks/tiers
  (would also lift any rate where the oracle prepends prose).
NEXT: fix the oracle parser (Sprint 20) via Neo→Linus+Codex→validate; then Tier-C N=3 to
confirm the python DB clean-ship RATE on the fixed parser (expect the de-risk spec to ship
once the parser honors the oracle's SHIP).

## 2026-06-12 — SPRINT 20 SHIPPED (oracle ship-gate parser hardening; merged main 467bf73)
Fixed `_parse_oracle_decision` (iteration.py): the ship-gate parser read only the FIRST
non-empty line and defaulted NO-SHIP on prose, so an oracle SHIP prefixed by a prose
sentence escalated a clean run. New parser scans EVERY line for a STANDALONE verdict token
(bare or SYMMETRIC-emphasis-wrapped via `(?(1)\1|)`), NO-SHIP dominates regardless of order,
non-str/None fail closed; near-miss forms (`* SHIP` bullet, `## SHIP` heading, `SHIP:` suffix,
`**SHIP` unclosed, `*SHIP_` mismatched) are rejected — over a multi-line scan they'd
false-ship a NO-SHIP rationale. Neo design + Linus+Codex DUAL GATE (4 ROUNDS — the gate
caught 5 real false-ship vectors before CLEAN; the catastrophic direction is a false-SHIP,
so the gate was deliberately exhaustive). 33 parser unit tests; AUTHORIZATION Audience:no-change
(ship-gate parse, no reviewer→implementer promotion). LESSON: a single-line scan over LLM
free-text is a fragile gate; the airtight form is an ANCHORED full-line match of the bare
token, fail-closed + NO-SHIP-dominant — never strip-then-compare (strip turns bullets/headings
into bare tokens = false-ship). VALIDATING: re-running notes-crud-pg on the fixed parser
(expect SHIP — recovering the escalated clean ship + completing Tier-C de-risk #1).

## 2026-06-12 — ✅ SPRINT 20 VALIDATED END-TO-END + 🎉 TIER-C: python DB/substrate CLEAN SHIP
Re-ran notes-crud-pg on the fixed parser: **CLEAN_SHIP** (exit 0, iters=3, satisfaction 88,
$10.18, run 1814b427). AIRTIGHT validation: the iter-3 oracle AGAIN led with a prose preamble
("Based on my analysis of all the provided files and information, I'm making my decision.") —
the EXACT bug condition that escalated the first run RECURRED independently (confirming it's
real, recurring oracle output-format variance, ~50/50) — and the fixed parser correctly read
the standalone SHIP line → SHIP → shipped. Old parser would have escalated it again; new parser
ships it. Sprint 20 recovers the precise failure it was built for, on a fresh run.
🎉 **TIER-C DE-RISK #1 COMPLETE: python ships a DB/substrate-backed spec** (postgres + SQLAlchemy
+ 1 Alembic migration, migration run at gate Setup, 30+ blind scenarios vs LIVE postgres).
The DB/substrate layer needed ZERO framework changes — the only fix was the cross-cutting oracle
parser (Sprint 20), which lifts ship reliability on ALL stacks/tiers. The breadth frontier
(DB on a 2nd stack) is PROVEN.
HONEST MATRIX (post Sprint 16/17/18/19/20, de-contaminated specs):
  TS:     Tier-A 3/3 | Tier-B 2/3 (genuine implementer variance)
  python: Tier-A 3/3 | Tier-B 3/3 | Tier-C 1/1 (DB clean ship)
NEXT (run-heavy, future session): Tier-C N=3 for the python DB clean-ship RATE; re-measure
TS Tier-B on the fixed parser (Sprint 20 may lift it — the prose-preamble escalation class is
now gone); Tier-C on TS (database-foundation de-contam — framework-coupled). Standing debt:
env-contract/agent-def sync; the python migration-tool dep-ordering (alembic in [dev] vs main
costs an iter — implementer self-recovers, but a deterministic "install migration tool before
Setup" fix would save the iter) + DB-test-harness conftest friction (both implementer-recovered
within 3 iters here, but are the recurring python-DB frictions to harden if Tier-C N=3 shows
they cost ships).

## 2026-06-12 — ⚠️ SPRINT 22 WAS A MIS-DIAGNOSIS (caught by pulling the trace) + ROADMAP Sprint 23: the REAL friction = user-site bin not on PATH (breadth-general env defect)
HONEST CORRECTION: Sprint 22 (move python dep-install to Install pre-Setup) is a VALID robustness
fix (a [dev]-only migration tool now installs pre-Setup — keep it) but it is ORTHOGONAL to the
actual iter-1 `Setup FAIL exit 127` friction and did NOT fix it. Pulled the live trace (the
Sprint-22 validation N=3): Install ran `pip install -e '.[dev]'` EXPLICITLY (Sprint 22 working),
the impl declared alembic in MAIN deps (installed), yet Setup still 127'd via the impl's
`scripts/df-setup.sh` running `alembic`/`/home/node/.local/bin/alembic`. I theorized dep-ordering
from the [dev] hypothesis instead of pulling the iter-1 df-setup.sh — exactly the
[[feedback_pull_trace_before_theorizing]] trap, on me.
ROOT CAUSE (CONFIRMED by reproducing in the real image): the gate (and ALL agent/server/mutation/
format containers, via build_base_args, args.py:67) runs as NON-ROOT uid 1000. `pip install
--break-system-packages` as non-root can't write system site-packages → USER install → console
scripts land in `$HOME/.local/bin` (pip's own "not on PATH" warning). Dockerfile.base:21 PATH has
NO user-site bin. So PROJECT-pip-installed CLIs (alembic; and the SAME class for rust
`~/.cargo/bin`) are uninvokable BY NAME. (Image-baked tools — pytest/mypy via the lockfile — are
on system PATH and work; modules imported via sys.path work; ONLY project console scripts are
unreachable. node is immune — npx/pnpm resolve project-local bins regardless of PATH. Go already
fixes it: Dockerfile.go:7 `PATH="$GOPATH/bin:$PATH"` — THE MODEL.) The implementer then flails on
df-setup.sh (bare `alembic` fails / hardcodes a guessed path), recovering on luck → non-determi-
nistic iter-1 waste that CAN convert to a lost ship under the PLATEAU-on-empty-failure-set mislabel.
ALSO SURFACED (HOME incoherence): the framework mounts a tmpfs home at /home/agent (args.py:69)
and caches pip there (mounts.py:255) but NEVER sets HOME=/home/agent, so HOME stays /home/node
(uid-1000 passwd) → pip installs to /home/node/.local/bin and the /home/agent pip-cache is INERT.
Neo + Codex DUAL CRITICAL REVIEW (Caleb-requested, to ensure framework-level breadth-robust, not a
point-patch) corrected my original point-patch (python-only, gate-only, /home/node literal) to:

## 2026-06-12 — SCENARIO-COUNT VARIANCE root-caused + SPRINT 25 A0 SHIPPED (merged main d11cb8a)
Option-3 (scenario-count variance) trace-mining: scenario_total is wildly non-deterministic FOR THE
SAME SPEC (notes-crud-pg 1→32 across 12 runs; even shared-parse-amount 33→51; several runs 0/1). ROOT
CAUSE (confirmed, gate report): blind-scenario tests import the app IN-PROCESS (`from app.main import
app` + TestClient). When the app's import surface throws at import (a13bfc6f: `src/app/database.py:13
engine = create_engine(DATABASE_URL)` — a MODULE-SCOPE side effect → `import psycopg2` →
ModuleNotFoundError, the driver mismatch), EVERY in-process test fails pytest COLLECTION together →
registration collapses BIMODALLY (clean import → ~30-44; any import error → 0-1). The bash parser
silently DISCARDED pytest's `errors` count, so 9-files-all-errored → fabricated SCENARIO_TOTAL=1 (binary
fallback) → fake 100% pass_rate feeding Oracle + the north-star metric. Ties the session together:
scenario-variance + driver mismatch (S21) + setup failures (S24) are all facets of "the app's import/
setup surface is fragile and the gate's signal silently collapses when it breaks."
USER DECISION (corrected my mislabel): do NOT resurrect Format A (the bespoke *.scenarios.md HTTP
runner, RETIRED Phase 4.6b.1a). The real axis is in-process TestClient vs live-server HTTP WITHIN
native tests; live-server would fix it by routing failures through the LOUD smoke gate, but spends
operational complexity we deliberately shed. CHOSEN: gate-integrity (make the silent failure loud) +
the module-scope-side-effect testability angle — NOT a format change.
NEO DESIGN (Option A, 3 increments): A0 = parser captures `errors` (telemetry only, measures freq);
A1 = route mass-collection-drop to iterate-with-feedback via the Sprint-24 channel (sibling predicate
should_scenario_mass_collection_drop, guarded exit_code!=2 so S24-B2 owns setup-driven cases, NOT
HardAbort — it's implementer-fixable); B = extend spec-validator + df-write-spec anti-pattern table to
flag module-scope import-time resource construction (create_engine/connect/client/file at module
scope) + a one-line architect constraint. KEY NEO CAVEAT: PART B is STRUCTURALLY INCOMPLETE — the
spec-validator only flags what the SPEC describes, but module-scope create_engine is usually an
IMPLEMENTER choice invisible to the spec, so B would NOT have prevented a13bfc6f; PART A is the real
net. Also: "fail loud" is imprecise — the gate already FAILS (exit code); the bug is the 100% pass_rate
on a 1-scenario denominator. Detection signal = capture the runner `errors` count (NOT a ratio); fixes
a latent parser-discard bug regardless.
A0 SHIPPED (merged d11cb8a; Neo + Linus+Codex 2 rounds — Codex caught the unanchored fallback regex,
anchored to the pytest `=`-summary line + 2 guard tests; both CLEAN): new SCENARIO_COLLECTION_ERRORS
on GateResult, kept out of failed/total, summed in both accumulators. 9 shell + 2 python tests.
A1 SHIPPED (merged c119891; Neo + Linus+Codex 3 ROUNDS): acts on A0's signal. New predicate
should_scenario_mass_collection_drop (collection_errors>0, guarded exit_code!=2 so B2 owns Setup-
aborts) + loop branch (after B2, before score_history): keeps the meaningless pass_rate OUT of the
trajectory/Oracle, writes import-surface-directed feedback (count + "fix module-scope resource
construction, NOT test logic" — barrier-clean, no traceback), escalates ENV_ISSUE on the 2nd
consecutive drop. The infra fast-fail HardAbort now yields on collection_errors>0 (implementer-
fixable, not a terminal format mismatch). DUAL-GATE FOUND 3 COUNTER BUGS across rounds (the scattered
per-continue reset approach repeatedly missed a continue site: Codex caught B2 + self-test/R1; Linus
caught smoke-fatal #4) → refactored to a TOP-OF-LOOP SNAPSHOT (zero cc at loop top, only the confirmed-
drop branch restores prev+1) so EVERY early-continue path is auto-correct — provably closes the class.
DOCUMENTED KNOWN LIMITATION (gate-accepted, Codex-confirmed unreachable for pure drops): a MIXED drop
whose survivor pass_rate also regresses is owned by R1 (rollback) not A1 → escalates via max_iters
rather than early ENV_ISSUE; pure drops (the common case, a13bfc6f) unaffected. 12 new tests, 4048
unit. Audience: implementer-readable iter-N+1, ONLY a count + constant directive (lower-risk than
Sprint 24 — nothing to sanitize). REMAINING: B (testability) — extend spec-validator + df-write-spec
anti-pattern table to flag module-scope import-time resource construction + a one-line architect
constraint. NEO CAVEAT STANDS: B is structurally INCOMPLETE (spec-validator only sees spec-described
side effects; the a13bfc6f create_engine was an implementer choice invisible to the spec — A1 is the
real net). So B is cheap-but-partial; weigh whether to do it now or treat A0+A1 as the substantive fix.

## 2026-06-12 — BARRIER AUDIT of the whole session (Sprints 19-24): CLEAN (independent review)
Caleb-requested barrier-violation sweep over all session commits (8e78ec9..HEAD). Independent
auditor + barriers.py cross-check: NO violations. Mask facts confirmed — implementer_masked =
[HOLDOUTS, REPORTS, AUDIT, STATE, FAILURES, GIT] (barriers.py:62); reviews/ + scenarios/ are
intentionally NOT masked (implementer-readable). The two barrier-relevant changes (Sprint 21
driver-preflight diagnostic, Sprint 24 setup-error feedback) only surface build/env/install/migration
diagnostics — sanitized by _sanitize_output at source + (Sprint 24) write-time audit_history_for_
barrier_leak with fail-to-empty. No reviewer/oracle-only signal, scenario/holdout content, test names,
or pass-rate judgments promoted. No d2fecbf recurrence. TWO NON-BLOCKING HARDENING NOTES (tracked,
not fixed): (1) run_db_driver_preflight (quality-gate.sh:~1216) is the ONLY summary writer that skips
_sanitize_output — safe today only because its content is closed (scheme + static-table driver name);
pipe it through _sanitize_output for defense-in-depth if that message ever carries freeform output;
(2) audit_history_for_barrier_leak's scenarios_dir param is unused (harmless, scenarios are readable).

## 2026-06-12 — META-AGENT RELIABILITY: trace-mining REFRAME + SPRINT 24/B2 SHIPPED (merged main 55fb8fd)
Started the meta-agent reliability item by MINING the preserved archives (≥30 runs across py+TS)
for real failure traces BEFORE theorizing (the [[feedback_pull_trace_before_theorizing]] mandate).
KEY REFRAME (changes the whole picture): the "green gate but ESCALATED" cases (exit0/sc44/pr100 →
PLATEAU → escalate, e.g. notes-crud-ts 0c14a85e/10291d57) are NOT meta-agent bugs — they're the
reviewer/oracle CORRECTLY blocking real defects the blind scenarios can't reach (0c14a85e: a real
prototype-pollution vuln in health.ts:15 the implementer failed to fix in 3 iters → honest
escalation). The system HOLDS there (Sprint 16 oracle security authority working). So the rate-
limiter is NOT reviewer over-strictness.
THE DETERMINISTIC LEVER = B2: setup failures misrouted through the CODE-trajectory classifier.
Evidence (systemic, ≥5 escalations TS+py): a gate exit_code==2 (Setup/build ABORT before any test
ran — drizzle-kit/alembic/migration tool missing) yields satisfaction_score=0, byte-identical to a
0%-pass test iter → appended to score_history → PLATEAU → "Fundamentally different approach needed"
CODE-strategy. And the old exit-2 handler was a bare `continue` that skipped review/oracle WITHOUT
writing feedback → the implementer re-implemented from spec BLIND for 3 iters → escalate with a
WRONG "code plateau" diagnosis. (c9d47e15: drizzle-kit-not-found ×3 → PLATEAU ×2 → escalate.)
FIX (Neo design Option A + Linus+Codex dual gate, 2 rounds): a new route (after ITER_COST, before
score_history.append) claims exit_code==2 && hard_gate_build and (a) keeps the abort OUT of the code
trajectory, (b) writes setup-DIRECTED feedback (new write_setup_failure_feedback: the sanitized
setup error + an explicit "this is env/build/migration, NOT app logic" directive — actionable signal
instead of nothing), (c) escalates ENV_ISSUE on the 2nd consecutive CLEAN failure (new
consecutive_setup_failures, mirrors consecutive_setup_hangs), saving the wasted blind iters; a HANG
is excluded from the clean counter (own counter upstream) but still trajectory-excluded + fed back.
Old post-trajectory exit-2 handler removed (unreachable); R1 rollback still owns mixed exit1→exit2.
DUAL-GATE CATCHES (round 1): Codex (HIGH) — the implementer reads the feedback file DIRECTLY (prompt
names reviews/{spec}-feedback-iter{N-1}.md; reviews/ is UNMASKED), bypassing the consumption-time
barrier audit → added a WRITE-TIME audit_history_for_barrier_leak, drop-error-to-"" on violation
(Neo's stated req, which I'd missed); Codex (MED) — hang double-counted both counters, fixed via the
is_setup_hang exclusion. Linus CLEAN both rounds. AUTHORIZATION: Implementer-readable on iter-N+1
(validator-bridge; sanitized setup/env diagnostics — same class as Sprint 21 5d7be87; no Reviewer-
only/scenario/holdout/test content; sanitized at source AND write-time-audited). VALIDATION: 13 unit
tests (every branch: counter/escalation/trajectory-exclusion/hang-exclusion/barrier-drop/feedback/
extraction) + REAL-summary integration check (the extractor pulls the actionable drizzle-kit error
from the actual c9d47e15 archived summary, barrier-clean) + dual-gate. Decisive for a deterministic
control-flow change (no 40-min run needed; a full run would only exercise B2 IF it hit a setup fail).
OTHER findings logged for later (NOT yet fixed): scenario-count variance (sc1→sc64 across runs — gate
rigor wildly inconsistent; a test-generation reliability item); the convergence gap on reviewer-found
NON-scenario defects (security/state-isolation the blind scenarios miss → implementer can't fix in 3
iters → honest escalation; softer, harder to fix deterministically). NEXT: cache-volume-ownership
(Sprint 23 deferral) and/or Tier-C-on-TS, then circle back to scenario-count variance.

## 2026-06-12 — SPRINT 23 SHIPPED (merged main 1f32bfc): non-root user-site-bin on PATH (python/playwright); rust/go DEFERRED (root-owned cache volume)
The genuine Tier-C iter-1 friction (Sprint 21 + 22 did NOT fix it): the gate runs as non-root
uid 1000 → a project `pip install` falls back to a USER install whose console scripts (alembic)
land in the user-site bin, NOT on PATH → bare `alembic upgrade head` in df-setup.sh → command
not found → iter-1 Setup churn the implementer can't diagnose from inside the barrier.
DESIGN DEVIATION (flagged before impl): Neo's design-confirm changed the fix from option i-a
(SET HOME=/home/agent) to option i-b (the Go pattern: pin PYTHONUSERBASE/CARGO_HOME + PATH,
HOME UNTOUCHED). HOME stays /home/node — load-bearing for node/TS, the 3/3 stack — so we do NOT
risk the proven-green path. SHIPPED: Dockerfile.python ENV PYTHONUSERBASE=/home/agent/.local +
PATH; args.py pins the /home/agent tmpfs uid=1000,gid=1000; Dockerfile.playwright parity (latent).
IMAGE-VERIFIED as uid 1000: bare `alembic` → /home/agent/.local/bin/alembic, no PATH warning,
baked tools (pytest/mypy/ruff) still /usr/bin (build-time --break-system-packages install is
non-user, unaffected by PYTHONUSERBASE).
RUST/GO DEFERRED — Linus dual-gate catch (Codex missed it): the naive CARGO_HOME=/home/agent/.cargo
/ GOPATH=/home/agent/go pins point at the ROOT-OWNED `df-cargo-cache`/`df-go-cache` named-volume
mountpoints (mounts.py:256-257), so `cargo install` can't even WRITE there as uid 1000 (a named
volume mounts root-owned under --user; the tmpfs uid-pin can't override it). Python works ONLY
because /home/agent/.local has NO named volume over it (pip cache is the distinct path
/home/agent/.cache/pip). Real rust/go fix = pre-own those volumes the df-pnpm-store way
(Dockerfile.base chown + copy-on-first-mount) — NEW DEFERRED ITEM "cache-volume-ownership";
rust/go have no active breadth coverage so they stay latent (Dockerfile.rust carries the NOTE;
Dockerfile.go's GOPATH has the same latent bug, never hit because go isn't run at runtime).
GATES: Neo design + Linus + Codex dual gate, 2 rounds (Linus caught the CARGO_HOME break → Rust
pulled to a deferred NOTE + 2 overclaiming comments corrected; both CLEAN round 2). 4018 unit.
VALIDATION (end-to-end, DONE — run 6eff20cde87245a6, notes-crud-pg from scaffold baseline
03397c7 on the fixed image): CLEAN SHIP iter 2, satisfaction 100, 30/30 scenarios. Sprint 23
EXERCISED IN-PIPELINE (not just image-level): iter-2 Setup ran `bash scripts/df-setup.sh` whose
line 3 is BARE `alembic upgrade head` — `alembic` is a project dep (NOT a baked tool), installed
by `pip install -e '.[dev]'` as a non-root user-site script → resolved on PATH at
/home/agent/.local/bin (Sprint 23's exact addition) → migration succeeded → scenarios ran. Pre-fix
that line is command-not-found and Setup FAILs there. Bonus: Sprint 21 ALSO validated in the wild
— iter-1 Setup FAIL was the driver preflight firing with a precise psycopg/scheme diagnostic; the
implementer self-resolved (added psycopg2-binary to match the bare postgresql:// scheme) and
shipped iter-2 — NO escalation, NO silent crash (the exact Tier-C friction the N=3 escalation hit).
META-AGENT NOTE (corrected — checked the event record before recording, per pull-trace discipline):
the iter-2 oracle ran its NORMAL TWO passes (decision 80s + feedback 114s), both attempt=1, both
clean — NO retry. An earlier `docker ps` snapshot showing a "fresh oracle Up 32s" was just the
second normal pass's container (events.jsonl was buffered); I nearly mis-recorded it as a retry.
So this run did NOT capture an oracle-reliability incident — the meta-agent reliability item still
needs its own trace capture (an actual escalation/retry), not a clean ship. NOTE: bare-`alembic` was
the implementer's iter-2 df-setup.sh choice (iter-1 it used `python -m alembic`); the fix makes the
idiomatic bare form — and detect.py's emitted commands.setup — robust regardless of impl variance.

## (SUPERSEDED by the SHIPPED record above) ROADMAP — Sprint 23: central user-site-bin PATH contract + HOME coherence
PRIORITY: HIGH within env/breadth robustness — deterministic env defect, breadth-general (python +
rust today, every future non-npx stack), can cost ships under PLATEAU mislabel. The genuine Tier-C
iter-1 friction (Sprint 21 + 22 did NOT fix it).
CHOSEN FIX (option i — coherent env, both reviewers' breadth-correct layer): in `build_base_args()`
(darkfactory/docker/args.py — covers ALL container types: gate, server-setup quality_gate.py:1450,
test-server, mutation, format-runner, agents) AND/OR Dockerfile.base:21 ENV:
  - SET `HOME=/home/agent` (-e HOME) so pip/cargo user-installs + caches use the intended tmpfs
    home coherently (also fixes the inert /home/agent pip-cache).
  - ADD `/home/agent/.local/bin` (python pip user-site) + `/home/agent/.cargo/bin` (rust cargo) to
    PATH, mirroring Go's existing `$GOPATH/bin`. Portable; NOT a /home/node literal, NOT per-image.
  - Standalone dark-factory-playwright (python:3.12-slim, different HOME) needs its OWN treatment
    (Neo: `--user` install actually FAILS there with HOME=/ — verify/handle separately).
REJECTED (both reviewers): install-as-root / run-as-root (claude --dangerously-skip-permissions
REFUSES root — the non-root uid 1000 + cap-drop is a LOAD-BEARING security barrier for running
untrusted project code); venv (python-only, stateful); `python -m alembic` as the PRIMARY fix
(prompt-layer; doesn't fix the architect's own commands.setup/server_setup_cmd; relies on impl
luck). DEFENSE-IN-DEPTH (optional, adapter layer): python detector emits `python -m alembic upgrade
head` (detect.py:398) instead of bare `alembic`; keep node setup examples in npx/pnpm-exec form.
AUTHORIZATION: barrier-adjacent gate/container-env change — read docs/AUTHORIZATION-MODEL.md;
changing HOME/PATH does not promote a reviewer-only signal to implementer-readable (no trigger) but
declare it. NEEDS AN IMAGE REBUILD (base + all FROM-base) — verify the version-pin discipline.
VALIDATION: (1) repro harness (the load-bearing test): in dark-factory-python, --user=1000:1000
+ HOME=/home/agent, `pip install -e .` an alembic-dep project → assert `which alembic` resolves +
`bash -c "alembic --version"` exits 0 (FAILS pre-fix, PASSES post). (2) python-DB pipeline run:
iter-1 Setup passes with a BARE-`alembic` df-setup.sh / the architect's bare commands.setup. (3)
rust + node regression (no breakage). (4) confirm server-setup container also resolves a bare
console script. Take through Neo (design already done here) → implement → Linus+Codex → validate.

## 2026-06-12 — SPRINT 22 SHIPPED (merged main eb58398): python dep-install runs at Install stage, before Setup
The CONSISTENT Tier-C iter-1 waster (Setup FAIL exit 127 = alembic-not-found, fired 3/3 in
the Sprint-21 N=3): detect.py put the python dep-install in build_cmd (→ commands.build, the
Build stage, AFTER Setup), leaving commands.install empty (→ gate fallback `pip install -e .`,
MAIN deps only). So a [dev]-declared migration tool was absent when the Setup migration ran.
FIX (Neo design A2, deterministic): new DetectionResult.install_cmd → env-contract
commands.install (the legacy PipelineConfig field `server_setup_cmd`) → the gate Install stage,
which runs BEFORE Setup. build_cmd left "" for python (gate Build falls back to py_compile).
Fixed BOTH producer tables (parallel-table trap Linus+Codex caught): autodetect_delta
_DETECT_TO_ENV (runtime) AND init _DETECT_TO_BOOTSTRAP (`dark-factory init`). Also fixed the
py_compile Build fallback to split colon-joined SOURCE_DIR (multi-source-dir python would
else false-FAIL). Neo + Linus+Codex 2-round dual gate (BLOCKING init omission + colon false-fail
caught + fixed). 4017 unit tests. VALIDATED (deterministic): regenerating py-fastapi's
env-contract with the new detect.py (`init --update`) yields `install = pip install -e '.[dev]'`,
`build = ""` — the dep-install (incl a [dev] migration tool) now lands at the Install stage,
pre-Setup. NOT YET pipeline-run-validated end-to-end (the full Tier-C re-run confirming iter-1
Setup ≠ 127 + the honest rate is the optional belt-and-suspenders; the env-contract + gate
mechanism + unit tests are decisive for a deterministic fix). NOTE: existing fixtures carry a
stale committed bootstrap (commands.build); the run's autodetect backfills the empty
commands.install, OR regenerate via `init --update` for a clean state.

## 2026-06-12 — TIER-C python DB N=3 RESULT: 2/3 clean_ship (the driver mismatch costs ~1/3 of runs)
notes-crud-pg N=3 on the Sprint-20-fixed framework: run 1 ESCALATED (psycopg2/psycopg
driver↔URL-scheme mismatch, blind scenarios crash), run 2 clean_ship (iters=3), run 3
clean_ship (iter-1, $5.67/41min). RATE = 2/3. The one escalation is the Sprint-21 driver
friction — a GENUINE, deterministically-fixable bottleneck (NOT the parser, NOT contamination).
HONEST MATRIX (post Sprint 16-20): TS-A 3/3 | TS-B 2/3 (genuine impl variance) || py-A 3/3 |
py-B 3/3 | py-C 2/3 (driver mismatch → Sprint 21). Expectation: Sprint 21 lifts py-C → 3/3.

## 2026-06-12 — SPRINT 21 VALIDATION (HONEST): py-C = 3/3, BUT the preflight did NOT fire — 3/3 is variance, not a demonstrated effect; the CONSISTENT friction is the alembic dep-ordering
Tier-C N=3 on the Sprint-21 framework: 3/3 clean_ship (all iters=2, ~$8/run). BUT pulling the
traces (archives c57ae51e / f1c54996 / 66d53c8f): the driver-scheme PREFLIGHT NEVER FIRED — all
3 implementers declared `psycopg2-binary` (the CORRECT driver for the injected bare
`postgresql://` scheme), so no mismatch occurred. So 3/3 vs the prior 2/3 is WITHIN N=3 VARIANCE
(the variance is the implementer's driver choice: last N=3 run-1 picked psycopg v3 → mismatch →
escalation; this N=3 all picked psycopg2-binary → match → ship). Sprint 21 is unit+gate-validated
INSURANCE, NOT YET OBSERVED RECOVERING A REAL MISMATCH IN THE WILD (same honest status as the
Sprint-14 TE-retry guard). Do NOT claim Sprint 21 "lifted the rate" — it didn't get the chance to.
WHAT DID FIRE (all 3 runs): iter-1 `Setup FAIL exit 127` = alembic-not-found — the python
DEP-ORDERING friction (the migration tool lands in [dev]/Build, AFTER the Setup-stage migration),
recovered at iter-2. This is the CONSISTENT, reliable iteration-waster (3/3 burned iter-1 on it).
It doesn't cost ships in a 3-iter budget here, but it eats the iter-1 margin every time (and the
first de-risk run escalated at iter-3 partly for lack of that margin). THE NEXT DETERMINISTIC FIX
(now strongly justified — fires 3/3): ensure the python dep-install (incl the migration tool) runs
at the Install stage BEFORE Setup, not at Build after it. detect.py:423 puts the dep-install in
build_cmd (→ commands.build, post-Setup); the gate order is Install→Setup→Build. Candidate: add an
install_cmd to DetectionResult mapped to commands.install (pre-Setup), or move the python
dep-install there. Regression-check python Tier-A/B (3/3 each) — affects all python specs.

## 2026-06-12 — SPRINT 21 SHIPPED (merged main 5d7be87): deterministic DB driver↔URL-scheme gate preflight
Chosen design (Neo): NOT the rejected image-preinstall band-aid, NOT framework-normalizes-URL
(Option B — rejected: reads image's psycopg-v3 tooling as intent, no-ops if impl hardcodes the
scheme, hides the defect). Shipped Option A1 (the load-bearing DETERMINISTIC fix): a gate
preflight (`scripts/lib/db_driver_preflight.py` + `run_db_driver_preflight` in quality-gate.sh)
that resolves the REQUIRED driver module from the injected DATABASE_URL scheme (longest-prefix,
deterministic) and on a missing driver fails Setup EARLY with a precise actionable message
instead of the silent `import psycopg2` collection crash. Driver stays PROJECT-DECLARED (it
surfaces the mismatch, installs nothing). python-toolchain + DATABASE_URL only; skips smoke/
pre-gate (mirrors run_setup_step — no services); fail-open on unknown scheme / absent python3.
14 unit tests. Neo + Linus+Codex 3-round dual gate (caught a smoke-mode false-FAIL + a staging
slip). A2 (architect-addendum scheme guidance) + A3 (python implementer addendum) DEFERRED:
governance caps addendum prose, python_fastapi has NO implementer-addendum injection hook, and
A1's deterministic gate feedback already delivers the contract to the implementer at failure
time. SCRIPTS ARE BIND-MOUNTED into the gate container (mounts.py build_scripts_mount) → the
change is LIVE without an image rebuild. VALIDATING: Tier-C N=3 on the fixed framework (expect
the preflight to convert the silent crash → actionable Setup-FAIL → implementer recovers →
py-C lifts toward 3/3).
NOTE for a future session: if A1's fast-fail isn't enough to reliably recover (implementer can't
act on the message within iters), revisit A3 (build the python implementer-addendum injection
hook + addendum) and/or have the architect inject a driver-explicit scheme deterministically.

## ROADMAP — Sprint 21 ORIGINAL DESIGN NOTES (shipped above): postgres DRIVER↔URL-scheme coordination
PRIORITY: TOP of the Tier-C reliability work — the N=3 confirmed this is SHIP-COSTING (2/3:
run 1 escalated on it). Do this before TS Tier-C / further breadth.
FINDING (root-caused from N=3 run 1, archive 20260612-062517-notes-crud-pg-a13bfc6f): the gate
crash is `ModuleNotFoundError: No module named 'psycopg2'` — NOT a dependency-detection failure.
Deps detect/declare/install fine (impl declared psycopg v3 + sqlalchemy + alembic; gate installed
them). It's a SCHEME↔DRIVER MISMATCH across the barrier: the ARCHITECT injects DATABASE_URL with
the bare `postgresql://` scheme (→ SQLAlchemy's psycopg2 dialect) via the env-contract inject_env;
the IMPLEMENTER independently picks psycopg v3 (`postgresql+psycopg://`). At gate time the injected
bare URL OVERRIDES the impl's fallback → psycopg2 dialect → `import psycopg2` → only psycopg v3
installed → blind scenarios crash at collection. Architect (owns URL scheme) and implementer (owns
driver) are barrier-separated and never coordinate; the python+postgres architect/impl addendum is
SILENT on the driver contract (GAP-1). De-risk #1 + the validation run shipped because their impls
happened to pick psycopg2-binary (matching the bare scheme) — LUCK, not contract.
REJECTED FIX (Caleb's steer, correct): pre-installing drivers in Dockerfile.python — a band-aid
that masks the mismatch, bypasses the project-declares-its-deps model, leaves the impl's deps
internally inconsistent with the URL it uses, and bakes opinionated cruft into the base image.
RECOMMENDED FIX (make the scheme↔driver contract EXPLICIT + one-directional; keep the driver
project-declared): (a) the architect/postgres-adapter injects a DRIVER-EXPLICIT scheme (e.g.
`postgresql+psycopg://`) instead of the ambiguous bare `postgresql://`, and the python+postgres
IMPLEMENTER addendum (currently silent — GAP-1) instructs: use the injected DATABASE_URL verbatim,
declare the driver its scheme names; PLUS (b) a DETERMINISTIC backstop — a precheck/gate check that
the impl's declared driver matches the injected scheme, turning the silent collection crash into an
early, actionable NO-SHIP ("injected scheme needs psycopg; deps declare none"). Keeps deps
project-owned, makes the architect↔impl contract explicit (no luck), adds determinism via a check
not a band-aid.
OPEN DESIGN Q (for Neo at pickup): which layer carries the contract — (a) explicit scheme +
addendum guidance (impl still declares the driver) vs. (b) a first-class env-contract "driver"
field the architect sets + the gate enforces vs. (c) gate-check-only. Take to Neo → Linus+Codex →
validate with a Tier-C re-run. NOTE: ties to GAP-1 (python DB architect/impl addendum) — fold them.
