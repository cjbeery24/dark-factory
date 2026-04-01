# Configuration Reference

Complete reference for all Dark Factory configuration variables.

> **See also:** [PROFILES.md](PROFILES.md) — per-spec overrides

---

## How Configuration Works

Dark Factory reads `.dark-factory/config.toml` at startup. If the file does not exist, all defaults apply — the pipeline is functional without any config file for simple projects.

### File location

```
your-project/
└── .dark-factory/
    └── config.toml
```

Run `dark-factory init` to generate a starter file with your detected language pre-filled.

### TOML section → PipelineConfig field mapping

Each TOML section maps to a group of `PipelineConfig` fields. For example, `[agents]` contains `max_turns`, `timeout`, etc. Nested tables use dotted notation: `[satisfaction.weights]`.

### Convention: `0 = unlimited`

Throughout this reference, `0` means "no limit / disabled" for numeric threshold fields. Explicitly noted per variable where applicable.

---

## `[core]` — Project identity

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `language` | string | `"node"` | Project language. Valid: `node`, `python`, `go`, `rust`, `unknown`. Controls which Docker image (`dark-factory-{language}`) runs Gates 1–4 and executable scenarios (C/D). **For multi-language projects, set each spec's language via a profile** — see [PROFILES.md](PROFILES.md). |
| `source_dirs` | list[string] | `["src"]` | Directories containing implementation source code. These dirs are masked (empty tmpfs) in the Scenario Architect's container to enforce the information barrier. For multi-language projects, set per profile so each spec masks only its own source tree. |
| `test_dir` | string | `"tests"` | Directory containing your project's self-tests. Runs inside `dark-factory-{language}`. For multi-language projects, set per profile. |
| `test_cmd` | string | `""` | Command to run your project's self-tests (Gate 4). Runs inside `dark-factory-{language}`. Example: `"pytest tests/ -v"` or `"npm test"`. For multi-language projects, set this per profile rather than globally. |
| `scenario_test_cmd` | string | `""` | Command to run **executable** blind scenario files (Format C/D — Gate 5). Use `{SCENARIOS_DIR}` as a placeholder. Example: `"pytest {SCENARIOS_DIR} -v"`. Not used for Format A (`*.scenarios.md`) or Format B (`*.journey.md`) — those are run automatically by the Python orchestrator using `dark-factory-python` and `dark-factory-playwright` respectively, with no command needed here. |
| `dark_factory_home` | string | `"dark-factory"` | Subdirectory within your project where the framework files (agents/, docker/, etc.) live. |

**Validation:** `language` must be one of `node`, `python`, `go`, `rust`, `unknown`. Any other value raises `ConfigError`.

**Example:**

```toml
[core]
language = "python"
source_dirs = ["src", "lib"]
test_dir = "tests"
test_cmd = "pytest tests/ -v"
scenario_test_cmd = "pytest {SCENARIOS_DIR} -v"   # only needed for executable scenarios (C/D)
                                                   # .scenarios.md and .journey.md run automatically
```

---

## `[agents]` — Container resource limits

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_turns` | int | `200` | Maximum number of Claude Code turns per agent invocation. Must be >= 1. |
| `timeout` | int | `600` | Maximum wall-clock seconds per agent invocation. Must be >= 1. |
| `retries` | int | `1` | Number of times to retry a failed agent invocation before escalating. Must be >= 0. Set to `0` to disable retries. |
| `memory` | string | `"4g"` | Docker memory limit for agent containers. Accepts Docker memory syntax: `"4g"`, `"2048m"`. |
| `cpus` | string | `"4"` | Docker CPU quota for agent containers. Accepts Docker CPU syntax: `"4"`, `"2.5"`. |
| `io_device` | string | `""` | Block device path for I/O throttling (e.g., `"/dev/sda"`). Leave empty to disable I/O limits. |
| `io_read_bps` | string | `""` | Read throughput limit (bytes per second). Requires `io_device` to be set. Example: `"100mb"`. |
| `io_write_bps` | string | `""` | Write throughput limit (bytes per second). Requires `io_device` to be set. Example: `"50mb"`. |

**Example:**

```toml
[agents]
max_turns = 300
timeout = 900
memory = "8g"
cpus = "8"
```

### `[agents.transient_retry]` — Transient failure retry

Transient failures are infrastructure-level failures that are not logic errors: container OOM, network timeouts, Docker daemon hiccups. These are automatically retried with exponential backoff.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max` | int | `2` | Maximum retry attempts for transient failures. `0` disables retry. Must be >= 0. |
| `base_s` | float | `30.0` | Base delay in seconds for the first retry. Subsequent retries use exponential backoff: `base_s * 2^(attempt-1)`. Must be >= 0. |

**Backoff schedule** (with `base_s = 30`):
- Attempt 1: 30s wait
- Attempt 2: 60s wait
- Attempt 3: 120s wait

**When to change:** Increase `max` and `base_s` in flaky network environments. Set `max = 0` to disable retry and fail fast (useful in CI to surface infrastructure issues quickly).

```toml
[agents.transient_retry]
max = 3
base_s = 60.0
```

### `[agents.strategy]` — Strategy switching

Strategy switching kicks in when the satisfaction score trajectory is classified as OSCILLATING — the implementation keeps fixing one area while breaking another across iterations. When enabled, the pipeline switches to an alternate implementation strategy (different model or approach) instead of retrying the same strategy.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `switch_enabled` | bool | `false` | Enable automatic strategy switching on oscillation detection. |
| `max_switches` | int | `2` | Maximum number of strategy switches per spec run. Must be >= 0. |

