# Feature Specification: [Feature Name]

<!-- dark-factory-metadata
feature-id: F-X
depends-on: []
plan:
profile:
-->

**Author:** [name]
**Date:** [YYYY-MM-DD]
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Problem Statement

**What problem exists today?**
[Describe the current pain point in concrete terms]

**Who is affected?**
[Users, systems, or processes that experience this problem]

**What is the impact?**
[Quantify: revenue lost, time wasted, errors per day, user complaints, etc.]

**What does success look like?**
[Describe the world after this feature is implemented]

---

## 2. Functional Requirements

<!-- Target 3-5 FRs per spec. If you need more, split into multiple specs with dependency chains. -->

### FR-1: [Verb + Behavior]
- **Input:** [Exact format with types]
- **Processing:** [Step-by-step logic]
- **Output:** [Exact format with types]
- **Side effects:** [State changes, events, logs, or "none"]

### FR-2: [Verb + Behavior]
- **Input:** [...]
- **Processing:** [...]
- **Output:** [...]
- **Side effects:** [...]

[Add more as needed]

---

## 3. Non-Functional Requirements

### Performance
- [Specific metric with target, e.g., "p95 response time < 200ms for payloads < 1MB"]

### Security
- [Specific controls, e.g., "All user inputs must be parameterized in database queries"]

### Scalability
- [Specific target, e.g., "Must handle 500 concurrent users without degradation"]

### Accessibility
- [Specific standard, e.g., "WCAG 2.1 AA compliance for all new UI elements"]

### Observability
- [Logging/metrics requirements, e.g., "Log all errors with request ID, emit latency histogram"]

---

## 4. Constraints

### Technology
- **Language/Runtime:** [e.g., "TypeScript 5.x, Node.js 20+"]
- **Framework:** [e.g., "Use existing Express.js server in src/server.ts"]
- **Dependencies:** [e.g., "Use zod for validation (already in package.json)"]

### Patterns
- [e.g., "Follow repository pattern in src/repositories/"]
- [e.g., "Use existing error handling middleware pattern"]

### Integration Points
- [e.g., "Must integrate with existing auth middleware at src/middleware/auth.ts"]
- [e.g., "Must emit events to existing event bus at src/events/"]

### File Locations
- [e.g., "Create new files under src/features/[feature-name]/"]
- [e.g., "Add routes to src/routes/index.ts"]

---

## 4a. Negative Space, Invariants, and Lifecycle

> **Optional but recommended.** Spec-side declarations of (a) what
> commands/test patterns MUST NOT appear, (b) positive-form contracts
> the architect's manifest MUST satisfy, (c) ordered procedural
> sequences the implementer MUST honor. Each block is YAML embedded in
> a fenced ```yaml block; the precheck and validator parse them and
> cross-check downstream artifacts (architect manifest, scenarios,
> holdouts).
>
> Delete sub-sections that don't apply. An empty §4a is acceptable
> for trivial specs; the validator will not block on missing content.

### forbidden_commands

> Patterns that MUST NOT appear in commands.setup / commands.install /
> any architect-emitted runtime command. The validator scans the
> architect manifest's `runtime.commands.*` against these patterns
> and blocks on match.

```yaml
forbidden_commands:
  - pattern: '\bdrizzle-kit\s+push\b'
    reason: "Spec mandates CLI-driven migrations (db:migrate); push-mode auto-applies and bypasses the journal."
    applies_to: [commands.setup, commands.install]
```

### forbidden_test_shapes

> Patterns that MUST NOT appear in scenarios / holdouts. Enforced by
> the gate's scenario-lint phase pre-execution.

```yaml
forbidden_test_shapes:
  - pattern: 'connection\s*`SELECT current_setting\('
    reason: "Peer postgres() client outside withTenantDb cannot read app.tenant_id (transaction-scoped GUC)."
    applies_to: [scenarios, holdouts]
```

### ecosystem_invariants

> Positive-form contracts the architect's manifest output MUST satisfy.
> Distinct from forbidden_*: enforces what MUST be true, not what
> MUST NOT be.

```yaml
ecosystem_invariants:
  - name: "rls-via-migration"
    invariant: "Row-level security policies are declared in SQL migration files, never in runtime application code."
    enforcement: architect.amend_check
  - name: "tenant-binding-via-callback"
    invariant: "Tenant-scoped database access ONLY through withTenantDb / request.withDb callbacks."
    enforcement: scenarios.lint
```

### required_lifecycle_sequences

> Ordered procedural constraints the implementer MUST satisfy.
> Encodes "X must happen BEFORE Y" relationships.

```yaml
required_lifecycle_sequences:
  - name: "migrate-needs-journal"
    sequence: ["seed_journal", "drizzle-kit migrate"]
    rationale: "drizzle-kit migrate hangs indefinitely if meta/_journal.json is missing."
    enforcement: substrate-agent + gate-Setup-phase
