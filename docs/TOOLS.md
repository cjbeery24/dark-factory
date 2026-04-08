# Recommended Tools & Setup

## Required Tools

### Docker (Container Isolation)
Docker provides the primary information barrier enforcement. Each agent runs in a Docker container with restricted filesystem mounts.

- **Install:** [Docker Desktop](https://docs.docker.com/get-docker/) (macOS/Windows) or Docker Engine (Linux)
- **Build images:** `bash docker/build-images.sh <language>` (node, python, go, rust, or all)
- **Licensing:** Docker Engine is free for all uses on Linux. Docker Desktop requires a paid license for organizations with >250 employees or >$10M annual revenue.

**Docker images included:**
| Image | Base | Additional Tools |
|-------|------|-----------------|
| `dark-factory-base` | `node:22-alpine` | bash, git, claude-code |
| `dark-factory-node` | base | typescript, eslint, prettier |
| `dark-factory-python` | base | python3, ruff, mypy, pytest |
| `dark-factory-playwright` | `python:3.12-slim` (Debian, standalone) | system chromium, playwright (Python) |
| `dark-factory-playwright-node` | node | system chromium, @playwright/test (Node) |
| `dark-factory-go` | base | go |
| `dark-factory-rust` | base | rust, cargo |

The `dark-factory-playwright` image is built automatically for all languages. The `dark-factory-playwright-node` image is built automatically for node and all. Both are included in every `dark-factory build` invocation:

```bash
dark-factory build            # auto-detects language from config
dark-factory build python     # or: node, go, rust, all
```

**Cache volume management:**
Per-role cache volumes prevent cache poisoning between agents:
```bash
# List Dark Factory cache volumes
docker volume ls --filter name=df-

# Clean all Dark Factory caches
docker volume ls --filter name=df- -q | xargs -r docker volume rm
```

### Claude Code (Primary AI Agent)
The Dark Factory pipeline runs on Claude Code as the execution engine for all agents.

- **Install:** `npm install -g @anthropic-ai/claude-code`
- **Usage:** Each agent runs in a separate Docker container with `claude -p --dangerously-skip-permissions`
- **Configuration:** CLAUDE.md at project root configures agent behavior

### Git
Version control is non-negotiable. Every spec, implementation, and scenario should be tracked.

- Specs go in `.dark-factory/specs/` (committed)
- Scenarios go in `.dark-factory/scenarios/` (committed, but implementer containers never mount them)
- Reviews and reports go in `.dark-factory/reviews/` and `.dark-factory/reports/`

---

## Testing Tools (by Language)

### TypeScript / JavaScript

| Tool | Purpose | Install |
|------|---------|---------|
| **Vitest** | Fast unit/integration testing | `npm i -D vitest` |
| **fast-check** | Property-based testing | `npm i -D fast-check` |
| **Stryker** | Mutation testing | `npm i -D @stryker-mutator/core` |
| **Supertest** | HTTP API testing | `npm i -D supertest` |
| **Playwright** | E2E / browser testing | `npm i -D @playwright/test` |
| **c8 / istanbul** | Code coverage | `npm i -D c8` |

**Recommended setup:**
```json
// package.json
{
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    "test:coverage": "vitest run --coverage",
    "test:mutation": "stryker run",
    "test:scenarios": "vitest run .dark-factory/scenarios/"
  }
}
```

### Python

| Tool | Purpose | Install |
|------|---------|---------|
| **pytest** | Testing framework | `pip install pytest` |
| **Hypothesis** | Property-based testing | `pip install hypothesis` |
| **mutmut** | Mutation testing | `pip install mutmut` |
| **httpx** | Async HTTP testing | `pip install httpx` |
| **coverage** | Code coverage | `pip install coverage` |
| **pytest-xdist** | Parallel test execution | `pip install pytest-xdist` |

**Recommended setup:**
```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests", ".dark-factory/scenarios"]

[tool.mutmut]
paths_to_mutate = "src/"
tests_dir = "tests/"
```

### Go

| Tool | Purpose | Install |
|------|---------|---------|
| **testing** | Built-in test framework | (included) |
| **gopter** | Property-based testing | `go get github.com/leanovate/gopter` |
| **go-mutesting** | Mutation testing | `go install github.com/zimmski/go-mutesting/cmd/go-mutesting` |
| **golangci-lint** | Linting | `go install github.com/golangci/golangci-lint/cmd/golangci-lint` |

### Rust

| Tool | Purpose | Install |
|------|---------|---------|
| **cargo test** | Built-in test framework | (included) |
| **proptest** | Property-based testing | Add to Cargo.toml dev-dependencies |
| **cargo-mutants** | Mutation testing | `cargo install cargo-mutants` |
| **clippy** | Linting | `rustup component add clippy` |

---

## Static Analysis Tools

### Multi-Language

| Tool | Purpose | Languages |
|------|---------|-----------|
| **SonarQube** | Comprehensive static analysis | All major languages |
| **Semgrep** | Pattern-based code scanning | All major languages |
| **CodeQL** | Security analysis (GitHub) | All major languages |

### Language-Specific

| Tool | Language | Purpose |
|------|----------|---------|
| **ESLint** | JS/TS | Linting + code quality |
| **Biome** | JS/TS | Fast linter + formatter |
| **ruff** | Python | Fast Python linter |
| **mypy / pyright** | Python | Type checking |
| **golangci-lint** | Go | Aggregated linting |
| **clippy** | Rust | Linting |

---

## CI/CD Integration

### GitHub Actions (Recommended)

Dark Factory includes a GitHub Actions workflow at `.github/workflows/dark-factory.yml` for full pipeline runs via `workflow_dispatch`.

For quality gate checks on PRs, add this workflow:

```yaml
# .github/workflows/quality-gate.yml
name: Dark Factory Quality Gate

on:
  pull_request:
    branches: [main]

jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build Docker images
        run: bash docker/build-images.sh node

      - name: Run quality gate
        run: |
          docker run --rm \
            -v "${PWD}:/workspace:rw" \
            --entrypoint bash \
            dark-factory-node \
            /workspace/scripts/quality-gate.sh my-feature

      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: quality-gate-report
          path: .dark-factory/reports/
```

---

## Project Structure Recommendations

### Monorepo

```
project/
├── .dark-factory/           # Dark factory runtime artifacts
│   ├── specs/
│   ├── scenarios/
│   ├── reviews/
│   └── reports/
├── src/                     # Source code
├── tests/                   # Implementation's own tests
├── .github/workflows/       # CI/CD
├── CLAUDE.md               # Claude Code project config
└── dark-factory/            # Dark factory framework (or reference)
    ├── AGENT.md
    ├── agents/
    ├── skills/
    ├── templates/
    └── scripts/
```

### Per-Project Integration

The fastest way to integrate Dark Factory into an existing project:

```bash
cd /path/to/your/project
dark-factory init
```

This creates the directory structure, auto-detects your language, generates a config, and updates your CLAUDE.md.

For detailed instructions, language-specific configuration, monorepo guidance, and manual setup steps, see **[docs/INTEGRATION.md](INTEGRATION.md)**.

For a 5-step quick start, see **[docs/QUICKSTART.md](QUICKSTART.md)**.

---

## HTTP and Browser Scenario Runners

Dark Factory supports four scenario formats. Format C/D (executable tests) run inside the
language container. Format A and B are **orchestrated by the Python pipeline in dedicated
Docker containers** — `dark-factory-python` for HTTP scenarios and `dark-factory-playwright`
for browser journeys. This ensures each runner has exactly the dependencies it needs,
independent of the project language.

### Format A — HTTP Behavioral Scenarios (`*.scenarios.md`) — **RETIRED (Phase 4.6b.1a)**

Format A and its `scripts/lib/http-scenario-runner.py` were retired in Phase 4.6b.1a. HTTP behavioral testing is now done by the Test Engineer writing native executable tests (vitest + `fastify.inject`, pytest + httpx, supertest, etc.) that run in the project's configured test framework. The sections below are preserved only as historical reference.

**Example:**
```markdown
# Scenario: reject invalid email format
# Requirement: EC-1

## Request
POST /api/users/register
Content-Type: application/json
{"email": "not-an-email", "password": "correct-horse"}

## Expect
- Status: 422
- Body contains field "error"
- Body field "error" contains "email"
```

**Multi-step with capture:**
```markdown
# Scenario: created resource is retrievable
# Requirement: FR-2

## Step 1
POST /api/items
Content-Type: application/json
{"name": "widget"}

## Expect
- Status: 201
- Capture: item_id = body.id

## Step 2
GET /api/items/{item_id}

## Expect
- Status: 200
- Body field "name" equals "widget"
```

> **Note:** Captured values can be used in URL paths (`{item_id}` in the path above) but **not** in request bodies. Body interpolation is not supported in v1.

**Supported assertions:**
| Assertion | Example |
|-----------|---------|
| Status code | `- Status: 201` |
| Body field exists | `- Body contains field "token"` |
| Body field equals | `- Body field "name" equals "Alice"` |
| Body field contains | `- Body field "error" contains "email"` |
| Header contains | `- Header "Content-Type" contains "json"` |
| Capture value | `- Capture: my_id = body.id` |

**Prerequisites:** `dark-factory-python` image must be built (required for all projects,
not just Python). It is built automatically with any `dark-factory build` invocation:
```bash
dark-factory build            # auto-detects language from config
dark-factory build go         # or: node, python, rust, all
```

**Server configuration** (in `[test_server]` TOML section):
```toml
[test_server]
base_url = "http://localhost:8000"
server_cmd = "uvicorn myapp:app --port 8000"   # optional: omit if server is external
startup_timeout = 30
health_path = "/health"
```

### Format B — Browser Journey Scenarios (`*.journey.md`)

Written as a numbered step list. Executed by `scripts/lib/journey-runner.py` using [Playwright](https://playwright.dev/python/). Requires the `dark-factory-playwright` Docker image.

**Example:**
```markdown
# Scenario: user can log in and see dashboard
# Requirement: AUTH-1

## Steps
1. navigate: /login
2. fill: "Email" with "user@example.com"
3. fill: "Password" with "secret"
4. click: Sign In
5. assert url: /dashboard
6. assert visible: "Welcome"
```

**Supported step actions:**
| Action | Example |
|--------|---------|
| Navigate | `navigate: /login` |
| Click (label or CSS) | `click: Sign In` / `click: .btn-primary` |
| Fill form field (label) | `fill: "Email" with "user@example.com"` |
| Fill form field (CSS) | `fill: .email-input with "user@example.com"` |
| Assert text visible | `assert visible: "Welcome"` |
| Assert current URL | `assert url: /dashboard` |
| Assert text hidden | `assert not visible: "Error"` |
| Wait | `wait: 500 ms` |

**Server configuration** (in `[test_server]` TOML section):
```toml
[test_server]
browser_base_url = "http://localhost:3000"
browser_server_cmd = "npm start"              # optional: omit if server is external
browser_startup_timeout = 60
```

**Setup:** The Playwright image is built automatically with `dark-factory build`.

### Running Runners Directly (Debug)

```bash
# HTTP runner
python3 scripts/lib/http-scenario-runner.py .dark-factory/scenarios/my-spec \
    --base-url http://localhost:8000 \
    --reports-dir .dark-factory/reports \
    --spec-name my-spec

# Journey runner
python3 scripts/lib/journey-runner.py .dark-factory/scenarios/my-spec \
    --base-url http://localhost:3000 \
    --reports-dir .dark-factory/reports \
    --spec-name my-spec \
    --headed   # show browser window
```

---

## Optional Advanced Tools

### Contract Testing
- **Pact** — Consumer-driven contract testing for microservices
- Ensures API producers and consumers agree on interface contracts

### Chaos Engineering
- **Chaos Monkey** / **Litmus** — For testing resilience
- Run after the basic pipeline passes to verify fault tolerance

### Performance Testing
- **k6** — Scriptable load testing
- **autocannon** — HTTP benchmarking for Node.js
- **locust** — Python-based load testing

### Security Scanning
- **Snyk** — Dependency vulnerability scanning
- **Trivy** — Container and dependency scanning
- **OWASP ZAP** — Dynamic application security testing

### Documentation Generation
- **TypeDoc** / **JSDoc** — API documentation from types/comments
- **Sphinx** — Python documentation
- **godoc** — Go documentation

---

## Recommended VS Code Extensions (for manual review)

While Dark Factory is designed for automated workflows, you'll occasionally review code manually:

- **Error Lens** — Inline error/warning display
- **GitLens** — Git blame and history
- **Todo Tree** — Track TODOs and FIXMEs
- **Coverage Gutters** — Visualize test coverage in editor
