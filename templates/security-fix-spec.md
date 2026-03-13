# Security Fix Specification: [Vulnerability Name]

<!-- dark-factory-metadata
feature-id: F-X
depends-on: []
plan:
profile:
-->

**Author:** [name]
**Date:** [YYYY-MM-DD]
**Severity:** CRITICAL | HIGH | MEDIUM | LOW
**CVE:** [CVE-YYYY-XXXXX or "N/A (internal finding)"]
**CVSS Score:** [0.0-10.0 or "Pending"]
**Disclosure Deadline:** [YYYY-MM-DD or "N/A (no external disclosure)"]
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Vulnerability Description

**What is the vulnerability?**
[Describe the vulnerability in precise technical terms]

**Vulnerability class:** [e.g., SQL Injection, XSS, CSRF, IDOR, Path Traversal, Deserialization, Authentication Bypass, etc.]

**How was it discovered?**
[Penetration test, code review, automated scanner, bug bounty report, incident, etc.]

**Affected component(s):**
- **File(s):** [exact file paths]
- **Function(s):** [exact function names]
- **Endpoint(s):** [affected routes/URLs if applicable]

---

## 2. Current Exposure

**Who can exploit this?**
[Unauthenticated users / authenticated users / admin users / adjacent network attackers]

**Attack vector:**
```
[Concrete proof-of-concept or attack description]
[e.g., curl command, crafted input, request sequence]
```

**What is the impact of exploitation?**
| Impact Type | Description |
|-------------|-------------|
| Confidentiality | [e.g., Attacker can read all user records] |
| Integrity | [e.g., Attacker can modify other users' data] |
| Availability | [e.g., Attacker can crash the service] |
| Compliance | [e.g., GDPR violation, PCI-DSS non-compliance] |

**Is this actively being exploited?**
[Yes (with evidence) / No / Unknown]

**Affected environments:**
[Production / Staging / Development / All]

---

## 3. Fix Requirements

### FR-1: [Primary fix action]
- **Current behavior:** [What happens now that is insecure]
- **Required behavior:** [What must happen after the fix]
- **Affected code:** [file:function]

### FR-2: [Secondary fix action]
- **Current behavior:** [...]
- **Required behavior:** [...]
- **Affected code:** [...]

### FR-3: [Defense-in-depth measure]
- **Current behavior:** [...]
- **Required behavior:** [...]
- **Affected code:** [...]

[Add more as needed]

---

## 4. Threat Model

**Attacker profile:** [Skill level, access level, motivation]

**Attack scenarios:**
| ID | Scenario | Preconditions | Steps | Impact |
|----|----------|---------------|-------|--------|
| TM-1 | [Primary attack path] | [What attacker needs] | [Step-by-step attack] | [Consequence] |
| TM-2 | [Variant attack path] | [What attacker needs] | [Step-by-step attack] | [Consequence] |
| TM-3 | [Post-fix bypass attempt] | [What attacker needs] | [Step-by-step attempt] | [Must fail] |

**Trust boundaries affected:**
- [e.g., User input -> application logic]
- [e.g., Application -> database]
- [e.g., Service A -> Service B]

---

## 5. Constraints

- **Backward compatibility:** [Fix must not break existing API consumers / data formats]
- **Performance:** [Fix must not increase latency by more than X ms]
- **Maximum scope:** [Limit the fix to specific files/functions — no refactoring]
- **Disclosure timeline:** [Fix must be deployed by YYYY-MM-DD]
- **Must not change:** [List behaviors that must remain unchanged]
- **Dependencies:** [Fix must not introduce new dependencies unless listed here]

---

## 6. Edge Cases (Bypass Attempts)

| ID | Bypass Attempt | Expected Behavior |
|----|----------------|-------------------|
| EC-1 | [e.g., Double-encoding malicious input] | [Fix handles double-encoded input correctly] |
| EC-2 | [e.g., Using null bytes to truncate input] | [Fix rejects null bytes in input] |
| EC-3 | [e.g., Exploiting race condition in auth check] | [Fix uses atomic check-and-act pattern] |
| EC-4 | [e.g., Unicode normalization to bypass filter] | [Fix normalizes input before validation] |
| EC-5 | [e.g., Chaining with another endpoint to bypass fix] | [Fix applies defense-in-depth; no bypass via chaining] |

---

## 7. Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN the original attack vector WHEN replaying the proof-of-concept THEN the attack is blocked and an appropriate error is returned |
| AC-2 | GIVEN a legitimate user WHEN performing normal operations THEN behavior is unchanged from before the fix |
| AC-3 | GIVEN each bypass attempt from Section 6 WHEN attempted THEN the attack is blocked |
| AC-4 | GIVEN the fix is applied WHEN running the full test suite THEN all existing tests pass |
| AC-5 | GIVEN the fix is applied WHEN auditing logs THEN blocked attack attempts are logged with sufficient detail for incident response |

**Negative test requirements:**
| ID | Test Description |
|----|------------------|
| NT-1 | [Verify original attack vector is rejected] |
| NT-2 | [Verify bypass attempt EC-1 is rejected] |
| NT-3 | [Verify bypass attempt EC-2 is rejected] |
| NT-4 | [Verify error messages do not leak sensitive information] |

---

## 8. Regression Prevention

**What test should exist to prevent this from recurring?**
[Describe the test that, if it had existed, would have caught this vulnerability]

**Root cause category:** [Missing input validation / Broken access control / Insecure configuration / Missing encryption / Injection flaw / Other]

**Preventive measures:**
- [e.g., Add SAST rule to detect this pattern]
- [e.g., Add security-focused test to CI pipeline]
- [e.g., Update secure coding guidelines for the team]

---

## 9. Examples

### Example 1: Happy path — legitimate request after fix
```
Request:
  [Show a normal, legitimate request]

Response:
  [Show the expected successful response — same as before the fix]

Result: Fix does not interfere with normal operations.
```

### Example 2: Error case — original attack vector blocked
```
Request:
  [Show the exact attack vector / proof-of-concept]

Response:
  [Show the error response returned by the fix]
  [e.g., HTTP 400 with generic error message, no sensitive data leaked]

Log entry:
  [Show what is logged for security monitoring]
```

### Example 3: Edge case — bypass attempt rejected
```
Request:
  [Show a crafted bypass attempt, e.g., double-encoded payload]

Response:
  [Show the error response — attack blocked]

Result: Defense-in-depth prevents bypass via [technique].
```

---

## 10. Dependencies

### Existing Code
- [File/module path] -- [What it provides, how the fix interacts with it]

### Security Libraries
- [Library name] -- [Version, purpose, e.g., "helmet 7.x for HTTP header hardening"]

### Infrastructure
- [e.g., WAF rules to update, CSP headers to modify]

---

## 11. Out of Scope

The following are explicitly NOT part of this security fix:
- [Broader security audit of related components]
- [Refactoring of the authentication/authorization system]
- [Addressing other known vulnerabilities (separate specs)]
- [Updating third-party dependencies beyond what is required for this fix]