**When to change:** Enable `switch_enabled` on mature pipelines where oscillation patterns have been observed. Oscillation is diagnosed when the satisfaction score alternates (e.g., 70% → 85% → 68% → 83%) rather than converging. Leave disabled until you have 10+ pipeline runs and can observe oscillation patterns.

```toml
[agents.strategy]
switch_enabled = true
max_switches = 2
```

---

## `[gates]` — Quality gate configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `timeout` | int | `300` | Maximum seconds for the quality gate to complete (all gates combined). |
| `hard_gate_build` | bool | `false` | If `true`, a build failure immediately terminates the pipeline rather than passing build results to the reviewer. |
| `min_ship_pass_rate` | int | `80` | Minimum unweighted blind-scenario pass rate (0–100) required for SHIP. The Oracle cannot override this floor — it is enforced by the orchestrator. `0` = no floor. Waived when a `SCENARIOS-NOT-APPLICABLE.md` sentinel is present or when `validate_scenarios = "off"`. |
| `validate_scenarios` | string | `"strict"` | Scenario quality validation mode: `strict` halts on failure, `warn` logs and continues, `off` skips validation entirely. When `"off"`, `min_ship_pass_rate` enforcement is also waived. Note: sentinel structural validation (before implementation runs) is separate and always active when a sentinel file is present. |
| `scenario_min_files` | int | `3` | Minimum number of scenario test files required (checked in `strict` and `warn` modes). |
| `scenario_min_assertions_per_file` | int | `1` | Minimum number of real assertion calls per scenario file. |
| `mutation_cmd` | string | `""` | Command to run mutation testing (Gate 6, opt-in). Leave empty to skip. Example: `"npx stryker run"`. |
| `mutation_min_score` | int | `0` | Minimum mutation score (0–100) required when `mutation_cmd` is set. `0` = no floor. |
| `spec_sanitization_enabled` | bool | `true` | Whether to scan spec content for prompt injection patterns before passing it to agents. **Should almost never be set to `false`.** Disabling removes protection against malicious spec content manipulating agent behavior. |
| `build_cmd` | string | `""` | Command to run the project build (Gate 1). Runs inside `dark-factory-{language}`. Example: `"npm run build"` or `"cargo build"`. For Python projects, auto-detected from `requirements.txt` / `pyproject.toml` / `setup.py` by `dark-factory init`. For multi-language projects, set per profile. |
| `lint_cmd` | string | `""` | Command to run linting (Gate 2). Runs inside `dark-factory-{language}`. Example: `"ruff check ."` or `"golangci-lint run"`. For multi-language projects, set per profile. |
| `typecheck_cmd` | string | `""` | Command to run type checking (Gate 3). Runs inside `dark-factory-{language}`. Example: `"npx tsc --noEmit"` or `"mypy ."`. For multi-language projects, set per profile. |
| `pre_gate_retries` | int | `2` | Maximum times to run the pre-gate (Gates 1–3 only: Build + Lint + Typecheck) before the full quality gate. Pre-gate failures are fed back to the implementer as a tight feedback loop and **do not consume an iteration slot**. Set to `0` to disable pre-gate entirely. Must be >= 0. |
| `scenario_smoke_test` | bool | `true` | Run scenarios against the project *before* implementation to catch scenario bugs (crashes, tautological tests). Respects `validate_scenarios` — disabled when `validate_scenarios = "off"`. |
| `trajectory_stable_failure_threshold` | float | `0.7` | Fraction of failing tests that must be invariant across iterations to classify the failure set as "stable" (suggesting scenario-dominated plateau). Must be in (0.0, 1.0]. |

**Validation:** `min_ship_pass_rate` must be 0–100. `pre_gate_retries` must be >= 0. `timeout` must be >= 1. `trajectory_stable_failure_threshold` must be in (0.0, 1.0].

**Example:**

```toml
[gates]
min_ship_pass_rate = 90
build_cmd = "npm run build"
lint_cmd = "npm run lint"
typecheck_cmd = "npx tsc --noEmit"
```

---

## `[architecture]` — Architecture Lock

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable the Architecture Lock step. When enabled, an Architect agent produces a YAML manifest (file paths, data models, API contracts, dependencies) that both the Scenario Architect and Implementer consume. This ensures structural alignment and reduces wasted iterations from path/schema mismatches. |
| `max_turns` | int | `100` | Maximum Claude Code turns for the Architect agent. Must be >= 1. The Architect typically needs more turns than other agents because it reads the full spec and scans the existing codebase before producing the manifest. |

**How it works:** After spec validation (Step 1) and before scenario generation (Step 2), the pipeline invokes the Architect agent. The Architect reads the spec and existing codebase, then writes a structured YAML manifest to `.dark-factory/architecture/<spec>-manifest.yaml`. The manifest is validated (FR traceability, path validity, no duplicates) and then injected as context into the SA and Implementer prompts.

**Resume behavior:** The architecture lock uses its own file-based resume gate (manifest existence + `spec_hash` match). If the manifest exists and the spec hasn't changed, the step is skipped on resume. `--fresh` and `--regen-scenarios` both delete the manifest.

**Disabling:** Set `enabled = false` to skip the Architecture Lock step entirely. This is useful for simple specs where structural alignment is not an issue, or for debugging.

---

