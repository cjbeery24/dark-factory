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

The `BUILD_CMD` fails outside the pipeline too. Check it works on its own:

```bash
# Run your build command directly:
npm run build    # or: cargo build, go build ./..., etc.

# Common causes:
# - Missing dependencies: npm install / pip install -e .
# - Wrong working directory
# - Stale lockfile
```

### `budget exceeded`

`COST_BUDGET_USD` is too low for the spec complexity.

```bash
# Check current budget in config.toml:
grep budget .dark-factory/config.toml

# Options:
# 1. Increase budget
# 2. Reduce MAX_ITERATIONS
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
