# Integration Specification: [Service/Integration Name]

<!-- dark-factory-metadata
feature-id: F-X
depends-on: []
plan:
profile:
-->

**Author:** [name]
**Date:** [YYYY-MM-DD]
**Service:** [Service name + version, e.g., Stripe API v2023-10-16]
**API Version:** [e.g., v2, GraphQL 2023-07]
**Auth Method:** [API Key | OAuth 2.0 | Bearer Token | mTLS | HMAC | None]
**Rate Limit:** [e.g., 100 req/s per API key, 1000 req/min per account]
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Problem Statement

**What problem does this integration solve?**
[Describe the business need that requires integrating with this external service]

**What exists today?**
[Nothing / manual process / existing integration that needs replacement / workaround being used]

**Who is affected?**
[Users, systems, or processes that will benefit from this integration]

**What does success look like?**
[Describe the system behavior after integration is complete]

---

## 2. Service Overview

**Service documentation:** [URL to official API docs]

**Authentication flow:**
1. [e.g., Obtain API key from service dashboard]
2. [e.g., Include API key in Authorization header]
3. [e.g., Tokens expire after X hours; refresh using endpoint Y]

**Base URL(s):**
| Environment | Base URL |
|-------------|----------|
| Production | [https://api.service.com/v1] |
| Sandbox | [https://sandbox.api.service.com/v1] |

**Service SLA:**
| Metric | Guarantee |
|--------|-----------|
| Uptime | [e.g., 99.9%] |
| Response time (p95) | [e.g., < 500ms] |
| Rate limit | [e.g., 100 req/s] |
| Data retention | [e.g., 90 days] |

---

## 3. Interface Contract (external service — pin only EXTERNALLY-FIXED shapes)

> Pin the **external service's** request/response/webhook shapes here — these are
> externally-fixed (the third-party service dictates them and we must match exactly),
> so they are observable behavior worth recording as literal, copy-pasteable examples.
> Do NOT pin our own internal client signatures, adapter interfaces, or wrapper return
> types — those the Architect derives from the observable integration behavior in your
> FRs/ACs and the data mapping in Section 6. The test: does the external service
> dictate this shape (pin it), or do we choose it (let the Architect derive it)?

### Endpoints Used

#### [METHOD] [/path/to/endpoint-1]
**Purpose:** [What this endpoint does in the context of our integration]

**Request:**
```json
{
  "field1": "string — description",
  "field2": 0
}
```

**Response (success):**
```json
{
  "id": "string",
  "status": "string",
  "result": {}
}
```

**Response (error):**
```json
{
  "error": {
    "code": "string",
    "message": "string"
  }
}
```

#### [METHOD] [/path/to/endpoint-2]
**Purpose:** [What this endpoint does]

**Request:**
```json
{
  "field1": "string — description"
}
```

**Response (success):**
```json
{
  "data": []
}
```

### Webhook Endpoints (if applicable)

#### [POST] [/our-webhook/endpoint]
**Event:** [e.g., payment.completed]
**Payload:**
```json
{
  "event": "string",
  "data": {}
}
```
**Verification:** [e.g., HMAC-SHA256 signature in X-Signature header]

---

## 4. Error Handling

| HTTP Status | Service Error Code | Meaning | Our Response |
|-------------|-------------------|---------|--------------|
| 400 | [INVALID_REQUEST] | [Malformed request] | [Log error, return 400 to caller with safe message] |
| 401 | [UNAUTHORIZED] | [Invalid credentials] | [Alert ops, return 503 to caller] |
| 403 | [FORBIDDEN] | [Insufficient permissions] | [Log error, return 403 to caller] |
| 404 | [NOT_FOUND] | [Resource does not exist] | [Return 404 to caller] |
| 429 | [RATE_LIMITED] | [Rate limit exceeded] | [Retry with backoff per Section 5] |
| 500 | [INTERNAL_ERROR] | [Service internal error] | [Retry per Section 5, then degrade gracefully] |
| 503 | [SERVICE_UNAVAILABLE] | [Service temporarily down] | [Retry per Section 5, activate circuit breaker] |
| Timeout | N/A | [Request exceeded timeout] | [Retry once, then return 504 to caller] |

**Timeout configuration:**
- Connection timeout: [X ms]
- Read timeout: [X ms]
- Total request timeout: [X ms]

---

## 5. Retry & Circuit Breaker Strategy

### Retry Policy
| Parameter | Value |
|-----------|-------|
| Max retries | [e.g., 3] |
| Backoff strategy | [Exponential with jitter / Linear / Fixed] |
| Initial delay | [e.g., 100ms] |
| Max delay | [e.g., 5s] |
| Retryable status codes | [429, 500, 503, timeout] |
| Non-retryable status codes | [400, 401, 403, 404, 409] |

### Circuit Breaker
| Parameter | Value |
|-----------|-------|
| Failure threshold | [e.g., 5 failures in 60 seconds] |
| Open state duration | [e.g., 30 seconds] |
| Half-open max requests | [e.g., 1 probe request] |
| Reset on success | [Yes — close circuit after 1 successful probe] |

### Degraded Mode
**When the circuit is open, the system should:**
- [e.g., Return cached data if available (max staleness: X minutes)]
- [e.g., Queue requests for later processing]
- [e.g., Return a user-friendly error message]
- [e.g., Emit alert to ops channel]

---

## 6. Data Mapping

### Outbound (Our System -> Service)
| Our Field | Service Field | Transformation |
|-----------|---------------|----------------|
| [user.email] | [customer.email_address] | [Direct mapping] |
| [order.total_cents] | [payment.amount] | [Divide by 100, format as decimal string] |
| [user.created_at] | [customer.since] | [Convert to ISO 8601 UTC] |

### Inbound (Service -> Our System)
| Service Field | Our Field | Transformation |
|---------------|-----------|----------------|
| [transaction.id] | [payment.external_id] | [Direct mapping, store as string] |
| [transaction.status] | [payment.status] | [Map: "succeeded" -> "completed", "failed" -> "failed"] |
| [transaction.amount] | [payment.amount_cents] | [Multiply by 100, round to integer] |

### Status Mapping (if applicable)
| Service Status | Our Status | Notes |
|----------------|------------|-------|
| [pending] | [processing] | [Transient state, poll until resolved] |
| [succeeded] | [completed] | [Final state] |
| [failed] | [failed] | [Final state, check error details] |
| [cancelled] | [cancelled] | [Final state] |

---

## 7. Constraints

- **Rate limits:** [Must stay within X req/s; implement client-side throttling]
- **Idempotency:** [All mutating requests must include an idempotency key]
- **Credentials storage:** [API keys stored in environment variables / secrets manager, never in code]
- **Data residency:** [Data must not leave region X / service operates in regions Y, Z]
- **PII handling:** [Minimize PII sent to external service; redact in logs]
- **No vendor lock-in:** [Integration layer must be abstracted behind an interface]
- **Backward compatibility:** [Must support service API versions X and Y during transition]

---

## 8. Edge Cases

| ID | Condition | Expected Behavior |
|----|-----------|-------------------|
| EC-1 | [Service returns unexpected/undocumented status code] | [Log warning, treat as server error, retry per Section 5] |
| EC-2 | [Service response payload is missing expected fields] | [Use defaults where safe, log warning, fail if critical field missing] |
| EC-3 | [Duplicate webhook delivery] | [Idempotent processing; detect by event ID, skip if already processed] |
| EC-4 | [Service API version is deprecated during runtime] | [Log error, alert ops, continue on current version until migration] |
| EC-5 | [Network partition causes timeout during mutation] | [Do not retry without idempotency key; query service to check if mutation was applied] |

---

## 9. Acceptance Criteria

### Tier 1: Test Contracts (OPTIONAL — usually omit)

You usually do NOT write Tier-1 contracts: the pipeline derives how each behavior
is verified. Include one ONLY to pin a hard, already-known deterministic fact you
want guaranteed (a fixed request maps to a fixed status, an idempotency key is sent).
Avoid pinning internal file paths or implementation-pattern greps — those are HOW,
which the Architect derives. When present, contracts compile to identical tests every
run. The schema (for the rare case you need it):

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: file_exists
    given: "after implementation"
    when:
      check: "src/integrations/[service]/client.ts"
    then:
      exists: true

  - id: TC-2
    fr: FR-1
    type: file_contains
    given: "after implementation"
    when:
      file: "src/integrations/[service]/client.ts"
      parse: text
      pattern: "retry|backoff"
    then:
      matches: true
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
| AC-1 | GIVEN the service returns a retryable error WHEN the retry policy fires THEN request succeeds or circuit breaker opens | no |
| AC-2 | GIVEN the circuit breaker is open WHEN a request is made THEN the system degrades gracefully | no |
| AC-3 | GIVEN a duplicate webhook WHEN processed THEN no duplicate side effects occur | no |

> **Important:** THEN clauses must describe externally observable behavior (responses to callers, side effects, requests sent to the service, log lines a consumer parses). Never reference implementation details like "uses library X" or "calls function Y". How an AC is verified — in-process, subprocess, a command — is derived by the pipeline, not declared by you.
>
> **Differential classification (validator-gated):** every `security` / `error` / `adversarial` rejection AC MUST be marked `disc:differential` or `disc:invariant: <reason>` (place the marker in the Criterion cell). A `disc:differential` AC names an otherwise-valid baseline that MUST succeed PLUS the triggering deviation that MUST be rejected, on the same fixture, with a `MUST NOT normalize/strip/coerce` clause where a lenient reading is defensible — so a test cannot pass for the wrong reason (this is the whitespace-token->401 protection). A `disc:invariant` AC is a content/forbidden-pattern property with no valid-input sibling — the `<reason>` is REQUIRED (the validator checks for it). The validator REVISEs a bare "input X -> rejection" (e.g. a tampered webhook signature) that lacks the valid-signature success baseline.
>
> **Blocking column (required):** `yes` for criteria whose failure must veto SHIP_WITH_GAPS promotion (webhook signature verification, idempotency guarantees, credential handling). `no` for criteria that are desired but not release-blocking. Every `yes` entry MUST be listed under the Non-Negotiable Acceptance Criteria section below.

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

The Test-Engineer derives the test scenarios from your Acceptance Criteria, Edge
Cases, and the error/retry/webhook behavior in Sections 4-6 — you do NOT enumerate
them here (pre-writing the test plan is the pipeline's job). The discrimination
protection that used to live here is now in the §9 AC table: mark every
`security`/`error`/`adversarial` rejection AC `disc:differential` / `disc:invariant`
(see §9). Write complete, atomic, observable ACs and the scenarios follow.

---

## 11. Examples

### Example 1: Happy path — successful service call
```
Our request:
  POST https://api.service.com/v1/resource
  Headers: Authorization: Bearer sk_test_xxx
  Body: {"field1": "value", "field2": 42}

Service response:
  HTTP 201
  Body: {"id": "res_abc123", "status": "active", "field1": "value"}

Our system:
  Mapped to internal model: {external_id: "res_abc123", status: "active"}
  Stored in database
  Returned success to caller
```

### Example 2: Error case — service returns 429, retry succeeds
```
Request 1:
  POST https://api.service.com/v1/resource
  Response: HTTP 429 {"error": {"code": "RATE_LIMITED", "message": "Too many requests"}}

  Action: Wait 200ms (exponential backoff + jitter)

Request 2 (retry):
  POST https://api.service.com/v1/resource
  Response: HTTP 201 {"id": "res_abc123", "status": "active"}

  Result: Request succeeds on retry. No error returned to caller.
```

### Example 3: Edge case — circuit breaker activates after repeated failures
```
Requests 1-5 (within 60 seconds):
  POST https://api.service.com/v1/resource
  Response: HTTP 503 (all five requests)

  Action: Circuit breaker OPENS after 5th failure.

Request 6 (circuit open):
  Skipped — circuit breaker returns cached data / error immediately.
  Response to caller: {"status": "degraded", "message": "Service temporarily unavailable, using cached data"}

30 seconds later (half-open):
  Probe request: POST https://api.service.com/v1/resource
  Response: HTTP 201
  Action: Circuit breaker CLOSES. Normal operation resumes.
```

---

## 12. Dependencies

### Existing Code
- [File/module path] -- [What it provides, e.g., "HTTP client wrapper"]
- [File/module path] -- [What it provides, e.g., "Data model for mapped entities"]

### External Services
- [Service name] -- [Sandbox account credentials location, e.g., "secrets/staging.env"]
- [Webhook relay for local development, e.g., "ngrok, smee.io"]

### Libraries
- [HTTP client library] -- [Version, purpose, already installed?]
- [Circuit breaker library] -- [Version, purpose, already installed?]
- [Retry library] -- [Version, purpose, already installed?]

---

## 13. Out of Scope

The following are explicitly NOT part of this integration:
- [Integrating additional endpoints beyond those listed in Section 3]
- [Migrating data from the old integration (if applicable)]
- [Building an admin UI for managing the integration]
- [Multi-tenant credential management]

---

## 14. Free Variables (implementation choices — DO NOT assert in scenarios)

List implementation decisions this spec intentionally leaves unconstrained.
The Scenario Architect will treat these as DO-NOT-ASSERT directives.

| ID | Variable | What Is Constrained | What Is Free |
|----|----------|---------------------|--------------|
| FV-1 | [e.g., HTTP client library] | [e.g., Must make requests to endpoints in Section 3] | [e.g., Whether to use fetch, axios, or native http] |
| FV-2 | [e.g., Retry strategy] | [e.g., Must retry on 5xx errors] | [e.g., Backoff algorithm, max retries] |

If no implementation choices are free, write "None — all observable behaviors are fully constrained."
