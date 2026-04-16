# Ecosystem adapter governance

Dark Factory's pipeline core deliberately knows nothing about TypeScript, Postgres, pnpm, or any other ecosystem-specific tool. That knowledge lives in **ecosystem adapters** — pluggable modules implementing the `EcosystemAdapter` ABC. This document defines what an adapter must do to be accepted into the project, who decides, and how adapters move between status tiers.

> **Status:** governance policy. Authoritative for any change touching `darkfactory/adapters/`. The interface itself is defined by `darkfactory/adapters/base.py` (Sprint 1, A1) and pinned by the contract harness at `tests/adapters/test_adapter_contract.py` (S0.2).
>
> **Note on forward references:** paths under `darkfactory/adapters/` cited below (`base.py`, `registry.py`) arrive in Sprint 1 (A1, A2, A3). This policy is committed pre-implementation so it's already in place when those modules land — there is intentionally nothing in `darkfactory/adapters/` today.

## The bar for blessing a new adapter

A submitted adapter MUST satisfy **all five** of the following before merge. Anything missing → not accepted. No exceptions.

1. **Implements `EcosystemAdapter`.** Inherits from the ABC at `darkfactory/adapters/base.py`. All abstract methods present and non-trivial. `name` is a stable kebab-case string (`typescript-node`, `postgres`, `python-fastapi`).
2. **Passes the contract harness.** `pytest tests/adapters/test_adapter_contract.py` runs against the adapter and all assertions pass — positive *and* negative checks, including catalog-drift guards. The pattern for adding a new adapter to the existing fixtures is documented in the module docstring at `tests/adapters/test_adapter_contract.py`; do not paraphrase from this doc, follow what the harness module specifies (the public extension surface lives there, not here).
3. **Ships a worked example spec.** At least one feature spec exercises the adapter end-to-end through `dark-factory run` — validation, scenarios, implementation, gate, review, oracle. The spec lives in the adapter's PR; the run report goes in `docs/pipeline-run-reports/`. "It compiles" is not enough; the SHIP decision must come from a real Oracle call against a real spec.
4. **Architect addendum ≤ 80 lines.** The adapter's `architect_addendum()` returns a prompt fragment of at most 80 non-blank lines. The cap is the value of `darkfactory.adapters.base.GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP` — change the constant to retune; the doc cites it by name so the two never drift. Longer addenda are a signal that ecosystem knowledge is leaking back into the architect prompt instead of being expressed as consistency rules / quality-gate commands. A justified exception requires an `# noqa: addendum-length` marker with a one-paragraph rationale at the top of the addendum file. **Enforcement is now executable** — `tests/adapters/test_adapter_contract.py::test_architect_addendum_within_governance_line_cap` runs against every parametrized adapter and fails the suite if the cap is exceeded; the prior reviewer-only enforcement is retired.
5. **At least one consistency rule.** `consistency_rules()` returns ≥ 1 `ConsistencyRule`. The rule must be motivated by a real spec/env-contract contradiction the adapter should catch — not a hypothetical. "Stub" rules are rejected; a real rule is a regression test against a class of LLM mistakes the architect actually makes for this ecosystem.

A passing contract test plus the four substantive criteria proves the adapter is **integrated**, not just **present**.

## Status tiers

Adapters are tagged with one of three status values in `darkfactory/adapters/registry.py`. The pipeline behaves differently for each.

| Tier | Bar to enter | Bar to maintain | Pipeline behavior |
|------|--------------|-----------------|-------------------|
| **`experimental`** | Passes all five blessing criteria. No production use yet. | None — but the adapter remains experimental until a `supported`-grade run is recorded. | Pipeline prints a banner before the architect step: `[experimental adapter: <name>] not validated in production; bug reports welcome but won't block releases.` |
| **`supported`** | At least one end-to-end pipeline run on a real (non-toy) spec, with the SHIP decision recorded in `docs/pipeline-run-reports/`. The spec must exercise the adapter's main code path (e.g., the Postgres adapter must run a spec that defines a real schema and migration). | Adapter still passes the contract harness on every release. A regression that breaks the supported adapter blocks the release. | Pipeline runs the adapter normally with no banner. |
| **`core`** | Maintained by the Dark Factory team. Currently: `typescript-node`, `postgres` (post-A2/A3). | Same as `supported` plus: bugs filed against core adapters get release-blocking priority. The team owns regression triage for core adapters. | Same as `supported`. |

