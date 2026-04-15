---
name: df-write-hints
description: Interactively build project-hints.toml policy (baseline walkthrough, natural-language rule add, or fix from a failed pipeline run)
argument-hint: "[init | add \"<description>\" | from-failure <run-id>]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. The skill edits `.dark-factory/project-hints.toml` — a policy input consumed by later barrier-enforced runs. It never writes implementation code.

# Write Project Hints

## Purpose

`project-hints.toml` is the **project-level policy layer** — it holds project-specific rules (workspace scope, banned command prefixes, migrations discipline, consistency regexes) that steer the pipeline independently of the ecosystem adapter. This skill builds that file three ways:

1. **`init`** — Conversational baseline walkthrough. The skill explains each question with rationale and examples, then hands off to `dark-factory hints init` (which the user runs in their own terminal) and `dark-factory hints add` (which the skill drives).
2. **`add "<description>"`** — Translate a natural-language rule into a structured `[[policy.consistency_rules]]` block, preview the TOML, apply via `dark-factory hints add` on confirmation.
3. **`from-failure <run-id>`** — Read the `REMEDIATION_TARGET` event from `.dark-factory/events.jsonl` matching the run-id, draft a project-hints rule that would have caught the contradiction, apply on confirm, suggest re-running the spec.

All writes go through `dark-factory hints add` / `dark-factory hints init`, which enforce atomic-write-and-round-trip-validate and reject duplicate rule names (without `--force`). The skill is a UX layer on top of those commands — it never writes the TOML directly.

**Two migration concepts, kept distinct.** `[policy.migrations].mode` is the *project discipline* short-form (free-form string — typical values `cli-only`, `on-start`, `skip`) used to pick the canonical `commands.setup` string. The "migrate-vs-push" contradiction is a *different* thing — an ORM/tool choice enforced via a `[[policy.consistency_rules]]` entry that inspects `commands.setup`. They are related but not interchangeable; don't conflate them.

## Pre-flight Checks

Before proceeding, verify ALL of the following. STOP with a clear error if any fails.

1. **`dark-factory hints` is registered:** run `dark-factory hints --help`. If it exits non-zero, tell the user to `pip install -e <dark-factory-repo>` or activate their framework venv.
2. **`.dark-factory/` directory exists.** If absent, tell the user to run `dark-factory init` first.
3. **Legacy migration complete:** if `.dark-factory/architecture/hints.json` or `.dark-factory/architecture/environment.toml` exist **and** `.dark-factory/project-hints.toml` does not, STOP and tell the user to run `dark-factory init --migrate-hints`. The writer refuses this state; the skill should not bypass the refusal.
4. **Mode argument provided:** one of `init`, `add`, or `from-failure` must be given. For `add`, the description must be non-empty; for `from-failure`, the run-id must be non-empty.
5. **For `from-failure` mode only:** `.dark-factory/events.jsonl` must exist. If absent, tell the user "no pipeline runs have been recorded for this project yet — `from-failure` needs an events history."

## Routing

Pick one of the three sections below based on the mode argument.

---

## Mode 1: `init` — Baseline Walkthrough

Use when setting up `[policy]` for the first time and wanting explanations alongside the questions.

### Step 1a: Detect Current State

Run `dark-factory hints list`. Three cases:

- **File does not exist** → proceed to step 1b.
- **File exists with only `[detected]` auto-facts, no `[policy]`** → proceed to step 1b.
- **File already has a non-empty `[policy]` section** → STOP. Show its contents. Suggest `/df-write-hints add "<description>"` for incremental additions, or `dark-factory hints init --force` if they truly want to overwrite (warn that comments are lost on full rewrite).

### Step 1b: Interview (5 always-asked questions)

Ask in this order. Explain *why* each question matters before asking — the user learns the framework by answering. Every question is asked even if the answer will be blank; a blank answer is still a recorded answer.

