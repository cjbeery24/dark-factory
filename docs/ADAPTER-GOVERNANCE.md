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
