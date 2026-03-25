# Integration Guide

How to add Dark Factory to an existing project.

> **See also:** [CONFIGURATION.md](CONFIGURATION.md) — complete config reference

## Prerequisites

- **Docker** installed and running (Docker Desktop or Docker Engine)
- Claude Code installed (`npm install -g @anthropic-ai/claude-code`)
- Git initialized in your project
- Authentication credentials (see [Authentication](#authentication) below)

**Docker licensing note:** Docker Engine is free for all uses on Linux. Docker Desktop requires a paid license for organizations with >250 employees or >$10M annual revenue.

---

## Authentication

The pipeline runs Claude Code inside Docker containers, which cannot access macOS Keychain or local config-file credentials. You must supply credentials via an environment variable.

### Option A — OAuth token (Claude Pro/Max subscription, recommended)

Generate a long-lived token once:

```bash
claude setup-token
```

This prints a token starting with `sk-ant-oat01-`. Add it to your shell profile (`~/.zshrc` or `~/.zprofile`):

```bash
export CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-..."
```

Then reload: `source ~/.zshrc`

**Token lifetime:** OAuth tokens expire after 1 year. To track expiry, set `token_expires` in your project's `.dark-factory/config.toml`:

```toml
[auth]
token_expires = "2027-03-02"   # 1 year from when you ran setup-token
```

The pipeline will warn 30 days before expiry and hard-block on/after the expiry date with instructions to regenerate.

**To regenerate:** run `claude setup-token` again, update your shell profile with the new token, and update `token_expires`.

### Option B — Anthropic API key

If you have an Anthropic Console API key (`sk-ant-api03-...`):

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."
```

Note: API keys are billed per-token. OAuth tokens (Option A) use your subscription's included usage instead.

---

## Quick Start (One Command)

```bash
cd /path/to/your/project
dark-factory init
```

This will:
1. Create `.dark-factory/` directory structure in your project
2. Auto-detect your language and generate `.dark-factory/config.toml`
3. Build Docker images for your language
4. Append Dark Factory rules to your `CLAUDE.md`
5. Run validation

### Options

| Flag | Description |
|------|-------------|
| `--force` | Overwrite existing files |
| `--language <lang>` | Override auto-detection (`node`, `python`, `go`, `rust`) |
| `--dry-run` | Preview what would be done without making changes |

### Examples

```bash
# Standard integration
dark-factory init

# Force Python even though package.json exists
dark-factory init --language python

# Preview without changes
dark-factory init --dry-run
```

---

## Manual Setup (Step by Step)

If you prefer to integrate manually:

### 1. Create directory structure

```bash
cd /path/to/your/project
mkdir -p .dark-factory/{specs,scenarios,reviews,reports,audit,state,plans}
```

### 2. Copy framework files

```bash
cp -r /path/to/dark-factory/agents dark-factory/agents
cp -r /path/to/dark-factory/templates dark-factory/templates
cp -r /path/to/dark-factory/docs dark-factory/docs
cp -r /path/to/dark-factory/scripts dark-factory/scripts
cp -r /path/to/dark-factory/docker dark-factory/docker
chmod +x dark-factory/scripts/*.sh dark-factory/docker/build-images.sh
```

### 3. Build Docker images

```bash
bash dark-factory/docker/build-images.sh node  # or python, go, rust, all
```

### 4. Create config file

Copy the template and fill in values:

```bash
cp dark-factory/templates/config.toml.template .dark-factory/config.toml
```

Edit `.dark-factory/config.toml` with your project's commands. Key settings:

```toml
[core]
language = "node"
source_dirs = ["src"]     # Dirs to mask from Scenario Architect (info barrier)
test_cmd = "npm test"
scenario_test_cmd = "npx vitest run {SCENARIOS_DIR}"

[agents]
timeout = 600             # Max seconds per agent invocation
memory = "4g"             # Container memory limit
cpus = "4"                # Container CPU limit

[docker]
network = "df-pipeline"
```

### 5. Update CLAUDE.md

Append the contents of `dark-factory/templates/project-claude-addition.md` to your project's `CLAUDE.md`. Replace `__SOURCE_DIR__` with your actual source directory.

### 6. Validate

```bash
dark-factory precheck
```

---

## Language-Specific Configuration

### TypeScript / JavaScript

```toml
# .dark-factory/config.toml

[core]
language = "node"
source_dirs = ["src"]
test_dir = "tests"
test_cmd = "npm test"
scenario_test_cmd = "npx vitest run {SCENARIOS_DIR}"
dark_factory_home = "dark-factory"

[gates]
build_cmd = "npm run build"
lint_cmd = "npm run lint"
typecheck_cmd = "npx tsc --noEmit"
```

**Prerequisites:**
```bash
npm install -D vitest typescript
```

**package.json scripts (recommended):**
```json
{
  "scripts": {
    "build": "tsc",
    "lint": "eslint .",
    "test": "vitest run",
    "test:scenarios": "vitest run .dark-factory/scenarios/"
  }
}
```

### Python

```toml
# .dark-factory/config.toml

[core]
language = "python"
source_dirs = ["src"]
test_dir = "tests"
test_cmd = "pytest tests/ -v"
scenario_test_cmd = "pytest {SCENARIOS_DIR} -v"
dark_factory_home = "dark-factory"

[gates]
lint_cmd = "ruff check ."
typecheck_cmd = "mypy ."
# build_cmd is auto-detected by `dark-factory init`:
#   pyproject.toml / setup.py / setup.cfg → "pip install -e . -q"
#   requirements.txt                       → "pip install -r requirements.txt -q"
#   requirements/base.txt                  → "pip install -r requirements/base.txt -q"
# Override here if your project uses a different install command.
```

**Prerequisites:**
```bash
pip install pytest ruff mypy
```

**pyproject.toml (recommended):**
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
src = ["src"]
```

### Go

```toml
# .dark-factory/config.toml

[core]
language = "go"
source_dirs = ["."]
test_dir = "."
test_cmd = "go test ./... -v"
scenario_test_cmd = "bash .dark-factory/run-go-scenarios.sh {SCENARIOS_DIR}"
dark_factory_home = "dark-factory"

[gates]
build_cmd = "go build ./..."
lint_cmd = "golangci-lint run"
```

**Prerequisites:**
```bash
go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
```

### Rust

```toml
# .dark-factory/config.toml

[core]
language = "rust"
source_dirs = ["src"]
test_dir = "tests"
test_cmd = "cargo test"
scenario_test_cmd = "bash .dark-factory/run-rust-scenarios.sh {SCENARIOS_DIR}"
dark_factory_home = "dark-factory"

[gates]
build_cmd = "cargo build"
lint_cmd = "cargo clippy -- -D warnings"
```

**Prerequisites:**
```bash
rustup component add clippy
```

---

## Migration Guide

### Projects with existing tests

Dark Factory does not replace your existing tests. Your project's tests run as "self-tests" (Gate 4 in the quality gate), while Dark Factory adds "blind scenarios" (Gate 5) that test the implementation without the implementer having seen them.

Your existing test command goes in `[core] test_cmd`. Blind scenarios use `[core] scenario_test_cmd`.

### Projects with existing CLAUDE.md

`dark-factory init` appends to your existing CLAUDE.md — it never overwrites it. The appended section contains information barrier rules that are critical for the pipeline to work correctly.

If you have conflicting instructions in your CLAUDE.md, the Dark Factory rules should take precedence for pipeline runs.

---

## Monorepo Guidance

For monorepos with multiple packages:

### Option A: One Dark Factory per package

```
monorepo/
├── packages/
│   ├── api/
│   │   ├── .dark-factory/    # API-specific specs and scenarios
│   │   ├── dark-factory/     # Framework copy (or symlink)
│   │   └── CLAUDE.md
│   └── web/
│       ├── .dark-factory/    # Web-specific specs and scenarios
│       ├── dark-factory/     # Framework copy (or symlink)
│       └── CLAUDE.md
```

Run integration per package:
```bash
cd ./packages/api && dark-factory init
cd ./packages/web && dark-factory init
```

### Option B: Shared Dark Factory at root

```
monorepo/
├── .dark-factory/            # Shared runtime artifacts
│   ├── specs/
│   │   ├── api-user-auth.md
│   │   └── web-dashboard.md
│   └── scenarios/
├── dark-factory/             # Single framework copy
├── CLAUDE.md                 # Root-level barrier rules
└── packages/
    ├── api/
    └── web/
```

Adjust `[core] source_dirs` in `config.toml` to point to the right package.

---

## Egress Filtering

Agent containers run with `--dangerously-skip-permissions`, which disables Claude Code's entire
permission system — no tool call requires human approval inside the container. The container's
restricted filesystem and egress controls are therefore the real security boundary; `--disallowedTools`
entries are defense-in-depth, not substitutes. To prevent a prompt-injected instruction or
compromised package script from exfiltrating data, Dark Factory routes all outbound traffic through
a tinyproxy sidecar that enforces a hostname allowlist.

### How It Works

Two internal Docker networks (`df-analysis`, `df-execution`) have **no default gateway** — containers
on them cannot reach the internet directly. The proxy containers (`df-analysis-proxy`,
`df-execution-proxy`) bridge the internal networks to a standard external network and only forward
`CONNECT` requests to allowlisted hostnames.

| Tier | Agents | Allowed hosts |
|------|--------|--------------|
| Analysis | spec-validator, scenario-architect, reviewer, oracle, escalation | `api.anthropic.com` only |
| Execution | implementer, quality-gate | Anthropic API + npm, PyPI, Go proxy, Rust registries |

### Setup

Egress filtering is **enabled by default**. The proxy image is built automatically by
`docker/build-images.sh`:

```bash
bash dark-factory/docker/build-images.sh node   # builds proxy + base + language images
```

Verify the image was built:

```bash
docker images | grep dark-factory-proxy
```

### Disabling

Set `egress_filtering = false` in `[docker]` to fall back to the legacy flat network. A startup warning will be printed on every pipeline run:

```toml
[docker]
egress_filtering = false
```

```
WARNING: Egress filtering is disabled ([docker] egress_filtering = false).
         Agent containers have unrestricted internet access.
```

Only disable if you have a specific reason (e.g., corporate proxy conflict, private registry not
yet in the allowlist).

### Adding Private Registries

Edit `dark-factory/docker/filter-execution.txt` and add a regex line per hostname.
Patterns use tinyproxy `FilterExtended` syntax and are matched against the `host:port`
CONNECT target string — include `(:\d+)?` to match both plain and port-suffixed forms.
Use `\.` for literal dots (`.` matches any character in regex):

```
^npm\.my-company\.internal(:\d+)?$
^artifactory\.example\.com(:\d+)?$
```

Rebuild the proxy image after editing:

```bash
docker build -t dark-factory-proxy -f dark-factory/docker/Dockerfile.proxy dark-factory/docker/
```

### Known Limitations

- **Go `direct` fallback**: If `GOPROXY` includes `direct`, Go will attempt to fetch modules
  directly from source (e.g., github.com) when the proxy doesn't have them. This fails by design
  on the internal network — use `GOPROXY=https://proxy.golang.org,off` to disable the fallback.
- **Cargo git dependencies**: `git = "..."` in `Cargo.toml` bypasses registry allowlist and fails
  by design. Use crates.io versions with `[patch]` instead.
- **GitHub Packages / Artifactory**: Not in default allowlist — add the hostname to
  `filter-execution.txt` (see above).

---

## CI/CD Closed Loop

Phase 2 adds automatic PR creation and merge queue integration after the Oracle SHIP decision.

### How Auto-PR Works

When `auto_pr = true` (or `--auto-pr` CLI flag), after the Oracle says SHIP:

1. **Branch creation:** `dark-factory/<spec-name>-<short-run-id>` based on `base_branch`
2. **Commit:** Project files (source_dirs, test_dir, package.json, etc.) plus per-spec regression artifacts
3. **Push:** To `remote` (default: `origin`)
4. **PR creation:** Via `gh pr create` with structured body (summary, quality gate results, pipeline metadata)
5. **Merge queue** (optional): `gh pr merge --merge-queue` if `merge_queue = true`

Branch names use the first 12 characters of the run ID to stay under git ref limits.

### Configuration

```toml
[git]
auto_pr = true                  # Create PR after SHIP
remote = "origin"               # Git remote (default: origin)
base_branch = "main"            # PR base branch (default: main)
merge_queue = false             # Add to merge queue after PR creation
skip_hooks = false              # Skip git hooks on commit (--no-verify)
```

CLI overrides: `--auto-pr`, `--no-pr`, `--base-branch <name>`.

### Merge Queue Setup

1. Enable merge queue in your GitHub repository settings (Settings > General > Pull Requests > Merge queue)
2. Set `merge_queue = true` in `[git]`
3. Configure required status checks for the merge queue

### Self-Hosted Runners

Use self-hosted runners when:
- **Docker-in-Docker:** Your pipeline needs DinD support not available on GitHub-hosted runners
- **Long-running specs:** Specs that exceed the 6-hour GitHub-hosted runner limit
- **Cost optimization:** Frequent pipeline runs on your own hardware

Configure via workflow dispatch input `runner` or set the `DF_RUNNER` repository variable:

```bash
# Repository variable (applies to all push-triggered runs)
gh variable set DF_RUNNER --body "self-hosted"

# Per-run override (workflow dispatch only)
# Select "self-hosted" in the runner input field
```

### Webhook Notifications

Configure notifications in `.dark-factory/config.toml`:

```toml
[notifications]
# Slack incoming webhook
slack_webhook_url = "https://hooks.slack.com/services/T.../B.../..."

# Generic webhook (receives JSON POST for all events)
webhook_url = "https://your-service.com/webhook"
```

Events: `shipped`, `escalated`, `failed`, `pr_created`, `regression_blocked`.

All notifications are fire-and-forget — failures never block the pipeline.

### Post-Merge Regression

The `dark-factory-regression.yml` workflow runs automatically when a PR with the `dark-factory` label merges to main. It executes all shipped-spec scenarios against the merged codebase to catch regressions.

You can also run regression manually:

```bash
dark-factory regression
dark-factory regression --exclude spec-a,spec-b --timeout 600
```

---

## Cost Budgeting

Set a USD budget to prevent runaway runs. A typical spec costs $0.50–$3.00 with Claude Sonnet.

```toml
[cost]
budget_usd = 10.0               # 0.0 = unlimited (NOT zero budget)
per_input_token = 0.000015      # Update if you change models
per_output_token = 0.000075
alert_thresholds = [50, 80, 100]
```

The pipeline warns at each threshold percentage and halts on budget exhaustion (100%). See [CONFIGURATION.md §cost](CONFIGURATION.md#cost--budget-and-cost-tracking) for full details.

---

## Model Selection

By default, all agents use `claude-sonnet-4-20250514`. Override globally or per-role:

```toml
[model]
id = "claude-sonnet-4-20250514"   # Default for all agents

[model.ids]
# Per-role overrides — use cheaper models where deep reasoning isn't needed
spec-validator = "claude-haiku-4-5-20251001"
reviewer = "claude-haiku-4-5-20251001"
implementer = "claude-sonnet-4-20250514"
oracle = "claude-sonnet-4-20250514"
```

Valid role names: `spec-validator`, `scenario-architect`, `implementer`, `quality-gate`, `reviewer`, `oracle`.

---

## Troubleshooting

### "Scenarios directory not found"

The pipeline expects scenarios at `.dark-factory/scenarios/<spec-name>/`. Make sure:
1. You've run the Scenario Architect step (or the full pipeline)
2. The spec name matches exactly (no `.md` extension)

### "No test runner found"

Set `scenario_test_cmd` in `[core]` in `.dark-factory/config.toml` to your project's test runner command. Use `{SCENARIOS_DIR}` as a placeholder for the scenarios path.

### "CLAUDE.md does not mention Dark Factory"

Re-run integration with `--force`, or manually append the contents of `dark-factory/templates/project-claude-addition.md` to your CLAUDE.md.

### precheck shows warnings

Warnings are non-fatal. Common causes:
- **language = "unknown"**: Set it explicitly in `[core] language`
- **build_cmd/test_cmd not set**: Fill in `[gates]` and `[core]` with your project's commands
- **Missing language tools**: Install the tools for your language (e.g., `npm`, `pytest`)

### Scripts not executable

```bash
chmod +x dark-factory/scripts/*.sh
```

### Config not being loaded

The CLI looks for `.dark-factory/config.toml` relative to the current working directory. Always run commands from your project root:

```bash
cd /path/to/your/project
dark-factory run my-spec
```