## `[autonomy]` — Human oversight level

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `level` | int | `3` | Autonomy level: `1` = Assisted (pause for human confirmation after SHIP), `2` = Supervised (auto-proceed, log decision, track metrics), `3` = Autonomous (full dark factory, no human in loop). Must be 1, 2, or 3. |
| `max_iterations` | int | `3` | Maximum implementation→gate→review→oracle cycles before escalation. Must be >= 1. |
| `max_total_agent_turns` | int | `0` | Hard cap on total Claude Code turns across all agents for one spec run. `0 = unlimited`. Useful as a cost safety net. |
| `dynamic_autonomy` | bool | `false` | When `true`, the orchestrator automatically adjusts autonomy level based on the spec's trust score. High-trust specs run at Level 3; lower-trust specs drop to Level 2 or 1. |
| `dynamic_iterations` | bool | `false` | When `true`, the orchestrator adjusts the iteration budget dynamically based on trust history. High-trust specs may get fewer iterations (early exit on SHIP); low-trust specs may get more. |

**Autonomy level details:**

| Level | Name | Behavior |
|-------|------|----------|
| 1 | Assisted | Pauses after Oracle SHIP for human confirmation before committing/pushing |
| 2 | Supervised | Auto-proceeds; logs SHIP decision; tracks pass-rate metrics |
| 3 | Autonomous | Full automation — no human touchpoint (default) |

**Progressive adoption:** Start at Level 1 for your first 5–10 specs. Move to Level 2 when first-pass rate exceeds 60%. Move to Level 3 when you trust spec quality and scenario coverage.

**`dynamic_autonomy` / `dynamic_iterations` warning:** Do not enable either of these before you have at least 20 pipeline runs. The trust scoring system needs history to make reliable adjustments — enabling dynamic modes on a new pipeline produces noisy, unreliable behavior.

**Validation:** `level` must be 1, 2, or 3. `max_iterations` must be >= 1.

---

## `[docker]` — Container networking

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `network` | string | `"df-pipeline"` | Docker network name for pipeline containers. |
| `egress_filtering` | bool | `true` | Whether to route agent traffic through a tinyproxy allowlist. Disable only if you have a specific conflict (e.g., corporate proxy). Disabling logs a startup warning on every run. |

---

## `[git]` — Git integration and auto-PR

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `remote` | string | `"origin"` | Git remote to push branches and PRs to. |
| `base_branch` | string | `"main"` | Branch that auto-created PRs target. |
| `auto_pr` | bool | `false` | Automatically create a PR after Oracle SHIP. Uses `gh pr create`. |
| `merge_queue` | bool | `false` | After creating a PR, add it to GitHub's merge queue. Requires merge queue enabled in repository settings. |
| `auto_merge_high_trust` | bool | `false` | Automatically merge (not just PR) specs with trust scores above the pipeline threshold. Requires `auto_pr = true`. Use only on mature pipelines with very high first-pass rates. |
| `skip_hooks` | bool | `false` | Pass `--no-verify` to git commands during auto-commit. Only enable if your hooks are incompatible with the pipeline environment. |

**Full `[git]` block example:**

```toml
[git]
remote = "origin"
base_branch = "main"
auto_pr = true
merge_queue = false
auto_merge_high_trust = false
skip_hooks = false
```

---

## `[satisfaction]` — Probabilistic scoring

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `min_score` | int | `0` | Minimum weighted satisfaction score (0–100) required for SHIP. `0` = disabled. When non-zero, an Oracle SHIP is rejected if the weighted score is below this floor — the Oracle cannot override it. |
| `multi_run_n` | int | `1` | Times to run each scenario file. `1` = single-run (standard). Set to `3` or `5` to detect flaky tests. |
| `multi_run_k` | int | `1` | Minimum successes required out of `multi_run_n` runs. Must be <= `multi_run_n`. Example: `n=3, k=2` means "pass if at least 2 of 3 runs succeed". |
| `plateau_threshold` | int | `5` | Percentage-point threshold for classifying satisfaction score movement as a "plateau". Scores changing by <= this amount per iteration are classified as PLATEAU. |

**K-of-N semantics example:** `n=5, k=3` means each scenario file runs 5 times; it is considered "passed" if at least 3 runs succeed. The pipeline early-exits when K successes or K-becomes-mathematically-impossible.

**Recommendation for critical paths:** `multi_run_n = 3`, `multi_run_k = 2`.

**Validation:** `multi_run_k` must be <= `multi_run_n`.

### `[satisfaction.weights]` — Category weights

Scenario files are grouped by their numeric prefix (`01-` = contract, `02-` = happy path, etc.). Per-category pass rates are weighted and averaged to produce the satisfaction score.

| Key | Type | Default | Category |
|-----|------|---------|----------|
| `contract` | int | `100` | 01- Contract tests |
| `happy_path` | int | `100` | 02- Happy Path tests |
| `edge_cases` | int | `80` | 03- Edge Case tests |
| `error_conditions` | int | `90` | 04- Error Condition tests |
| `state_sequence` | int | `80` | 05- State & Sequence tests |
| `security` | int | `120` | 06- Security tests (higher = more important) |
| `property_based` | int | `70` | 07- Property-Based tests |
| `adversarial` | int | `60` | 08- Adversarial tests |

Weights are relative — only their ratios matter. `security = 120` with `happy_path = 100` means security failures hurt the score 20% more than happy path failures.

**Example:**

```toml
[satisfaction.weights]
security = 200     # Double-weight security for a security-critical service
contract = 150
happy_path = 100
```

---

## `[cost]` — Budget and cost tracking

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `budget_usd` | float | `0.0` | Maximum USD budget for one spec run. **`0.0` = unlimited budget** (not zero budget). When a budget is set, the pipeline stops and escalates when cost exceeds the limit. |
| `per_input_token` | float | `0.000015` | USD cost per input token. Update this if you switch models. Default is Claude Sonnet pricing. |
| `per_output_token` | float | `0.000075` | USD cost per output token. Update this if you switch models. |
| `est_input_tokens_per_turn` | int | `4000` | Estimated input tokens per agent turn, used for pre-flight cost estimation. |
| `est_output_tokens_per_turn` | int | `2000` | Estimated output tokens per agent turn. |
| `correction_factor` | float | `1.0` | Multiplier applied to cost estimates to correct for systematic over/underestimates. Must be > 0. Calibrate using `dark-factory calibrate-cost`. |
| `alert_thresholds` | list[int] | `[50, 80, 100]` | Budget percentage thresholds at which warnings are emitted. Default: warn at 50%, 80%, and 100% of `budget_usd`. |

