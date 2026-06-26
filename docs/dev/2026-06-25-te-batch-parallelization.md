# TE batch parallelization — measured finding + draft change

**Date:** 2026-06-25
**Status:** DRAFT for the backlog (Neo design + Linus code before build).
**Relation:** empirically confirms the plan's **S2 / lever L3** ("parallel
anchor-batched TE", 00-RESET-PLAN.md ~line 320, identified 2026-06-18 as the top
structural lever). The SHIPPED sequential batching (P1 TE-decomposition) was the
interim; this is the durable follow-on.

## The question
The TE generate stage runs as K SEQUENTIAL cold batch invocations (the
decomposition that fixed the monolithic `exit 124` mega-think). It runs in
PARALLEL with the single QA-Holdout stage, so the stage wall-time =
`max(TE_serial, QA)`. If TE ≫ QA, parallelizing the TE batches (run them
concurrently instead of in series) collapses TE from `Σ(batches)` to
`max(batch)`, shortening the whole TE/QA stage.

## The data (17 batched runs, from events.jsonl AGENT_START/COMPLETED)
- **QA is stable and short: ~650–1250s** (~11–21 min) across every run. QA is
  NEVER the bottleneck.
- **TE (serial) is 2–14× longer**, the binding constraint in every multi-batch run.
- **Parallel-TE = max(batch) → ~2.2× median speedup on the TE/QA stage** (range
  1.4×–4.9×; the win scales with batch count). Representative:

| run | batches (s) | QA (s) | serial stage | parallel stage | speedup |
|---|---|---|---|---|---|
| ec200291 (run20) | 795,508,473,1386* | 892 | 3170 | 1386 | 2.3× |
| 037e5b | 757,1158,1617 | 772 | 3286 | 1617 | 2.0× |
| 214682 | 1191,1061,509,765 | 889 | 3710 | 1191 | 3.1× |
| 923767 | 1324,915,755,988 | 1213 | 4102 | 1324 | 3.1× |
| e1c7c9 | 7 batches, up to 2915 | 1247 | 14380 | 2915 | **4.9×** |

`*` run20's 4th "batch" (1386s) is a serial REPAIR pass, not an initial batch.

**Wall-time saved: ~25–45 min on a typical run; ~3 hours on a big multi-batch
spec.** This compounds across every future run — including the campaign's own
measurement/validation cycles.

## The caveat (the floor)
Parallel-TE floor = `max(slowest batch, QA)`. The slowest batch is usually
1500–2900s, still > QA — so even parallelized, TE stays the (much shorter)
bottleneck. And batch *times* are uneven even when balanced by anchor *count*
(e.g. `9d7c1f`: 1431,1478,242,143 — two long, two tiny). So a **complementary
second lever** is to **time-balance the batches** (predicted-time, not
anchor-count) to minimize the max-batch floor. The serial REPAIR pass is a
separate post-step parallelization does not touch.

## Draft change
- Convert `_run_batched_te_generation`'s **sequential loop → a parallel fan-out**:
  N cold TE invocations (each `session_container_id=""`, ≤K anchors, disjoint
  `b{i}-` output files) launched concurrently; join; then the existing coverage
  floor / repair path runs as today.
- **Reuse the existing concurrency infra:** `scenario_parallel_generation` already
  runs TE+QA in parallel containers, so the "launch agent containers concurrently
  + collect" pattern exists — this generalizes it to N TE batches + the 1 QA.
- **Config gate:** a `scenario_batch_parallel` knob (default OFF → ON after a live
  validation run), mirroring how `scenario_parallel_generation` was shipped
  OFF-then-validated.
- **Concurrency cap:** bound concurrent TE containers (e.g. min(K, cores−2) like
  the workflow cap) so a 7-batch spec doesn't oversubscribe; excess batches queue.

## Why it's safe (no mega-think regression)
Parallelizing batches changes CONCURRENCY, not batch SIZE. Each batch still
reasons over ≤K anchors, so per-batch thinking/output is unchanged — the
`exit 124` mega-think that motivated the decomposition was about a *monolithic*
turn, which this does not reintroduce. Batches are independent (disjoint anchors,
disjoint files), so no shared-state hazard. Cost is N concurrent containers
(memory/API-concurrency), bounded by the cap.

## Open questions for Neo / Linus (before code)
1. Resource/concurrency: the right cap on concurrent TE containers; API
   rate-limit interaction at high K; memory headroom for N+1 agent containers.
2. Failure handling: one batch fails/times-out → regen just that batch (others
   unaffected)? Confirm the join handles partial failure cleanly (the coverage
   floor already backstops gaps).
3. The repair pass: keep serial (post-join) or fold into the fan-out? (It depends
   on the joined output, so likely serial.)
