# Phase 8: Plug-and-Play Onboarding — Implementation Plan

## Context

Phases 1-7 deliver a production-grade framework: information barriers, concurrent execution, progressive deployment, strategy switching, digital twins, and all supporting infrastructure. The framework is **functionally complete** for Level 5 operation.

However, first-run experience requires significant manual configuration. A new user must understand 40+ config keys, build Docker images manually, write deployment commands from scratch, and navigate architecture docs before running a single spec. This phase closes the gap between "framework complete" and "usable in 5 minutes."

**Goal:** A developer with Docker and Claude Code installed can go from `git clone` to a successful pipeline run in under 10 minutes.

---

## Sub-Phase 8A: Zero-Config Bootstrap

**Items:** Interactive init command (1), auto-detect project structure (2), prerequisite validation (3)

**Rationale:** These are the highest-impact changes for adoption. Every new user hits the same wall: creating `config.toml` from scratch with no guidance. This sub-phase eliminates that wall entirely.

### Deliverables

1. **`dark-factory init` command** — Interactive project bootstrap that detects language/framework, generates a working `config.toml`, builds Docker images, creates `.dark-factory/` directory structure, and validates prerequisites in a single command
2. **Auto-detect `SOURCE_DIRS` and `TEST_DIR`** — Infer from project structure using language-specific conventions; only require explicit config when conventions don't match
3. **Prerequisite validator with actionable errors** — Check Docker, Claude Code, git, and Python version; output exact fix commands for each failure (not just "Docker not found")

### Files to create

| File | Purpose |
|------|---------|
| `darkfactory/setup/init.py` | Core init logic: language detection, config generation, Docker build orchestration |
| `darkfactory/setup/detect.py` | Project structure detection: language, source dirs, test dirs, framework |
| `tests/test_init.py` | Init command flow, detection heuristics, config generation |
| `tests/test_detect.py` | Language detection, source/test dir inference edge cases |

### Files to modify

| File | Change |
|------|--------|
| `darkfactory/cli.py` | Add `init` subcommand with optional `--non-interactive` flag for CI |
| `darkfactory/setup/validator.py` | Enhance prerequisite checks with actionable error messages (exact install commands per platform) |
| `darkfactory/config/defaults.py` | Extract language-specific default sets (e.g., Python: `SOURCE_DIRS=["src"]`, `TEST_DIR="tests"`, `LINT_CMD="ruff check"`) |

### Detection heuristics

The detector should recognize these patterns (in priority order):

| Signal | Language | Source Dir | Test Dir |
|--------|----------|------------|----------|
| `pyproject.toml` or `setup.py` | Python | `src/` or package dir | `tests/` |
| `package.json` | Node.js/TypeScript | `src/` or `lib/` | `test/` or `tests/` or `__tests__/` |
| `go.mod` | Go | `.` (module root) | `.` (co-located `_test.go`) |
| `Cargo.toml` | Rust | `src/` | `tests/` |
| `*.sln` or `*.csproj` | C# | auto from project file | auto from test project |

When multiple signals exist, prefer the one with the most files.

### Init flow

```
$ dark-factory init

Detected: Python project (pyproject.toml)
  Source dirs: src/myapp
  Test dir:    tests
  Framework:   FastAPI (from dependencies)

Docker images:
  Building df-python:latest ... done (42s)

Config written: .dark-factory/config.toml
Directory structure created: .dark-factory/{specs,scenarios,reviews,reports,audit,state}

Ready. Write a spec:
  dark-factory new-spec --template feature "Add user authentication"

Or run the pipeline on an existing spec:
  dark-factory .dark-factory/specs/my-spec.md
```

### Non-interactive mode

```
$ dark-factory init --non-interactive --language python
```

Uses detected defaults without prompting. For CI/CD bootstrapping.

**Complexity:** Large

---

## Sub-Phase 8B: Deployment Presets

**Items:** Named deployment presets (4), built-in health checks (5), auto-wired twin recording (6)