**Alert behavior:** When budget is exceeded (100% threshold), the pipeline stops and generates an escalation report. The pipeline does not hard-kill a running agent mid-turn — it checks the budget between turns.

**Recommendation for production pipelines:** Always set `budget_usd`. A typical spec costs $0.50–$3.00 with Claude Sonnet. Set a ceiling at 3–5× your average to catch runaway specs without blocking normal runs.

```toml
[cost]
budget_usd = 10.0
per_input_token = 0.000015
per_output_token = 0.000075
alert_thresholds = [50, 80, 100]
```

---

## `[anomaly]` — Anomaly detection thresholds

The anomaly detector compares current run metrics against a rolling baseline. When a metric exceeds its threshold, the pipeline flags the run as anomalous and may escalate.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_agent_turns` | int | `150` | Maximum turns any single agent invocation may use before flagging as anomalous. |
| `max_spec_turns` | int | `800` | Maximum total turns across all agents for one spec run. |
| `max_spec_wall_s` | int | `7200` | Maximum wall-clock seconds (2 hours) for one spec run. |
| `max_files_written` | int | `50` | Maximum files written by the Implementer in one invocation. Large numbers may indicate runaway code generation. |
| `baseline_window` | int | `20` | Number of recent runs used to compute the rolling baseline for comparison. |

**When to change:** Relax thresholds for large, complex specs that legitimately need more turns or time. `max_agent_turns = 300` and `max_spec_wall_s = 14400` (4 hours) are reasonable for very large implementation tasks.

---

## `[trust]` — Trust scoring

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `min_history` | int | `5` | Minimum number of pipeline runs required before the trust scoring system produces reliable scores. Specs run before this threshold use default trust values. |

---

## `[plan]` — Multi-spec orchestration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_concurrent` | int | `3` | Maximum specs that can run concurrently in a plan. Higher values use more Docker resources. |
| `token_budget` | int | `0` | Total Claude Code token budget for the entire plan run. `0 = unlimited`. |
| `max_wall_time_s` | int | `0` | Maximum wall-clock seconds for the entire plan run. `0 = unlimited`. |
| `conflict_strategy` | string | `"abort"` | What to do when two concurrent specs would write to the same file: `abort` = fail the later spec; `serialize` = queue it to run after the first finishes; `warn` = log the conflict and continue. |
| `worktree_cleanup_on_ship` | bool | `true` | Delete the git worktree after a spec SHIPs successfully. |
| `worktree_cleanup_on_fail` | bool | `false` | Delete the git worktree if a spec fails or escalates. Set to `false` to preserve the worktree for debugging. |

**`conflict_strategy` details:**

| Value | When to use |
|-------|-------------|
| `abort` | Default. Safest — surface conflicts immediately. |
| `serialize` | When specs have partial dependency: each must finish before the next starts. |
| `warn` | When you've reviewed the conflict and know it's safe (rare). |

**Validation:** `conflict_strategy` must be `abort`, `serialize`, or `warn`.

---

## `[notifications]` — Webhooks and shell hooks

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `slack_webhook_url` | string | `""` | Slack incoming webhook URL. Leave empty to disable. |
| `webhook_url` | string | `""` | Generic webhook URL (receives JSON POST). Leave empty to disable. |
| `notify_on_spec_shipped` | string | `""` | Shell command when a spec ships. |
| `notify_on_spec_failed` | string | `""` | Shell command when a spec fails after retries. |
| `notify_on_spec_escalated` | string | `""` | Shell command when a spec is escalated to human review. |
| `notify_on_spec_hold` | string | `""` | Shell command when a spec is paused awaiting human confirmation (autonomy=1). |
| `notify_on_plan_complete` | string | `""` | Shell command when a full plan finishes. |
| `notify_on_pr_created` | string | `""` | Shell command when a PR is automatically created. |
| `notify_on_budget_alert` | string | `""` | Shell command when cost budget threshold is crossed. |
| `notify_on_anomaly` | string | `""` | Shell command when an anomaly is detected (runaway agent, excessive turns, etc.). |
| `notify_on_regression_blocked` | string | `""` | Shell command when regression tests block a ship. |
| `notify_on_canary_failed` | string | `""` | Shell command when a canary deployment fails. |
| `notify_on_rollback` | string | `""` | Shell command when a rollback is triggered. |
| `notify_on_spec_start` | string | `""` | Shell command when the pipeline begins working on a spec. Variables: `{spec}`, `{iteration}`. |
| `notify_on_scenarios_generated` | string | `""` | Shell command after scenarios are written and implementation is about to start. Variables: `{spec}`. |
| `notify_on_gate_complete` | string | `""` | Shell command after the quality gate finishes. Variables: `{spec}`, `{iteration}`, `{pass_rate}`, `{status}` (`PASS` or `FAIL`). |
| `notify_on_no_ship` | string | `""` | Shell command when Oracle says NO-SHIP and the pipeline iterates. Variables: `{spec}`, `{iteration}`, `{pass_rate}`. |

**Events fired:** `shipped`, `escalated`, `failed`, `pr_created`, `regression_blocked`, `start`, `scenarios_generated`, `gate_pass`, `gate_fail`, `no_ship`.

