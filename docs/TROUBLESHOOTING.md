# Troubleshooting Guide

Common errors and their fixes, ordered by frequency.

## Setup & Prerequisites

### `Docker daemon not running`

Docker Desktop is not started.

```bash
# macOS
open -a Docker

# Linux
sudo systemctl start docker
```

### `claude: command not found`

Claude Code is not installed.

```bash
npm install -g @anthropic-ai/claude-code
```

### `git: command not found` or `Not inside a git repository`

```bash
# Install git
# macOS:
xcode-select --install
# Linux:
sudo apt install git

# Initialize repo if needed
git init
```

### `Python 3.x detected, but 3.11+ required`

Dark Factory requires Python 3.11+ for `tomllib` support.

```bash
# Check your version
python3 --version

# Upgrade: https://www.python.org/downloads/
```

## Pipeline Errors

### `No spec file found`

Wrong path or missing `.md` extension.

```bash
# Correct format:
dark-factory run my-spec-name          # Looks for .dark-factory/specs/my-spec-name.md
dark-factory run 01-health-endpoint    # Not the full path, just the name
```

### `spec sanitization failed`

The spec contains patterns that look like prompt injection. The sanitizer flags lines that could manipulate agent behavior.

```bash
# See which line triggered:
dark-factory validate my-spec

# If it's a false positive, disable sanitization for this spec:
# Add to config.toml:
# [gates]
# spec_sanitization_enabled = false
```

### `build failure at iteration 1`

The `[gates] build_cmd` fails outside the pipeline too. Check it works on its own:

```bash
# Run your build command directly:
npm run build    # or: cargo build, go build ./..., etc.

# Common causes:
# - Missing dependencies: npm install / pip install -e .
# - Wrong working directory
# - Stale lockfile
```

### `budget exceeded`

`[cost] budget_usd` is too low for the spec complexity. Note: `budget_usd = 0` means **unlimited** — it does not set a zero-dollar budget.

```bash
# Check current budget in config.toml:
grep budget .dark-factory/config.toml

# Options:
# 1. Increase budget: [cost] budget_usd = 20.0
# 2. Reduce iterations: [autonomy] max_iterations = 2
# 3. Decompose the spec into smaller pieces
# 4. Run cost calibration: dark-factory calibrate-cost
```

## Docker Errors

### `Docker image not found` / `Docker image dark-factory-python not built`

Images haven't been built or have the wrong tag.

```bash
# Rebuild images:
dark-factory init                          # Validates and suggests fixes
bash docker/build-images.sh python         # Or: node, go, rust

# Verify:
docker images | grep dark-factory
```

### `dark-factory-playwright Docker image is not available`

Your spec has `*.journey.md` browser scenario files but the Playwright image hasn't been built. The playwright image is built automatically with any `dark-factory build` invocation:

```bash
dark-factory build            # auto-detects language from config
dark-factory build python     # or: node, go, rust, all
```

### `permission denied: .dark-factory/`

Directory owned by root (common after Docker operations).

```bash
sudo chown -R $(whoami) .dark-factory/
```

## State & Lock Errors

### `lock acquisition failed`

A stale lock from a previously killed process.

```bash
# Remove stale locks:
rm -f .dark-factory/state/*.lock

# Verify no other pipeline is running:
ps aux | grep dark-factory
```

## Quality Gate Errors

### Pre-gate keeps failing / pre-gate exhausted warning

The pre-gate runs Gates 1–3 (Build + Lint + Typecheck) before the full quality gate as a
fast feedback loop. Failures are fed back to the implementer and do not consume an iteration
slot.

If the pre-gate exhausts all retries (`pre_gate_retries`, default 2), a warning is logged
and the pipeline falls through to the full quality gate anyway. The pre-gate report is at:

```
.dark-factory/reports/<spec>-pre-gate.md
```

Common causes:
- **Build 403 errors** (`ERR_PNPM_FETCH_403`, `pip` network errors): egress filtering is
  blocking a package registry. Check `docker/filter-execution.txt` and ensure the needed
  registry hostname is listed with `(:\d+)?` in the pattern.
