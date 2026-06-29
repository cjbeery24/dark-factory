---
name: df-write-spec
description: Write a new Dark Factory specification
argument-hint: "[feature-name | --all]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. For full information barrier compliance, use `dark-factory run <spec>`.

# Write Specification

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Specs dir exists:** `.dark-factory/specs/` exists (create if needed)
2. **Templates accessible:** `.dark-factory/templates/` exists and contains template files. If missing, tell the user to run `dark-factory init --update` to restore framework files.
3. **Config present (warn only):** `.dark-factory/config.toml` exists

## Mode Detection

**Check the argument provided:**

- **`--all` or no argument:** Scan `.dark-factory/specs/` for all specs that need work — see [Batch Mode](#batch-mode) below.
- **Specific feature name:** Write or complete a single spec — see [Single Spec Mode](#single-spec-mode) below.

---

## Batch Mode

Triggered by `--all` or no argument.

### Step 1: Scan and triage

Read every file in `.dark-factory/specs/`. For each, determine its status:
- **STUB** — contains `Status: STUB` in the header
- **INCOMPLETE** — missing required sections or has unfilled placeholders (e.g., `[TODO]`, `[PLACEHOLDER]`, empty sections)
- **VALIDATION FAILED** — has a `.dark-factory/reviews/[spec-name]-validation.status` with `verdict` not `PASS`, `score` < 100, or `divergence=HIGH`; read the report to extract specific failures
- **UNVALIDATED** — complete but no validation status file at all
- **READY** — validated and passing (`verdict=PASS`, `score=100`, `divergence` not `HIGH`); skip these

If a plan file exists in `.dark-factory/plans/`, read it to extract the build-phase order and feature IDs (F-1, F-2, ...). Use this to sort the work queue in dependency order.

### Step 2: Present the queue

Show the user a numbered list:
```
Specs needing work (in build-phase order):
  1. [STUB]              2026-03-24-user-auth.md         (F-1)
  2. [STUB]              2026-03-24-transaction-api.md   (F-2, depends on F-1)
  3. [VALIDATION FAILED] 2026-03-24-dashboard.md         (F-3) — score 72/100, 3 issues
  4. [INCOMPLETE]        2026-03-24-reporting.md         (F-4)

4 specs to complete. Ready to start with F-1 (user-auth)?
```

If no specs need work, tell the user everything is ready and suggest `dark-factory plan <plan-name>`.

### Step 3: Work through the queue

For each spec in order:

1. Tell the user: **"Starting spec N of M: [spec-name] ([status])"**
2. Read the current spec file (stub or partial)
3. Read the appropriate template for this spec type
4. Complete the spec using the [Interview Process](#interview-process) below
5. Write the completed spec
6. Run mental validation (completeness checklist + Precision Analysis awareness)
7. Report: **"✓ Spec [name] complete. [N-1] remaining."**
8. Ask: **"Ready to continue to the next spec, or do you want to stop here?"**
   - If the user wants to stop: summarize what's done and what remains, tell them to re-run `/df-write-spec --all` to continue
   - If ready: proceed to next spec

---

## Single Spec Mode

Triggered when a specific feature name is provided.

### Steps

1. Check for an existing validation report at `.dark-factory/reviews/[spec-name]-validation.md` and `.dark-factory/reviews/[spec-name]-validation.status`.
   - **If a failing report exists** (`verdict` is not `PASS`, or `score` < 100, or `divergence` is `HIGH`): read the report, extract the specific failures and suggestions, and use those to focus the interview — only ask about the failing sections. Tell the user upfront: "This spec has a prior validation report. I'll focus on the [N] issues it identified."
   - **If no report exists or it passed:** treat as a new or complete spec — read the spec file if it exists, determine the spec type, and proceed with a full or targeted interview.
2. Read the appropriate template.
3. Complete the spec using the [Interview Process](#interview-process) below, focused on identified gaps.
4. Write the updated spec to its existing path (or `.dark-factory/specs/[YYYY-MM-DD]-[name].md` if new).
5. Run mental validation.
6. If gaps remain, point them out and help the user fill them.
7. Suggest: `dark-factory validate <spec-name>` as the next step.

---

## Spec Type (REQUIRED — ask this second, right after the spec name)

Every spec **must** declare a `spec_type` in its `dark-factory-metadata` block.
The pipeline hard-fails on specs missing this field. Allowed values and their
implications:

- **`bootstrap`** — the spec exists to get a project's runtime/server/environment
  standing up for the first time. Smoke gate is advisory on iter 1, fatal from
  iter 2.
  **You MUST pre-fill the Functional Requirements section with exactly these
  three FRs before starting the FR interview, then ask the user only whether
  to refine wording or add at most 1-2 more:**
  1. FR-1: Server process starts without error and stays up for the boot timeout
  2. FR-2: `GET <health_path>` returns HTTP 200
  3. FR-3: Required environment variables are validated at startup; missing
     vars cause a fail-fast exit with a clear error
  If the user proposes more than 5 FRs total, hard-stop and tell them to split
  the spec — bootstrap specs are narrow by construction.
- **`bugfix`** — the spec fixes an existing regression.
  **You MUST NOT write the spec file unless a `## Reproduction` section is
  present containing (a) ordered steps to trigger the bug and (b) the
  observable symptom (status code, response body, log line, crash, etc.).
  If the section is missing, hard-stop and re-prompt the user for it before
  writing anything.** The pipeline's coverage gate requires at least one
  scenario tagged with `@regression` that reproduces the bug; the reproduction
  section is where the scenario architect draws that scenario from.
- **`feature`** — new functionality on top of an already-running environment.
  Smoke is fatal from iter 1. Standard interview flow.
- **`refactor`** — behaviour-preserving code restructure. No new FRs for user-
  visible behaviour; ACs assert preservation of existing contracts.

Ask the user the spec type *immediately after* confirming the spec name, before
beginning the interview. Do not guess from the title — always confirm.

Write the chosen value into the metadata HTML comment block at the top of the
spec, on its own line alongside the other metadata keys. Example:

```
<!-- dark-factory-metadata
feature-id: F-3
spec_type: bugfix
plan: 2026-04-01-checkout-rewrite
depends-on: [F-1, F-2]
-->
```

When working on a stub that already has `spec_type` set, preserve it verbatim
and do not re-prompt.

**Spec type drives everything else.** Ask for it as the first question after
the spec name, before any other interview content. The "Spec type → template
mapping" later in this skill is a *derivative* choice — once you know
`spec_type`, pick the closest matching template; do not ask the user a second
"what kind of spec is this?" question.

## Interview Process

For each spec (stub, incomplete, or new), conduct the interview efficiently:

- **For stubs:** read what the plan pre-filled (Problem Statement, NFRs, Constraints, Dependencies, Out of Scope) and treat those as already answered. Focus the interview on the placeholder sections: Functional Requirements, Interface Contract, Edge Cases, Error Handling, Acceptance Criteria, Examples.
- **For validation failures:** read the validation report first. Show the user the specific failures (e.g., "Edge cases section is missing boundary conditions for X", "NFR-2 uses weasel word 'fast'"). Ask only about those issues — don't re-interview passing sections.
- **For incomplete specs:** identify exactly which sections are missing or vague and ask only about those. Don't re-interview sections that are already solid.
- **For new specs:** confirm `spec_type` first (per the [Spec Type](#spec-type-required--ask-this-second-right-after-the-spec-name) section above), pick the matching template, then work through every section. Do not ask a separate "what kind of spec" question — `spec_type` already answered it.

### Metadata Preservation (critical)

When writing or updating a spec, you **must** preserve and validate the `dark-factory-metadata` HTML comment block at the top of the file:

1. **Read before writing:** Before overwriting a stub or existing spec, read its current metadata block (`feature-id`, `depends-on`, `plan`, `spec_type`). These were set by `/df-write-plan` from the project plan's dependency graph. If `spec_type` is already present, preserve it verbatim; do not re-prompt or change it. If it is absent, confirm with the user per the [Spec Type](#spec-type-required--ask-this-second-right-after-the-spec-name) section above and write it in.
2. **Never reset depends-on:** Do not replace the metadata with template defaults (`depends-on: []`). The stub's `depends-on` is authoritative — it came from the plan's build phases.
3. **Validate against spec content:** After completing the spec, cross-check `depends-on` against what the spec actually references. If the spec's Functional Requirements or Interface Contract reference infrastructure, frameworks, or APIs from other specs not listed in `depends-on`, flag it: *"This spec references [X] from F-Nb but depends-on doesn't include F-Nb. Should I add it?"*
4. **Read the plan if available:** If a plan file is referenced in the metadata (`plan: ...`), read it to verify the build-phase ordering matches `depends-on`. The plan's phase ordering is the source of truth for dependencies.

**Interview rules:**
- Ask targeted questions to extract details — don't ask open-ended "tell me about X"
- Push back on vague answers: "Can you be more specific about what 'fast' means? What latency target in milliseconds?"
- Suggest edge cases they may not have considered
- Suggest error conditions they may have missed
- **FR count check:** If the spec has more than 5 Functional Requirements, flag it: "This spec has N FRs, which exceeds the recommended maximum of 5. Broad specs cause scenario explosion in the pipeline. Should we split this into smaller specs?"
- **Atomic acceptance criteria:** Each AC must test exactly one observable behavior. If an AC tests multiple conditions, split it. Target format: `GIVEN [one precondition] WHEN [one action] THEN [one observable output with exact expected value]`. Ask: "What exact output would you check to verify this works?" For API specs, the THEN clause must describe what the HTTP response contains (status code, body fields, error codes) — never implementation details like "uses try/catch", "calls function X", or "logs a message". The pipeline tests ACs by making HTTP requests, not by reading source code.
- **Observable-shape elicitation (when an EXTERNAL contract is fixed):** After listing FRs and ACs, scan them for shape-bearing words: JSON, envelope, response, request body, log, stdout, format, field, header, status code, redacted, schema, payload. For each, ask whether the shape is an **externally-fixed contract** — a backend's success/error envelope the frontend must match, a third-party webhook payload, a log format a downstream consumer parses. If so, capture a literal example as OBSERVABLE BEHAVIOR (in Examples / Error Handling, or by reference to the spec that owns it) — two readers must not invent two different "obvious" shapes for a contract that is actually pinned elsewhere. If the shape is NOT externally fixed (it's the internal API the team is free to design), do NOT elicit it — the Architect derives that interface (signatures) from the behavior. The test: is this shape something the world outside our code already dictates, or something we get to choose? Only pin the former.
- **Testability pass (mandatory, after FRs drafted):** For each FR, scan the Processing and Side effects fields for startup side-effect anti-patterns: `process.exit`, `sys.exit`, `os.Exit`, module-scope validation that triggers exit, `.listen()` / `.bind()` at module scope, signal handler exercise (`SIGTERM`/`SIGINT`), worker/cluster spawns (`cluster.fork`, `new Worker`). Pure reads like `const DEBUG = process.env.DEBUG` are NOT anti-patterns — only reads that trigger process-terminating or port-binding side effects in the same scope. For each match, intervene with a specific rewrite suggestion:
  > "FR-N says `<quoted text>`. This is untestable in-process because `<reason>`. Recommended rewrite: `<specific alternative>`. Example: instead of 'validate env at startup and call process.exit(1)', write 'createServer() throws ConfigError on invalid env; index.ts catches ConfigError, logs to stderr, and calls process.exit(1)'. This makes createServer() testable — tests call it and assert on the thrown error. Want me to restructure FR-N this way?"

  Default to — and stop at — the in-process rewrite: restate the FR so the behavior is observable from a callable boundary (the thrown error, the returned value), which is almost always the right assertion target. Do NOT have the author choose a verification vehicle, write a Tier-1 contract, or add a `vehicle:` marker — verification mechanism is HOW the pipeline derives (the Architect picks the seam; the coverage floor suggests a vehicle only if an anchor turns out uncoverable). The author's job here is purely to make the behavior observable.
- **Observable-precision pass (after FRs drafted):** Pin only ambiguities in OBSERVABLE behavior — the interface SHAPE (function signatures, return types, sync-vs-async, import style, internal lifecycle ordering) is HOW the Architect now derives, so do NOT interrogate the author about it. Ask only about behavior a caller/test can observe that the spec leaves ambiguous:
  1. **Multiple config sources** — if an FR mentions env vars AND config files or defaults, ask: "What is the precedence order? env > file > defaults, or something else?" (the resolved value is observable).
  2. **Concurrency outcome** — if an FR says "concurrent", "thread-safe", or "parallel", ask: "What does a caller observe under contention — are requests queued, rejected, or retried?" (the outcome is observable; the enforcing mechanism is the implementer's).
- **Deliverable-scope pass (mandatory, after ACs drafted):** Every AC must assert behavior of THE PRODUCT WE BUILD — an outcome determined by code or configuration WE author — NOT the built-in behavior of a third-party dependency (a framework, library, build tool, runtime, or platform) that holds regardless of what we write. We verify that we WIRED a dependency correctly; we never verify that the dependency itself works.

  **The decisive test — apply to every drafted AC/intent:** mentally remove every third-party dependency and keep only code and configuration WE authored. Does the assertion still have a subject whose outcome our code/config determines?
  - **Yes** → it's ours. This includes a constraint/policy/schema/rule we DECLARE even though a dependency enforces it (our `UNIQUE`/FK migration → the DB rejects the duplicate; our RLS policy → the cross-tenant read is blocked; our request schema → the extra field is rejected; our auth/route rule → a bad token → 401, an anonymous `/admin` → redirect). We authored the discriminating artifact, so asserting the outcome it produces is testing OUR declaration (this is the same class the Differential-Rejection rules below tell you to write) — observe these as IN-PROCESS DATA/RESPONSE differentials against a correctly-built artifact (query the row, make the request). This carve-in does NOT cover a tool's exit-code rejection of STAGED-BAD input (`engine-strict`/lint/typecheck rejecting a planted wrong package manager / bad file): that stays a config-content assertion per the Disposition below, never a staged subprocess. Keep the data/response case as a normal AC.
  - **No** — the assertion evaporates, or its outcome is fixed by the dependency no matter what we do (a tool detecting/rejecting/rewriting bad input; a framework / runtime / build-tool's built-in default, output, or transform) → it tests that the dependency is the dependency. Do NOT write it as a product AC.

  Do NOT enumerate which dependencies do what — you already know how common tools, frameworks, build steps, and runtimes behave; apply the test from that knowledge, every time. Two rationalizations do NOT rescue a dependency-behavior AC: (1) a **"security"/"correctness" label** does not exempt it (e.g. "an injected env global is `undefined` for an unprefixed var" reads like a security check but is the build tool's prefix-filtering default, not our code); (2) **"we configure it, so we could misconfigure it"** does not make the runtime-default assertion in-scope — configuring a dependency to KEEP its safe default is not authoring the behavior. If the real value is guarding against OUR misconfiguration, the in-scope form is a **config-content assertion** (our config file contains/omits the driving value) or a scan of the **produced artifact** — not the dependency's runtime default (which is also usually redundant with the artifact check); (3) **"it's product-visible through our API / wrapper"** does not make it ours — a dependency's pass/fail routed through OUR wrapper, script, or CI still tests the dependency; trace the signal to where the deciding logic lives (a linter behind `pnpm verify` still tests the linter; a library transform we merely pass through behind our endpoint still tests the library — but an outcome WE chose, like our configured thumbnail dimensions or our env-validation, is ours).

  **Disposition for a flagged AC:**
  - **Pure dependency default, no config of ours drives it** → **DROP it.** There is nothing of ours to assert.
  - **Our config drives the behavior** (we set a value the dependency acts on) → replace the dependency-behavior assertion with a **config-content assertion** (Tier-1 `file_contains`: the produced file contains the value WE set), plus — ONLY when the FR's deliverable genuinely IS "the configured command runs" — ONE fast, bounded **run-clean smoke** (Tier-1 `command`: our own conforming repo exits 0; prefer a check-mode read over heavy install/build work — a slow clean command stalls the gate as surely as a staged-bad subprocess does). NEVER stage deliberately-bad input and watch the dependency reject it: that exercises the dependency's enforcement and spawns a stall-prone subprocess. Drop "rewrites X"-style and dependency-internal-detail claims entirely.

  Tell the user when you drop or replace an AC — name whose behavior it was really asserting and what in-scope claim (if any) replaces it.
- **Scenario Intents — do NOT author them.** The §9 Scenario Intent table (5-15 intents per FR, layer tags, the `ac_id` orphan-mapping) is DERIVED by the Test-Engineer from your ACs and edge cases — pre-enumerating the test plan is the pipeline's job, not the author's. Write complete, atomic, observable ACs (and the Differential-Rejection ACs below) and the scenarios follow. The run20 whitespace-token→401 protection that used to live in the orphan-intent gate now lives in the **Differential rejection ACs** pass below — so that pass is mandatory; it is where a behavioral-rejection behavior gets pinned, at the AC level, no §9 needed.
- **Differential rejection ACs (mandatory for the security/error/adversarial slice):** a behavioral-rejection AC asserts that *some input is rejected* (a 401/403/422, an error code, a refused insert). State it **discriminatingly**, or a test can pass for the wrong reason (run20 shipped a `.trim()` bug because "whitespace token → 401" never said the token was *otherwise valid*, so the test used a junk token that 401'd from an unrelated identity failure). Two kinds, and you must classify each rejection AC:
  1. **Input-attributable rejection** — the rejection is *caused by a property of the input* and there is a "valid-except-for-X" sibling (expired-vs-fresh JWT; whitespace-padded-vs-clean token; FK-violating-vs-valid insert; unknown-vs-seeded user). Write the AC as a **differential**: name the *otherwise-valid* baseline that MUST succeed AND the triggering deviation that MUST be rejected, on the same fixture — e.g. *"GIVEN a whitespace-padded but otherwise-valid, resolvable token WHEN a request is made THEN 401 — the server MUST NOT trim/normalize whitespace before verification (a clean token for the same user returns 200)."* The parenthetical success baseline is the **positive control**; it makes the 401 *attributable* to the whitespace, not to a junk token. Add a **"MUST NOT normalize/strip/coerce"** clause wherever a lenient reading (trim, lowercase, pad) is defensible — that clause is what kills the ambiguous fork.
  2. **Invariant rejection** — a property of the *response/log/state* with no valid sibling (no secrets in the error body; no secrets in logs; no PII in telemetry). These are NOT differential and need no positive control — they are verified by content/forbidden-pattern checks, not a behavioral pair. Do not invent a success baseline for them.
  Classify every security/error/adversarial AC as input-attributable or invariant, **and declare it with a marker in the §8 AC table** (the validator REVISEs any mapped security/error/adversarial AC that carries neither): write `disc:differential` on the AC row (and write the criterion with the explicit success baseline + the `MUST NOT normalize/strip/coerce` clause), OR `disc:invariant: <reason>` (reason required) for a content/log invariant with no valid sibling. The marker is consumed downstream to demand an *attributable* test, so an under-specified rejection cannot silently ship.
- **Blocking Pass (mandatory, before final validation):** Decide which ACs / ECs / FRs / NFRs must pass before SHIP, regardless of overall pass rate. This feeds the Non-Negotiable Acceptance Criteria section that the pipeline consults to veto SHIP_WITH_GAPS promotion. Workflow:
  1. **Walk each AC / EC row** and ask: "If this specific criterion is failing, should the pipeline ship anyway?" The default for most criteria is **no** (non-blocking) — the stable-failure SHIP_WITH_GAPS mechanism exists precisely because small, classified gaps shouldn't block a useful release. Only flag as **blocking** when the failure crosses a release-critical line:
     - **Always blocking:** tenant-isolation invariants (RLS, auth enforcement), data-integrity guarantees (no silent data loss, no orphaned writes), security invariants (no credential leakage, no privilege escalation), contract-breaking API changes (wrong status code, wrong response shape).
     - **Sometimes blocking** (discuss with user): performance SLOs when they're contractual, schema migrations that cannot be rolled back forward, external-system compatibility guarantees.
     - **Never blocking:** cosmetic UI, telemetry/logging, retry/backoff tuning, dev-ergonomics features.
  2. **Mark the `Blocking` column** (`yes` / `no`) for each AC row in the Tier 2 Acceptance Criteria table.
  3. **List every `yes` entry** under `## Non-Negotiable Acceptance Criteria` (or `## 8a. Non-Negotiable Acceptance Criteria`, depending on the template), with a one-line reason:
     ```markdown
     - **AC-4** — tenant-isolation invariant. RLS failure means cross-tenant data visible.
     - **EC-3** — null tenant_id must be rejected at middleware, not silently accepted.
     ```
  4. **If no AC is blocking**, the section must carry a `Justification:` paragraph:
     ```markdown
     Justification: this spec is a pure refactor with no runtime-exposed invariants beyond existing coverage.
     ```
     The precheck rule rejects an empty section without a justification — empty acceptance must be a deliberate choice, not a default.
  5. **Consistency check:** every `Blocking: yes` row has a matching bullet under §8a, and every bullet matches an existing AC / EC / FR / NFR defined elsewhere in the spec. The precheck rule hard-fails on unresolved IDs.
  6. **Pushback case:** if the user wants to mark every AC as blocking, push back — "if everything is blocking, SHIP_WITH_GAPS never fires, and a single flaky scenario becomes a release-blocker. Pick the criteria whose failure is genuinely unacceptable."

**Spec type → template mapping:**
- Feature → `.dark-factory/templates/feature-spec.md`
- Bug fix → `.dark-factory/templates/bug-fix-spec.md`
- API endpoint → `.dark-factory/templates/api-spec.md`
- Refactoring → `.dark-factory/templates/refactor-spec.md`
- Database migration → `.dark-factory/templates/db-migration-spec.md`
- Performance optimization → `.dark-factory/templates/performance-spec.md`
- Security fix → `.dark-factory/templates/security-fix-spec.md`
- Service integration → `.dark-factory/templates/integration-spec.md`

## Completeness Checklist (mental validation before writing)
- Every section filled in?
- **Framework-/stack-agnostic?** The spec describes only WHAT to build as observable product behavior — NO dark-factory internals. Never name pipeline agents (implementer, reviewer, architect, oracle, test engineer, QA holdout), `.dark-factory/` paths, information barriers, or blind scenarios/holdouts, and never MANDATE a specific import/module mechanism (e.g. "must use dynamic import") in a normative FR/AC/EC clause. Tooling workarounds belong in the framework, never in a product requirement. (`dark-factory validate` flags these as advisory contamination findings.)
- No weasel words?
- All types defined?
- All edge cases enumerated?
- All errors handled?
- At least 3 examples (happy path, error, edge case)?
- FR count ≤ 5? (flag if exceeded)
- Each AC tests exactly one behavior with an exact expected value?
- Every `security`/`error`/`adversarial` rejection AC is classified input-attributable (→ written as a **differential**: an otherwise-valid baseline that MUST succeed + the triggering deviation that MUST be rejected, on the same fixture, with a `MUST NOT normalize/strip/coerce` clause where a lenient reading is defensible) OR invariant (→ content/forbidden-pattern check, no positive control)? No bare "input X → 401" without the success baseline that makes the rejection attributable?
- Each FR/AC passes the Deliverable-scope test — none asserts a third-party dependency's built-in behavior (a tool detecting/rejecting/rewriting bad input, or a framework/runtime/build-tool's default/output/transform); every AC's outcome is determined by code or config WE author. Apply the disambiguator to every AC, including ones wearing a "security"/"correctness" label.
- An externally-FIXED contract shape (a backend envelope, a webhook payload, a downstream-parsed log) is pinned by example as observable behavior — but an INTERNAL API shape is NOT authored (the Architect derives it).
- Metadata `depends-on` preserved from stub and consistent with spec content?
- Metadata `spec_type` present and one of `bootstrap` / `feature` / `bugfix` / `refactor`?
- If `spec_type: bugfix` — `## Reproduction` section present with steps + observed symptom? (HARD STOP if missing)
- If `spec_type: bootstrap` — FR count ≤ 5 and the three baseline FRs (server boots / health 200 / env validation) present?
- Startup side-effect anti-patterns rewritten so the behavior is observable from a callable boundary (NOT routed to an author-chosen `vehicle: subprocess` — verification is pipeline-derived)?
- Multiple config sources have explicit precedence order (the resolved value is observable)?
- `## Non-Negotiable Acceptance Criteria` section present, and either (a) every listed ID matches an AC / EC / FR / NFR row defined elsewhere, or (b) the section carries a `Justification:` paragraph?
- `Blocking` column filled (`yes` / `no`) on every AC row, and every `yes` is cross-listed under the Non-Negotiable section?
- NOT authored (pipeline-derived): §5 Interface Contract / signatures, §9 Scenario Intent, Tier-1 contracts, Vehicle annotations — their absence is correct, not a gap.

## Precision Analysis Awareness
The spec will be subjected to a Precision Analysis (Implementation Divergence Test) during validation, where the validator sketches 3 maximally-different implementations. If those sketches diverge on specified behavior (HIGH divergence), the spec will be blocked. Write the spec precisely enough that two independent readers would build the same externally observable behavior.

## Output
One or more completed specification files ready for `dark-factory validate`.
