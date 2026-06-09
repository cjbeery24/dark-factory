# Sprint 3 — Validator verdict determinism

> Design for Neo review BEFORE implementation. The reset's benchmark showed the
> spec-validator is a coin-flip gate: the SAME 100/100 spec
> (shared-parse-amount) PASSED validation once (reached architect) but returned
> REVISE/blockers=2/consistency=FAIL 3/3 later — non-deterministically escalating
> a real §5/§10 error-message contradiction from "warning" to "blocker". The
> validator gates EVERYTHING, so this is the dominant reliability blocker.

## State (investigated, file:line, current on main)

- **`blockers` is an LLM SELF-COUNT.** `agents/spec-validator.md:675-680` tells
  the LLM to count its own "Critical Issues" entries and write `blockers=N` to
  the `.status` file. **No code derives blockers from typed findings.**
- **The gate reads the LLM's self-report:** `scenarios/detection.py:177` —
  `blockers>0` → validation fail; `verdict!=PASS` → fail. Separately,
  `orchestrator.py:1245-1307` aborts on `consistency=FAIL` + non-empty ledger.
- **temp=0 is pinned** (`agents.py:108-111`, commit `3a9b01a`) but un-instrumented;
  the pin's own commit predicted **verdict instability** as its falsifier
  (`agents.py:88-94`) — the shared-parse-amount observation IS that falsifier.
- **The orchestrator ALREADY trusts the typed ledger over the status field**
  (`orchestrator.py:1287-1297`: catches PASS-with-contradictions and
  FAIL-without-evidence). So deriving the gate from typed artifacts is an
  incremental extension of an existing seam, not a new design.
- **Two distinct axes:** (a) zero-emission *detection* variance (validator finds
  nothing on 2/5 runs — what the reproducibility harness measures, isolated
  Consistency-Check surface); (b) *severity/verdict* variance (same detected
  finding rated warning vs blocker → PASS vs FAIL — what blocked shared-parse-
  amount). The harness does NOT measure axis (b).

## The crux (must resolve FIRST, by measurement)

Is shared-parse-amount's variance **detection** or **classification**?
- If the §5/§10 contradiction appears in the typed consistency-ledger EVERY run
  but is rated blocking only sometimes → **classification variance** → deriving
  blockers deterministically from the ledger FIXES it (always blocks → correct +
  deterministic).
- If the contradiction appears in the ledger only SOME runs → **detection
  variance** → deterministic derivation just moves the non-determinism upstream
  to detection; need an axis-(a) fix too.

We can't tell from the wiped run artifacts. **Step 1 is to measure it.**

## Plan (measure → derive → instrument → verify)

### Step 1 — MEASURE (evidence-first, before any fix)
Run the FULL validator on shared-parse-amount **N=5** times (via the benchmark —
each aborts at the validator, ~3.5 min/run, ~$2 total) and capture each run's
`<spec>-consistency-ledger.yaml` + `validation.status` from the per-run archive
(`snapshot_final` preserves them; the framework reset only wipes the live tree).
Classify: does the §5/§10 contradiction appear in the ledger 5/5 (classification
variance) or <5/5 (detection variance)? This decides the fix.

### Step 2 — DERIVE the gate deterministically from typed artifacts (Option A)
Stop trusting the LLM's self-reported `blockers=N`/`verdict`/`consistency`.
Derive them in code from the typed artifacts the validator already emits:
- `consistency` = FAIL iff the consistency-ledger contains ≥1 contradiction
  (already cross-checked at `orchestrator.py:1287-1297`; make it the SOURCE OF
  TRUTH, not a cross-check).
- `blockers` = count of typed blocking findings: ledger contradictions +
  `unresolved_contracts` (already YAML at agent `:567-591`) + [policy decision
  for scope / startup-anti-pattern findings, which are prose-only today].
- `verdict`/pass = deterministic function of (derived blockers, divergence,
  derived consistency). Same typed artifacts → same gate decision.
This makes the DECISION deterministic given the findings — fixing axis (b).

### Step 3 — INSTRUMENT the temp-0 pin (Option E, cheap precondition)
Emit a telemetry event confirming temperature=0 was actually applied to the
validator (mirror the classifier's `CLASSIFIER_IO_HASH`) — close the
silent-ignore blind spot flagged at `agents.py:101-103`.

### Step 4 — VERIFY determinism (extend the harness, Option F)
The current harness measures detection catch-rate, not verdict determinism. Add
a measurement that runs the FULL validator on a fixed spec N times and asserts
the DERIVED verdict is identical across runs (and, if Step 1 found detection
variance, tracks the residual detection-variance rate separately). This is
Sprint 3's exit criterion: same spec → same ship/no-ship, N/N.

## Out of scope (separate)
- Axis (a) detection determinism (the Session 1.5 structured-output Pass-1
  extraction, never implemented) — only pursue if Step 1 shows detection
  variance is the dominant problem. Deterministic derivation (Step 2) is the
  fix for the OBSERVED failure.
- Re-running the full Sprint 5 verification — once the validator is
  deterministic, the shared-parse-amount run can proceed to the architect (after
  the real §5/§10 spec contradiction is fixed, which is legitimate spec work).

## Review — APPROVE-WITH-CHANGES (folded in)

> CAVEAT: the neo:neo tool's output post-processor CRASHED (structured_parser /
> JSON control-char bug) — this is a code-grounded adversarial review, NOT an
> actual Neo-engine ruling. Retry Neo (or rely on Linus on the diff) before the
> Step-2 gate change. The required changes below are folded in.

