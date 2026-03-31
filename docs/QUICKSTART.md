# Quick Start

Get Dark Factory running in your project. Realistic time: **10-15 minutes** for a simple feature (prerequisites already installed).

---

## Prerequisites

Before starting, verify these are installed and working:

| Tool | Check | Install |
|------|-------|---------|
| Docker | `docker info` (no errors) | macOS: `brew install --cask docker` then `open -a Docker` / Linux: `curl -fsSL https://get.docker.com \| sh` |
| Claude Code | `claude --version` | `npm install -g @anthropic-ai/claude-code` |
| Git | `git --version` | macOS: `xcode-select --install` / Linux: `sudo apt install git` |
| Python 3.11+ | `python3 --version` | [python.org/downloads](https://www.python.org/downloads/) |

Or run `dark-factory setup validate` to check everything at once.

### Install Dark Factory

```bash
pip install /path/to/dark-factory          # from anywhere
# or, for development:
pip install -e /path/to/dark-factory       # editable install (changes take effect immediately)
```

This installs the `dark-factory` CLI command. Verify with:

```bash
dark-factory --help
```

### Authentication (required)

The pipeline runs Claude Code inside Docker containers that cannot access your local credentials. You must set one of these environment variables:

**Option A — OAuth token (recommended for Claude Pro/Max subscribers):**

```bash
claude setup-token                           # generates a token
export CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-..."   # add to ~/.zshrc
source ~/.zshrc
```

**Option B — API key (pay-per-token):**

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."  # add to ~/.zshrc
source ~/.zshrc
```

See [INTEGRATION.md](INTEGRATION.md#authentication) for token lifetime and renewal details.

---

## 1. Initialize your project

```bash
cd /path/to/your/project
dark-factory init
```

This will:
- Detect your language, source dirs, test dir, and framework
- Create the `.dark-factory/` directory structure
- Generate `.dark-factory/config.toml` with working defaults
- Validate prerequisites

If auto-detection fails or you want to skip prompts:

```bash
dark-factory init --non-interactive --language python
```

### Alternative: initialize from the framework directory

If you prefer to run initialization from the framework directory:

```bash
cd /path/to/your/project
dark-factory init
```

## 2. Build Docker images

```bash
bash docker/build-images.sh python    # or: node, go, rust
```

This builds the sandboxed containers that enforce information barriers between agents.

## 3. Review your config

```bash
cat .dark-factory/config.toml
```

Verify the auto-detected settings match your project. Key fields:

```toml
[core]
language = "python"
source_dirs = ["src/myapp"]
test_dir = "tests"
test_cmd = "pytest"

[gates]
lint_cmd = "ruff check ."
```

**For HTTP API projects,** add a test server so the pipeline can run behavioral HTTP scenarios (Format A) instead of static artifact verification:

```toml
[test_server]
server_cmd = "uvicorn myapp:app --port 8000"
base_url = "http://localhost:8000"
health_path = "/health"
```

**If your server needs a database or other services,** add `[[services]]` entries:

```toml
[[services]]
name = "postgres"
image = "postgres:16-alpine"
health_cmd = "pg_isready -U postgres"
env = { POSTGRES_PASSWORD = "postgres", POSTGRES_DB = "testdb" }
inject_env = { DATABASE_URL = "postgres://postgres:postgres@postgres:5432/testdb" }
```

See [Configuration Reference](CONFIGURATION.md#services--service-sidecar-containers) for all options.

Edit if needed — most projects work with the generated defaults.

## 4. Write a spec

**Option A — Generate from a description (fastest):**

```bash
dark-factory new-spec "Add rate limiting to the upload endpoint"
```

This auto-selects a template, generates a draft at `.dark-factory/specs/add-rate-limiting-to-the-upload-endpoint.md`, and tells you what's missing. Edit the draft to fill in project-specific details.

**Option B — Start from a template:**

```bash
cp templates/feature-spec.md .dark-factory/specs/my-feature.md
# Edit the spec with your feature details
```

**What makes a good spec:** Include functional requirements (FR-1, FR-2...), edge cases (EC-1, EC-2...), non-functional requirements (NFR-1...), and target files. See [SPECIFICATION-GUIDE.md](SPECIFICATION-GUIDE.md) for the full anatomy.

**Validate before running:**

```bash
dark-factory validate my-feature
```

## 5. Run the pipeline

```bash
dark-factory run my-feature
```

The pipeline will:
1. **Validate** your spec for completeness
2. **Generate blind test scenarios** from the spec (agent never sees the code) — or write a `SCENARIOS-NOT-APPLICABLE.md` sentinel for pure infrastructure/scaffolding specs
3. **Implement** the feature from the spec (agent never sees the test scenarios)
4. **Run quality gates** — build, lint, type check, self-tests, blind scenarios (or SKIP if sentinel)
5. **Review** the implementation
6. **Ship or iterate** (up to 3 rounds)

## 6. Check results

```bash
dark-factory dashboard         # event timeline
dark-factory metrics           # pass rates, iteration counts
```

Output files:
- Implementation: your source files (modified in place)
- Quality report: `.dark-factory/reports/<spec>-quality-gate.md`
- Review: `.dark-factory/reviews/<spec>-review.md`

---

## What just happened?

The pipeline ran 5+ agents in sequence with **information barriers**:

1. **Spec Validator** checked your spec for completeness
2. **Scenario Architect** generated blind tests from the spec (never saw the code)
3. **Implementer** wrote code from the spec (never saw the blind tests)
4. **Quality Gate** ran build, lint, type check, self-tests, and blind scenarios
5. **Reviewer + Oracle** evaluated quality and made the ship decision

If it failed, the pipeline iterates with targeted feedback. After 3 iterations, it escalates to a human.

---

## Deployment presets (optional)

For projects with deployment, add a preset to skip manual deploy configuration:

```toml
# .dark-factory/config.toml
[deploy]
preset = "docker-compose"          # or "k8s-argo"
base_url = "http://localhost:8080"
```

This auto-configures deploy, teardown, rollback, and health check commands. See [Phase 8B docs](plans/phase-8-plug-and-play.md#sub-phase-8b-deployment-presets) for details.

---

## Troubleshooting

Hit an error? See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for exact fix commands for the 15 most common failures.

## Next steps

- **Plan a multi-spec project:** Use the `/df-write-plan` skill in Claude Code
- **See all spec templates:** `ls templates/`
- **Calibrate cost estimates:** `dark-factory calibrate-cost`
- **Full workflow reference:** [WORKFLOW.md](WORKFLOW.md)
- **Team setup:** [TEAM-SETUP.md](TEAM-SETUP.md)
- **Try the example project:** `examples/rest-api/README.md`
