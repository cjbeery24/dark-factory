# Plan: Post-Implementation Gate Command Refresh

## Problem

`dark-factory init` detects gate commands from the project as it exists at init time. For
scaffolding specs — where the implementation step creates the project structure (e.g. a
pnpm workspace) — the project doesn't yet have the files that detection depends on. This
produces incorrect or absent gate commands in config.toml, and the user has to manually
fix them after scaffolding runs.

The fix: after every implementation step, re-run project detection and fill in any gate
command fields that are currently absent from the config. Fields the user has explicitly
set are never touched. config.toml is written first (atomically), then the in-memory
config object is updated so the current iteration uses the new commands without requiring
a restart.

---

## Design

### Trigger Point

`darkfactory/pipeline/iteration.py`, immediately after `_step_implementation` returns
(line ~117), before the pre-gate check. Runs once per full iteration — **not** per
pre-gate retry.

```
Step 3: Implementation      ← _step_implementation()
  [NEW] Step 3.1: Refresh gate commands if absent
Step 3.5: Pre-gate
Step 4: Quality gate
```

**Known limitation — pre-gate retry gap:** The pre-gate retry loop calls
`_step_implementation` again on retries (iteration.py lines ~138-142). The refresh does
not fire after those secondary calls. This is accepted: by the time a pre-gate retry
occurs, commands are either already filled in from step 3.1, or detection found nothing
(e.g. pnpm-workspace.yaml was created but root scripts weren't yet written to
package.json). In the latter case, the pre-gate retries in that iteration run with absent
commands and will likely fail, but the full quality gate in the next full iteration will
re-run detection and fill in the commands once the scripts exist. This is consistent with
the "filled in next iteration" fallback noted in the edge cases table.

### "Absent" vs "User-set" Distinction

`PipelineConfig` initialises all gate command fields to `""`. The config loader
(`darkfactory/config/loader.py`, line 54) only assigns a field when the corresponding key
is **present** in config.toml — it never writes empty-string defaults. So an empty string
in memory reliably means the key was absent from config.toml. Non-empty means the user
explicitly set it.

**Behavioral contract:** this heuristic depends on `init` never writing `key = ""` to
config.toml. This is currently enforced by `_write_config` in `init.py` (which only emits
a line when the value is truthy). The implementation must include a comment near
`refresh_gate_commands` documenting this contract so future changes to `init` preserve it.

**`test_cmd` dual-section note:** `test_cmd` is mapped under both `[core]` and `[gates]`
in the config loader. Write-back from this feature always targets `[gates]` (the canonical
section). If a user has `[core] test_cmd` but no `[gates] test_cmd`, the refresh will
write `[gates] test_cmd`; on the next `load_config`, `[gates]` wins (it is loaded after
`[core]`). This is correct and consistent, but documented here so it is not surprising.

### Refresh Logic

New function `refresh_gate_commands(project_dir, config, config_path)` in
`darkfactory/setup/detect.py`:

1. Run `detect_project(project_dir)` → `DetectionResult`
2. For each of the four gate command fields
   (`build_cmd`, `lint_cmd`, `typecheck_cmd`, `test_cmd`):
   - If `config.<field> == ""` **and** `detected.<field> != ""`: record the update
3. If any fields changed:
   - **Write TOML first** (`_patch_config_toml`) — atomic write so a crash cannot corrupt
     the on-disk config
   - **Then** update in-memory `config.<field>` for each changed field
   - Return the list of `(field, new_value)` pairs (for progress output)
4. If nothing changed, return empty list (no-op, no file write)

Write order (TOML before in-memory) is critical: if the process crashes after the file
write but before in-memory update, the next run resumes from a correct on-disk config. If
it crashes before the file write, neither is updated — same as today.

### TOML Write-back (`_patch_config_toml`)

**Dependency decision:** add `tomli-w` as a project dependency. It is ~500 lines with
zero transitive dependencies and is the standard write companion to `tomllib`. The
in-place string-patching alternative requires handling three structural cases — (a)
`[gates]` exists with the key, (b) `[gates]` exists without the key, (c) `[gates]` does
not exist at all — plus correct handling of multi-section insertion points. That
complexity is not worth owning when `tomli-w` already solves it correctly.

**Atomic write pattern:**
1. `tomllib.load` the current config to get the raw dict
2. Merge new values into `data["gates"]` (creating the section if absent)
3. Serialize with `tomli_w.dumps`
4. Write to a temp file in the same directory, then `os.replace()` into the target path

`os.replace()` is atomic on POSIX and effectively atomic on Windows (same volume). This
ensures a crash mid-write leaves the original config.toml intact.

**Comment preservation:** `tomllib` + `tomli-w` does not preserve comments. This is an
accepted limitation for a config file that is machine-generated by `dark-factory init`.
The warning `"Refreshed config.toml — hand-authored comments in [gates] may have been
removed."` is emitted **every time a write occurs**, regardless of whether comments were
actually present. This keeps the implementation simple (no comment-detection logic) and
ensures users are always aware the file was rewritten.

### In-memory Config Update

After `_patch_config_toml` succeeds, directly assign the updated fields on `config`.
`PipelineConfig` is a dataclass; fields are mutable. The pre-gate, quality gate,
reviewer, and oracle all receive `config` by reference in the same iteration and will
immediately see the updated commands. No restart required.

