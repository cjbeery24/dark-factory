# #5 — Restricted-role (NOBYPASSRLS) gate: design

**Status:** DESIGN DRAFT (autonomous, written during run6). Needs Neo (design) + Linus (code) +
`Audience:` declaration + a **Docker gate-run validation** before it lands/activates. Opt-in,
default OFF — cannot affect any in-flight run (incl. run6).

## Problem (evidence)
run5 escalated: 100% scenarios + clean security scan, but the reviewer caught an RLS-scoping bug
because the test Postgres runs as **superuser (BYPASSRLS)** — RLS is never enforced in the harness,
so an RLS-bypassing impl is observationally identical to a correct one. The §8a non-negotiable
(tenant isolation) is therefore protected only by the variance-prone reviewer (~1/3), not the
deterministic gate. Recurring (spec-4 too). Neo's verdict: a declared non-negotiable that *has* a
behavioral expression MUST be deterministically gate-enforced.

## Mechanism: dual-connection gate
- **SETUP (privileged):** the existing privileged `DATABASE_URL` runs migrations, creates a
  restricted role, GRANTs it table privileges, and (for tenant-isolation specs) ensures
  `FORCE ROW LEVEL SECURITY` so even table owners are subject to policy.
- **EXECUTION (restricted):** scenario execution connects via a `DATABASE_URL` whose role is
  `NOSUPERUSER NOBYPASSRLS`, granted `SELECT/INSERT/UPDATE/DELETE` on the tables but NOT ownership.
  RLS is now live during assertions → an RLS-bypass impl returns zero rows → scenarios go red →
  deterministic gate NO-SHIP, every run.

## Surface
- `scripts/lib/scenarios.sh` — where the postgres reset setupFile + `DATABASE_URL` are wired
  (~193-222). Add: create-restricted-role SQL (run via the privileged conn in setup), and point the
  scenario runner's `DATABASE_URL` at the restricted role.
- `darkfactory/pipeline/quality_gate.py` — `extra_env` injects `DATABASE_URL` (~1310). Thread a
  separate restricted execution URL.
- `darkfactory/adapters/postgres/substrate-rules.toml` — `force`/`force_check` already give the
  *structural* check (`relforcerowsecurity`). #5 adds the *execution* half. For tenant-isolation
  specs, make FORCE required (not optional) when the restricted gate is on.
- `darkfactory/models/config.py` — opt-in flag `restricted_role_gate: bool = False` (pattern matches
  `scenario_parallel_generation` etc.). Default OFF.

## Authorization
The restricted-role grant SQL is **gate test-infrastructure**, the same character as the existing
`_df_setup/_df_reset` (AUTHORIZATION-MODEL.md §117-136 "Intentionally Implementer-readable gate-infra"):
catalog/role plumbing, no holdout/reviewer/scenario-shape strings → NOT a d2fecbf-class audience
escalation. Commit needs an `Audience:` line; it is not a barrier change. (It DOES escalate gate
severity — an RLS-bypass now fails the gate — but that's the intended deterministic-floor purpose,
opt-in.)

## Adapter-elimination link (C2-2)
A faithful restricted-role gate makes the **blind scenarios the behavioral verifier** of the
spec-declared tenant-isolation invariant — which **retires the adapter's `verify_substrate`**
structural check (the invariant is proven by observable behavior, stack-agnostic). This is the
Category-2-preservation half of the adapter-elimination plan.

## ⚠ Complication surfaced by this design (Neo must weigh in)
spec-5's own MAJOR-2 carve-out means the **app uses TWO connection modes**: a non-tenant-scoped/
privileged path for the auth-resolution bootstrap (resolveUser/resolveTenant, which precede tenant
context) **and** a tenant-scoped path (`withDb`) for application queries. So the gate **cannot** just
hand the app a single restricted role — under a pure restricted role, the app's *legitimate*
auth-resolution queries would themselves return zero rows and break (the very bootstrap the carve-out
exempts). The restricted-role gate must therefore accommodate the app's dual-connection model:
- Option A: the restricted EXECUTION role gets a **targeted bypass** (e.g. `SECURITY DEFINER`
  helper / a second privileged credential the app uses only for auth-resolution) — mirrors how the
  app is *supposed* to run in production.
- Option B: grant the restricted role row access to the specific bootstrap lookups (by unique index)
  without full bypass.
This is exactly the production privilege model spec-5's carve-out now describes — so the gate should
**model production**, not a single all-or-nothing role. This complication is why #5 needs Neo design
+ a real Docker gate-run validation, not a quick autonomous land.

