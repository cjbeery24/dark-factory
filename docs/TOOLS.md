# Recommended Tools & Setup

## Required Tools

### Claude Code (Primary AI Agent)
The Dark Factory pipeline runs on Claude Code as the execution engine for all agents.

- **Install:** `npm install -g @anthropic-ai/claude-code`
- **Usage:** Each agent role runs in a separate `claude` session to enforce information barriers
- **Configuration:** CLAUDE.md at project root configures agent behavior

### Git
Version control is non-negotiable. Every spec, implementation, and scenario should be tracked.

- Specs go in `.dark-factory/specs/` (committed)
- Scenarios go in `.dark-factory/scenarios/` (committed, but implementer sessions never read them)
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

```yaml
# .github/workflows/dark-factory.yml
name: Dark Factory Quality Gate

on:
  pull_request:
    branches: [main]

jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup
        uses: actions/setup-node@v4  # or setup-python, setup-go, etc.
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Build
        run: npm run build

      - name: Lint
        run: npm run lint

      - name: Type check
        run: npm run typecheck

      - name: Unit tests
        run: npm test

      - name: Blind scenarios
        run: npm run test:scenarios

      - name: Coverage report
        run: npm run test:coverage

      - name: Mutation testing (optional, slow)
        if: github.event.pull_request.draft == false
        run: npm run test:mutation
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
./scripts/integrate.sh /path/to/your/project
```

This creates the directory structure, copies framework files, auto-detects your language, generates a config, and updates your CLAUDE.md.

For detailed instructions, language-specific configuration, monorepo guidance, and manual setup steps, see **[docs/INTEGRATION.md](INTEGRATION.md)**.

For a 5-step quick start, see **[docs/QUICKSTART.md](QUICKSTART.md)**.

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