- **ESLint "all files ignored"**: ESLint flat config (`eslint.config.js`) has TypeScript
  files in its `ignores` list but the lint script targets a TypeScript-only directory. Add
  `@typescript-eslint/parser` or adjust the lint target.
- **Missing `node_modules`**: build step failed before lint ran, so `node_modules` were
  never created. Fix the build failure first.

To disable the pre-gate entirely:

```toml
[gates]
pre_gate_retries = 0
```

### `regression blocked ship`

A previously-shipped scenario now fails after your changes.

```bash
# See which scenario failed:
dark-factory regression

# Options:
# 1. Fix the regression in your code
# 2. Update the scenario if behavior intentionally changed
# 3. Use --only-affected to limit scope:
dark-factory regression --only-affected
```

### `strategy switches exhausted`

The spec is too complex for automated resolution after multiple strategy attempts.

```bash
# Decompose the spec into smaller, more focused pieces:
# Instead of one large feature, write 2-3 smaller specs
# that each handle one aspect of the feature.
```

### `no registered specs for regression`

This is expected on the first pipeline run — there are no previous specs to regress against. No action needed.

## Deployment Errors

### `health check timeout`

The deployment target is not ready within the configured timeout.

```bash
# Check if the service starts manually:
docker compose up
curl http://localhost:8080/health

# Increase timeout in config.toml:
# [deploy]
# health_timeout = 120
# Or increase poll interval:
# rollback_poll_interval = 30
```

### `barrier violation detected`

An agent attempted to read across an information barrier (e.g., implementer tried to read scenarios).

```bash
# Check audit logs:
cat .dark-factory/audit/*.jsonl | grep violation

# This usually means a Docker mount is misconfigured.
# Re-run init to regenerate the setup:
dark-factory init

# If persistent, check docker/build-images.sh and report the issue.
```

## HTTP and Browser Scenario Errors (Format A / Format B)

### `Step N: connection error — Connection refused`

The HTTP scenario runner tried to reach the test server but it wasn't running. Causes:

1. **No `server_cmd` configured** — the pipeline doesn't start a server automatically unless `[test_server] server_cmd` is set. For CI or external servers, ensure the server is started before calling the pipeline.
2. **Server took longer than `startup_timeout`** — increase the timeout in `config.toml`:
   ```toml
   [test_server]
   startup_timeout = 60
   ```
3. **Missing database migrations or setup** — if the server requires migrations before it can start, add a `setup_cmd`:
   ```toml
   [test_server]
   setup_cmd = "cd apps/api && pnpm drizzle-kit generate"
   ```
   The setup command runs in the same container with access to service sidecars (e.g., Postgres) before the server starts. Dependencies are automatically installed before the setup command runs (e.g., `pnpm install --frozen-lockfile` for Node projects).
4. **Wrong `base_url`** — confirm the server is listening on the expected host/port.
5. **`server_setup_cmd` fails consistently** — if the setup command (e.g., `drizzle-kit push`) fails on every iteration, the pipeline will escalate after 2 consecutive failures with an ENV_ISSUE classification. Check:
   - The command syntax matches the installed tool version (e.g., `drizzle-kit push:pg` vs `drizzle-kit push`)
   - Any required CLI tools are listed in the architecture manifest's `environment.tools` so they are available in the container
   - The setup command works locally: `docker run --rm <image> sh -c "<your setup_cmd>"`

   When a server error occurs, the review prepass deducts 15 points from the Test Analysis score (25 → 10 max) and surfaces a `SERVER ERROR` warning in the prepass report. This prevents the reviewer from being misled by a high pass rate that only reflects static file-existence checks.

### `ERROR: No scenarios could be parsed from any *.scenarios.md file`

All blocks in the scenario files were malformed and skipped. Check that:
- Each scenario block has `# Scenario: <name>` as the first non-blank header line.
- Each block has either `## Request` + `## Expect` (single-step) or `## Step N` + `## Expect` pairs (multi-step).
- Blocks are separated by lines containing only `---`.

Run the runner directly to see which files warn:
```bash
python3 scripts/lib/http-scenario-runner.py .dark-factory/scenarios/my-spec \
    --base-url http://localhost:8000 --spec-name my-spec
```

