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

## Open questions for Neo
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
