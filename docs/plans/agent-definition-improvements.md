# Plan: Agent Definition Improvements

## Motivation

Dark-factory's agent definitions (`agents/*.md`) are long-form process instructions injected
as system prompts into Docker containers. They work, but they have two structural gaps:

1. **No structured output contract** — the implementer produces a markdown "Implementation Notes"
   document the pipeline cannot parse. Silent failures (container exits 0 but wrote nothing) and
   blocked states (implementer can't proceed) are indistinguishable from success.

2. **No YAML frontmatter** — agent files lack the `name`/`description`/`model` fields that make
   them proper Claude Code subagent definitions. Skills-based workflows (`/df-implement`) invoke
   the right prompt manually; adding frontmatter enables automatic routing and forward compatibility
   with non-Docker invocation paths.

A secondary finding: the Linus persona pattern is a better fit for the **Reviewer** role than the
Implementer role. The Reviewer needs to emit sharp ACCEPT/REJECT/NEEDS WORK signals for the Oracle.
The current reviewer likely produces diplomatic prose; a structured harsh-critic output would
produce cleaner Oracle input.

---

## Changes

### 1. Implementer: structured JSON output envelope

Replace the free-form markdown "Implementation Notes" with a JSON envelope at the end of the
implementer's output. The pipeline can then parse this to detect silent failures and blocks.

**Schema:**

```json
{
  "status": "success | blocked | partial",
  "spec": "<spec-name>",
  "files_modified": ["path/to/file.ext"],
  "files_created": ["path/to/new.ext"],
  "blocked_actions": ["description of anything the agent could not do"],
  "self_test_summary": {
    "tests_written": 0,
    "all_passing": true
  },
  "spec_compliance": [
    { "id": "FR-1", "implemented": true, "tested": true, "notes": "" }
  ],
  "architecture_decisions": [
    { "decision": "...", "rationale": "..." }
  ],
  "risk_areas": [
    { "area": "...", "mitigation": "..." }
  ],
  "assumptions": []
}
```

**`status` semantics:**
- `success` — all deliverables complete, self-tests pass, lint/typecheck clean
- `blocked` — implementer cannot proceed without information or capability it doesn't have;
  `blocked_actions` must be non-empty
- `partial` — deliverables written but known failures remain (e.g., one test cannot be made
  to pass); explains what and why

**Pipeline changes in `iteration.py`:**
- After the implementer container exits 0, parse the JSON envelope from the container output
- If `status == "blocked"`: escalate immediately (do not run quality gate, do not consume
  an iteration slot); write a human-escalation notice
- If `status == "partial"`: log warning, proceed to quality gate, include the partial notes
  in Oracle feedback
- If `files_modified` and `files_created` are both empty: treat as silent failure, escalate
- If JSON parse fails: log warning and fall through to existing behavior (no regression)

**`agents/implementer.md` changes:**
- Replace the "Output Format" section markdown template with the JSON schema above
- Keep the Spec Compliance Matrix as a JSON array (already structured, just changes format)
- Retain all information barrier rules — the JSON schema does not weaken them

---

### 2. All agent files: YAML frontmatter

Add frontmatter to every agent file:

```yaml
---
name: Implementer          # or ScenarioArchitect, Reviewer, Oracle, etc.
description: <one-line description for agent routing>
model: inherit
---
```

The `description` field matters most for skills-based workflows where Claude Code routes
automatically. It should describe *when* the agent should be invoked, not what it does.
Keep it concise — one sentence.

No behavioral changes. Frontmatter is metadata only.

---

### 3. Reviewer: Linus-style structured critique

Replace the current reviewer output format with a structured critique pattern:

**Output format:**

```
## Verdict
ACCEPT | REJECT | NEEDS WORK

## Critical Issues
[Things that will cause the Oracle to NO-SHIP — must be fixed]
- ...

## Quality Issues
[Things that should improve but won't block ship on their own]
- ...

## Spec Compliance Gaps
[Spec sections (FR-N, EC-N) where the implementation appears incomplete or incorrect]
- FR-N: <description of gap>

## Positive Observations
[Brief — what was done well]

## Recommendation to Oracle
[One paragraph — direct input for the Oracle's ship/no-ship decision]
```

**Why this matters for the Oracle:** The Oracle currently synthesizes reviewer prose + gate
results to make a ship decision. A structured verdict with explicit spec compliance gaps maps
directly onto the Oracle's output format (which already references FR-N, EC-N). This reduces
inference and makes the Oracle's reasoning more reliable.

**Persona framing (optional):** A "harsh critic" persona (Linus-style) can be added to
encourage bluntness over diplomacy. This is a tone adjustment, not a structural requirement.
The structured output format is the critical piece; the persona is secondary. If added, it
should be scoped to the communication style section only — not the access rules or process
steps, which must remain precise.

---

## What NOT to change

- **Information barrier rules** — these are non-negotiable and must remain explicit in every
  agent file regardless of output format changes
- **Implementer process phases** (Comprehension → Design → Implementation → Self-Testing →
  Verify) — these are correct and should not be touched
- **Persona framing for the Implementer** — the implementer's job is to produce code, not
  perform a character. No persona should be added here.
- **Gate pipeline** — the quality gate runs after the implementer regardless of these changes;
  structured output is additive observability, not a gate replacement

---

## Implementation order

1. **Frontmatter** (lowest risk, no behavior change) — add to all agent files in one commit
2. **Reviewer output format** (medium risk — affects Oracle input) — update reviewer.md and
   test that Oracle still produces correct ship/no-ship decisions on a sample run
3. **Implementer JSON envelope** (highest risk — requires iteration.py changes) — implement
   JSON parsing in iteration.py with graceful fallback, then update implementer.md

Each step is independently shippable. Don't bundle them.

---

## Open questions

- Should `spec_compliance` in the JSON be required or optional? Required is cleaner for the
  Oracle but adds implementer burden. Start optional, make required after validating the format
  works in practice.
- Should the reviewer persona be Linus (kernel-level harshness) or a more domain-neutral
  "senior engineer with zero tolerance for vagueness"? Linus is well-calibrated for systems
  code but may be miscalibrated for frontend/API work. Consider a domain-neutral framing.
