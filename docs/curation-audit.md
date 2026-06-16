# Curation audit & budget

Baseline inventory + classification of dark-factory's **bespoke, hand-curated,
stack-specific** rules — the "curation" the scalability/curation policy
(`docs/plans/00-RESET-PLAN.md` → "Generalization thesis & curation policy",
Neo+Codex consensus 2026-06-15) budgets and aims to minimize.

**Thesis recap:** generality = orchestrate each ecosystem's own verification tools
as ground-truth gates; minimize *bespoke curation* (not deterministic-check count);
LLM = sensor, deterministic evidence = authority. Decision rule for new friction:
live ground-truth → maintained AST/SAST → convert LLM finding to a deterministic
check → bespoke rule (last resort). The counts below are pinned by
`tests/unit/test_curation_budget.py` so growth is deliberate, not silent.

## Labels
- `adapter_contract` — legitimate per-ecosystem semantics feeding a real
  verification (keep; counts as curation but is load-bearing).
- `ground_truth_proxy` — derives from ground truth (AST / compiler / catalog);
  low-curation, scalable (keep).
- `security_sast_candidate` — bespoke security regex that a maintained SAST tool
  should subsume (the planned swap target; should shrink toward 0).
- `temporary_workaround` / `incident_specific_backstop` — added reactively for a
  specific incident; audit periodically for deletion/demotion once the
  underlying gap is closed by a ground-truth/tool check.

## Inventory (2026-06-15)

| Surface | Count | Label | Disposition |
|---|---|---|---|
| `review/patterns.py` SECURITY_PATTERNS | 15 (py5/node5/go3/rust2) | `security_sast_candidate` | **SAST swap IN PROGRESS** — compare-mode shipped (Sprint 30); still authoritative pending the demote bar below. Replace/demote to advisory once Semgrep clears the bar; e.g. `prototype_pollution = Object.assign(...)` is a known FP generator (cost a re-measure confound this campaign). Keep `owned_surface` scoping (Sprint 27). |
| `review/sast_rules/` vendored Semgrep ruleset | 122 (py91/node31) | *maintained-external (NOT bespoke)* | **Not budgeted** — externally maintained (official `semgrep-rules`, pinned by SHA), amortized across the Semgrep community. This is the *scalable* layer-3 the swap moves weight TOWARD; growth here is good. `test_curation_budget.py` deliberately does not count it. |
| `adapters/postgres/substrate-rules.toml` | 3 (custom_guc, rls_policy, tenant_isolation) | `adapter_contract` | Keep — these feed live `verify_substrate()` catalog checks (RLS owner-bypass etc. are invisible to app tests). Narrow fix outstanding: the prose-regex *trigger* false-fires on out-of-scope mentions (backlog); the trajectory is prose → manifest → **catalog**-driven triggering. |
| `adapters/postgres/mechanism-tokens.toml` | 14 | `adapter_contract` / some `incident_specific_backstop` | Pre-LLM spec-hygiene gate ("describe observable, not mechanism"). Several trace to specific database-foundation footguns — audit for which are still load-bearing vs subsumable. |
| `adapters/postgres/sql_lint.py`, `sql_tag_syntax.py` | — | `ground_truth_proxy` | Keep — AST-based via **pglast** (a maintained parser), not bespoke regex. Already on the scalable path. |
| `adapters/typescript/` import_path_normalize, type_only_exports, stub_compile, library_symbols | — | `adapter_contract` / partial `ground_truth_proxy` | TS wire/typecheck semantics; several defer to `tsc`. Keep; revisit if any can fully delegate to the compiler. |