All notifications are fire-and-forget — failures never block the pipeline.

### Shell hook variables

Shell command strings support `{key}` substitution:

| Variable | Available in | Description |
|----------|-------------|-------------|
| `{spec}` | all hooks | Spec name |
| `{iterations}` | `shipped`, `escalated` | Number of pipeline iterations |
| `{reason}` | `failed`, `canary_failed`, `rollback` | Failure or rollback reason |
| `{trust}` | `hold` | Trust score at time of hold |
| `{pr_url}` | `pr_created` | Pull request URL |
| `{percent}` | `budget_alert` | Budget consumed (e.g. `81.2`) |
| `{spent}` | `budget_alert` | Amount spent in USD |
| `{budget}` | `budget_alert` | Total budget in USD |
| `{anomaly_type}` | `anomaly` | Type of anomaly detected |
| `{score}` | `anomaly` | Anomaly severity score |
| `{passed}` | `regression_blocked` | Passing regression test count |
| `{failed}` | `regression_blocked` | Failing regression test count |
| `{layer}` | `rollback` | Rollback layer triggered |
| `{shipped}` | `plan_complete` | Count of shipped specs |
| `{failed}` | `plan_complete` | Count of failed specs |
| `{skipped}` | `plan_complete` | Count of skipped specs |

### Examples

```toml
[notifications]
# Slack
slack_webhook_url = "https://hooks.slack.com/services/T.../B.../..."

# Generic JSON webhook
webhook_url = "https://your-service.com/dark-factory-events"

# Pushover (https://pushover.net) — mobile push notifications
notify_on_spec_shipped   = "curl -s --form-string 'token=APP_TOKEN' --form-string 'user=USER_KEY' --form-string 'message=✓ Shipped: {spec} ({iterations} iterations)' https://api.pushover.net/1/messages.json"
notify_on_spec_failed    = "curl -s --form-string 'token=APP_TOKEN' --form-string 'user=USER_KEY' --form-string 'message=✗ Failed: {spec} — {reason}' https://api.pushover.net/1/messages.json"
notify_on_spec_escalated = "curl -s --form-string 'token=APP_TOKEN' --form-string 'user=USER_KEY' --form-string 'message=⚠ Escalated: {spec} after {iterations} iterations' https://api.pushover.net/1/messages.json"
notify_on_plan_complete  = "curl -s --form-string 'token=APP_TOKEN' --form-string 'user=USER_KEY' --form-string 'message=Plan complete — {shipped} shipped, {failed} failed' https://api.pushover.net/1/messages.json"
```

---

## `[regression]` — Cross-spec regression testing

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Enable regression testing (runs previously-shipped scenarios after each SHIP). |
| `timeout` | int | `300` | Maximum seconds for the full regression suite. |
| `policy` | string | `"block"` | What to do when a regression is detected: `block` halts the pipeline; `warn` logs and continues. |
| `on_ship` | bool | `false` | Run regression automatically after every Oracle SHIP (equivalent to always passing `--regression`). |

**Validation:** `policy` must be `block` or `warn`.

---

## `[implementer]` — Implementer write permissions

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `write_dirs` | string | `""` | **Security-relevant.** Colon-separated list of additional directories the Implementer may write to. Default empty = Implementer can only write within `source_dirs` and `test_dir`. |

**When to add extra paths:** If your spec requires generating files outside `source_dirs` (e.g., writing to `scripts/` or `config/`), add those paths here.

**Information barrier implication:** Directories listed in `write_dirs` should not contain sensitive content. The Implementer runs in a container where `.dark-factory/scenarios/` is masked, but `write_dirs` paths are bind-mounted read-write. Do not add `.dark-factory/` or any barrier-protected path.

```toml
[implementer]
write_dirs = "scripts:config"
```

---

## `[test_server]` — HTTP and browser scenario servers

Required when your spec uses Format A (HTTP behavioral scenarios, `*.scenarios.md`) or Format B (browser journey scenarios, `*.journey.md`).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `base_url` | string | `""` | Base URL for HTTP scenario runner (Format A). Example: `"http://localhost:8000"`. Overridable per spec via profile — useful when different specs start servers on different ports. |
| `server_cmd` | string | `""` | Command to start the HTTP test server. Omit if using an external/CI-managed server. Overridable per spec via profile. |
| `setup_cmd` | string | `""` | Command to run before the server starts (e.g., database migrations). Runs in the same container with access to service sidecars. Only executes when HTTP or browser scenarios are present. |
| `startup_timeout` | int | `30` | Seconds to wait for the HTTP server to become ready (polls `health_path`). Must be >= 1. Overridable per spec via profile. |
| `health_path` | string | `"/health"` | HTTP path polled to determine if the server is ready. Overridable per spec via profile. |
| `browser_base_url` | string | `""` | Base URL for browser journey runner (Format B). Example: `"http://localhost:3000"`. Overridable per spec via profile. |
| `browser_server_cmd` | string | `""` | Command to start the browser test server. If identical to `server_cmd`, one server instance is reused for both formats. Overridable per spec via profile. |
| `browser_startup_timeout` | int | `60` | Seconds to wait for the browser server to become ready. Must be >= 1. Overridable per spec via profile. |

**Validation:** `startup_timeout` and `browser_startup_timeout` must be >= 1.

**Example:**

```toml
[test_server]
base_url = "http://localhost:8000"
setup_cmd = "python manage.py migrate"
server_cmd = "uvicorn myapp:app --port 8000"
startup_timeout = 30
health_path = "/health"

browser_base_url = "http://localhost:3000"
browser_server_cmd = "npm start"
browser_startup_timeout = 60
```

---