**Rationale:** The progressive deployment pipeline (Phase 6) and digital twins (Phase 7) require users to write shell commands for every deployment hook. Presets make the common cases zero-config while preserving full customizability.

### Deliverables

1. **Named deployment presets** — `[deploy] preset = "docker-compose"` auto-loads the reference scripts from `scripts/deploy/` with project-specific variable interpolation; supported presets: `docker-compose`, `k8s-argo`, `none` (default)
2. **Built-in health check strategies** — Ship 4 reusable health check implementations that cover 80% of use cases without custom scripts
3. **Auto-recording digital twins** — `[twins] mode = "auto"` records on first pipeline run, replays on subsequent runs

### Files to create

| File | Purpose |
|------|---------|
| `darkfactory/deployment/presets.py` | Preset loader: reads preset name, resolves script paths, interpolates project variables, populates config command fields |
| `darkfactory/deployment/health.py` | Built-in health check implementations: HTTP, TCP, Docker container status, process liveness |
| `tests/test_presets.py` | Preset loading, variable interpolation, fallback behavior |
| `tests/test_health_checks.py` | Each health check strategy: success, failure, timeout |

### Files to modify

| File | Change |
|------|--------|
| `darkfactory/models/config.py` | Add `deploy_preset: str = "none"`, `health_check_strategy: str = "http"`, `health_check_url: str = "http://localhost:8080/health"`, `twins_mode: str = "manual"` |
| `darkfactory/config/loader.py` | After loading config, if `deploy_preset != "none"`, call `apply_preset()` to populate empty command fields |
| `darkfactory/deployment/monitor.py` | When `rollback_monitor_cmd` is empty but `health_check_strategy` is set, use built-in health check |
| `darkfactory/deployment/environment.py` | When `deploy_cmd` is empty but `deploy_preset` is set, use preset scripts |
| `darkfactory/twins/manager.py` | Add `auto` mode: check if recording exists, record if not, replay if so |

### Preset resolution

```toml
# Minimal config — preset fills in the rest
[deploy]
preset = "docker-compose"
compose_file = "docker-compose.yml"  # optional override, defaults to project root

# Equivalent expanded config (what the preset generates):
# deploy_cmd = "scripts/deploy/docker-compose/canary-shift.sh"
# teardown_cmd = "docker compose -f docker-compose.yml down --remove-orphans"
# rollback_cmd = "scripts/deploy/docker-compose/rollback.sh"
# flag_create_cmd = ""  (no feature flags in compose preset)
# health_check_strategy = "http"
# health_check_url = "http://localhost:8080/health"
```

### Built-in health checks

| Strategy | Implementation | Config |
|----------|---------------|--------|
| `http` | `GET <url>`, expect 2xx, with `--connect-timeout 5 --max-time 10` | `health_check_url` |
| `tcp` | `socket.create_connection((host, port), timeout=5)` | `health_check_host`, `health_check_port` |
| `docker` | `docker inspect --format '{{.State.Health.Status}}' <container>` | `health_check_container` |
| `command` | Run arbitrary command, exit 0 = healthy | `rollback_monitor_cmd` (existing) |

**Complexity:** Medium

---

## Sub-Phase 8C: Spec Authoring Assistance

**Items:** Natural language spec generation (7), template auto-selection (8)

**Rationale:** The gap between "I want X" and a valid Dark Factory spec is the second-largest adoption barrier after initial setup. These items reduce spec authoring from 30+ minutes to under 5 minutes.

### Deliverables

1. **`dark-factory new-spec` with natural language input** — Takes a short description, uses Claude to generate a draft spec from the appropriate template, then runs the Spec Validator to surface gaps before the user finalizes
2. **Template auto-selection** — Classify the description into feature/bug-fix/refactor/API based on keywords and project context, select the matching template automatically

### Files to create

| File | Purpose |
|------|---------|
| `darkfactory/spec/generator.py` | Spec generation: template selection, prompt construction, Claude invocation, draft output |
| `tests/test_spec_generator.py` | Template classification, prompt construction, edge cases |