```

[Delete any sub-section that doesn't apply to this spec.]

---

## 5. Interface Contract (OPTIONAL)

> **You usually do NOT write this section.** The Architect derives the full
> interface — function signatures, return/error types, the test-injection seam,
> param-object keys — from the observable behavior in your FRs/ACs, and all three
> blind test arms build against it. Describe WHAT the code does (§2/§8), not its
> signatures. Include this section ONLY to pin an **externally-fixed** contract you
> already know and must match exactly (a published API shape, a hard library
> signature). If you do, the A6 pre-check below typechecks it; if you don't, the
> Architect derives it — both are correct.
>
> **If you do pin TypeScript here:** every fenced block must be a `typescript`
> (or `ts`, `tsx`, `mts`, `cts`) fence starting with a one-line filepath comment.
> The pre-check (A6) compiles these against the project's installed `node_modules`
> — a mismatch with the library's real shape fails the spec early. One fence per
> file. Non-TS projects: any language fence; the stub round-trip is a no-op.

### Types / Data Structures

```typescript
// path/to/file.ts
[Define all exported types, interfaces, and runtime values with their types]
```

### Function Signatures / API Endpoints

```typescript
// path/to/file.ts
[Define every public function signature or API endpoint]
[Include request/response shapes for APIs]
[Include all possible status codes for APIs]
```

---

## 5b. Wire Contracts

> **Pin only EXTERNALLY-FIXED shapes** — a serialized cross-boundary structure the
> world outside our code already dictates and we must match (a backend's
> success/error envelope, a third-party webhook payload, a log line a downstream
> consumer parses). These are observable behavior: give a named, literal,
> copy-pasteable example. Do NOT pin a shape we are free to design — that internal
> contract is the Architect's to derive. The test: does something outside our code
> dictate this shape, or do we choose it? Pin only the former.

### Error Envelope (failure)
```json
{ "ok": false, "error": { "code": "STRING", "message": "STRING", "details": [] } }
```

### Request Log Line
```json
{ "level": "info", "ts": "ISO8601", "requestId": "uuid", "method": "GET", "url": "/path", "statusCode": 200, "responseTimeMs": 12 }
```

### test_server (optional — required for HTTP-tested specs)

> **When this spec exposes an endpoint that holdout / Tier 1 tests
> reach via `fetch()`,** declare the test-server contract here. The
> architect honors the declared command + base URL when emitting
> `environment.toml`; the gate exports `TEST_BASE_URL=<base_url>`
> for the test runner; the Tier 1 compiler reads the base URL from
> the manifest (no hardcoded fallback); the qa-holdout prompt
> instructs the LLM to read `process.env.TEST_BASE_URL`. Single
> source of truth for the test server lifecycle.

```yaml
test_server:
  command: "cd apps/api && pnpm start"   # how to start the test server
  base_url: "http://localhost:3000"      # where tests connect; gate exports as TEST_BASE_URL
  startup_timeout_seconds: 10            # max wait for server readiness before tests run
```

[Add additional named contracts as needed. Delete the examples above if
this spec has no serialized cross-boundary structures.]

---

## 6. Edge Cases

| ID | Condition | Expected Behavior |
|----|-----------|-------------------|
| EC-1 | [Specific condition] | [Exact expected behavior] |
| EC-2 | [Specific condition] | [Exact expected behavior] |
| EC-3 | [Specific condition] | [Exact expected behavior] |

[Add rows as needed. Common categories: empty inputs, boundary values, unicode, concurrent access, missing fields, type mismatches]

---

## 7. Error Handling

| Condition | Response/Behavior | Error Code | Logging |
|-----------|-------------------|------------|---------|
| [Error condition] | [Exact response] | [Code] | [What to log] |
| [Error condition] | [Exact response] | [Code] | [What to log] |

---

## 8. Acceptance Criteria

### Tier 1: Test Contracts (OPTIONAL — usually omit)

You usually do NOT write Tier-1 contracts: the pipeline derives how each behavior
is verified. Include one ONLY to pin a hard, already-known deterministic fact you
want guaranteed (a specific file must exist, a command must exit 0, a build artifact
must or must-not contain a value). When present, they compile to identical tests
every run. The schema (for the rare case you need it):

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: file_exists     # http | file_exists | file_contains | command | import
    given: "after implementation"
    when:
      check: "path/to/expected/file"
    then:
      exists: true
    invariant: false       # true = already verified by build/lint gate, SA skips

  - id: TC-2
    fr: FR-1
    type: file_contains
    given: "after implementation"
    when:
      file: "package.json"
      parse: json
      path: "scripts.build"
    then:
      exists: true

  - id: TC-3
    fr: FR-2
    type: command
    given: "after build"
    when:
      cmd: "node -e \"require('./dist/index.js')\""
    then:
      exit_code: 0
```

**Contract types and `when`/`then` schemas:**
- `file_exists` — `when: { check: "path" }` → `then: { exists: true }`
- `file_contains` — two modes:
  - Structured: `when: { file: "path", parse: "json"|"yaml", path: "dot.path" }` → `then: { exists: true }`
  - Pattern: `when: { file: "path", parse: "text", pattern: "regex" }` → `then: { matches: true }`
- `command` — `when: { cmd: "..." }` → `then: { exit_code: 0 }`
- `http` — `when: { method, path, body (request) }` → `then: { status, body (assertions) }`
  - `then.body` values use operators: `{ type: "string" }`, `{ equals: "value" }`, `{ contains: "sub" }`
