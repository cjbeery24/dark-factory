# API Specification: [Endpoint/Resource Name]

<!-- dark-factory-metadata
feature-id: F-X
depends-on: []
plan:
profile:
-->

**Author:** [name]
**Date:** [YYYY-MM-DD]
**API Version:** [e.g., v1]
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Problem Statement

**What problem does this API solve?**
[Describe the need this endpoint addresses]

**Who consumes this API?**
[Frontend app, mobile app, third-party service, internal microservice]

**What exists today?**
[Nothing / existing endpoint that needs replacement / workaround being used]

---

## 2. Endpoint Definition

### [METHOD] [/path/to/resource]

**Authentication:** [Required (Bearer token) / API key / None]
**Authorization:** [Roles/permissions required, e.g., "admin" or "owner of resource"]
**Rate Limit:** [e.g., "100 requests/minute per API key" or "N/A"]
**Idempotent:** [Yes / No]

---

## 3. Request

### Headers
| Header | Required | Value |
|--------|----------|-------|
| Content-Type | Yes | application/json |
| Authorization | Yes | Bearer {token} |
| X-Request-ID | No | UUID (generated if not provided) |

### Path Parameters
| Parameter | Type | Required | Description | Constraints |
|-----------|------|----------|-------------|-------------|
| [name] | [type] | [yes/no] | [description] | [validation rules] |

### Query Parameters
| Parameter | Type | Required | Default | Description | Constraints |
|-----------|------|----------|---------|-------------|-------------|
| [name] | [type] | [yes/no] | [default] | [description] | [validation rules] |

### Request Body
```json
{
  "field1": "string — description (constraints)",
  "field2": 0,
  "nested": {
    "field3": true
  }
}
```

**Field Definitions:**
| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| field1 | string | Yes | 1-255 chars, UTF-8 | [description] |
| field2 | integer | Yes | 0-10000 | [description] |
| nested.field3 | boolean | No | Default: false | [description] |

---

## 4. Responses

> **Pin only EXTERNALLY-FIXED response shapes** — a serialized
> cross-boundary structure the world outside our code dictates and we must
> match (a published API contract a client already consumes, a third-party
> webhook payload, a log line a downstream consumer parses). These are
> observable behavior: give a named, literal, copy-pasteable example. Do
> NOT pin a shape we are free to design — that internal envelope is the
> Architect's to derive. The test: does something outside our code dictate
> this shape, or do we choose it? Pin only the former. Add additional
> externally-fixed contracts (log lines, stdout, redaction targets) under
> a `Wire Contracts` subsection if applicable — anywhere this spec mentions
> a serialized cross-boundary structure, the literal shape must appear in
> this document or it will be flagged by the precheck.

### 200 OK / 201 Created (Success)
```json
{
  "id": "uuid",
  "field1": "value",
  "field2": 0,
  "createdAt": "2025-01-15T10:30:00Z",
  "updatedAt": "2025-01-15T10:30:00Z"
}
```

**Field Definitions:**
| Field | Type | Description |
|-------|------|-------------|
| id | string (UUIDv4) | System-generated unique identifier |
| createdAt | string (ISO 8601 UTC) | Creation timestamp |
| updatedAt | string (ISO 8601 UTC) | Last modification timestamp |

### 400 Bad Request (Validation Error)
```json
{
  "error": "Validation failed",
  "code": "VALIDATION_ERROR",
  "details": [
    {
      "field": "field1",
      "message": "Must be between 1 and 255 characters",
      "value": ""
    }
  ]
}
```

### 401 Unauthorized
```json
{
  "error": "Invalid or expired token",
  "code": "UNAUTHORIZED"
}
```

### 403 Forbidden
```json
{
  "error": "Insufficient permissions",
  "code": "FORBIDDEN"
}
```

### 404 Not Found
```json
{
  "error": "Resource not found",
  "code": "NOT_FOUND"
}
```

### 409 Conflict
```json
{
  "error": "[Description of conflict]",
  "code": "CONFLICT"
}
```

### 429 Too Many Requests
```json
{
  "error": "Rate limit exceeded",
  "code": "RATE_LIMITED",
  "retryAfter": 60
}
```

### 500 Internal Server Error
```json
{
  "error": "An unexpected error occurred",
  "code": "INTERNAL_ERROR"
}
```