## `[[services]]` — Service sidecar containers

Required when your test server depends on backing services (databases, caches, message queues) that must be running before the server can start. Services are started on the Docker execution network before the test server and torn down after it.

Each `[[services]]` entry defines one container:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `name` | string | **required** | Docker network alias. Must be DNS-safe (lowercase alphanumeric + hyphens). The server container reaches this service via `name:port` (e.g., `postgres:5432`). |
| `image` | string | **required** | Docker image to run (e.g., `"postgres:16-alpine"`, `"redis:7-alpine"`). |
| `env` | inline table | `{}` | Environment variables passed to the service container. |
| `health_cmd` | string | `""` | Shell command run via `docker exec` to check readiness (e.g., `"pg_isready -U postgres"`). Polled every 1s until exit 0. |
| `health_timeout` | int | `30` | Seconds to wait for `health_cmd` to succeed before failing the gate. |
| `inject_env` | inline table | `{}` | Environment variables injected into the **server** container (not this service). Use this to pass connection strings like `DATABASE_URL`. |

**Validation:** `name` must be non-empty and DNS-safe. `image` must be non-empty. If two services define the same `inject_env` key, the pipeline raises an error.

**Example — API with Postgres:**

```toml
[test_server]
setup_cmd = "cd apps/api && pnpm drizzle-kit generate"
server_cmd = "cd apps/api && node dist/index.js"
base_url = "http://localhost:3000"
health_path = "/health"

[[services]]
name = "postgres"
image = "postgres:16-alpine"
health_cmd = "pg_isready -U postgres"
health_timeout = 30
env = { POSTGRES_USER = "postgres", POSTGRES_PASSWORD = "postgres", POSTGRES_DB = "testdb" }
inject_env = { DATABASE_URL = "postgres://postgres:postgres@postgres:5432/testdb" }
```

**Example — API with Postgres and Redis:**

```toml
[[services]]
name = "postgres"
image = "postgres:16-alpine"
health_cmd = "pg_isready -U postgres"
env = { POSTGRES_PASSWORD = "postgres", POSTGRES_DB = "testdb" }
inject_env = { DATABASE_URL = "postgres://postgres:postgres@postgres:5432/testdb" }

[[services]]
name = "redis"
image = "redis:7-alpine"
health_cmd = "redis-cli ping"
inject_env = { REDIS_URL = "redis://redis:6379" }
```

**How it works:** Services start sequentially in TOML order. Each must pass its health check before the next starts. All `inject_env` values are merged and passed as environment variables to the test server container. On cleanup (or crash), containers are removed via `df-run-id` label matching.

---

## `[deploy]` — Deployment configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `preset` | string | `"none"` | Deployment preset: `none` (manual), `docker-compose`, `k8s-argo`. Presets fill in common defaults automatically. |
| `health_check_strategy` | string | `"http"` | How to verify the deployment is healthy: `http` (poll HTTP endpoint), `tcp` (TCP port check), `docker` (container health status), `command` (run a command). |
| `health_check_url` | string | `""` | URL for HTTP health check strategy. |
| `health_check_host` | string | `""` | Host for TCP health check strategy. |
| `health_check_port` | int | `0` | Port for TCP health check strategy. |
| `health_check_container` | string | `""` | Container name for docker health check strategy. |
| `twins_mode` | string | `"manual"` | Digital twins mode: `manual` (you define twins explicitly in `[[twins]]`) or `auto` (orchestrator auto-discovers dependencies). See [DIGITAL-TWINS.md](DIGITAL-TWINS.md). |
| `cmd` | string | `""` | Command to deploy the application. Validated as a safe script path unless `cmd_allow_shell = true`. |
| `base_url` | string | `""` | Base URL of the deployed application (used by the Evaluator agent). |
| `health_endpoint` | string | `"/health"` | Health check endpoint path on the deployed application. |
| `health_timeout` | int | `60` | Seconds to wait for the deployment health check to succeed. |
| `teardown_cmd` | string | `""` | Command to tear down the deployment after evaluation. Validated as a safe script path unless `cmd_allow_shell = true`. |
| `cmd_allow_shell` | bool | `false` | Allow `cmd` and `teardown_cmd` to be shell strings (legacy escape hatch). See note below. |
| `compose_file` | string | `""` | Path to docker-compose file (used by docker-compose preset). |
| `env` | table | `{}` | Environment variables to pass to the deployment command. |
| `ttl_seconds` | int | `3600` | Maximum time (seconds) the ephemeral deployment may live. |
| `rollback_cmd` | string | `""` | Command to roll back the deployment. Validated as a safe script path. |
| `rollback_timeout` | int | `120` | Seconds allowed for rollback to complete. |
| `rollback_window_seconds` | int | `3600` | Time window (seconds) during which an automatic rollback may be triggered. |
| `rollback_poll_interval` | int | `60` | Seconds between health check polls during the rollback window. |
| `rollback_monitor_cmd` | string | `""` | Command to check whether rollback is complete. Validated as a safe script path. |

**Validation:** `preset` must be `none`, `docker-compose`, or `k8s-argo`. `health_check_strategy` must be `http`, `tcp`, `docker`, or `command`.

**`cmd_allow_shell` note:** By default, `cmd`, `teardown_cmd`, and related command fields are validated as safe script paths at startup by `validate()`. A relative path, shell string (e.g., `"./deploy.sh && echo done"`), or pipe will fail this check with a `ConfigError`. Set `cmd_allow_shell = true` to bypass validation for `cmd` and `teardown_cmd` only (the other command fields are always validated). This is a legacy escape hatch — prefer using a wrapper script.

**Example:**

