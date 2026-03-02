# Integration Guide

How to add Dark Factory to an existing project.

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

**Token lifetime:** OAuth tokens expire after 1 year. To track expiry, set `DF_TOKEN_EXPIRES` in your project's `.dark-factory/config.sh`:

```bash
DF_TOKEN_EXPIRES="2027-03-02"   # 1 year from when you ran setup-token
```

The pipeline will warn 30 days before expiry and hard-block on/after the expiry date with instructions to regenerate.

**To regenerate:** run `claude setup-token` again, update your shell profile with the new token, and update `DF_TOKEN_EXPIRES`.

### Option B — Anthropic API key

If you have an Anthropic Console API key (`sk-ant-api03-...`):

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."
```

Note: API keys are billed per-token. OAuth tokens (Option A) use your subscription's included usage instead.

---

## Quick Start (One Command)

```bash
/path/to/dark-factory/scripts/integrate.sh /path/to/your/project
```

This will:
1. Create `.dark-factory/` directory structure in your project
2. Copy framework files (including Docker infrastructure) into `your-project/dark-factory/`
3. Auto-detect your language and generate `.dark-factory/config.sh`
4. Build Docker images for your language
5. Append Dark Factory rules to your `CLAUDE.md`
6. Run validation

### Options

| Flag | Description |
|------|-------------|
| `--symlink` | Symlink framework files instead of copying (keeps in sync with source) |
| `--force` | Overwrite existing files |
| `--language <lang>` | Override auto-detection (`node`, `python`, `go`, `rust`) |
| `--dry-run` | Preview what would be done without making changes |

### Examples

```bash
# Standard integration
./dark-factory/scripts/integrate.sh ~/projects/my-api

# Use symlinks (for development — stays in sync with framework changes)
./dark-factory/scripts/integrate.sh ~/projects/my-api --symlink

# Force Python even though package.json exists
./dark-factory/scripts/integrate.sh ~/projects/my-api --language python

# Preview without changes
./dark-factory/scripts/integrate.sh ~/projects/my-api --dry-run
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
cp dark-factory/templates/config.sh.template .dark-factory/config.sh
```

Edit `.dark-factory/config.sh` with your project's commands. Key Docker-related settings:

```bash
SOURCE_DIRS="src"        # Space-separated dirs to mask from Scenario Architect
AGENT_TIMEOUT="600"      # Max seconds per agent invocation
AGENT_MEMORY="4g"        # Container memory limit
AGENT_CPUS="4"           # Container CPU limit
DOCKER_NETWORK="df-pipeline"
```

### 5. Update CLAUDE.md

Append the contents of `dark-factory/templates/project-claude-addition.md` to your project's `CLAUDE.md`. Replace `__SOURCE_DIR__` with your actual source directory.

### 6. Validate

```bash
./dark-factory/scripts/validate-setup.sh .
```

---

## Language-Specific Configuration

### TypeScript / JavaScript

```bash
# .dark-factory/config.sh
LANGUAGE="node"
SOURCE_DIR="src"
TEST_DIR="tests"
BUILD_CMD="npm run build"
LINT_CMD="npm run lint"
TYPECHECK_CMD="npx tsc --noEmit"
TEST_CMD="npm test"
SCENARIO_TEST_CMD="npx vitest run {SCENARIOS_DIR}"
DARK_FACTORY_HOME="dark-factory"
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

```bash
# .dark-factory/config.sh
LANGUAGE="python"
SOURCE_DIR="src"
TEST_DIR="tests"
BUILD_CMD=""
LINT_CMD="ruff check ."
TYPECHECK_CMD="mypy ."
TEST_CMD="pytest tests/ -v"
SCENARIO_TEST_CMD="pytest {SCENARIOS_DIR} -v"
DARK_FACTORY_HOME="dark-factory"
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

```bash
# .dark-factory/config.sh
LANGUAGE="go"
SOURCE_DIR="."
TEST_DIR="."
BUILD_CMD="go build ./..."
LINT_CMD="golangci-lint run"
TYPECHECK_CMD=""
TEST_CMD="go test ./... -v"
SCENARIO_TEST_CMD="go test ./{SCENARIOS_DIR}/... -v"
DARK_FACTORY_HOME="dark-factory"
```

**Prerequisites:**
```bash
go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
```

### Rust

```bash
# .dark-factory/config.sh
LANGUAGE="rust"
SOURCE_DIR="src"
TEST_DIR="tests"
BUILD_CMD="cargo build"
LINT_CMD="cargo clippy -- -D warnings"
TYPECHECK_CMD=""
TEST_CMD="cargo test"
SCENARIO_TEST_CMD="cargo test --test '*'"
DARK_FACTORY_HOME="dark-factory"
```

**Prerequisites:**
```bash
rustup component add clippy
```

---

## Migration Guide

### Projects with existing tests

Dark Factory does not replace your existing tests. Your project's tests run as "self-tests" (Gate 4 in the quality gate), while Dark Factory adds "blind scenarios" (Gate 5) that test the implementation without the implementer having seen them.

Your existing test command goes in `TEST_CMD`. Blind scenarios use `SCENARIO_TEST_CMD`.

### Projects with existing CLAUDE.md

The integration script appends to your existing CLAUDE.md — it never overwrites it. The appended section contains information barrier rules that are critical for the pipeline to work correctly.

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
./scripts/integrate.sh ./packages/api
./scripts/integrate.sh ./packages/web
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

Adjust `SOURCE_DIR` and `TEST_DIR` in config.sh to point to the right package.

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

Set `EGRESS_FILTERING_ENABLED=false` in `.dark-factory/config.sh` to fall back to the legacy flat
network. A startup warning will be printed on every pipeline run:

```
WARNING: Egress filtering is disabled (EGRESS_FILTERING_ENABLED=false).
         Agent containers have unrestricted internet access.
```

Only disable if you have a specific reason (e.g., corporate proxy conflict, private registry not
yet in the allowlist).

### Adding Private Registries

Edit `dark-factory/docker/filter-execution.txt` and add a regex line per hostname:

```
^npm\.my-company\.internal$
^artifactory\.example\.com$
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

## Troubleshooting

### "Scenarios directory not found"

The pipeline expects scenarios at `.dark-factory/scenarios/<spec-name>/`. Make sure:
1. You've run the Scenario Architect step (or the full pipeline)
2. The spec name matches exactly (no `.md` extension)

### "No test runner found"

Set `SCENARIO_TEST_CMD` in `.dark-factory/config.sh` to your project's test runner command. Use `{SCENARIOS_DIR}` as a placeholder for the scenarios path.

### "CLAUDE.md does not mention Dark Factory"

Re-run integration with `--force`, or manually append the contents of `dark-factory/templates/project-claude-addition.md` to your CLAUDE.md.

### validate-setup.sh shows warnings

Warnings are non-fatal. Common causes:
- **LANGUAGE=unknown**: Set it explicitly in config.sh
- **BUILD_CMD/TEST_CMD not set**: Fill in config.sh with your project's commands
- **Missing language tools**: Install the tools for your language (e.g., `npm`, `pytest`)

### Scripts not executable

```bash
chmod +x dark-factory/scripts/*.sh
```

### Config not being sourced

Scripts look for `.dark-factory/config.sh` relative to the current working directory. Always run scripts from your project root:

```bash
cd /path/to/your/project
./dark-factory/scripts/dark-factory.sh my-spec
```