### `WARNING: Unrecognised step line` (journey runner)

A line in `## Steps` didn't match any known action pattern. Supported patterns:
- `navigate: /path`
- `click: Label or .css-selector`
- `fill: "Label" with "value"`
- `assert visible: "text"`
- `assert url: /path`
- `assert not visible: "text"`
- `wait: 500 ms`

The runner skips unrecognised lines and continues; if **all** lines are unrecognised, the scenario block is skipped entirely.

### Scenario validation report shows files missing `## ` section headers

Open `.dark-factory/reports/<spec>-scenario-validation.md`. If the `has_sections` check shows FAIL, one or more `*.scenarios.md` or `*.journey.md` files have no `## ` section headers. The validator requires at least one H2 header per file. `### ` (H3) headers do not count.

### `SCENARIO_TOTAL=0` after gate runs

All runners ran but parsed zero scenarios. Check:
1. Scenario files are in the correct directory: `.dark-factory/scenarios/<spec-name>/`
2. File names match the patterns: `*.scenarios.md`, `*.journey.md`, `*.test.ts`, `test_*.py`, etc.
3. Plain `.md` files (e.g., `01-setup.md`) are **not** recognized as scenario files. If the Scenario Architect wrote plain `.md` files and the spec has no testable runtime behavior, use a `SCENARIOS-NOT-APPLICABLE.md` sentinel instead.
4. Run the relevant runner directly (see above) to see parse warnings.

### `Scenario sentinel rejected`

The pipeline validates `SCENARIOS-NOT-APPLICABLE.md` before implementation runs. Rejection means the sentinel is structurally incomplete. Common causes:

| Error | Fix |
|-------|-----|
| `Missing required section heading: '### Format A'` | Add all four `### Format A/B/C/D` sections |
| `Section 'Format B' has no substantive justification` | Replace generic text ("N/A") with a specific reason citing the spec |
| `No functional requirement coverage mapping found` | Add a `## Functional Requirement Coverage` table mapping each FR to a build/lint/typecheck gate |

If the rejection seems wrong but the spec genuinely has no testable runtime behavior, write real Format C integration tests instead — they can verify the build artifacts produced by scaffolding specs.

## Transient Failures

### `transient failure after N retries`

A persistent infrastructure issue (not a code problem).

```bash
# Check Docker resources:
docker system df
docker system prune     # Clean up unused resources

# Check network:
curl -I https://api.anthropic.com

# Check API key:
echo $ANTHROPIC_API_KEY | head -c 10
```


## Config Validation Errors

### `ConfigError` at startup

Dark Factory validates all config fields before starting the pipeline. If you see a `ConfigError`, the pipeline will not run. Common causes and fixes:

| Error message | Cause | Fix |
|--------------|-------|-----|
| `language must be node/python/go/rust` | Invalid `[core] language` | Use `node`, `python`, `go`, `rust`, or `unknown` |
| `autonomy_level must be 1, 2, or 3` | Invalid `[autonomy] level` | Use `1`, `2`, or `3` |
| `min_ship_pass_rate must be 0-100` | Out-of-range value | Use an integer 0–100 |
| `max_iterations must be >= 1` | `[autonomy] max_iterations = 0` | Use >= 1 |
| `agent_max_turns must be >= 1` | `[agents] max_turns = 0` | Use >= 1 |
| `agent_timeout must be >= 1` | `[agents] timeout = 0` | Use >= 1 |
| `conflict_strategy must be abort/serialize/warn` | Typo in value | Use `abort`, `serialize`, or `warn` |
| `multi_run_k cannot exceed multi_run_n` | k > n | Reduce `multi_run_k` or increase `multi_run_n` |
| `canary_rollback_cmd is required when canary_enabled=true` | Missing rollback | Set `[canary] rollback_cmd = "scripts/rollback.sh"` |
| `canary_stages percent must be monotonically increasing` | Stages out of order | Sort `[[canary.stages]]` by `percent` ascending |
| `migration_check_policy must be 'warn' or 'block'` | Typo | Use `warn` or `block` |
| `cost_correction_factor must be > 0` | Zero or negative | Use a positive float |
| `deploy_preset must be none/docker-compose/k8s-argo` | Typo | Use one of the valid values |
| `health_check_strategy must be http/tcp/docker/command` | Typo | Use one of the valid values |
| `twins_mode must be manual/auto` | Typo | Use `manual` or `auto` |
| `scale_concurrency must be >= 1` | Zero | Use >= 1 |
| `server_startup_timeout must be >= 1` | Zero | Use >= 1 |
| `browser_startup_timeout must be >= 1` | Zero | Use >= 1 |