```toml
[deploy]
preset = "docker-compose"
compose_file = "docker-compose.prod.yml"
health_check_strategy = "http"
health_check_url = "http://localhost:8080/health"
health_timeout = 120
cmd = "scripts/deploy.sh"
teardown_cmd = "scripts/teardown.sh"

[deploy.env]
APP_ENV = "staging"
LOG_LEVEL = "info"
```

---

## `[canary]` — Canary deployments

Canary deployment changes the post-SHIP flow: instead of deploying directly, traffic is shifted gradually through configurable stages with metric evaluation between stages.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Enable canary deployment mode. |
| `shift_cmd` | string | `""` | Command to shift traffic to the new version at a given percentage. |
| `rollback_cmd` | string | `""` | **Required when `enabled = true`.** Command to roll back traffic to the previous version. Validated as a safe script path. |
| `lock_timeout` | int | `600` | Seconds the canary lock is held before automatically releasing. |

**Validation:** `canary_rollback_cmd` is required when `canary_enabled = true`. `canary_stages[*].percent` must be monotonically increasing.

### `[[canary.stages]]` — Canary rollout stages

Defines the traffic-shifting progression. Each stage is a TOML array-of-tables entry.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `percent` | int | Yes | Traffic percentage to shift to the new version. Must be 1–100. Stages must be in strictly increasing order. |
| `soak_seconds` | int | Yes | Seconds to hold at this traffic percentage and evaluate metrics before advancing. Must be >= 0. |
| `name` | string | No | Human-readable stage name. Auto-generated as `"canary-{percent}pct"` if omitted. |

### `[[canary.metrics]]` — Canary metric thresholds

Metrics evaluated at each stage. If any metric breaches its threshold, the canary rolls back.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Metric name (used in logs and rollback reason). |
| `max_value` | float | Conditional | Maximum acceptable value. At least one of `max_value` or `min_value` must be set. |
| `min_value` | float | Conditional | Minimum acceptable value. |
| `metric_cmd` | string | No | Command to fetch the current metric value (output must be a number). |

**Full canary example:**

```toml
[canary]
enabled = true
shift_cmd = "scripts/shift-traffic.sh"
rollback_cmd = "scripts/rollback-traffic.sh"
lock_timeout = 900

[[canary.stages]]
name = "canary-10"
percent = 10
soak_seconds = 300   # 5 minutes at 10%

[[canary.stages]]
name = "canary-50"
percent = 50
soak_seconds = 600   # 10 minutes at 50%

[[canary.stages]]
name = "full"
percent = 100
soak_seconds = 0

[[canary.metrics]]
name = "error_rate"
max_value = 0.01     # Rollback if error rate exceeds 1%
metric_cmd = "scripts/get-error-rate.sh"

[[canary.metrics]]
name = "p99_latency_ms"
max_value = 500.0
metric_cmd = "scripts/get-p99-latency.sh"
```

---

## `[flags]` — Feature flags

Wires up any feature flag system. The pipeline creates a flag before implementation, enables it after SHIP, and disables it on failure.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `create_cmd` | string | `""` | Command to create the feature flag. Validated as a safe script path. |
| `enable_cmd` | string | `""` | Command to enable the feature flag post-SHIP. Validated as a safe script path. |
| `disable_cmd` | string | `""` | Command to disable the feature flag on failure. Validated as a safe script path. |
| `check_cmd` | string | `""` | Command to check whether the feature flag is enabled. Validated as a safe script path. |

**Lifecycle:** Flag created at pipeline start → implementation runs with flag disabled → after Oracle SHIP, flag enabled → on failure or NO-SHIP, flag disabled.

---

## `[migration]` — Migration safety checks

Detects potentially destructive database migration operations before deployment.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `check_enabled` | bool | `false` | Enable migration safety scanning. |
| `check_policy` | string | `"warn"` | `warn` = log destructive operations and continue; `block` = halt the pipeline if destructive operations are found. |
| `dirs` | list[string] | `["migrations/", "db/migrate/", "alembic/versions/"]` | Directories to scan for migration files. |
| `extra_patterns` | table | `{}` | Additional regex patterns to detect. Keys are pattern names; values are lists of regex strings. |

**What it detects:** DROP TABLE, DROP COLUMN, TRUNCATE, DELETE without WHERE, and other schema-destructive patterns.

**Validation:** `check_policy` must be `warn` or `block`.

**Example with custom patterns:**

```toml
[migration]
check_enabled = true
check_policy = "block"
dirs = ["migrations/", "db/"]

[migration.extra_patterns]
unsafe_alter = ["ALTER TABLE .* DROP", "ALTER TABLE .* MODIFY.*NOT NULL"]
```

---

## `[model]` — Model selection

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `id` | string | `"claude-sonnet-4-20250514"` | Default Claude model ID used by all agents that don't have a per-role override. |

### `[model.ids]` — Per-role model overrides

Individual agents can use different models. Keys are role names; values are Claude model IDs.

| Role name | Agent |
|-----------|-------|
| `spec-validator` | Spec Validator |
| `scenario-architect` | Scenario Architect |
| `implementer` | Implementer |
| `quality-gate` | Quality Gate |
| `reviewer` | Reviewer |
| `oracle` | Oracle |

**When to use per-role overrides:** Use a cheaper/faster model for agents that do not require deep reasoning (spec-validator, reviewer pre-pass). Use the most capable model for the implementer.

**Recommendation:** Haiku for `spec-validator` and `reviewer`; Sonnet for `implementer` and `oracle`.

```toml
[model]
id = "claude-sonnet-4-20250514"

[model.ids]
spec-validator = "claude-haiku-4-5-20251001"
reviewer = "claude-haiku-4-5-20251001"
implementer = "claude-sonnet-4-20250514"
oracle = "claude-sonnet-4-20250514"
```

