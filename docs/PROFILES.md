# Per-Spec Configuration Profiles

Profiles let you define reusable configuration presets for groups of specs, and override individual settings directly in a spec's metadata block.

## Multi-language projects

If your project has multiple languages in a single repo (e.g., a Python backend and a React frontend), profiles are the recommended approach. Define one profile per language; each spec references the profile that matches its part of the codebase.

**`config.toml`:**

```toml
[core]
language = "node"   # fallback if a spec declares no profile

[profile.python-backend]
language = "python"
source_dirs = ["backend/src"]
test_dir = "backend/tests"
test_cmd = "pytest backend/tests/ -v"
scenario_test_cmd = "pytest {SCENARIOS_DIR} -v"
build_cmd = ""
lint_cmd = "ruff check backend/"
typecheck_cmd = "mypy backend/"

[profile.react-frontend]
language = "node"
source_dirs = ["frontend/src"]
test_dir = "frontend/tests"
test_cmd = "npm test --prefix frontend"
scenario_test_cmd = "npx vitest run {SCENARIOS_DIR}"
build_cmd = "npm run build --prefix frontend"
lint_cmd = "npm run lint --prefix frontend"
typecheck_cmd = "npx tsc --noEmit --project frontend/tsconfig.json"
```

Each spec then declares its profile:

```markdown
<!-- dark-factory-metadata
profile: python-backend
-->
```

The pipeline resolves the spec's config before launching any container. If the resolved `language` image (`dark-factory-python`, `dark-factory-node`, etc.) hasn't been built, the pipeline fails immediately with a clear error and the build command needed to fix it.

**Images you need to build:**

```bash
bash docker/build-images.sh python   # for python-backend specs
bash docker/build-images.sh node     # for react-frontend specs
bash docker/build-images.sh python   # also required for Format A (HTTP) scenarios in any language
```

> **See also:** [CONFIGURATION.md](CONFIGURATION.md) for the full config reference.

---

## What profiles are

By default every spec runs with the global config from `.dark-factory/config.toml`. Profiles let you override a subset of config fields for specific specs without touching the global config.

**Two mechanisms:**

1. **Named profiles** — Define a preset in `config.toml` (e.g., `[profile.browser]`), then reference it from a spec with `profile: browser` in the spec's metadata block.
2. **Inline spec metadata overrides** — Override individual fields directly in the spec's `<!-- dark-factory-metadata ... -->` block, without needing a named profile.

Both mechanisms can be combined: reference a profile *and* add inline overrides — the inline overrides take precedence.

---

## Precedence order

When multiple sources set the same field, the higher-precedence value wins:

```
Per-spec inline override  (highest)
    ↓
Named profile value
    ↓
Global config.toml default  (lowest)
```

---

## Named profiles in `config.toml`

Define a named profile by adding a `[profile.<name>]` table to your `config.toml`. The profile name can be any valid TOML key.

```toml
# .dark-factory/config.toml

[profile.browser]
language = "node"
browser_base_url = "http://localhost:3000"
browser_server_cmd = "npm start"
browser_startup_timeout = 90

[profile.go-payments]
language = "go"
lint_cmd = "golangci-lint run ./internal/payments/..."
test_cmd = "go test ./internal/payments/... -v"
scenario_test_cmd = "bash .dark-factory/run-go-scenarios.sh {SCENARIOS_DIR}"
```