### Files to modify

| File | Change |
|------|--------|
| `darkfactory/cli.py` | Add `new-spec` subcommand: `dark-factory new-spec "Add JWT auth to /api/users"` with optional `--template` override |

### Template classification heuristics

| Keywords | Template |
|----------|----------|
| `fix`, `bug`, `crash`, `error`, `broken`, `regression` | `bug-fix` |
| `refactor`, `rename`, `extract`, `simplify`, `clean up`, `migrate` | `refactor` |
| `endpoint`, `route`, `API`, `REST`, `GraphQL`, `webhook` | `api` |
| (default) | `feature` |

### Generation flow

```
$ dark-factory new-spec "Add rate limiting to the /api/upload endpoint, 10 req/min per user"

Template: api (auto-detected from "endpoint")
Generating draft spec...

Draft written: .dark-factory/specs/add-rate-limiting.md

Running spec validator...
  Score: 85/100
  Missing:
    - EC-3: No error case for exceeded rate limit response format
    - NFR-1: No performance requirement for rate limit check latency

Edit the spec to address these gaps, then run:
  dark-factory .dark-factory/specs/add-rate-limiting.md
```

### Implementation notes

- Spec generation runs Claude in a single-shot prompt (not an agent session) to keep cost low (~$0.05 per generation)
- The prompt includes the full template text, the project's existing spec examples (if any), and the user's description
- The Spec Validator runs immediately after generation to provide actionable feedback
- `--template` flag overrides auto-detection when the user knows better

**Complexity:** Medium

---

## Sub-Phase 8D: Cost Calibration

**Items:** Cost estimation calibration (9)

**Rationale:** Turn-based cost estimation drifts from actual token spend. A one-time calibration brings estimates within 10-20% of reality without the complexity of real-time token parsing.

### Deliverables

1. **`dark-factory calibrate-cost` command** — Runs a small benchmark (3-5 short agent invocations), measures actual token usage from Claude API response metadata, computes a correction factor, writes it to config

### Files to create

| File | Purpose |
|------|---------|
| `darkfactory/intelligence/calibrate.py` | Benchmark runner: invoke agents with known prompts, parse token counts from output, compute correction factor |
| `tests/test_calibrate.py` | Factor computation, edge cases (no output, wildly different models) |

### Files to modify

| File | Change |
|------|--------|
| `darkfactory/cli.py` | Add `calibrate-cost` subcommand |
| `darkfactory/intelligence/cost.py` | Apply `config.cost_correction_factor` (default: 1.0) to all estimates |
| `darkfactory/models/config.py` | Add `cost_correction_factor: float = 1.0` |

### Calibration flow

```
$ dark-factory calibrate-cost

Running calibration benchmark (3 agent invocations)...
  Agent 1: estimated 2,400 tokens, actual 3,180 tokens
  Agent 2: estimated 2,400 tokens, actual 2,950 tokens
  Agent 3: estimated 2,400 tokens, actual 3,400 tokens

Correction factor: 1.32 (estimates were 24% low)
Written to .dark-factory/config.toml: cost_correction_factor = 1.32

Future cost estimates will be adjusted automatically.
```

**Complexity:** Small

---

## Sub-Phase 8E: Worked Example & Troubleshooting

**Items:** End-to-end example project (10), troubleshooting guide (11)

**Rationale:** Developers learn frameworks by running working examples. Documentation catches the users who hit edge cases the init command doesn't cover.

### Deliverables

1. **Example project** — A complete, runnable example (small REST API) with pre-written plan, 3 specs, `docker-compose.test.yml`, and a README walking through the entire flow from `dark-factory init` to shipped code
2. **Troubleshooting guide** — Maps exact error messages to fixes for the 15 most common failure modes

### Files to create

