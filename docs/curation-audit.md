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
| `review/patterns.py` SECURITY_PATTERNS | 15 (py5/node5/go3/rust2) | `security_sast_candidate` | **SAST swap target** — replace/demote to advisory once Semgrep (then CodeQL) runs; e.g. `prototype_pollution = Object.assign(...)` is a known FP generator (cost a re-measure confound this campaign). Keep `owned_surface` scoping (Sprint 27). |
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
3. Periodic re-audit: anything `temporary_workaround` / `incident_specific_backstop`
   whose underlying gap is now covered by a ground-truth/tool check → delete/demote.

## Litmus (for a new stack)
A new stack needing a *handful* of bespoke entries = layering works; needing
*dozens* = leaning on the wrong layer (push to ground-truth/SAST/LLM-sensor instead).
The budget test makes the count visible so this is measurable, per stack.
