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

## 3. Interface Contract

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

### Tier 1: Test Contracts (compiled deterministically)

These contracts are compiled to test code mechanically by the pipeline. They
produce identical tests every run. Use for verifiable integration artifacts.

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

| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the service returns a retryable error WHEN the retry policy fires THEN request succeeds or circuit breaker opens |
| AC-2 | GIVEN the circuit breaker is open WHEN a request is made THEN the system degrades gracefully |
| AC-3 | GIVEN a duplicate webhook WHEN processed THEN no duplicate side effects occur |

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

## 10. Scenario Intent

Declare what scenarios should verify for each requirement. The Scenario Architect
compiles these intents into executable test code — it does not invent scenarios
beyond what is declared here plus a small cross-cutting set.

### Service Communication
| # | Intent | Layer |
|---|--------|-------|
| 1 | Successful API call returns expected response shape | happy_path |
| 2 | Request payload maps correctly from our data model | contract |
| 3 | Response payload maps correctly to our data model | contract |
| 4 | Authentication credentials are included in requests | security |
| 5 | Idempotency keys are sent with mutating requests | contract |

### Error Handling & Resilience
| # | Intent | Layer |
|---|--------|-------|
| 1 | 429 triggers retry with backoff | error |
| 2 | 5xx triggers retry per retry policy | error |
| 3 | Non-retryable errors (400, 401, 403, 404) are not retried | error |
| 4 | Circuit breaker opens after failure threshold | state |
| 5 | Circuit breaker half-open probe restores service on success | state |
| 6 | Degraded mode activates when circuit is open | state |
| 7 | Timeout returns appropriate error to caller | error |

### Webhook Processing
| # | Intent | Layer |
|---|--------|-------|
| 1 | Valid webhook updates system state correctly | happy_path |
| 2 | Duplicate webhook delivery is idempotent | state |
| 3 | Webhook signature verification rejects tampered payloads | security |
| 4 | Unexpected webhook event types are logged and ignored | edge_case |
| 5 | Webhook with missing required fields is rejected gracefully | edge_case |

Layer values: `contract`, `happy_path`, `edge_case`, `error`, `state`, `security`, `property`, `adversarial`

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