## Next actions (per consensus, sequenced)
1. ✅ Curation-count metric (this audit + `test_curation_budget.py`) — done.
2. **SAST swap** (the big move): introduce Semgrep in advisory/compare-mode against
   the same diff surface, keep `owned_surface` scoping, measure noise, then demote
   the bespoke `security_sast_candidate` regexes and lower the budget. CodeQL later.
   (Framework change → Neo design + Linus+Codex gate.)
   - ✅ **Step 2a — compare-mode SHIPPED (Sprint 30, Neo APPROVE-WITH-CHANGES).**
     `darkfactory/review/sast.py` runs a vendored, externally-maintained Semgrep
     ruleset (`darkfactory/review/sast_rules/`, official `semgrep-rules` py+node
     security packs, pinned by SHA in `RULESET_VERSION`) ALONGSIDE the regexes and
     writes a per-iteration regex-vs-Semgrep compare artifact
     (`{spec}-sast-compare-iter{N}.jsonl`). **ADVISORY ONLY** — gated on the
     `sast_compare_enabled` flag (default off), does NOT feed `security_high_count`
     / score / `GATE_FAILED`, never inlined into an agent prompt. Reuses the
     Sprint-27 `owned_surface` partition so the compare is like-for-like. Findings
     join the seven regex categories via **CWE** metadata (standard taxonomy, not
     bespoke per-rule curation). Runs host-side (the prepass is host-side; Semgrep
     is a host pip extra `dark-factory[sast]`, sibling of `pglast` — NOT an agent-
     image dep), offline/hermetic (`--config <local dir>`, `--metrics=off`, zero
     network). Graceful no-op when semgrep is absent/out-of-pin (`ran=False` ≠
     clean). `go`/`rust` rulesets deferred to the 3rd-stack breadth work (no active
     coverage today; unsupported lang → `ran=False`, honest non-scan).
   - ⏭ **Step 2b — the flip (NOT YET):** make Semgrep (co-)authoritative + demote
     regexes once the compare data clears the bar below. This is
     AUTHORIZATION-SENSITIVE (it changes which findings feed `security_high_count`)
     → separate Neo + Linus+Codex gate, `Audience:` declaration, and at the flip
     `ran=False` must set `security_high_count = -1` (unknown), never silent-clean.
3. Periodic re-audit: anything `temporary_workaround` / `incident_specific_backstop`
   whose underlying gap is now covered by a ground-truth/tool check → delete/demote.

## Demote criteria (the compare-mode exit condition — written down BEFORE measuring)

So the flip decision is mechanical, not motivated. All counts on the **gating
surface** (the surface that actually ship-gates), read from the per-run
`{spec}-sast-compare-iter{N}.jsonl` summary rows (`agree` / `regex_only` /
`semgrep_only` / `regex_only_high`).

- **Window:** aggregate ≥ 30 *finding-bearing* runs spanning python + node and all
  three tiers (≥ 2× the benchmark matrix + the Tier-C de-risk runs). Security
  findings are sparse — volume is needed to see the FP/coverage tails.
- **Per-regex, independent decision** (demotion is not all-or-nothing):
  - **Demote** a regex → Semgrep-authoritative for its category when Semgrep shows
    **no coverage regression** vs that regex's true positives, AND the regex is
    either **noisier** (`regex_only_high` > 0 for it — candidate FPs that can
    NO-SHIP a clean run) **or redundant** (it contributes no `regex_only` true
    positives over the window).
  - **Keep** a regex that has `regex_only` **true positives** Semgrep misses (a
    real coverage gap) at an acceptably low HIGH-FP rate — the narrow layer-3
    backstop the policy explicitly preserves. NB: Semgrep OSS has no generic
    "hardcoded literal secret" rule, so `hardcoded_secret` is an early keep-candidate.
  - Every demotion is **human-adjudicated** by reading the disagreement rows. The
    artifact triages; it does not auto-decide (LLM/automated sensors discover,
    humans + the authorization review decide what gates).
- **On a demotion:** delete the regex from `SECURITY_PATTERNS`, lower
  `SECURITY_PATTERN_BUDGET` by exactly that count (justify in the bump PR), and
  wire Semgrep HIGH findings (owned-surface-scoped, identically) into
  `security_high_count` — under the step-2b authorization gate above.