Profile keys use underscores (TOML style). See [Overridable fields](#overridable-fields) for the complete list.

---

## Spec metadata block

To apply a profile (or inline overrides) to a spec, add a metadata block anywhere in the spec file. The block is an HTML comment and is invisible in rendered Markdown.

**Syntax:**

```markdown
<!-- dark-factory-metadata
profile: <profile-name>
<field>: <value>
<field>: <value>
-->
```

The block ends at the first `-->`. Fields use kebab-case (e.g., `browser-base-url`, not `browser_base_url`).

**Example — spec using the `browser` profile with an extra override:**

```markdown
<!-- dark-factory-metadata
profile: browser
browser-startup-timeout: 120
-->

# Spec: Dashboard checkout flow

...
```

**Example — inline-only (no named profile):**

```markdown
<!-- dark-factory-metadata
lint-cmd: golangci-lint run ./internal/orders/...
test-cmd: go test ./internal/orders/... -v
-->
```

---

## Overridable fields

There are **15 overridable fields**. The table shows the spec metadata key (kebab-case) and its corresponding profile TOML key (underscore) and `PipelineConfig` field.

| Spec metadata key | Profile TOML key | Config field | Type | Notes |
|-------------------|-----------------|-------------|------|-------|
| `language` | `language` | `language` | string | Must be `node`, `python`, `go`, `rust`, or `unknown` |
| `source-dirs` | `source_dirs` | `source_dirs` | string/list | Colon-delimited in metadata (`"src:lib"`); list in profile TOML |
| `test-dir` | `test_dir` | `test_dir` | string | |
| `test-cmd` | `test_cmd` | `test_cmd` | string | |
| `lint-cmd` | `lint_cmd` | `lint_cmd` | string | |
| `build-cmd` | `build_cmd` | `build_cmd` | string | |
| `typecheck-cmd` | `typecheck_cmd` | `typecheck_cmd` | string | |
| `scenario-test-cmd` | `scenario_test_cmd` | `scenario_test_cmd` | string | Use `{SCENARIOS_DIR}` placeholder |
| `test-server-cmd` | `test_server_cmd` | `test_server_cmd` | string | |
| `test-base-url` | `test_base_url` | `test_base_url` | string | |
| `browser-server-cmd` | `browser_server_cmd` | `browser_server_cmd` | string | |
| `browser-base-url` | `browser_base_url` | `browser_base_url` | string | |
| `server-health-path` | `server_health_path` | `server_health_path` | string | |
| `server-startup-timeout` | `server_startup_timeout` | `server_startup_timeout` | int | Must be >= 1 |
| `browser-startup-timeout` | `browser_startup_timeout` | `browser_startup_timeout` | int | Must be >= 1 |

The `profile` key in spec metadata is special — it selects the named profile and is not a config field itself.

> Fields not in this table cannot be overridden per-spec. Configuration like `autonomy_level`, `cost_budget_usd`, and `max_iterations` is global only.

---

## Examples

### Browser spec using a named profile

`config.toml`:
```toml
[profile.browser]
language = "node"
browser_base_url = "http://localhost:3000"
browser_server_cmd = "npm start"
browser_startup_timeout = 90
```

Spec `checkout-flow.md`:
```markdown
<!-- dark-factory-metadata
profile: browser
-->

# Spec: Checkout flow
...
```

### Python backend spec with a per-spec server override

```markdown
<!-- dark-factory-metadata
profile: python-backend
test-server-cmd: uvicorn orders.main:app --port 9000
test-base-url: http://localhost:9000
server-startup-timeout: 45
-->

# Spec: Orders refund endpoint
...
```

### Microservice spec overriding lint scope

`config.toml`:
```toml
[core]
language = "go"
lint_cmd = "golangci-lint run ./..."
test_cmd = "go test ./... -v"
```

Spec `payments-refund.md`:
```markdown
<!-- dark-factory-metadata
lint-cmd: golangci-lint run ./internal/payments/...
test-cmd: go test ./internal/payments/... -v
-->

# Spec: Payments refund endpoint
...
```

### Multiple source dirs with colon delimiter

```markdown
<!-- dark-factory-metadata
source-dirs: src:lib:pkg
-->
```

---

## Known limitation

If a spec metadata override sets a field back to its global default value, the override has no visible effect. For example, if `[core] language = "node"` globally and a spec sets `language: node`, nothing changes — this is intentional (the resolved value equals the current value and no replacement is made).

---

## Validation

- If a spec's `profile:` value references a name that does not exist as a `[profile.<name>]` table in `config.toml`, the pipeline raises a `ConfigError` before the spec runs.
- Integer fields (`server-startup-timeout`, `browser-startup-timeout`) in spec metadata must parse as integers; non-integer values raise `ConfigError`.
- Empty values (e.g., `lint-cmd:`) in spec metadata are treated as "not set" and do not override the profile or global value.
