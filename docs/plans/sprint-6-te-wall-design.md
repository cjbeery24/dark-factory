# Sprint 6 — Test Engineer scenario-validation wall

> The N=3 ship attempt cleared the validator + architect but aborted 2/2 at the
> Test Engineer: "TE scenarios failed validation after 3 attempts." TWO checks
> fail every retry. Investigation (file:line-verified) found the dominant one is
> a FALSE POSITIVE triggered by the spec's own text.

## Root causes

### A — `contract_helper_usage` FAIL is a false-positive cascade (the load-bearing bug)
- The spec is a pure function (`parseAmount`). Line 269 *denies* substrate:
  "...no cross-cutting, **tenant-isolation**, security, or persistence invariant."
- The postgres adapter's `tenant_isolation` substrate rule
  (`darkfactory/adapters/postgres/substrate-rules.toml:74-79`, pattern
  `tenant.*isolat`) matches that substring with **no negation awareness** →
  `triggered_rules(spec_text)` returns `['tenant_isolation']` (verified live).
- Cascade: trigger fires → postgres added to active_adapters
  (`orchestrator.py:442`) → `stage_adapter_contracts` (`:2917`) copies postgres
  TS helpers into `_contracts/postgres/` → `_check_contract_helper_usage`
  (`validator.py:1155-1217`) hard-fails because no test imports `_contracts/`.
- The TE is **correct** not to import them: `agents/test-engineer.md:225` says
  import a helper only if its trigger "matches a substrate primitive in your
  spec" — postgres RLS helpers are irrelevant to number parsing. The validator
  check has **no semantic gate** (presence + non-import = fail), so it punishes
  the correct behavior. **Unsatisfiable by construction.**
- Same class as the known "spec_validator wire-language FP on foot-gun docs"
  (project_database_foundation_deferred_bugs): a spec that *mentions* a
  substrate concept (even to deny it) wrongly activates that machinery.

### B — `assertion_upper_bound` FAIL is a real tension with dead-end feedback
- `_check_assertion_upper_bound` (`validator.py:842-864`), cap
  `_MAX_ASSERTIONS_PER_FILE = 30` (`:162`), **per-file**, `_MIN_FILES = 3` (`:31`).
- parse-amount.test.ts has 47 assertions. A rich spec (4 error codes + many ECs)
  naturally exceeds 30 in one file. **Splitting into more files is the intended
  escape valve** (no max file count) — BUT the TE prompt (`test-engineer.md:224`)
  and the retry feedback (`orchestrator.py:3140-3147`) only say *consolidate*,
  never *split a fat file into more files*. The LLM can't fix "too many
  assertions" by deleting coverage → dead-end retry loop.

### C — why all 3 retries fail identically
Both are hard fails (`validator.py:411` `all(c.passed)`). For A there's no valid
fix (importing an irrelevant helper is nonsensical); for B the feedback never
signposts the one reachable fix (split). So every attempt fails the same two.

## Fix options

