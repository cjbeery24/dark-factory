"""Registry of brief, assertion-relevant documentation for common tools.

Injected into the Scenario Architect's execution context when the spec
references one of these tools (Phase 4 — Tool Documentation Injection).
"""

from __future__ import annotations

import re

# Registry of assertion-relevant documentation for common tools.
# Keys are display names used in the SA prompt heading.
TOOL_DOCS: dict[str, str] = {
    "biome": """
Biome Configuration (biome.json):
- Formatting options are NESTED under language-specific keys:
  - `javascript.formatter.quoteStyle` (not `formatter.quoteStyle`)
  - `javascript.formatter.trailingCommas` (not `formatter.trailingCommas`)
  - `javascript.formatter.indentStyle` and `javascript.formatter.indentWidth`
- Top-level `formatter` only has: `enabled`, `indentStyle`, `indentWidth`, `lineWidth`
- CLI: `biome check` runs both linter and formatter
- CLI: `biome format --write` auto-fixes formatting
""",
    "eslint": """
ESLint (Flat Config - eslint.config.js):
- `--print-config` requires a FILE path, not a directory (e.g., `--print-config src/App.tsx`)
- Flat config exports an array of config objects, not a single object
- Plugin names in flat config don't use `eslint-plugin-` prefix
""",
    "railway": """
Railway Configuration (railway.json):
- Uses NESTED structure: `build.buildCommand`, `deploy.startCommand`
- NOT flat: `buildCommand` at top level is invalid
- Alternative: railway.toml with `[build]` and `[deploy]` sections
""",
    "pnpm": """
pnpm Workspace:
- Workspace config is in `pnpm-workspace.yaml`, NOT `package.json` `workspaces` field
- `pnpm ls` output format varies by version; parse as structured data, not regex
- `pnpm --filter` accepts both package names (@scope/pkg) and directory paths (./apps/web)
- `pnpm exec` PATH is non-deterministic in containers; prefer direct binary invocation
""",
    "vitest": """
Vitest in Monorepos:
- Dynamic `import()` of workspace packages may not resolve in test context
- Prefer `execSync('node -e "require(...)"')` for testing module resolution
- `process.cwd()` in test files is the project root, not the scenario directory
""",
}

# Separate trigger patterns for tools where the tool name alone is too generic.
# Maps tool name → compiled regex. Tools not listed here use \b{name}\b.
_TOOL_TRIGGERS: dict[str, re.Pattern[str]] = {
    # "yaml" alone is too generic — matches any spec mentioning YAML files.
    # Trigger only when the spec discusses YAML assertions/parsing/workflows.
}

# YAML docs are injected via a dedicated key with a specific trigger.
TOOL_DOCS["yaml_assertions"] = """
YAML Assertions:
- YAML has multiple valid representations for the same data:
  - `branches: [main]` and `branches:\\n  - main` are identical
  - Quoted and unquoted strings are identical: `name: "foo"` = `name: foo`
- Parse YAML before asserting on structure. Do NOT use regex on YAML files.
- If you must use regex, use `/m` flag (multiline) and match key-value pairs, not structure.
"""
_TOOL_TRIGGERS["yaml_assertions"] = re.compile(
    r"\b(?:yaml\.(?:parse|safe_load|load)|\.ya?ml\b.*(?:assert|expect|verify|check))",
    re.IGNORECASE,
)