- `import` — `when: { module: "path", symbols: ["name", ...] }` → `then: { exports: true }`

`invariant` (optional, default `false`): when `true`, the pipeline skips this contract —
it documents that the build/lint/test gate already covers the check.
`given` is freeform text used as the test description in compiled output.

### Tier 2: Adversarial Criteria (SA-generated)

These criteria guide the SA's creative adversarial testing (layers 3-8: edge
cases, error conditions, state, security, property-based, adversarial). The SA
does not re-test what Tier 1 contracts already cover.

| ID | Criterion | Blocking |
|----|-----------|----------|
| AC-1 | `disc:differential` — GIVEN a valid token AND the same token with trailing whitespace WHEN authenticating THEN the valid one succeeds and the whitespace one returns 401 (the server MUST NOT strip/normalize) | yes |
| AC-2 | GIVEN [edge/error condition] WHEN [action] THEN [observable result — what the endpoint returns, not how the code works] | no |
| AC-3 | GIVEN [edge/error condition] WHEN [action] THEN [observable result] | no |

> **Important:** THEN clauses must describe externally observable behavior (HTTP status codes, response body fields, error codes). Never reference implementation details like "uses try/catch", "calls function X", or "logs internally". The pipeline tests these criteria via HTTP requests against a running server. (No "Vehicle" column: how an AC is verified — in-process, subprocess, a command — is derived by the pipeline, not declared by you.)
>
> **Differential classification (validator-gated):** every `security` / `error` / `adversarial` rejection AC MUST be marked `disc:differential` or `disc:invariant: <reason>` (place the marker in the Criterion cell). A `disc:differential` AC names an otherwise-valid baseline that MUST succeed PLUS the triggering deviation that MUST be rejected, on the same fixture, with a `MUST NOT normalize/strip/coerce` clause where a lenient reading is defensible — so a test cannot pass for the wrong reason (this is the whitespace-token→401 protection). A `disc:invariant` AC is a content/forbidden-pattern property with no valid-input sibling — the `<reason>` is REQUIRED (the validator checks for it). The validator REVISEs a bare "input X → 401" that lacks the success baseline.
>
> **Blocking column (required):** `yes` for criteria whose failure must veto SHIP_WITH_GAPS promotion (tenant-isolation invariants, data-integrity guarantees, auth enforcement). `no` for criteria that are desired but not release-blocking. Every `yes` entry MUST be listed under §8a below.

---

## 8a. Non-Negotiable Acceptance Criteria

List the AC / EC / FR / NFR IDs whose failure must veto SHIP, regardless of
overall pass rate. The pipeline's stable-failure SHIP_WITH_GAPS promotion is
vetoed when any listed ID has a failing scenario.

Leave empty only when the spec has no release-blocking invariants — in that
case a `Justification:` paragraph is required.

- **AC-N** — [one-line reason, e.g. "tenant-isolation invariant"]
- **EC-N** — [one-line reason]

<!-- If no AC is blocking, replace the bullets with a justification paragraph:
Justification: this spec is a pure refactor/documentation/style change with no
runtime-exposed invariants beyond existing coverage. -->

---

## 9. Scenario Intent — DERIVED (do not author)

The Test-Engineer derives the per-FR test scenarios from your Acceptance Criteria
and Edge Cases — you do NOT enumerate them here (pre-writing the test plan is the
pipeline's job). The discrimination protection that used to live here is now in the
§8 AC table: mark every `security`/`error`/`adversarial` rejection AC
`disc:differential` / `disc:invariant` (see §8). Write complete, atomic, observable
ACs and the scenarios follow.

---

## 10. Examples

### Example 1: [Happy path description]
```
Input: [exact input]
Expected Output: [exact output]
```

### Example 2: [Error case description]
```
Input: [exact input]
Expected Output: [exact output/error]
```

### Example 3: [Edge case description]
```
Input: [exact input]
Expected Output: [exact output]
```

---

## 11. Dependencies

### Existing Code
- [File/module path] — [What it provides]

### External Services
- [Service name] — [What it provides, how to connect]

### Libraries
- [Library name] — [Version, purpose, already installed?]

---

## 12. Out of Scope

The following are explicitly NOT part of this specification:
- [Thing 1 — brief reason why]
- [Thing 2 — brief reason why]
- [Thing 3 — brief reason why]

---

## 13. Free Variables (implementation choices — DO NOT assert in scenarios)

List implementation decisions this spec intentionally leaves unconstrained.
The Scenario Architect will treat these as DO-NOT-ASSERT directives.

| ID | Variable | What Is Constrained | What Is Free |
|----|----------|---------------------|--------------|
| FV-1 | [e.g., Config schema structure] | [e.g., Must contain build and start commands] | [e.g., JSON key nesting (flat vs nested)] |
| FV-2 | [e.g., Package manager format] | [e.g., Must use pnpm] | [e.g., workspace config in package.json vs pnpm-workspace.yaml] |

If no implementation choices are free, write "None — all observable behaviors are fully constrained."