4. Time-balancing (the second lever): worth doing now (predicted-time batching to
   lower the max-batch floor), or ship parallel-first and balance later?
5. Log/stream integrity: N concurrent streams must not collide (the prior
   sequential design had a sidecar-collision fix — confirm the parallel streams
   are uniquely named, which the `p{pid}s{seq}` suffix already gives).

## Priority rationale
High ROI, low risk, **already-validated-as-the-top-lever** (S2/L3) and now
empirically backed. Not a correctness item (the campaign's spine), but a
throughput multiplier that *accelerates* the correctness work's own validation
runs. Slot: **after the in-flight discrimination work lands**, or opportunistically
— it pays for itself on the first big run.

---

## v1 vs v2 — parallelize the GENERATE batches, or the FULL per-batch cycle?

The TE phase is `generate (×K batches) → validate → coverage-floor → architect-amend
→ coverage-regen → repair`. The generate batches run serial today; **so does the
entire post-batch tail**. The question is whether the tail must be serial. It is
phase-dependent — two of the four are global, two are not:

| Phase | Global or per-batch? | Why |
|---|---|---|
| **generate** | per-batch | disjoint anchor subsets, disjoint `b{i}-` files — independent |
| **validate** (scenario-lint / `SCENARIO_FINGERPRINT`) | **per-FILE** | checks each scenario in isolation (imports, no-CWD, Check 17) — not inherently global |
| **coverage floor** | **GLOBAL (by nature)** | a reduction over the whole corpus ("every demanded anchor has ≥1 crediting scenario") + read-global signals (architect substrate-`covers`, `@untestable` waivers); today even allows CROSS-CREDIT (a batch-i scenario can credit anchor owned by batch j) |
| **architect-amend** | **GLOBAL (by resource)** | mutates the shared architecture manifest / `file_map` — concurrent amends conflict (this is the `entry_point not in file_map` step) |
| **repair** | mostly per-file | fixes a specific scenario's compile/import issue; occasionally wants cross-file context |

### v1 — parallelize the GENERATE batches (the original draft above)
Cheap, low-risk: `_run_batched_te_generation` sequential loop → fan-out; the tail
(validate/floor/amend/regen/repair) stays as a single global pass afterward. Win =
gen `Σ→max(batch)` (~2.2× median on the *gen* portion). Behind
`scenario_batch_parallel`.

### v2 — parallelize the FULL per-batch TE cycle ("N independent cycles")
N cycles of `generate→validate→cover-own-anchors→regen→repair` in parallel, each
owning a disjoint anchor set, then a thin global join. Attacks the per-batch tail
(validate + repair + per-anchor regen), not just generate. **Requires:**
1. **Strict disjoint anchor ownership** — batch i covers `anchors[i]` with its OWN
   scenarios (kill cross-credit). A small coverage-floor SEMANTICS tightening that
   makes each batch's floor LOCAL: `anchors[i] covered by batch-i scenarios OR
   globally-architect-credited` (architect-credits read-only, fixed pre-batch).
2. **Read-only shared inputs** — manifest / substrate-`covers` / waivers are fixed
   before the batches → all N cycles read concurrently, none writes.
3. **A thin global JOIN** — union scenarios + one cheap final completeness check
   (catches anything per-batch ownership missed).
4. **Serialized architect-amend** — when a batch can't cover its anchor and needs a
   new module (the AC-9/AC-10 RLS case), that WRITES the shared `file_map` → must be
   a single serialized post-pass, never concurrent.

### The irreducible serial core (honest)
Even v2 cannot parallelize the **global coverage-completeness check** (cheap) or the
**architect-amend** (rare, mutates the shared `file_map`). On run `427991975dc744bf`
(this design's live datapoint), the AC-9/AC-10 RLS gap drove an architect-amend
(FAILED: `entry_point not in file_map`) → global regen (~21 min) — a genuinely
cross-batch event that lands in the serial join regardless. So on RLS/substrate-heavy
specs the global core is a meaningful chunk; v2's win is largest on specs whose tail
is dominated by per-file validate/repair, not global amend.

### Sequencing
- **v1** first (cheap, ships behind a flag, ~2.2× on gen).
- **v2** as the end-state (the "N independent cycles" target) — bigger win, but it
  touches coverage-floor SEMANTICS (strict ownership) + needs the join + the
  serialized amend, so it is a Neo/Linus-heavy design, not a quick flip.

## Backlog experiment: BALANCE-SPLIT (measure cold-start floor → pick the size knob)

**Status:** EXPERIMENT, not yet run. Pairs with v1 (parallel fan-out). Run this
BEFORE hard-coding a new `scenario_batch_size` default.

### Why (measured, run `427991975dc744bf`, the [8,8,8,1] split)
The batch COUNT is not chosen — it's `ceil(anchors / scenario_batch_size)`
(`_batch_anchor_ids`, orchestrator.py:3645). 25 anchors / size-8 → 4 batches
`[8,8,8,1]`. Per-batch stream summaries decompose the cost:

| batch | anchors | wall | idle (spin-up) | thinking | out tok | cache_creation |
|---|---|---|---|---|---|
| gen-1 | 8 | 654s | 172 | 481 | 42k | 142k |
| gen-2 | 8 | 921s | 341 | 576 | 53k | 101k |
| gen-3 | 8 | 483s | 213 | 270 | 32k | 79k |
| **gen-4** | **1** | **351s** | **86** | **265** | **26k** | **66k** |

- **Cold-start FLOOR (direct measurement, the n=1 batch): ~350s wall + ~26k
  output + ~66k cache-creation tokens, paid PER batch regardless of anchor
  count.** It is almost entirely `idle (container/queue ~86–170s) + a baseline
  thinking pass (~265s)` — spin up, read spec/manifest/conventions cold, reason
  about fixtures BEFORE writing the first scenario.
- **Marginal cost per added anchor ≈ ~40s wall + ~3k output** (fit, small sample,
  high variance — gen-2 vs gen-3 both n=8 ran 921 vs 483s; treat as directional).
- So an 8-anchor batch (~650s) is only ~1.8× a 1-anchor batch (~350s): the fixed
  floor dominates at small sizes. The `[8,8,8,1]` straggler cost a full ~350s
  cold invocation for ONE anchor.

### Projection (floor ≈ 310s + 43s/anchor; prompt cost ≈ K × ~73k)
| size | #batches | serial wall (today) | parallel floor = max-batch | prompt tok |
|---|---|---|---|---|
| 8 (current) | 4 | ~2300s | ~650s | ~290k |
| 5 | 5 | ~2600s | ~525s | ~365k |
| 4 | 7 | ~3250s | ~480s | ~510k |
| 2 | 13 | ~5100s | ~400s | ~950k |
| 1 | 25 | ~8800s | ~350s | ~1.8M |

Parallel floor asymptotes at the ~350s fixed wall: 8→4 buys ~26% (650→480s);
4→1 buys almost nothing more while multiplying prompt tokens 6×. **Sweet spot:
size ~5–6, NOT 1.** And the larger immediate win is BALANCE, not "smaller" —
re-split `[8,8,8,1]→[7,6,6,6]` at the SAME batch count lowers the parallel floor
(set by the max batch) and kills the 1-anchor straggler, at zero extra batches.

### The experiment
1. **Instrument** a predicted-time estimate per anchor (start with the flat
   `310 + 43·n` floor model; refine if a cheap per-anchor signal exists — e.g.
   declared-intent count for that anchor, since more intents ⇒ more scenarios).
2. **Balance-split**: replace the fixed `ids[i:i+size]` chunking with a split that
   equalizes predicted batch TIME (or, cheap proxy, anchor COUNT) across a chosen
   batch count K — eliminating stragglers like the 1-anchor 4th batch.
3. **Sweep** `scenario_batch_size` ∈ {8 (control), 6, 5, 4} on a fixed heavy spec
   (the 25-anchor auth spec is the natural fixture), under v1 parallel fan-out.
   Measure: TE/QA stage wall, max-batch floor, total cache-creation tokens, and
   coverage-floor regen rate (smaller batches lean harder on cross-credit/regen).
4. **Decide** the default from the measured floor + token cost + regen rate — do
   NOT hard-code a number ahead of the data. Expected outcome: size ~5–6 + balance.

### Gating / caveats
- The slope is a SMALL-SAMPLE fit; the experiment exists to replace it with
  measured points before changing the default.
- Watch the **coverage-floor regen rate**: smaller batches fragment cross-anchor
  fixture reuse and may raise regen (a hidden serial cost the floor model ignores).
- Strictly a v1-parallel optimization — serially, ANY smaller size is worse
  (each batch adds the full ~310s fixed floor).

### Live finding: the repair tail is itself an `exit 124` liability
On run `427991975dc744bf`, the **repair pass TIMED OUT** — `AGENT_FAILED exit_code=124`
at the 1800s wall (ran 23:20:35→23:50:35, full 30 min) — and the run continued to the
implementer only via the salvage backstop. The repair step is an UN-decomposed,
whole-corpus monolithic pass, so it mega-thinks on a heavy spec — **the exact failure
mode the generate-decomposition was built to eliminate, recurring in the repair tail.**
This strengthens the v2 case beyond throughput: decomposing/parallelizing the tail
(per-batch repair) also removes a real TIMEOUT liability, not just wall-time. (A cheaper
interim: batch/cap the repair pass the way generate was, even before full v2.)