### Progress Output

When commands are refreshed, emit visible pipeline messages:

```
  Auto-detected gate commands from updated project structure:
    typecheck_cmd = "pnpm -r type-check"
    lint_cmd = "pnpm -r lint"
  Updated config.toml (hand-authored comments in [gates] may have been removed).
```

---

## Files to Change

| File | Change |
|------|--------|
| `darkfactory/setup/detect.py` | Add `refresh_gate_commands(project_dir, config, config_path)` and `_patch_config_toml(config_path, updates)` |
| `darkfactory/pipeline/iteration.py` | Call `refresh_gate_commands` after `_step_implementation`, log results via `_progress` |
| `pyproject.toml` | Add `tomli-w` to dependencies |

No other files need changes. The orchestrator, quality gate, and pre-gate all receive
`config` by reference and will see the updated commands automatically.

---

## Edge Cases

| Case | Behaviour |
|------|-----------|
| User has explicitly set a command | Field is non-empty → never overwritten |
| Detection finds nothing new | Returns `[]` → no file write, no log noise |
| Config.toml is read-only (e.g. CI mount) | `_patch_config_toml` catches `OSError` internally and logs a warning — it never re-raises; the caller always proceeds to apply in-memory updates regardless, so the current iteration uses the detected commands |
| Detection raises an exception | Catch, log warning, continue — commands stay absent (same as today) |
| Iteration 2+ after commands already filled | Commands non-empty → no-op |
| `pnpm-workspace.yaml` created but root scripts not yet present | Detection returns `""` for those scripts → no update; filled in the following iteration once scripts exist |
| Crash after TOML write, before in-memory update | On-disk config correct; in-memory config stale for current iteration only; resume picks up correct values from disk |
| Crash before TOML write | Neither updated → identical to no-refresh; detection runs again on next resume |
| Concurrent spec runs (parallel worktrees) | Each spec runs in an isolated worktree directory (enforced by the orchestrator's worktree manager before the iteration loop starts); each worktree has its own config.toml path → no shared-file write conflict |

---

## What This Does NOT Do

- Does not overwrite commands the user has set.
- Does not run detection at any point other than post-implementation (not at pipeline
  start, not post-quality-gate).
- Does not update `source_dirs`, `language`, or any non-gate field. `source_dirs` feeds
  directly into barrier construction, which is configured once before the loop starts in
  the orchestrator. Mutating `source_dirs` mid-loop would desync the in-memory barrier
  config from the updated config object; changing source dirs is a security-sensitive
  action that warrants deliberate user action via `dark-factory init --update`.
- Does not remove commands once set, even if detection would no longer find them.

---

## Out of Scope

- Updating `source_dirs` when the implementer creates new packages. Separate mechanism
  required.
- Detection of `scenario_test_cmd` — handled by the scenario format compat system.

---

## Tests

Unit tests in `tests/setup/test_detect_refresh.py`:

1. **No-op when all commands set** — config with all four fields non-empty; assert no
   file write, returned list is empty, config fields unchanged.
2. **Fills absent commands** — config with all fields empty; mock `detect_project`
   returning non-empty commands; assert returned list has all four, config fields updated,
   TOML written with correct values under `[gates]`.
3. **Partial fill** — two fields set, two absent; only absent ones updated in both
   in-memory config and TOML.
4. **Detection exception** — `detect_project` raises; assert config unchanged, no crash,
   warning logged.
5. **Read-only config.toml** — chmod 444 on a temp file; assert warning logged,
   in-memory config still updated (no OSError propagation).
6. **Round-trip TOML** — write a config.toml with existing keys and hand-authored
   comments; run refresh; re-read with `load_config`; assert patched values present and
   loadable. Verify comment-loss warning is emitted.
7. **Absent `[gates]` section** — config.toml has only `[core]`; refresh adds `[gates]`
   section with new commands; re-read with `load_config` and assert values correct.
8. **Atomic write crash safety** — mock `os.replace` to raise after temp file written;
   assert original config.toml is unchanged and no in-memory mutation occurred.
9. **`test_cmd` dual-section round-trip** — config.toml has `[core] test_cmd = "pytest"`,
   no `[gates]` section; `config.test_cmd` is non-empty after `load_config` → refresh
   is a no-op; assert no `[gates] test_cmd` written.
10. **Idempotency** — run `load_config` → `refresh_gate_commands` → `load_config` →
    `refresh_gate_commands`; assert second refresh is a no-op (no file write, empty list).
11. **Pre-gate retry non-re-run** — verify that `refresh_gate_commands` is called exactly
    once per full iteration even when the pre-gate retry loop calls `_step_implementation`
    multiple times (integration-level test or mock-based).
12. **`test_cmd` write path** — config.toml has no `test_cmd` in either `[core]` or
    `[gates]`; `config.test_cmd == ""`; detection returns `test_cmd = "pnpm -r run test"`;
    assert refresh writes `test_cmd` under `[gates]` (not `[core]`) and that a subsequent
    `load_config` round-trip returns the correct value via the `[gates]` wins-over-`[core]`
    loader precedence.