---

## `[scale]` — Scale validation

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `concurrency` | int | `4` | Number of concurrent workers used in scale validation scenarios. Must be >= 1. |

---

## `[auth]` — Authentication (informational)

> **Note:** Actual auth tokens must be passed via environment variables (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`), not in `config.toml`. The `[auth]` section contains only informational/expiry metadata.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `token_expires` | string | `""` | Expiry date of your OAuth token in `YYYY-MM-DD` format. The pipeline warns 30 days before this date and hard-blocks on/after it. Example: `"2027-03-15"`. |

```toml
[auth]
token_expires = "2027-03-15"
```

---

## `[[twins]]` — Digital twins

Digital twins proxy real external services for use in testing. Each entry in the `[[twins]]` array defines one twin.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Identifier for this twin service. Used in logs and as a key in the twin registry. |
| `upstream` | string | Yes | URL of the real service to proxy/record. |
| `port` | int | No | Local port for the replay server. `0` = auto-assign. |
| `record_file` | string | No | Path to the JSONL file where interactions are recorded. Defaults to `.dark-factory/twins/{name}.jsonl`. |

**`twins_mode`** (set in `[deploy]`): `manual` means the twins listed in `[[twins]]` are used as-is. `auto` means the orchestrator auto-discovers dependency URLs in the spec and creates twins automatically.

See [DIGITAL-TWINS.md](DIGITAL-TWINS.md) for setup and usage.

```toml
[deploy]
twins_mode = "manual"

[[twins]]
name = "payments-api"
upstream = "https://api.payments.example.com"
port = 9001
record_file = ".dark-factory/twins/payments.jsonl"

[[twins]]
name = "user-service"
upstream = "https://users.internal.example.com"
port = 9002
```

---

## `[profile.*]` — Named profiles

Named profiles are reusable configuration presets that can be applied to individual specs. See [PROFILES.md](PROFILES.md) for full documentation.

```toml
[profile.browser]
language = "node"
browser_base_url = "http://localhost:3000"
browser_server_cmd = "npm start"
browser_startup_timeout = 90

[profile.go-service]
language = "go"
lint_cmd = "golangci-lint run ./internal/myservice/..."
test_cmd = "go test ./internal/myservice/... -v"
```

---

## Config validation errors reference

Dark Factory validates all config values at startup. If your config file triggers a `ConfigError`, the pipeline will not start. Common errors:

| Error | Cause | Fix |
|-------|-------|-----|
| `language must be node/python/go/rust` | Invalid `[core] language` value | Use one of the valid values |
| `autonomy_level must be 1, 2, or 3` | Invalid `[autonomy] level` | Use 1, 2, or 3 |
| `min_ship_pass_rate must be 0-100` | Out-of-range value | Use 0–100 |
| `min_satisfaction_score must be 0-100` | Out-of-range value | Use 0–100 |
| `max_iterations must be >= 1` | `[autonomy] max_iterations = 0` | Use >= 1 |
| `agent_max_turns must be >= 1` | `[agents] max_turns = 0` | Use >= 1 |
| `agent_timeout must be >= 1` | `[agents] timeout = 0` | Use >= 1 |
| `agent_retries must be >= 0` | Negative retries | Use >= 0 |
| `conflict_strategy must be abort/serialize/warn` | Invalid value | Use one of the valid values |
| `regression_policy must be block/warn` | Invalid value | Use `block` or `warn` |
| `multi_run_k cannot exceed multi_run_n` | k > n | Reduce k or increase n |
| `canary_rollback_cmd is required when canary_enabled=true` | Missing rollback command | Set `[canary] rollback_cmd` |
| `canary_stages percent must be monotonically increasing` | Stages out of order | Sort stages by percent ascending |
| `migration_check_policy must be 'warn' or 'block'` | Invalid value | Use `warn` or `block` |
| `transient_retry_max must be >= 0` | Negative value | Use >= 0 |
| `transient_retry_base_s must be >= 0` | Negative value | Use >= 0 |
| `max_pre_gate_retries must be >= 0` | Negative value | Use >= 0 |
| `gate_timeout must be >= 1` | Zero or negative | Use >= 1 |
| `strategy_max_switches must be >= 0` | Negative value | Use >= 0 |
| `cost_correction_factor must be > 0` | Zero or negative | Use a positive value |
| `deploy_preset must be none/docker-compose/k8s-argo` | Invalid value | Use one of the valid values |
| `health_check_strategy must be http/tcp/docker/command` | Invalid value | Use one of the valid values |
| `twins_mode must be manual/auto` | Invalid value | Use `manual` or `auto` |
| `scale_concurrency must be >= 1` | Zero or negative | Use >= 1 |
| `server_startup_timeout must be >= 1` | Zero or negative | Use >= 1 |
| `browser_startup_timeout must be >= 1` | Zero or negative | Use >= 1 |

**Command path validation errors** (from `validate()`, not `__post_init__`):

The following fields are validated as safe script paths when a project directory is known: `deploy_cmd`, `deploy_teardown_cmd`, `deploy_rollback_cmd`, `canary_shift_cmd`, `canary_rollback_cmd`, `flag_create_cmd`, `flag_enable_cmd`, `flag_disable_cmd`, `flag_check_cmd`, `rollback_monitor_cmd`.

A `ConfigError` from `validate()` means one of these fields contains a relative path, a shell string (pipe, `&&`, etc.), or a path that doesn't exist. Fix: use an absolute path to a wrapper script, or set `[deploy] cmd_allow_shell = true` for `deploy_cmd`/`deploy_teardown_cmd` only.