### A (primary — the false positive)
- **A1 — negation/affirmative-aware trigger.** Make `tenant_isolation` (and
  siblings) not fire on denial/negated prose. Highest leverage (kills the
  cascade at the root; fixes any spec that merely mentions tenancy in prose),
  TOML/regex-local, unit-testable. RISK: regex negation detection is fragile —
  could re-open a real-trigger MISS (the TOML's own comment already worries about
  misses). Needs a test pair (affirmative fires, denial doesn't).
- **A2 — semantic validator check.** `_check_contract_helper_usage` requires an
  import only if a staged helper's trigger matches a substrate primitive in the
  spec (mirror test-engineer.md:225's own condition). RISK: more validator logic;
  weakens the deliberately strict "you staged it, use it" rule.
- **A3 — tie the check to the architect's actual substrate manifest.** If the
  only reason `_contracts/` exists is a trigger fire but the architect produced
  a substrate-EMPTY manifest (no DB primitives modeled), skip the hard fail.
  More robust than regex tuning (anchors on what the architect actually modeled,
  not spec-text matching), but couples the validator to manifest state.

### B (secondary — the cap)
- **B1 — teach splitting.** Add to the TE prompt (`test-engineer.md:224`) AND the
  retry feedback (`orchestrator.py:3140-3147`): "if a file exceeds 30 assertions,
  SPLIT it into more files by concern (error code / anchor group); min 3 files,
  no max." Converts the dead-end into a reachable fix. Low risk (pure guidance;
  escape valve already exists). Keep the cap.
- **B2 — raise/wire the cap** (`max_assertions` param exists at `validator.py:283`
  but unwired). Weakens the granularity guard; only if B1 proves insufficient.

## Recommendation (investigation): **A1 + B1**, possibly A3 instead of/with A1.
A is the load-bearing bug (no valid fix today); B is a guidance gap with an
existing escape valve. Neither touches the Implementer barrier or the
gate→Implementer bridge (TE-facing, pre-implementer) — NOT authorization-
sensitive (AUTHORIZATION-MODEL.md's scenario_lint.py ref is stale; live checks
are in scenarios/validator.py).

## Neo ruling — APPROVE-WITH-CHANGES (clean) — FINAL APPROACH

**Seam = STAGING, gated on the architect's substrate manifest (A3). Reject A1
(fragile negation regex) and A2 (duplicate inference, weakens the strict rule).
Fix BOTH A and B.**

Decisive discovery: `orchestrator.py:3044-3048` ALREADY gates the
scenario-architect addendum injection on `substrate_manifest.is_file()`, with a
comment calling the gate "generic, not Postgres-specific" and naming the exact
"prose-asks-LLM-to-ignore-itself anti-pattern this whole substrate-verification
effort is closing." So A3 is NOT novel coupling — it APPLIES an already-blessed
pattern to the adjacent staging call. The architect's substrate manifest is the
pipeline's only authoritative "substrate work was prescribed" signal; spec prose
(A1) and a later validator inference (A2) are downstream proxies.

**Q5 (false-negative) solved by construction:** affirmative RLS spec → has a
`## Substrate Primitives` section → architect emits substrate TOML → `is_file()`
True → helpers stage → check fires → TE must import. Pure function → no section →
no TOML → `is_file()` False → no staging → empty `_contracts/` → check passes
(`validator.py:1182-1186` short-circuits to PASS on no staged helpers). Strictly
MORE precise than today; keys on the architect's modeling, not prose matching.

**Implementation (Neo's required corrections — the tool hallucinated some paths;
these are the verified real ones):**
1. **Gate the real call site `orchestrator.py:2917`** (`stage_adapter_contracts(...)`,
   single call site). **Hoist** the `substrate_manifest` path computation
   (currently `:3044`) ABOVE 2917 and gate staging on `substrate_manifest.is_file()`.
   The architect phase runs before 2917, so the manifest's presence is settled.
2. **Do NOT add a parallel `spec_triggers` check** — `adapter_staging.py:111`
   already uses the poisoned predicate. Make the MANIFEST the authority at this
   seam (pass `substrate_present: bool` into `stage_adapter_contracts`, or gate
   the 2917 call directly). Don't stack two prose gates.
3. **Don't gate the validator axis too** — staging-gate alone fully closes the
   unsatisfiable state (`_check_contract_helper_usage` short-circuits to PASS when
   `_contracts/` has no helper files). Leave `validator.py:1155-1217` intact.
4. **B1:** add split guidance to `agents/test-engineer.md:224` AND retry feedback
   `orchestrator.py:3140-3147` — "if a file exceeds 30 assertions, SPLIT by
   concern; **min 3 files, no max**" (restate the floor so the agent doesn't
   split below `_MIN_FILES`). Keep guidance generic (not an assertion dump).
   Q4: no new conflict — `_MIN_FILES=3` is a floor with no ceiling; no per-file
   MINIMUM-assertion lint exists.

**Q6 / repeated-mistake:** this is the "wire-language FP on foot-gun docs" family;
the SYSTEMIC fix already exists in-codebase (anchor substrate decisions on the
manifest, not spec-text regex). A3 extends it — don't build a generalized
negation-aware NLP layer (over-engineering trap). **Authorization: NOT sensitive**
(TE-facing, pre-implementer; no barrier/report-row/barriers.py surface; confirmed).

**Highest-value first move:** hoist `substrate_manifest` above 2917 + gate
`stage_adapter_contracts` on `is_file()`. Ship B1 same sprint.

## Open questions for Neo (superseded by the ruling above)
- **Q1:** For A, is A1 (negation-aware regex — fragile), A2 (semantic validator),
  or A3 (anchor on the architect's empty substrate manifest — robust but couples
  validator↔manifest) the right primary fix? A3 seems most principled (the
  architect, not a spec-text regex, is the authority on whether substrate
  exists) — agree, or does it over-couple?
- **Q2:** Is the REAL bug that a pure-function spec activates the postgres
  adapter at all? Should the fix be at the TRIGGER (don't activate), at STAGING
  (don't stage helpers when the manifest is substrate-empty), or at the CHECK
  (don't mandate usage)? Where's the right seam?
- **Q3:** Scope — fix BOTH A and B this sprint (both block the ship), or is A
  alone enough to test the next wall (B may not bite once A is fixed if the TE,
  unblocked, happens to land ≤30/file — but that's luck, not determinism)?
- **Q4:** B1 risk — does telling the TE to split files conflict with `_MIN_FILES`
  or any other lint (e.g. a per-file MINIMUM assertions check that splitting
  could violate)? Verify no new unsatisfiable conflict is introduced.
- **Q5:** A1/A2 false-negative risk — if we make the trigger/check less eager,
  do we risk a REAL substrate spec slipping past contract-helper enforcement?
  How to keep the affirmative case firing (the canonical RLS spec must still
  stage + require helpers)?
- **Q6:** Repeated-mistake check — this IS the "persisted-but-not-consumed /
  wire-language FP" family. Is there a systemic fix, or is per-trigger precision
  the pragmatic move per "earn the next class with data"?