## Validation plan (before activation)
1. Neo design review (esp. the dual-connection complication above) + Linus code review.
2. Build + a **Docker gate run** under the restricted role on a known-good impl (must PASS) AND a
   deliberately RLS-bypassing impl (must FAIL) — the differential proof.
3. Land opt-in (default OFF). Flip on only after the differential validates.

## Neo review outcome (2026-06-24, autonomous) — APPROVE-WITH-CHANGES → revised design

Neo reviewed this design during run7's gate phase. Verdict: core mechanism sound, build it — but
5 required changes. **Adopt these; they supersede the Mechanism/Complication/Validation sections
above where they conflict.**

1. **Option C (replaces A and B): two credentials, app-driven, gate provides topology only.**
   The dual-connection model is the APPLICATION's property (every real RLS multi-tenant app has a
   privileged bootstrap/auth path + a restricted tenant-scoped path), NOT a gate workaround. The
   gate exposes BOTH connection strings and the app/spec wires to them:
   - `DATABASE_URL_PRIVILEGED` — superuser/owner: SETUP (migrate, create role/policy, FORCE RLS)
     + the bootstrap/auth-resolution path the spec's carve-out names.
   - `DATABASE_URL` (restricted `NOSUPERUSER NOBYPASSRLS`) — the app's tenant-scoped path; what
     scenarios assert through.
   The gate decides NOTHING about which query uses which — it provides the topology, the spec/impl
   wire to it. **Reject Option B outright** (per-row grants to the restricted role contaminate the
   very instrument measuring isolation — a granted hole is indistinguishable from an RLS bug at
   assertion time). **Reject A's SECURITY DEFINER helper** (gate authoring app logic + per-stack
   coupling = the bespoke-tooling treadmill). A's "second privileged credential" sub-bullet WAS the
   right piece — it's now the whole design (Option C).
2. **Spec-DECLARED topology (surface the need, don't mask it).** The two-credential model must be
   DECLARED by the spec via a substrate primitive (`[[connection]]` or an extension of the
   `tenant_isolation`/`rls_policy` rules in substrate-rules.toml), with spec-validation firing when
   a tenant-isolation spec doesn't declare its connection model. Do NOT have the gate hand-supply a
   bootstrap bypass from framework internals — that masks the gap. A spec needing a privileged
   bootstrap path that didn't declare one = a spec-validation finding, not a gate-side fixup.
   (This is a CO-REQUISITE, not a follow-up.)
3. **d2fecbf guard on failure feedback.** Severity escalation IS the canonical path to audience
   escalation. The restricted-role RED scenario's failure feedback MUST stay a bare behavioral
   assertion failure (`expected 1 row, got 0`) — NEVER an RLS-diagnosis string ("RLS bypass
   detected / your impl ignores tenant scoping"), which would leak barrier-side reasoning to the
   Implementer. Add this constraint explicitly to the implementation.
4. **Differential validation → 2×2+ (not just 2 cells):** (a) known-good + restricted → PASS;
   (b) RLS-bypass + restricted → FAIL; (c) **legitimate bootstrap/auth-resolution stays GREEN**
   (the carve-out half — the exact thing that made this hard, must be tested); (d) **credential-
   misrouting impl FAILS** (tenant-scoped query routed through the privileged credential to dodge
   isolation). Make the bypass REALISTIC (drop `SET LOCAL app.tenant_id` / omit the tenant
   predicate under FORCE RLS), not a contrived `SET ROLE postgres`.
5. **DROP the C2-2 `verify_substrate` retirement claim** (over-stated). Structural (`force_check`:
   policy/FORCE actually exists in the catalog) and behavioral (scenarios observe isolation) are
   COMPLEMENTARY, not redundant — scenarios aren't exhaustive over the schema (a missing policy on
   a 2nd table could pass behaviorally). Keep BOTH. Adapter-elimination is a separate decision; do
   not entangle. Also: adding fields to substrate-rules.toml is a minor-version bump per
   ADAPTER-GOVERNANCE.md — note the governance clock.

## Status for the morning
Design drafted + **Neo-reviewed (APPROVE-WITH-CHANGES, all 5 folded in above → Option C)**. NOT
implemented/landed: needs (a) the spec-side `[[connection]]` substrate primitive + spec-validation
(co-requisite #2), (b) Linus code review, (c) a real Docker gate-run 2×2+ differential validation,
(d) opt-in/default-OFF. spec-5's run7 does NOT depend on this (the spec carve-out makes the impl
compliant; #5 is future-hardening). RECOMMEND: schedule the build + Docker validation as a focused
(non-overnight, human-in-loop) task — it's substantial, authorization-sensitive, and the gate-
execution change genuinely warrants Docker-validated human review before activation.