Tier transitions are explicit decisions, not automatic. Promoting `experimental` → `supported` requires (a) a referenced run report under `docs/pipeline-run-reports/` and (b) a one-line entry in the **adapter subclass's class docstring** naming the spec and date that earned promotion (e.g., `# Promoted to supported by run 2026-04-14-budget-brain-v2-database-foundation`).

Demotion (`supported` → `experimental`, or removing an adapter outright) is a plan-owner judgment call. There is no mechanical rule (no "fails N consecutive releases → auto-demote"). A regression that breaks a `supported` adapter blocks the release; whether it also demotes the adapter is decided case-by-case.

## Stubs and the path to `supported`

Some adapters ship as **honest stubs** — the five blessing criteria are satisfied (ABC compliance, contract harness, worked spec, ≤80-line addendum, ≥1 consistency rule) but the substantive per-phase work (precheck, proposal-rehearsal, computed-addendum content) is intentionally minimal. A stub's job is to prove the extension point, not to do the ecosystem's work yet. `python-fastapi` (Sprint 6 #2) is the canonical example.

A stub is **still an `experimental`-tier adapter**. It never short-circuits the five criteria; it satisfies them at the cheapest honest level. What a stub specifically does:

- `precheck()` returns `[]`. Not "ran, nothing found" — literally has nothing to run. Distinct from a broken precheck that silently passes everything.
- `proposal_rehearsal_typecheck()` returns `None` (opt-out sentinel).
- `consistency_rules()` returns only `self.extra_consistency_rules` (whatever project-hints supplies); no baseline rules are shipped until a real post-mortem motivates them.
- `architect_addendum()` ships static prompt guidance describing the ecosystem's shape, but defers computed content to post-promotion work.

A stub is strictly better than the pre-adapter failure mode (where a Python project ran through the TypeScript adapter and produced misleading `ts-undeclared-import` findings). It is strictly worse than a `supported` adapter that does the real work.

**Promotion from stub → `supported`** requires the same bar every promotion needs (one real end-to-end run under `docs/pipeline-run-reports/`), plus the substantive gap closed:

1. **Computed addendum content — the LLM stops reading the filesystem.** The stub's static addendum tells the architect *how* to pick a command based on what's on disk (e.g. "emit `uv sync` when `uv.lock` is present; `pip install -r requirements.txt` when `requirements.txt` is present"). The architect is an LLM. LLM filesystem inspection is the single most reliable source of hallucinations in this codebase — it reports files that don't exist, misses files that do, and confuses transitive dependencies for direct ones. A `supported` adapter detects the lockfile flavor *in adapter code* and bakes the resolved answer into `architect_addendum()` as a computed string. The architect receives `"Install command: uv sync"` as a fact, not `"Pick one of these three based on which file exists"` as a puzzle. This pattern applies to any ecosystem-specific decision grounded in the repo tree (test runner, typecheck tool, monorepo workspace layout).
2. **Precheck does real work.** At least one check motivated by a seen failure class — `python-fastapi` would add import validation against declared deps, Pydantic model stub round-trip, or both. An empty precheck past promotion is a smell: either the ecosystem produces no class of failure the adapter could catch (unlikely) or the adapter isn't earning its tier.
3. **Proposal-rehearsal either opts in or pins its opt-out reason.** `None` is legal at `supported` tier too (Go has no typecheck pass distinct from compile) but the opt-out must be *explained* in the method's docstring, not just defaulted. A `supported` adapter that opts out silently is hiding the decision.
4. **Baseline consistency rules where the ecosystem has a real cross-project pattern** — migration-mode discipline, banned commands, forbidden hostnames. "Ship with ≥1 rule" is the blessing-criteria floor; `supported` adapters usually have several, each motivated by a concrete post-mortem. Rules without motivation are rejected regardless of tier (criterion #5 applies everywhere).

The computed-addendum rule matters most because it crosses an architectural boundary: **filesystem inspection moves from the architect (LLM) to the adapter (deterministic Python)**. The adapter author decides what "which package manager does this project use?" means once, in code, with tests; the architect receives the answer as part of a prompt it can't second-guess. Anything the adapter could resolve deterministically, it should — the addendum's static text is for ecosystem-wide contracts ("use `pytest`, not `unittest`"), not per-project branching logic.

## Who decides

Today: this is a one-person project. **The plan owner decides** what gets merged, what tier each adapter sits in, and when one ships.

Future contributors should not assume an open-door PR policy. Submit an issue first describing the adapter you want to add (ecosystem, why core can't already handle it, sketch of the addendum and consistency rules) and get plan-owner sign-off **before** writing the implementation. Adapters submitted as cold PRs are expected to bounce.

This isn't gatekeeping for its own sake. Each adapter expands the surface area the contract harness has to defend, and unmaintained adapters become silent bit-rot. A small, tightly-curated set of well-maintained adapters is the design goal.

## Deprecation lifecycle

Whenever an adapter's public surface changes — a method gets renamed, a re-export is moved, a config key gets a new spelling — the old surface MUST stay in place with a `DeprecationWarning` for at least **two minor versions** (see `docs/RELEASE-CADENCE.md` for what counts as a minor version). The clock starts the minor version where the deprecation lands.

| Phase | Version range | What ships |
|-------|---------------|------------|
| Live | `X.Y` (deprecation lands) | Old name + new name both work. Old name emits `DeprecationWarning(<message naming the new name and the removal version>)`. |
| Eligible | `X.Y+1` | Same: old still works, warning still fires. Removal candidate but not required. |
| Removable | `X.Y+2` and later | Old name MAY be deleted in this minor or any later one. Deletion bumps the minor (it's a public surface change). |

This applies to:
- Module re-exports added during refactors (e.g., `darkfactory/spec/__init__.py` re-exporting symbols moved into adapters in A2).
- Renamed `EcosystemAdapter` methods or attributes.
- Renamed config keys (with `tomllib` migration shim).
- Changed import paths inside `darkfactory/adapters/`.

It does NOT apply to:
- Internal helpers (anything not exported from a package's `__init__.py` and not used outside its own module).
- `experimental` adapter surfaces — those carry no compatibility promise by definition.

### Substrate-rule schema as public surface

The substrate-rule TOML schema (`substrate-rules.toml` in any adapter that ships substrate verification — currently `darkfactory/adapters/postgres/substrate-rules.toml`) is a **semver-governed public surface** at the same status as `architect_addendum.md` and the `EcosystemAdapter` ABC shape.

Two distinct concerns are governed differently:

- **Schema (parse-time contract)** — field names (`trigger`, `match_patterns`, `required_primitives`), file location, and top-level table layout. Loader code (`load_substrate_rules`) and any downstream tool that introspects the TOML break silently when these change. Schema changes need the standard deprecation window.
- **Rule-firing semantics (validator-behavior contract)** — *which* rules exist, what they match, what primitives they require. These move specs across the pass/fail line in either direction; treating them as schema changes would freeze the validator's ability to learn from new failure modes.

| Change | Bump | Rationale |
|--------|------|-----------|
| Add an optional field to the rule schema | Minor | Net-new public surface; existing rules continue to load |
| Rename or remove a field on the rule schema | Major | Downstream parsers / loader break without warning; needs the standard two-minor-version deprecation window |
| Rename the TOML file or top-level keys | Major | Same — parse-time contract change |
| Add, remove, tighten, or loosen a rule (or its `match_patterns`) | Minor | Validator behavior change. The set of specs that pass/fail shifts; the schema does not. Symmetric — adds and removes are both behavior changes that shift specs in opposite directions, and asymmetric treatment would force the validator's vocabulary to grow forever |

Schema changes follow the deprecation table above (live → eligible → removable). When deprecating a field, leave it in the schema and keep the loader tolerant of both spellings until the eligible version; the addendum prose may reference the new name immediately.

The `DeprecationWarning` message MUST name (a) the replacement, (b) the version after which removal is eligible. Example:

```python
warnings.warn(
    "darkfactory.spec.sql_lint is deprecated; use "
    "darkfactory.adapters.postgres.sql_lint. Removal eligible 1.5.0.",
    DeprecationWarning,
    stacklevel=2,
)
```

## How this connects

- The contract harness (S0.2, `tests/adapters/`) is the **mechanical** half — it proves an adapter implements the ABC correctly.
- This document is the **policy** half — it answers questions the harness can't: "should we accept this adapter at all?" "when can we delete the old re-export?"
- Both are needed. Without the harness, "implements the ABC" is unverified. Without policy, every adapter PR re-litigates the same questions.

## See also

- `darkfactory/adapters/base.py` — `EcosystemAdapter` ABC (Sprint 1, A1).
- `tests/adapters/` — contract harness that pins the ABC's behavior.
- `docs/RELEASE-CADENCE.md` — what "minor version" means for the lifecycle clock.
- `docs/audits/2026-04-14-ecosystem-leakage.md` — original audit motivating the adapter layer.
- `docs/plans/2026-04-14-pipeline-modularity-and-remediation.md` — the plan that produced this governance.
