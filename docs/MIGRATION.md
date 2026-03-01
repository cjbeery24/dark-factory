# Migration Guide

How to upgrade an existing Dark Factory installation to Docker-based isolation.

---

## What Changed

Dark Factory now uses **Docker containers** as the primary information barrier enforcement mechanism, replacing the previous approach of `--disallowedTools` flags alone.

### Before (soft barriers)
- Agents ran directly on the host machine
- Information barriers enforced via `--disallowedTools` flags and `--append-system-prompt`
- Agents could bypass barriers using the Bash tool to read restricted files

### After (hard barriers)
- Each agent runs inside a Docker container with restricted filesystem mounts
- Restricted directories are masked with empty read-only tmpfs overlays — files don't exist in the container
- `--disallowedTools` and `--append-system-prompt` are retained as defense-in-depth
- `.git/` is masked in barrier-sensitive containers to prevent `git log`/`git show` leaks
- Per-role cache volumes prevent cache poisoning between agents

---

## Migration Steps

### 1. Install Docker

- **macOS/Windows:** Install [Docker Desktop](https://docs.docker.com/get-docker/)
- **Linux:** Install [Docker Engine](https://docs.docker.com/engine/install/)

Verify Docker is running:
```bash
docker info
```

### 2. Copy Docker infrastructure

Copy the new `docker/` directory into your project's `dark-factory/` installation:

```bash
cp -r /path/to/dark-factory/docker your-project/dark-factory/docker
chmod +x your-project/dark-factory/docker/build-images.sh
```

### 3. Build Docker images

```bash
bash dark-factory/docker/build-images.sh node  # or python, go, rust, all
```

### 4. Update scripts

Replace your existing pipeline scripts with the updated versions:

```bash
cp /path/to/dark-factory/scripts/dark-factory.sh your-project/dark-factory/scripts/
cp /path/to/dark-factory/scripts/validate-setup.sh your-project/dark-factory/scripts/
cp /path/to/dark-factory/scripts/integrate.sh your-project/dark-factory/scripts/
chmod +x your-project/dark-factory/scripts/*.sh
```

### 5. Update config.sh

Add the new Docker-related configuration variables to your `.dark-factory/config.sh`:

```bash
# --- Docker Isolation Settings ---
SOURCE_DIRS="src"              # Space-separated list of source dirs to mask from Scenario Architect
AGENT_TIMEOUT="600"            # Max wall-clock seconds per agent invocation
AGENT_MEMORY="4g"              # Container memory limit
AGENT_CPUS="4"                 # Container CPU limit
DOCKER_NETWORK="df-pipeline"   # Docker network name
```

**Note:** `SOURCE_DIR` is still used by `quality-gate.sh` for backward compatibility. `SOURCE_DIRS` (plural) controls which directories are masked in the Scenario Architect's container.

### 6. Update agent definitions

Replace your Oracle agent definition to fix output format parsing:

```bash
cp /path/to/dark-factory/agents/oracle.md your-project/dark-factory/agents/oracle.md
```

### 7. Validate

```bash
./dark-factory/scripts/validate-setup.sh .
```

The validator now checks for Docker installation, daemon status, image availability, and network setup.

---

## New Features

### Debug mode

The `--debug` flag keeps containers alive on failure for inspection:

```bash
./scripts/dark-factory.sh my-feature --debug

# Inspect a failed container:
docker exec -it df-my-feature-implementer-<pid> sh
```

### Container cleanup

Failed containers from interrupted runs are automatically cleaned up via the EXIT trap. To manually clean up:

```bash
docker ps -q --filter "name=df-" | xargs -r docker rm -f
```

### Per-role cache volumes

Package manager caches are now isolated per agent role to prevent cache poisoning:
- Implementer uses `df-implementer-npm` (or pip/go/cargo)
- Quality Gate uses `df-qg-npm`

### Robust Oracle parsing

The Oracle output parser now handles case variations (`ship`, `SHIP`, `Ship`) and whitespace. Malformed output defaults to NO-SHIP with a warning.

### Quality gate crash fix

The quality gate no longer crashes the pipeline on failure. Exit codes are captured and passed to the reviewer/oracle for evaluation.

---

## Breaking Changes

1. **Docker is required** — There is no bare-metal fallback. Docker must be installed and running.
2. **Docker images must be built** before the first pipeline run: `bash docker/build-images.sh <language>`
3. The pipeline now requires the `ANTHROPIC_API_KEY` environment variable to be set (it's passed into containers via `-e ANTHROPIC_API_KEY`).

---

## Known Limitations

1. **Network egress is unrestricted** — Agents can reach any internet host, not just api.anthropic.com. Hardening via proxy sidecar or Docker Sandboxes network policies is documented as a future enhancement.
2. ~~**Reviewer can leak scenario details to Implementer** via feedback text~~ — **Resolved.** `.dark-factory/scenarios/` is now masked in the Reviewer's container. The Oracle retains scenario access for SHIP/NO-SHIP decisions; its structured feedback template constrains the residual leak surface.
3. **No iteration rollback** — If iteration 2 makes things worse, there's no snapshot to revert to.