## Measured demote evidence (2026-06-15, offline corpus sweep)

Gathered with `scripts/sast_corpus_compare.py` (reuses the pipeline's own
`analyze_security` + `run_semgrep` + `build_compare`) over a code-only,
data-excluded corpus: **safe** = 7 mature OSS repos (requests, flask, httpx,
**sqlalchemy**, fastify, **knex**, express — ~480 files, injection-heavy; any HIGH
regex finding Semgrep doesn't corroborate = candidate FP) vs **targets** =
semgrep-rules' own vulnerable test targets (recall/coverage). Zero API cost; far
more finding-bearing data than live pipeline runs (clean impls produce 0/0/0).

Per-regex result (HUMAN-ADJUDICATED, not auto-decided — the bigger corpus exposed
that not all `regex_only` is a clean FP):

| regex | verdict | measured basis |
|---|---|---|
| `prototype_pollution` (node) | **DEMOTE** | **22/22 HIGH on safe code are FPs**, Semgrep corroborates 0, 0 TP. The `Object.assign(non-literal, …)` pattern flags the *normal* safe use (`Object.assign(X.prototype, …)`, `Object.assign(Object.create(null), …)`). Pure noise generator — the rule that caused a re-measure confound this campaign. |
| `sql_injection` | **DEMOTE→Semgrep** | regex FPs on safe internal-identifier concat (`execute("FETCH … " + cursor_name)`, `query(\`set search_path to ${schema}\`)`); Semgrep stays correctly silent (needs user-taint) AND has ~6× recall on targets (sg_only 87 vs regex 13). Better on both axes. |
| `command_injection` | **DEMOTE→Semgrep** | 0 regex FP on safe code, but Semgrep ~7× recall on targets (sg_only 282 vs regex 37). Coverage gain at no FP cost. |
| `path_traversal`, `xss` | demote-lean | regex barely fires; Semgrep covers (sg_only 16 / 5). Low value retained. |
| `insecure_deserialization` | **KEEP** | regex_only on safe = REAL `pickle.loads(`/`eval(` in sqlalchemy that Semgrep's vendored subset MISSED (9). Not a benign-code FP; genuine coverage the regex adds. |
| `hardcoded_secret` | **KEEP** | Semgrep OSS has no generic literal-secret rule (2 vs 19 on targets). MEDIUM severity, low FP. Policy predicted this. |

**Flip sequencing (safety-ordered) for step 2:**
1. **`prototype_pollution` FIRST — a pure DE-ESCALATION.** Removing it only shrinks
   the gating set (it never produced a true positive, only false NO-SHIPs), so it
   cannot add a gate — the safe direction (cf. S26/S27 de-escalations: Audience line,
   no audience-escalation trigger). `SECURITY_PATTERN_BUDGET` 15→14.
2. **Injection categories (sql/command/path/xss) SECOND — these make Semgrep
   AUTHORITATIVE**, which ADDS gating power (Semgrep findings feed
   `security_high_count`) → genuinely audience-escalating → full
   `docs/AUTHORIZATION-MODEL.md` review, `ran=False → security_high_count=-1` guard,
   owned-surface scoping applied to Semgrep findings, Neo + Linus/Codex gate.
3. **KEEP `insecure_deserialization` + `hardcoded_secret`** — Semgrep does not subsume them.

Caveat: safe-corpus FP-freeness for command_injection rests on ~480 files; the
`prototype_pollution` (decisive) and the two KEEPs are robust. Report:
`scripts/sast_corpus_compare.py` (re-runnable).

## Litmus (for a new stack)
A new stack needing a *handful* of bespoke entries = layering works; needing
*dozens* = leaning on the wrong layer (push to ground-truth/SAST/LLM-sensor instead).
The budget test makes the count visible so this is measurable, per stack.