[Delete any response codes that don't apply to this endpoint]

---

## 5. Business Logic

<!-- Target 3-5 FRs/endpoints per spec. If you need more, split into multiple specs. -->

### Processing Steps
1. [Authenticate the request (verify Bearer token)]
2. [Authorize (verify user has required permissions)]
3. [Validate request body against schema]
4. [Business logic step 1]
5. [Business logic step 2]
6. [Persist to database]
7. [Emit events (if applicable)]
8. [Return response]

### Validation Rules
| Field | Rules |
|-------|-------|
| field1 | Required, string, 1-255 chars, no HTML tags |
| field2 | Required, integer, 0 <= value <= 10000 |

---

## 6. Constraints

| Constraint | Value |
|------------|-------|
| Language / framework | [e.g., Node.js 20 + Express, Python 3.12 + FastAPI] |
| Auth mechanism | [e.g., JWT via existing middleware, API key header] |
| Database / ORM | [e.g., PostgreSQL via Prisma, existing schema — no migrations] |
| Integration points | [e.g., must call PaymentService.charge(), emit user.created event to Kafka] |
| Backward compatibility | [e.g., v1 endpoint must remain unchanged, or: no backwards compat required] |
| Deployment constraints | [e.g., runs in existing Docker container, no new env vars without DevOps approval] |

---

## 7. Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Response time (p95) | < [X]ms |
| Max payload size | [X] KB/MB |
| Concurrent requests | [X] simultaneous |
| Availability | [X]% uptime |

---

## 8. Edge Cases

| ID | Condition | Expected Response |
|----|-----------|-------------------|
| EC-1 | Empty request body | 400 with "Request body is required" |
| EC-2 | Extra fields in request | Ignore extra fields, process valid ones |
| EC-3 | Content-Type not application/json | 415 Unsupported Media Type |
| EC-4 | Request body exceeds max size | 413 Payload Too Large |
| EC-5 | [Specific to this endpoint] | [Expected response] |

---

## 9. Acceptance Criteria

### Tier 1: Test Contracts (OPTIONAL — usually omit)

You usually do NOT write Tier-1 contracts: the pipeline derives how each behavior
is verified. Include one ONLY to pin a hard, already-known deterministic fact you
want guaranteed (a specific status code for a fixed request, a required header, an
error-envelope format dictated externally). When present, they compile to identical
tests every run. The schema (for the rare case you need it):

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: http
    given: "valid auth and valid body"
    when:
      method: POST
      path: /api/v1/resource
      body: { "name": "Test" }
    then:
      status: 201
      body:
        id: { type: string }
        name: { equals: "Test" }

  - id: TC-2
    fr: FR-1
    type: http
    given: "missing auth"
    when:
      method: POST
      path: /api/v1/resource
    then:
      status: 401
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
| AC-1 | GIVEN [edge case] WHEN [METHOD] [path] THEN [observable response — HTTP status, body fields, error code] | no |
| AC-2 | GIVEN [concurrent requests] WHEN [METHOD] [path] THEN [observable result — no race condition] | no |

> **Important:** THEN clauses must describe externally observable behavior (HTTP status codes, response body fields, error codes). Never reference implementation details like "uses try/catch", "calls function X", or "logs internally". The pipeline tests these criteria via HTTP requests against a running server. How an AC is verified — in-process, subprocess, a command — is derived by the pipeline, not declared by you.
>
> **Differential classification (validator-gated):** every `security` / `error` / `adversarial` rejection AC MUST be marked `disc:differential` or `disc:invariant: <reason>` (place the marker in the Criterion cell). A `disc:differential` AC names an otherwise-valid baseline that MUST succeed PLUS the triggering deviation that MUST be rejected, on the same fixture, with a `MUST NOT normalize/strip/coerce` clause where a lenient reading is defensible — so a test cannot pass for the wrong reason (this is the whitespace-token->401 protection). A `disc:invariant` AC is a content/forbidden-pattern property with no valid-input sibling — the `<reason>` is REQUIRED (the validator checks for it). The validator REVISEs a bare "input X -> 401" that lacks the success baseline.
>
> **Blocking column (required):** `yes` for criteria whose failure must veto SHIP_WITH_GAPS promotion (auth enforcement, tenant-isolation invariants, data-integrity guarantees). `no` for criteria that are desired but not release-blocking. Every `yes` entry MUST be listed under the Non-Negotiable Acceptance Criteria section below.

## Non-Negotiable Acceptance Criteria

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

## 10. Scenario Intent — DERIVED (do not author)

The Test-Engineer derives the per-endpoint test scenarios from your Acceptance
Criteria and Edge Cases — you do NOT enumerate them here (pre-writing the test
plan is the pipeline's job). The discrimination protection that used to live here
is now in the §9 AC table: mark every `security`/`error`/`adversarial` rejection AC
`disc:differential` / `disc:invariant` (see §9). Write complete, atomic, observable
ACs and the scenarios follow.

---

## 11. Examples

### Example 1: Successful request
```bash
curl -X POST https://api.example.com/v1/resource \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer eyJ..." \
  -d '{"field1": "value", "field2": 42}'
```
```json
// Response: 201 Created
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "field1": "value",
  "field2": 42,
  "createdAt": "2025-01-15T10:30:00Z",
  "updatedAt": "2025-01-15T10:30:00Z"
}
```

### Example 2: Validation error
```bash
curl -X POST https://api.example.com/v1/resource \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer eyJ..." \
  -d '{"field1": "", "field2": -1}'
```
```json
// Response: 400 Bad Request
{
  "error": "Validation failed",
  "code": "VALIDATION_ERROR",
  "details": [
    {"field": "field1", "message": "Must be between 1 and 255 characters"},
    {"field": "field2", "message": "Must be >= 0"}
  ]
}
```

### Example 3: Edge case
```bash
curl -X POST https://api.example.com/v1/resource \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer eyJ..." \
  -d '{"field1": "a]", "field2": 10000}'
```
```json
// Response: [expected status code]
{
  "id": "...",
  "field1": "a]",
  "field2": 10000
}
```

---

## 12. Dependencies

- **Middleware:** [Auth middleware path, validation middleware, etc.]
- **Database:** [Table/collection name, schema reference]
- **Services:** [External services called]
- **Events:** [Events emitted after success]

---

## 13. Out of Scope

- [Related endpoints not covered here]
- [Batch operations]
- [Webhooks/notifications]

---

## 14. Free Variables (implementation choices — DO NOT assert in scenarios)

List implementation decisions this spec intentionally leaves unconstrained.
The Scenario Architect will treat these as DO-NOT-ASSERT directives.

| ID | Variable | What Is Constrained | What Is Free |
|----|----------|---------------------|--------------|
| FV-1 | [e.g., Response envelope structure] | [e.g., Must return JSON with data field] | [e.g., Additional metadata fields like pagination format] |
| FV-2 | [e.g., Error response format] | [e.g., Must include error code and message] | [e.g., Additional fields like timestamp, requestId] |

If no implementation choices are free, write "None — all observable behaviors are fully constrained."