| File | Purpose |
|------|---------|
| `examples/rest-api/README.md` | Step-by-step walkthrough |
| `examples/rest-api/pyproject.toml` | Minimal FastAPI project |
| `examples/rest-api/src/app/main.py` | 3-endpoint REST API (health, users CRUD) |
| `examples/rest-api/tests/test_health.py` | Starter test |
| `examples/rest-api/.dark-factory/plans/rest-api-plan.md` | 3-spec plan (auth, CRUD, validation) |
| `examples/rest-api/.dark-factory/specs/01-health-endpoint.md` | Simple starter spec |
| `examples/rest-api/docker-compose.test.yml` | Working compose for ephemeral env |
| `docs/TROUBLESHOOTING.md` | Error → fix mapping |

### Troubleshooting coverage

The guide should cover these failure modes (ordered by frequency):

| Error | Cause | Fix |
|-------|-------|-----|
| `Docker daemon not running` | Docker Desktop not started | `open -a Docker` (macOS) / `sudo systemctl start docker` (Linux) |
| `claude: command not found` | Claude Code not installed | `npm install -g @anthropic-ai/claude-code` |
| `No spec file found` | Wrong path or missing `.md` extension | Show correct path format |
| `spec sanitization failed` | Spec contains prompt injection patterns | Show which line triggered, explain false positive override |
| `build failure at iteration 1` | Missing dependencies, wrong `BUILD_CMD` | Check `BUILD_CMD` runs successfully outside the pipeline |
| `budget exceeded` | `COST_BUDGET_USD` too low for spec complexity | Increase budget or reduce `MAX_ITERATIONS` |
| `Docker image not found` | Images not built or wrong tag | `dark-factory init` rebuilds, or `bash docker/build-images.sh <lang>` |
| `permission denied: .dark-factory/` | Directory owned by root (from Docker) | `sudo chown -R $(whoami) .dark-factory/` |
| `lock acquisition failed` | Stale lock from killed process | `rm -rf .dark-factory/state/*.lock` |
| `regression blocked ship` | Previously-shipped scenario now fails | Check which scenario failed, fix regression or update scenario |
| `transient failure after N retries` | Persistent infrastructure issue | Check Docker resources, network, API keys |
| `strategy switches exhausted` | Spec too complex for automated resolution | Decompose spec into smaller pieces |
| `no registered specs for regression` | First spec, no regression suite yet | Expected on first run — no action needed |
| `barrier violation detected` | Agent attempted cross-barrier read | Check container mounts, report if persistent |
| `health check timeout` | Deployment target not ready | Increase `rollback_poll_interval` or fix service startup |

**Complexity:** Medium

---

## Dependency Graph

```
8A (Zero-Config Bootstrap)
 |
 ├── 8B (Deployment Presets) — depends on config loader changes in 8A
 |
 ├── 8C (Spec Authoring) — depends on CLI structure from 8A
 |
 └── 8D (Cost Calibration) — independent, but benefits from init flow
      |
      └── 8E (Example & Docs) — should be written last, references all other sub-phases
```

8A must come first. 8B, 8C, and 8D can be implemented in parallel after 8A. 8E should be last because it documents the final user experience.

---

## Implementation Order

| Sub-Phase | Scope | New Files | Modified Files |
|-----------|-------|-----------|----------------|
| **8A** | Zero-config bootstrap | 4 + 2 test | 3 |
| **8B** | Deployment presets | 2 + 2 test | 5 |
| **8C** | Spec authoring | 1 + 1 test | 1 |
| **8D** | Cost calibration | 1 + 1 test | 3 |
| **8E** | Example & docs | 8 + 1 doc | 0 |
| **Total** | | **16 + 6 test** | **12** |

---

## Success Criteria

After Phase 8, a new user should be able to:

1. Clone the repo
2. Run `dark-factory init` (< 2 minutes including Docker build)
3. Run `dark-factory new-spec "Add user login"` (< 1 minute)
4. Edit the generated spec to add project-specific details (5 minutes)
5. Run `dark-factory .dark-factory/specs/add-user-login.md` and get shipped code

Total: **under 10 minutes** from clone to first pipeline run, with no manual config file editing required.
