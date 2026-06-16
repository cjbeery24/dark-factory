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
- **Wire Contract elicitation (mandatory):** After listing FRs and ACs, scan them for shape-bearing words: JSON, envelope, response, request body, log, stdout, format, field, header, status code, redacted, schema, payload. (This interview list is intentionally broader than the precheck's `SHAPE_TOKENS` — humans filter false positives during the interview, the precheck cannot. Do not sync the two lists.) For each occurrence ask: *"You mentioned <token> in <FR-N>. Paste a literal example of the exact shape — this becomes a wire contract that both the implementer and the scenario architect will treat as ground truth."* Capture each example into the Wire Contracts section under a descriptive heading. Do not let the user wave this off — if they say "obvious" or "standard", ask them to write it down anyway, because two LLMs will otherwise produce two different "obvious" shapes.
- **Testability pass (mandatory, after FRs drafted):** For each FR, scan the Processing and Side effects fields for startup side-effect anti-patterns: `process.exit`, `sys.exit`, `os.Exit`, module-scope validation that triggers exit, `.listen()` / `.bind()` at module scope, signal handler exercise (`SIGTERM`/`SIGINT`), worker/cluster spawns (`cluster.fork`, `new Worker`). Pure reads like `const DEBUG = process.env.DEBUG` are NOT anti-patterns — only reads that trigger process-terminating or port-binding side effects in the same scope. For each match, intervene with a specific rewrite suggestion:
  > "FR-N says `<quoted text>`. This is untestable in-process because `<reason>`. Recommended rewrite: `<specific alternative>`. Example: instead of 'validate env at startup and call process.exit(1)', write 'createServer() throws ConfigError on invalid env; index.ts catches ConfigError, logs to stderr, and calls process.exit(1)'. This makes createServer() testable — tests call it and assert on the thrown error. Want me to restructure FR-N this way?"

  Default to the in-process rewrite. Only suggest `vehicle: subprocess` if the user explicitly wants to test the exit behavior itself (rare — usually the thrown error is the right assertion target). If the user chooses subprocess testing, guide them to add a Tier 1 contract block in the Acceptance Criteria section:
  > "Since AC-N needs subprocess testing, let's add a Tier 1 contract for it. This goes in the contracts YAML block in your Acceptance Criteria section:"
  > ```yaml
  > - id: TC-N
  >   fr: FR-M
  >   type: command
  >   given: "after build"
  >   when:
  >     cmd: "PORT=abc node apps/api/dist/index.js"
  >   then:
  >     exit_code: 1
  > ```
  > "Also add `subprocess` in the Vehicle column for AC-N."
- **Precision gap pass (mandatory, after Wire Contracts):** For each FR, scan for:
  1. **Factory functions** (`createX()`, `buildX()`, `initX()`, `makeX()`, `configureX()`) — ask: "What exact type does `createX()` return? Is it the raw instance (e.g., `FastifyInstance`, `http.Server`) or a wrapper object? Write the return type explicitly."
  2. **Error throwing without sync/async** — if an FR says "throws X" or "raises X", ask: "Is this function synchronous or asynchronous? If sync, it 'throws XError'. If async, it 'rejects with XError'. The test assertion is different — which one?"
  3. **Plugin/middleware registration** — if an FR mentions registering plugins or middleware, ask: "How is this plugin imported? Static import at module top or dynamic `await import()` inside the function? What is the registration order relative to other plugins?"
  4. **Multiple config sources** — if an FR mentions env vars AND config files or defaults, ask: "What is the precedence order? env > file > defaults, or something else?"
  5. **Lifecycle hooks** — if an FR describes shutdown, startup, or cleanup behavior, ask: "What is the exact sequence? List the steps in order: 1. ..., 2. ..., 3. ..."
  6. **Concurrency requirements** — if an FR says "concurrent", "thread-safe", or "parallel", ask: "What happens under contention? Are requests queued, rejected, or retried? What mechanism enforces this?"
- **Deliverable-scope pass (mandatory, after Precision Gap, before Scenario intents):** Before finalizing ACs or generating intents for an FR, classify what the FR's observable behavior actually exercises: **our deliverable** (the artifacts WE produce — source, config files, manifests, workflow files — and those artifacts behaving correctly on the repo WE built) versus **a third-party tool's built-in behavior** (a lint/format/test/build/install/package-manager/CI tool DETECTING, REJECTING, or TRANSFORMING input). We test that we **configured/wired the tool correctly**; we do NOT test that the tool itself works — we trust ESLint to catch lint errors, npm to enforce engines, GitHub Actions to run steps. Apply the **third-party-behavior heuristic** below to every drafted AC and every candidate intent.

  For each AC/intent that the heuristic flags as testing third-party behavior, do NOT write it as a behavioral scenario. **Replace it** with the deterministic in-scope pair:
  1. A **config-content assertion** — the produced file exists and contains the exact value that drives the behavior (e.g., `biome.json` has `indentWidth: 2`; `package.json` has `scripts.lint` and `engines.pnpm`; `.github/workflows/ci.yml` has the Node-22 setup step and the `pnpm typecheck` step; `.npmrc` has `engine-strict=true`). Express this as a **Tier-1 `file_contains` contract** (structured `parse: json`/`yaml` + `path:` for config keys; `parse: text` + `pattern:` for non-parseable files), NOT a Tier-2 intent.
  2. A **run-clean smoke** — the configured command exits 0 on the repo WE built (the repo is correctly formatted, lint-clean, type-clean by construction). Express this as a **Tier-1 `command` contract** (`when: { cmd: "pnpm lint" }` → `then: { exit_code: 0 }`), NOT a subprocess intent that stages bad input. **Keep the smoke command FAST and bounded:** prefer a check-mode command that reads the repo (`pnpm lint`, `pnpm format:check`, `pnpm typecheck`) over one that does heavy work (`pnpm install`, `pnpm build`, `tsc -b` over a cold monorepo) — a slow clean command stalls the gate just as a staged-bad subprocess does. One fast command per tool; do NOT add an install/build smoke unless the FR's deliverable IS the install/build itself.

  Tell the user explicitly when you do this: *"AC-N as written ('GIVEN a lint violation WHEN pnpm lint runs THEN it exits non-zero') tests that ESLint detects violations — that's ESLint's job, and verifying it would force the pipeline to stage a deliberately-broken file and spawn a subprocess blind test (a known pipeline-stall source). What's actually OUR deliverable is that we configured lint correctly. I'm replacing AC-N with: (1) `eslint.config.js` exists and contains `rules['no-unused-vars']` set to `error` [file_contains], and (2) `pnpm lint` exits 0 on our own clean repo [command]. Both are deterministic and need no staged fixtures. OK?"*

  **Rewrite-observation ACs are DROPPED, not re-admitted.** An AC of the form "`pnpm format` REWRITES unformatted code to the right style" cannot be a run-clean smoke (a clean repo has nothing to rewrite), and you must NOT re-admit it as a `command` contract with a `then.file` side-effect that stages an unformatted fixture — that re-introduces the staged-bad input and the subprocess stall. We trust the formatter to transform correctly; the in-scope claim is the config-content assertion (`biome.json` configures the formatter rules) + the run-clean smoke (`pnpm format:check` exits 0 on our formatted repo). Drop the "rewrites" assertion entirely.

  **Config-driven rejection — assert the config, NEVER the rejection.** Some "tool rejects bad input" behaviors are driven by **a config value WE set** (`npm install` fails on a wrong package manager *because* we set `engine-strict=true` + `engines.pnpm`; CI blocks a merge *because* our workflow has a typecheck step). Because the rejection is DRIVEN BY a config value you can READ, the **config-content assertion is ALWAYS sufficient and is the required in-scope test** — assert that the config file contains the driving value (`file_contains`: `package.json` has `engine-strict`/`engines.pnpm`; `ci.yml` has the typecheck step). Do NOT write a `command` contract that stages the wrong tool / bad input and watches the rejection fire: that exercises the third-party tool's enforcement, not our config, and IS the staged-bad-input subprocess test (and stall) this pass exists to prevent. There is no case where a config-DRIVEN rejection needs a subprocess rejection test — the config value is the deliverable and the config-content assertion is its complete in-scope proof.

  **Third-party-behavior heuristic** — flag an AC/intent as *testing a third-party tool, not our config* when its observable (the THEN) **holds ONLY because a tool's BUILT-IN CHECK acted on non-conforming input** — i.e. the test passes only because a linter / formatter / type-checker / test-runner / bundler / package-manager / CI runner flagged, rejected, or rewrote something about deliberately-imperfect input (exits non-zero on a lint violation / type error / engine mismatch / stale lock; rewrites unformatted code). The disqualifying property is **WHAT BEHAVIOR IS ASSERTED, not how the test is wired** — so the following do NOT make it OK:
  - It does NOT matter whether the bad input is STAGED per-run or COMMITTED as a fixture (a checked-in `fixtures/bad.ts`, a committed stale lockfile, a wrong package manager). Either way the assertion exercises the tool's detection logic, needs an imperfect artifact to mean anything, and spawns the subprocess.
  - It does NOT matter whether the tool is invoked DIRECTLY (`pnpm lint`) or LAUNDERED through a wrapper / script / CI job WE wrote (`node scripts/check.js`, `pnpm verify`, a CI workflow step). Look THROUGH the wrapper to where the pass/fail signal actually originates: if it comes from a third-party tool's detect/reject/rewrite logic, it tests the tool, no matter what command name sits on top.

  When this holds → replace per the rewrite above: config-content assertion + run-clean smoke; **drop rewrite-observation entirely**.

  **Decisive disambiguator (apply to every candidate):** *mentally remove the third-party tool and keep only code/config WE authored — does the assertion still have a subject?* If yes (an API handler still returns 401, a function still throws, a config file still contains the value) → it's OURS, do NOT flag. If the assertion EVAPORATES because it was really about the tool flagging bad input → flag it.

  **Do NOT flag (these ARE our deliverable):**
  - The configured command runs **clean on our own conforming repo** (`pnpm lint`/`format:check`/`typecheck` exit 0) — no non-conforming input, nothing being detected/rejected; this is the run-clean smoke itself.
  - A produced file **contains the right content** (`biome.json` has `indentWidth: 2`).
  - **Our own application code's runtime behavior**, even when it's a "rejection": an API endpoint returning 401 on a bad token, a handler returning 422 on a malformed body, a function throwing on invalid args, or a codemod/codegen tool WE authored transforming input. Here the deciding logic is CODE WE WROTE, not a third-party tool's built-in check (it survives the disambiguator) — so it stays a normal behavioral AC/scenario.
  - A tool rejecting input **because of a config value WE set**, tested via the config-content assertion (per "Config-driven rejection" above).
- **Scenario intents:** After writing Acceptance Criteria, generate Scenario Intents. For each FR, propose 5-15 intents covering happy path, error cases, edge cases, and security. Present to user for review. Ask: "What else would you check to be sure FR-N works?" Each intent is a one-line testable description with a layer tag. When the project has `test_server_cmd` configured, these intents are compiled into HTTP behavioral scenarios (Format A) — so write them as observable HTTP behaviors (e.g., "returns 401 with error code X") not source-level assertions (e.g., "file contains pattern Y"). **Carve-out for config/tooling/infra FRs:** an FR whose deliverable is configuration (lint/format/CI/build/package-manager setup) may legitimately yield FEWER than 5 intents — often just Tier-1 `file_contains` + `command` contracts and 0-2 Tier-2 intents — because, after the Deliverable-scope pass, its in-scope surface is "files have correct content" + "configured commands run clean," with no in-scope adversarial-input surface. Do NOT pad such an FR back up to 5-15 by re-adding staged-bad-input intents the Deliverable-scope pass just removed. The 5-15 target applies to FRs that own application behavior, not to FRs that own configuration. **Floor:** a config FR is exempt from the 5-intent minimum but MUST still carry at least one Tier-1 contract (`file_contains` or `command`) — a config FR with no Tier-1 contract is unverified and is a defect, not a valid carve-out (zero contracts and zero intents is the degenerate case).
- **Blocking Pass (mandatory, after Precision Gap, before final validation):** Decide which ACs / ECs / FRs / NFRs must pass before SHIP, regardless of overall pass rate. This feeds the Non-Negotiable Acceptance Criteria section that the pipeline consults to veto SHIP_WITH_GAPS promotion. Workflow:
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
- Scenario Intent section present with 5-15 intents per FR (config/tooling FRs exempt — may have fewer, see Deliverable-scope carve-out)?
- Each FR/AC/intent classified by the Deliverable-scope pass — none assert that a third-party tool (lint/format/typecheck/build/install/package-manager/CI) DETECTS/REJECTS/REWRITES deliberately-staged bad input?
- For config/tooling/infra FRs: in-scope behavior expressed as Tier-1 `file_contains` (config content) + `command` (run-clean smoke, exit 0 on our own repo) contracts, not Tier-2 staged-bad-input intents — and every config FR carries at least one Tier-1 contract (carve-out floor)?
- Run-clean `command` smokes are FAST check-mode commands (lint/format:check/typecheck), not heavy install/build/`tsc -b` over a cold monorepo?
- Every shape token in FR/AC/Interface Contract/Error Handling has a corresponding Wire Contract?
- Metadata `depends-on` preserved from stub and consistent with spec content?
- Metadata `spec_type` present and one of `bootstrap` / `feature` / `bugfix` / `refactor`?
- If `spec_type: bugfix` — `## Reproduction` section present with steps + observed symptom? (HARD STOP if missing)
- If `spec_type: bootstrap` — FR count ≤ 5 and the three baseline FRs (server boots / health 200 / env validation) present?
- No startup side-effect anti-patterns in FRs without testability rewrite or `vehicle: subprocess` annotation?
- Every factory/constructor function has an explicit return type?
- Every "throws"/"raises" specifies sync or async?
- Plugin/middleware registration specifies import style (static/dynamic) and order?
- Multiple config sources have explicit precedence order?
- Lifecycle sequences are numbered steps, not prose?
- `## Non-Negotiable Acceptance Criteria` section present, and either (a) every listed ID matches an AC / EC / FR / NFR row defined elsewhere, or (b) the section carries a `Justification:` paragraph?
- `Blocking` column filled (`yes` / `no`) on every AC row, and every `yes` is cross-listed under the Non-Negotiable section?

## Precision Analysis Awareness
The spec will be subjected to a Precision Analysis (Implementation Divergence Test) during validation, where the validator sketches 3 maximally-different implementations. If those sketches diverge on specified behavior (HIGH divergence), the spec will be blocked. Write the spec precisely enough that two independent readers would build the same externally observable behavior.

## Output
One or more completed specification files ready for `dark-factory validate`.
