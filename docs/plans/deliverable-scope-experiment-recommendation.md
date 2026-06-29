# WHAT-only spec experiment — findings & recommendation (supersedes the Phase-2 plan)

> Status: COMPLETE. Authoring + validator fixes merged to main (`6ee722f`, `58eb9b7`).
> Phase 2 (#48) CLOSED — do not build. Genuine residual elevated to #32.

## The question (Caleb)
"If a coding LLM can figure out the vehicle for an AC — even give the feedback to fix
the spec — why does the spec carry the vehicle at all? Is the whole vehicle machinery
(Phase 0/1, and the planned Phase-2 redesign) solving a problem that bad spec authoring
created?"

## Method
1. Audited the shipped budget-brain-v2 specs. The floor-uncoverable / vehicle-needing
   anchors concentrate almost entirely in ONE spec — `frontend-bootstrap` (the only
   frontend scaffold). The backend/data/auth specs are clean product behavior; the one
   other tooling spec (`monorepo-tooling`) was already handled by the existing
   input-rejection guard.
2. Diagnosed `frontend-bootstrap`'s defects: AC-1 (Vite dev server starts), AC-9
   (Tailwind computes CSS), AC-11 (HMR) test a *dependency's behavior*, not our product;
   FR-1/FR-4 were framed as setup tasks ("Create the scaffold"); and the spec was
   mis-typed `bootstrap` (a server-centric type whose mandated FRs — server boots /
   health 200 / env-validate — forced "the dev server starts" onto a frontend).
3. Re-authored a clean WHAT-only spec (`frontend-bootstrap-clean`, `spec_type: feature`)
   applying a deliverable-scope discipline; dropped the dependency-behavior ACs.
4. Ran it: standalone validate, then the full pipeline to the coverage floor.

## Findings
- **Clean spec: precheck PASS, LLM validator PASS (95/100). Manifest: ZERO vehicle
  markers** beyond the one residual. 8 of 11 ACs (routing + API-client behavior) covered
  cleanly — **no vehicle machinery engaged at all.**
- The floor aborted only on the env-var-safety FR (FR-4/AC-10/AC-11). On scrutiny these
  are NOT a vehicle need so much as more defects + one narrow gap:
  - **AC-10** ("`import.meta.env.SECRET_VALUE` is `undefined`") — Caleb's catch: this is
    ALSO a dependency-behavior defect. A non-`VITE_` var is `undefined` *because Vite
    filters by prefix*, not because of our code. Drop it (the in-scope sliver — "our
    module consumes `VITE_API_URL`" — is an ordinary covered test through a project
    module).
  - **AC-11** ("no secret in the produced bundle") — the genuine residual. A build-artifact
    content invariant. Handled today by `vehicle: subprocess` + a behavioral Tier-1
    command contract; handled cleanly by #32 (let a Tier-1 contract credit the floor).
- **Principle beats examples (the decisive measurement).** A deliverable-scope check with
  ZERO enumerated dependencies — just the disambiguator ("remove the dependency; does the
  assertion still have a subject whose outcome our code determines?") — scored 11/11 at
  K=3 on the refined corpus: caught AC-10/dev-server/HMR/Tailwind/eslint-wrapper as
  OUT, kept RLS/FK/JWT/Zod declarative ACs + genuine product ACs as IN. Enumerating
  gotchas (lint/HMR/bundler/CSS-framework) is the adapter treadmill; the principle + the
  LLM's general knowledge does the whole job.

## The bounded loophole set (closed — NOT a treadmill)
Three rationalization loopholes the check explicitly closes:
1. A "security"/"correctness" label does NOT exempt a dependency-behavior AC.
2. Configuring a dependency's default isn't authoring it.
3. Wrapper-laundering — trace a pass/fail through our wrapper/script/CI to where the
   deciding logic lives (a linter behind `pnpm verify`, a library transform behind our
   endpoint, still tests the dependency).

Counter-balanced by the **declarative carve-in** (prevents over-fire): a constraint the
team DECLARES even though a dependency ENFORCES it (UNIQUE/FK migration, RLS policy, Zod
schema, JWT/route rule) is IN-SCOPE as an in-process data/response differential.
Discriminator: AUTHORED a discriminating artifact (→in) vs LEFT A DEPENDENCY'S DEFAULT
ALONE (→out). Engine-strict seam: the carve-in is scoped to in-process data/response
differentials, NOT a tool's exit-code rejection of staged-bad input (that would
re-introduce the subprocess stall). Testability↔deliverable-scope precedence: a
DEPENDENCY's startup/runtime (dev server, HMR, bundler) → DROP, not `vehicle: subprocess`;
the team's OWN process booting → `vehicle: subprocess`.

## Fixes shipped (on main)
1. `/df-write-spec` deliverable-scope pass: replaced ~30 lines of enumerated gotchas with
   the principle + disambiguator + the three closed loopholes + the declarative carve-in.
2. `agents/spec-validator.md` **Deliverable-Scope Check** (Critical Issue → REVISE): the
   validator previously had NO such check — it passed AC-10 at 95/100 while *noting* "this
   is determined by Vite, not the app" and scoring that as a divergence positive. Now that
   observation is a flag. This is the backstop that catches dependency-behavior ACs at
   validation regardless of authoring.

## Capstone
The fixes were re-applied to the ORIGINAL bb-plan spec (`2026-03-28-frontend-bootstrap`,
the one in the canonical plan, not just the throwaway `-clean`): dropped the
dependency-behavior ACs, reclassified `bootstrap → feature`, routed AC-11 to
`disc:invariant` + a behavioral Tier-1 contract. Standalone validate: **PASS 95/100,
0 blockers, consistency PASS.** Re-run through the updated pipeline to confirm clean
coverage end-to-end.

## Recommendation
**Do not build Phase 2 (the vehicles-leave-the-spec classifier/auto-draft/ledger
redesign). Close #48.** Its premise — "the vehicle burden is real and must be shifted" —
is mostly false: the burden was spec defects.
- **Shipped** the two authoring/validation fixes (Neo-designed, Linus/Codex-gated). They
  delete the defect class at the source and add the missing validator backstop.
- **Keep** Phase 0 + Phase 1 (already on main). Inert on clean specs (zero markers → gate
  inert, classifier never fires), so cheap insurance — but their PRIMARY justification
  (many vehicle-needing anchors) is gone.
- **The one genuine residual** is the AC-11-class build-artifact invariant → **#32** (let
  a Tier-1 `command`/`file_contains` contract count toward the floor for its `covers:`
  anchors). That, not Phase 2, is the real framework gap.

## Honest caveats
One project, one re-authored spec (twice — `-clean` and the original). The *pattern*
(frontend scaffolds are the prone class; dependency-behavior-as-requirement is the defect)
is well-evidenced and the principle measurement is robust (11/11), but it is not proven
across many specs/stacks. The fixes are low-risk regardless (a stricter
authoring/validation discipline + a backstop). The high-value claim ("don't build
Phase 2") rests on this evidence plus the Q1-circularity finding from Neo's Phase-2 review
(the subprocess auto-draft's mutation arm is circular).