1. **Workspace scope (monorepo only):** "Is this a monorepo? If so, what directory is the primary workspace — e.g., `apps/api`, `packages/web`? This flags amendment commands that should stay scoped to that package. Blank if single-package."
2. **Banned recursive command prefixes:** "What command *prefixes* should never appear in amendment ops? Common answers for pnpm monorepos: `pnpm -r`, `pnpm --recursive`. Comma-separated; blank for none. Only meaningful if you set a workspace scope above — otherwise the CLI skips the prompt with an explanation."
3. **Migrations mode:** "For databases with migration tooling, what's your discipline short-form? Typical values: `cli-only` (CLI-driven migrations, run explicitly), `on-start` (run at app startup), `skip` (no migrations / managed externally). Blank if no DB."
4. **Migrations command:** "If mode is `cli-only` or `on-start`: what's the exact command for `commands.setup`? Examples: `cd apps/api && pnpm drizzle:push`, `alembic upgrade head`, `python manage.py migrate`. The architect uses this verbatim; any divergence is rejected. Blank if no command step."
5. **Prior contradictions seen** (always asked, even if blank answer): "Have you seen spec/env contradictions bite in prior runs? Answer yes/no. If yes, describe each one in plain language — examples: 'any spec mentioning migrations must not produce `commands.setup = drizzle-kit push`', 'any spec requiring Redis must declare a `redis` service'. I'll draft one `[[policy.consistency_rules]]` per contradiction in step 1d."

If the user's answer is vague, push back: "What directory exactly? What command prefix? What regex would match the forbidden form?" Do not guess patterns.

### Step 1c: Apply workspace + migrations (user runs `dark-factory hints init` themselves)

`dark-factory hints init` is a TTY-interactive command — it refuses to use piped stdin (when `sys.stdin.isatty()` is false it writes an empty `PolicySection` regardless of piped input). The skill therefore does **not** try to drive it programmatically. Instead, produce an answer sheet and ask the user to run the CLI themselves in their terminal.

Render the sheet like this, substituting the answers from step 1b:

```
Run this in your terminal:

  dark-factory hints init

Type these answers at the prompts (press Enter for blanks):

  workspace scope:         <answer or blank>
  banned recursive:        <answer or blank — only prompted if scope set>
  migrations mode:         <answer or blank>
  migrations command:      <answer or blank — only prompted if mode non-blank + non-skip>

Then paste the CLI's output back here so I can verify the write.
```

If the user answered blank to **all four** of the workspace/migrations questions (1, 2, 3, 4), skip this step entirely — running `dark-factory hints init` would just write an empty `[policy]` section. Tell the user "no workspace/migrations policy to apply; moving on to consistency rules."

When the user pastes the CLI output:
- Expect `wrote baseline policy → .dark-factory/project-hints.toml`.
- If the output also includes `(empty policy — edit the file or run `dark-factory hints add` to extend it)`, they answered blank to everything or the CLI fell into its non-TTY fallback (running through a wrapper that lies about stdin). Ask them to confirm which it was.
- If the CLI reported a `ConfigError` (legacy hints present, already-populated policy without `--force`), surface the error and loop back to step 1a.

### Step 1d: Apply consistency rules via `dark-factory hints add`