### `ConfigError` from command path validation

The fields `deploy_cmd`, `deploy_teardown_cmd`, `deploy_rollback_cmd`, `canary_shift_cmd`, `canary_rollback_cmd`, `flag_create_cmd`, `flag_enable_cmd`, `flag_disable_cmd`, `flag_check_cmd`, and `rollback_monitor_cmd` are validated as safe script paths at pipeline startup.

Common errors:
- **Relative path:** `cmd = "scripts/deploy.sh"` — use an absolute path or `./scripts/deploy.sh` via a wrapper
- **Shell string:** `cmd = "docker compose up && curl ..."` — extract to a script file
- **File not found:** The path doesn't exist at pipeline start time

To use shell strings in `deploy_cmd` and `deploy_teardown_cmd` only (legacy escape hatch):

```toml
[deploy]
cmd = "docker compose up && echo done"
cmd_allow_shell = true    # bypasses path validation for cmd and teardown_cmd only
```

### `cost_budget_usd = 0` is unlimited

A common point of confusion: setting `budget_usd = 0` in `[cost]` means **no budget limit**, not a zero-dollar limit. To set a real budget, use a positive value:

```toml
[cost]
budget_usd = 10.0    # Hard stop at $10
```

---

## Anomaly Detection

### `anomaly detected` during pipeline run

The anomaly detector compares current run metrics against a rolling baseline of recent runs. When a metric exceeds a threshold, the pipeline flags the anomaly.

Each threshold controls a different metric:

| Threshold | Config key | Default | What it measures |
|-----------|-----------|---------|-----------------|
| Agent turns | `[anomaly] max_agent_turns` | `150` | Turns used by a single agent invocation |
| Total spec turns | `[anomaly] max_spec_turns` | `800` | Total turns across all agents for one spec |
| Wall clock | `[anomaly] max_spec_wall_s` | `7200` | Seconds for the entire spec run (2 hours) |
| Files written | `[anomaly] max_files_written` | `50` | Files written by the Implementer |
| Baseline window | `[anomaly] baseline_window` | `20` | Runs used to compute the rolling baseline |

**For large specs that legitimately need more resources:**

```toml
[anomaly]
max_agent_turns = 300
max_spec_turns = 1500
max_spec_wall_s = 14400   # 4 hours
max_files_written = 100
```

**Interpreting anomalies:**
- **High agent turns on implementer:** Spec may be too large; consider decomposing
- **High files written:** Implementer may be generating code outside the intended scope; check `[implementer] write_dirs`
- **High wall time:** Container resource limits may be too low (`[agents] memory`, `[agents] cpus`)

---

## Cache Disk Space

### Reclaiming cache disk space after many pipeline runs

Dark Factory uses named Docker volumes for language package caches. These volumes persist across runs by design — reusing cached packages speeds up subsequent runs. The four volume families are:

| Language | Volume name pattern |
|----------|---------------------|
| Node | `df-npm-cache-{role}` |
| Python | `df-pip-cache-{role}` |
| Go | `df-go-cache-{role}` |
| Rust | `df-cargo-cache-{role}` |

Where `{role}` is the pipeline role string (e.g. `implementer`, `quality-gate`, `http-runner`, `journey-runner`, `mutation`).

To reclaim disk space:

```bash
# List all dark-factory cache volumes (shows exact names to use in rm commands)
docker volume ls --filter name=df-

# Remove a specific volume — use the exact name from the ls output above
docker volume rm df-pip-cache-implementer

# Remove all dark-factory volumes (removes all caches — next run will re-download packages)
docker volume ls --filter name=df- -q | xargs docker volume rm
```