1. **The crux is "is the contradictions-list stable across runs," NOT
   classification-vs-detection.** There is no severity knob in the consistency
   procedure (`spec-validator.md:101-409`): a pair matches a T1-T5 trigger →
   contradiction → forces `consistency=FAIL` (`:276-278`), or it doesn't.
   Variance has 3 origins: (1) emission/detection, (2) self-count *transcription*
   (ledger stable, status file differs — the PASS-with-contradictions drift the
   orchestrator already catches at `:1287-1297`), (3) typing. **Derivation (Step
   2) fully fixes (2); does nothing for (1)/(3).**
2. **Step 2 is CONDITIONAL on the measurement.** Derivation ships either way
   (it makes the existing ledger cross-check authoritative — strictly correct),
   BUT it only CLOSES the sprint if N=5 shows the contradictions-hash is stable
   (cause 2). If the ledger flips (cause 1/3), the headline goal becomes
   DETECTION determinism and Step 2 is necessary-but-insufficient — do not let
   it close the sprint.
3. **Instrument FIRST (promote Step 3 ahead of Step 1).** The decisive cheap
   experiment: emit one telemetry event per validator run with
   {contradictions-hash, fr_ledger claim-set hash, self-reported
   blockers/consistency, per-category blocker counts, temp=0-applied-confirmation}.
   Run N=5, read the answer off telemetry (machine-checkable). Folds Steps 1+3,
   closes the temp-pin blind spot (`agents.py:101-103`). ~$2.
4. **Reject the typed per-finding severity field** — it re-imports the exact
   self-classification variance we're killing (a `severity` the LLM sets per
   finding is a second non-deterministic self-classification dressed as typed).
5. **Q2 policy:** fold the FR-count scope check (`:98-99`) into code-derivation
   (it's already mechanical — count FR headings). Leave startup anti-patterns
   (`:73-96`) LLM-counted FOR NOW, but document the derived-blockers formula
   honestly: `derived(consistency)+derived(contracts)+derived(fr_count)+
   llm(startup_antipatterns)` — deterministic-modulo-one-semantic-category, not
   fully. Capture per-category counts in the Step-3 telemetry to size the residual.
6. **Fail-closed is correct (Q4).** A gate that sometimes admits a spec with a
   real contradiction is non-deterministically shipping broken specs downstream,
   not "letting good specs through." A consistent false-block is loud, located
   (ledger names the claim pair/anchors), and fixable at the typology — a far
   better failure mode than a non-deterministic false-pass.
7. **Before flipping the gate's source of truth, read docs/AUTHORIZATION-MODEL.md**
   and confirm the `consistency`/`blockers` status fields aren't consumed by any
   Implementer-visible path (clean per the review's check, but verify all readers).

## Open questions (superseded by the review above)
- **Q1:** Is deterministic derivation from typed artifacts (Step 2) the right
  primary fix, or does it merely relocate non-determinism to detection? (Hinges
  on Step 1's measurement — should we GATE the design on measuring first?)
- **Q2:** Policy for prose-only blocking categories (scope count, startup
  anti-patterns): promote them to typed ledger entries (so they too are derived
  deterministically), or leave them LLM-counted (residual non-determinism)? What
  fraction of real blockers are these vs consistency/contracts?
- **Q3:** Deriving `consistency=FAIL` from "ledger has ≥1 contradiction" means a
  real contradiction ALWAYS blocks (deterministic + correct). But the validator
  must still DETECT it reliably (axis a). Is making the decision deterministic
  while detection stays fuzzy a real improvement, or theater?
- **Q4:** Risk — deterministic derivation removes the LLM's ability to
  down-rate a false-positive contradiction to "warning". If detection emits a
  spurious contradiction, it now ALWAYS blocks. Is that acceptable (fail-closed)
  or do we need a typed "severity" field the LLM sets per finding (which
  reintroduces classification variance)?
- **Q5:** Scope/size — is this a measure-first, single-step (Step 1+2) sprint, or
  should Steps 3/4 be separate? Is the whole thing too big to land safely in one
  pass?