For each consistency rule the user described in step 1b-5, hand off to **Mode 2 Steps 2a–2d** (parse → preview → apply). These are per-rule and each gets its own confirmation gate. The skill *does* drive `hints add` directly (it's non-interactive: all inputs via flags).

### Step 1e: Verify and Summarize

After all writes, run `dark-factory hints list`. Report:
- Path: `.dark-factory/project-hints.toml`
- Count of consistency rules written
- Any rules the user described but that couldn't be structured — surface these so nothing is silently dropped
- Next step: "run `dark-factory run <spec>` to enforce the policy on the next pipeline run."

---

## Mode 2: `add "<description>"` — Natural-Language Rule

Use when the user knows what they want to forbid but not the exact `--spec-pattern` / `--forbidden-env` / `--env-path` flags.

### Step 2a: Parse the Description

Identify these five fields. For each, point to the user's words and explain the mapping — don't invent values.

| Field | What it means | Example (`ban drizzle-kit push when migrations are required`) |
|---|---|---|
| `name` | Stable identifier (slug) — appears in error messages and novelty detection | `migrate-mode-vs-push-mode` |
| `spec_patterns` | Regexes matching spec text that *triggers* the rule | `\bmigration(s)?\b`, `\bmigrate\b`, `\bschema\s+change\b` |
| `forbidden_env` | Regexes that must NOT appear under `env_paths` when the rule triggers | `drizzle-kit\s+push` |
| `env_paths` | Dotted paths in `environment.toml` to inspect (≥1 required) | `commands.setup` |
| `description` | Human-readable rationale | `migrations require versioned migration files, not push-mode schema sync` |

If any field is unclear, ask. A wrong regex silently fails to match and the rule never fires — worse than no rule.

### Step 2b: Check for Duplicate Name

Run `dark-factory hints list`, look for an existing rule with the proposed `name`.

- **Exists:** show it. Ask: "rule `<name>` already exists (above). Overwrite with `--force`? The CLI replaces the whole stanza in-place — comments inside the old stanza are lost, but comments elsewhere in the file are preserved. If you're trying to *extend* the existing rule with more patterns, that's safer done by hand-editing the file in your editor, because `--force` drops the old stanza's contents entirely."
- If the user confirms, carry `--force` into the invocation.
- **Does not exist:** proceed.

### Step 2c: Preview

Render the rule as it will appear in the file:

```toml
[[policy.consistency_rules]]
name = "<name>"
spec_patterns = ["<pattern-1>", "<pattern-2>"]
forbidden_env = ["<pattern>"]
env_paths = ["<dotted-path>"]
description = "<description>"
```

Show the `dark-factory hints add` command that will run:

```
dark-factory hints add \
  --name '<name>' \
  --spec-pattern '<pattern-1>' \
  --spec-pattern '<pattern-2>' \
  --forbidden-env '<pattern>' \
  --env-path '<dotted-path>' \
  --description '<description>'
```

Wait for explicit confirmation. Accept case-insensitive `yes` or `y`; treat everything else (including silence or clarifying questions) as *not* confirmed — ask again rather than assume.

### Step 2d: Apply

Invoke the command via Bash. If the CLI exits non-zero, show stderr verbatim — do not paper over validation failures. Common causes:

- `rule '<name>' already exists` → user forgot `--force`, or name collision discovered after list-check.
- `legacy hints file(s) present but project-hints.toml is missing` → pre-flight check 3 should have caught this; something changed on disk between pre-flight and write. Tell the user to run `dark-factory init --migrate-hints`.
- TOML round-trip failure → a regex contains an unescaped character that breaks the parser; surface the message and re-ask that field.
- `at least one --env-path is required` → the CLI rejects rules without env paths (they'd be silently inert).

On success: run `dark-factory hints list` to confirm. Tell the user the rule is active on the next `dark-factory run <spec>`.

---

## Mode 3: `from-failure <run-id>` — Fix a Failing Run

Use when a pipeline run aborted at A1 (architecture consistency) and the user wants to codify the rule so the architect routes around the contradiction on retry.

### Step 3a: Locate the Failure Event

Read `.dark-factory/events.jsonl` as JSONL. Prefer a one-shot Python invocation over grep — `events.jsonl` is JSON with no guaranteed key ordering, and fixed-string greps are fragile:

```bash
python3 - <<'PY'
import json, sys
run_id = "<run-id>"
targets = []
with open(".dark-factory/events.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") != "REMEDIATION_TARGET":
            continue
        if rec.get("run_id") != run_id:
            continue
        targets.append(rec)
for t in targets:
    print(json.dumps(t, indent=2))
print(f"# {len(targets)} target(s)", file=sys.stderr)
PY
```

Substitute the actual run-id. If the result is zero targets:
- STOP. Tell the user: "no REMEDIATION_TARGET event for run `<run-id>` in events.jsonl. Either the run-id is wrong, the run predates C1 structured events (Sprint 5 / 2026-04+), or the failure is a class that doesn't emit a remediation target (implementer failures, oracle NO-SHIP). Use `dark-factory` event-inspection tooling or `git log .dark-factory/events.jsonl` to look up recent run-ids."

If multiple targets exist for the same run (one per rule violation), list them by `data.rule_or_check` and ask which to codify. If the user says "all", iterate 3b–3g per target.

### Step 3b: Route by Failure Class

Read `data.failure_class`:

| `failure_class` | Action |
|---|---|
| `consistency_violation` | Continue — Step 3c. |
| `spec_quality` | STOP. Tell the user: "this is a spec-quality failure. Run `/df-write-feedback <spec> <iteration>` or edit `.dark-factory/specs/<spec>.md` directly — project-hints can't fix a bad spec." |
| `precheck_finding` | STOP. Precheck findings are advisory; the suggested action depends on the finding type. Show `data.rationale` and `data.remediation_target_path`. If `data.suggested_diff` is non-empty, offer it as a paste-ready patch; if empty, work from `rationale` + `remediation_target_path` alone and ask the user to describe the fix they want to apply. |
| `stub_compile_error` / `proposal_typecheck_failure` | STOP. These are code-shaped, not policy-shaped. Show the event; suggest fixing the referenced file directly. |

### Step 3c: Check If the Rule Already Exists

Pull `data.rule_or_check` (the rule name). Run `dark-factory hints list`, look for a `[[policy.consistency_rules]]` with matching `name`.

- **Exists:** STOP with a warning. "Rule `<name>` is already defined in project-hints.toml but fired anyway. This means the rule's `spec_patterns` / `forbidden_env` / `env_paths` didn't match the actual spec/env content. To refine, follow these steps:
    1. Run `dark-factory hints list` and copy the current rule's patterns.
    2. Open the manifest at `data.remediation_target_path` and identify the offending env-side snippet.
    3. Open `.dark-factory/specs/<spec>.md` and identify the spec-side snippet that *should* have triggered the rule.
    4. Then re-invoke `/df-write-hints add \"<new description>\"` — the skill will detect the name collision and prompt for `--force`, and you can supply broadened regexes."
- **Does not exist:** proceed.

### Step 3d: Present the Event

Show the user:

1. **Rationale** — `data.rationale` verbatim. This is the `ConsistencyError.format()` output and explains the contradiction in natural language.
2. **Suggested TOML block** — `data.suggested_diff`. State explicitly: **this block is shape-only.** Today's `_consistency_suggested_diff` emits `name` + `description` only; it does NOT carry `spec_patterns`, `forbidden_env`, or `env_paths`, because `ConsistencyError` doesn't expose the source rule's regex payload. The user (or skill) must supply those three fields before the rule can fire.
3. **Manifest path to inspect** — `data.remediation_target_path` (the architecture manifest). Offer to read it to extract the offending env-side value.

### Step 3e: Fill in the Missing Fields

Ask for the three fields the event doesn't carry:

- **`spec_patterns`** — "what spec text should trigger this rule? Paste a phrase from the spec that *should have* been caught — I'll turn it into a regex (with word boundaries and escaping)."
- **`forbidden_env`** — "what env-side value should have been rejected? Paste the exact string from the failing manifest — I'll escape it to a regex. Read the manifest at `<remediation_target_path>` first if you don't have it handy."
- **`env_paths`** — "which `environment.toml` dotted path(s) should this rule inspect? For contradictions in the setup command (the common A1 case), answer `commands.setup`."

**Canonical shortcut for `rule_or_check == "migrate-mode-vs-push-mode"`:** if *and only if* the manifest at `remediation_target_path` actually contains `drizzle-kit push` (verify by reading it), the following defaults are safe to propose without further asking:
- `spec_patterns = ['\\bmigration(s)?\\b', '\\bmigrate\\b']`
- `forbidden_env = ['drizzle-kit\\s+push']`
- `env_paths = ['commands.setup']`

If the manifest uses a different push-mode tool (`prisma db push`, Knex `migrate:latest --env push`, etc.), adapt the `forbidden_env` regex to that exact tool and show the user what you did. Do NOT reuse the drizzle-specific regex against a Prisma project. For any other `rule_or_check` name, always ask — don't guess.

### Step 3f: Preview and Apply

Same as Mode 2 Steps 2c–2d: render the preview, show the CLI command, wait for confirmation, invoke, surface any CLI error verbatim.

### Step 3g: Suggest Re-Run

On success, print:

```
Rule '<name>' added to .dark-factory/project-hints.toml.

Re-run the spec to verify the architect now routes around the contradiction:

  dark-factory run <spec-name>

If the same rule fires again, the patterns didn't match the actual spec/env —
come back and refine with /df-write-hints add "<description>" and --force.
```

Extract `<spec-name>` from the event's top-level `spec` field (not `data.spec`; `emit_event` writes `record["spec"] = spec_name`).

---

## Output

The skill's net effect in each mode:

- **`init` mode:** `[policy.workspace]` + `[policy.migrations]` (written by the user's own `dark-factory hints init` invocation, skipped if all four answers were blank) plus N `[[policy.consistency_rules]]` (written by skill-driven `dark-factory hints add`).
- **`add` mode:** one new `[[policy.consistency_rules]]` stanza appended (or force-replaced).
- **`from-failure` mode:** one new `[[policy.consistency_rules]]` derived from the failure event, plus a suggested `dark-factory run <spec>` re-invocation.

Writes always flow through `dark-factory hints add` / `init`, which enforce atomic-write-and-round-trip-validate. If the CLI rejects a write, surface the error verbatim and help refine the input. The skill never writes the TOML directly.
