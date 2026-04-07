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
- **Scenario intents:** After writing Acceptance Criteria, generate Scenario Intents. For each FR, propose 5-15 intents covering happy path, error cases, edge cases, and security. Present to user for review. Ask: "What else would you check to be sure FR-N works?" Each intent is a one-line testable description with a layer tag. When the project has `test_server_cmd` configured, these intents are compiled into HTTP behavioral scenarios (Format A) — so write them as observable HTTP behaviors (e.g., "returns 401 with error code X") not source-level assertions (e.g., "file contains pattern Y").

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
- No weasel words?
- All types defined?
- All edge cases enumerated?
- All errors handled?
- At least 3 examples (happy path, error, edge case)?
- FR count ≤ 5? (flag if exceeded)
- Each AC tests exactly one behavior with an exact expected value?
- Scenario Intent section present with 5-15 intents per FR?
- Every shape token in FR/AC/Interface Contract/Error Handling has a corresponding Wire Contract?
- Metadata `depends-on` preserved from stub and consistent with spec content?
- Metadata `spec_type` present and one of `bootstrap` / `feature` / `bugfix` / `refactor`?
- If `spec_type: bugfix` — `## Reproduction` section present with steps + observed symptom? (HARD STOP if missing)
- If `spec_type: bootstrap` — FR count ≤ 5 and the three baseline FRs (server boots / health 200 / env validation) present?

## Precision Analysis Awareness
The spec will be subjected to a Precision Analysis (Implementation Divergence Test) during validation, where the validator sketches 3 maximally-different implementations. If those sketches diverge on specified behavior (HIGH divergence), the spec will be blocked. Write the spec precisely enough that two independent readers would build the same externally observable behavior.

## Output
One or more completed specification files ready for `dark-factory validate`.
