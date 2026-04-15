"""A3 — library-symbol existence check (layered).

Covers Layer 1 (dependency declaration). Layer 2 (symbol existence) is
scaffolded but currently a no-op; add tests when it lands.

Motivated by 2026-04-14 database-foundation escalation. See
``docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md``
(Sprint 2, A3).
"""
from __future__ import annotations

import json
from pathlib import Path

from darkfactory.spec.spec_model import parse_spec_model
from darkfactory.adapters.typescript.library_symbols import (
    LibrarySymbolFinding,
    _parse_pnpm_packages,
    build_dependency_index,
    check_library_symbols,
    extract_spec_imports,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SPEC_WITH_IMPORTS = """# Feature

## 5. Interface Contract

### Types

```typescript
// apps/api/src/db/index.ts
import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import { FastifyPluginAsync } from 'fastify';
import { z } from 'zod';
```

### Config

```typescript
// apps/api/drizzle.config.ts
import { defineConfig } from 'drizzle-kit';
```
"""


def _write_pkg(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj))


def _bare_monorepo(root: Path) -> None:
    """Create a minimal pnpm-workspace monorepo with one app."""
    (root / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'apps/*'\n  - 'packages/*'\n"
    )
    _write_pkg(root / "package.json", {
        "name": "monorepo-root",
        "devDependencies": {"typescript": "^5.0.0"},
    })
    (root / "apps").mkdir()
    (root / "apps" / "api").mkdir()
    _write_pkg(root / "apps" / "api" / "package.json", {
        "name": "@bb/api",
        "dependencies": {
            "drizzle-orm": "^0.44.0",
            "postgres": "^3.4.0",
            "fastify": "^5.0.0",
            "zod": "^3.0.0",
        },
        "devDependencies": {"drizzle-kit": "^0.31.0"},
    })


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------


def test_extract_imports_only_from_interface_contract():
    markdown = (
        "# Spec\n\n"
        "## 2. Functional Requirements\n\n"
        "```typescript\nimport { foo } from 'should-be-ignored';\n```\n\n"
        "## 5. Interface Contract\n\n"
        "```typescript\nimport { bar } from 'should-be-found';\n```\n"
    )
    model = parse_spec_model(markdown)
    imports = extract_spec_imports(model)
    pkgs = [pkg for _, pkg, _ in imports]
    assert "should-be-found" in pkgs
    assert "should-be-ignored" not in pkgs


def test_extract_imports_skips_relative_paths():
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "import { foo } from './local';\n"
        "import { bar } from '../../sibling';\n"
        "import { baz } from 'real-lib';\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    pkgs = [pkg for _, pkg, _ in extract_spec_imports(model)]
    assert pkgs == ["real-lib"]


def test_extract_imports_skips_node_builtins():
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "import * as fs from 'fs';\n"
        "import { readFile } from 'node:fs/promises';\n"
        "import crypto from 'crypto';\n"
        "import { customLib } from 'custom-lib';\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    pkgs = [pkg for _, pkg, _ in extract_spec_imports(model)]
    assert pkgs == ["custom-lib"]


def test_extract_imports_normalizes_subpath_and_scopes():
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "import { foo } from 'drizzle-orm/pg-core';\n"
        "import { bar } from '@fastify/cors';\n"
        "import { baz } from '@fastify/cors/plugin';\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    pkgs = [pkg for _, pkg, _ in extract_spec_imports(model)]
    assert pkgs == ["drizzle-orm", "@fastify/cors", "@fastify/cors"]


def test_extract_imports_ignores_non_typescript_fences():
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```json\n"
        '{"dependencies": {"fake-pkg": "1.0.0"}}\n'
        "```\n\n"
        "```sql\n"
        "SELECT * FROM users;\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    assert extract_spec_imports(model) == []


def test_extract_imports_matches_require_form():
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "const foo = require('legacy-cjs-lib');\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    pkgs = [pkg for _, pkg, _ in extract_spec_imports(model)]
    assert pkgs == ["legacy-cjs-lib"]


def test_extract_imports_matches_dynamic_import_with_literal():
    """Regression test for Linus-C2: ``import('x')`` with a literal
    string is still a static dependency declaration and must be caught."""
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "const mod = await import('lazy-route-handler');\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    pkgs = [pkg for _, pkg, _ in extract_spec_imports(model)]
    assert pkgs == ["lazy-route-handler"]


def test_extract_imports_covers_mts_and_cts_fences():
    """mts/cts are Node ESM/CJS TypeScript fence aliases; treat as ts."""
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```mts\nimport { a } from 'esm-only';\n```\n"
        "```cts\nconst b = require('cjs-only');\n```\n"
    )
    model = parse_spec_model(markdown)
    pkgs = [pkg for _, pkg, _ in extract_spec_imports(model)]
    assert pkgs == ["esm-only", "cjs-only"]


def test_malformed_scoped_import_is_ignored_not_crashed():
    """A bare ``@foo`` (no slash) is malformed; don't raise, just drop it."""
    markdown = (
        "# Spec\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "import { x } from '@foo';\n"
        "import { y } from 'real-lib';\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    pkgs = [pkg for _, pkg, _ in extract_spec_imports(model)]
    assert pkgs == ["real-lib"]


# ---------------------------------------------------------------------------
# Workspace pattern parsing — no PyYAML dep
# ---------------------------------------------------------------------------


def test_pnpm_workspace_parse_handles_quoted_and_unquoted():
    text = (
        "packages:\n"
        "  - 'apps/*'\n"
        "  - packages/*\n"
        '  - "tools/*"\n'
    )
    assert _parse_pnpm_packages(text) == ["apps/*", "packages/*", "tools/*"]


def test_pnpm_workspace_parse_stops_at_next_top_level_key():
    text = (
        "packages:\n"
        "  - 'apps/*'\n"
        "other:\n"
        "  - should-not-appear\n"
    )
    assert _parse_pnpm_packages(text) == ["apps/*"]


def test_pnpm_workspace_parse_skips_comments_and_blanks():
    text = (
        "# top comment\n"
        "packages:\n"
        "\n"
        "  # inline comment\n"
        "  - 'apps/*'\n"
    )
    assert _parse_pnpm_packages(text) == ["apps/*"]


def test_pnpm_workspace_parse_drops_negations_at_scanner():
    """We don't support ``!pattern`` semantics — drop them at the parser
    so downstream callers see a consistent set."""
    text = (
        "packages:\n"
        "  - 'apps/*'\n"
        "  - '!apps/excluded'\n"
    )
    assert _parse_pnpm_packages(text) == ["apps/*"]


def test_pnpm_workspace_parse_ignores_nested_packages_key():
    """A nested ``packages:`` under another top-level map is NOT the
    workspace anchor — pnpm uses the column-0 ``packages:`` only.

    Regression test for Linus-C1: without the column check, the scanner
    would pick up ``should-not-count`` from the nested block.
    """
    text = (
        "catalog:\n"
        "  packages:\n"
        "    - should-not-count\n"
        "packages:\n"
        "  - 'apps/*'\n"
    )
    assert _parse_pnpm_packages(text) == ["apps/*"]


# ---------------------------------------------------------------------------
# Dependency index — workspace walk
# ---------------------------------------------------------------------------


def test_single_package_repo_yields_one_manifest(tmp_path):
    _write_pkg(tmp_path / "package.json", {
        "name": "solo",
        "dependencies": {"lodash": "^4.0.0"},
    })
    idx = build_dependency_index(tmp_path)
    assert len(idx.manifests) == 1
    assert idx.any_declares("lodash")
    assert not idx.any_declares("unknown")


def test_pnpm_workspace_walk_finds_sibling_package(tmp_path):
    _bare_monorepo(tmp_path)
    idx = build_dependency_index(tmp_path)
    names = {m.name for m in idx.manifests}
    assert names == {"monorepo-root", "@bb/api"}
    # A dep declared in apps/api shows up through the index.
    assert idx.any_declares("drizzle-orm")
    assert idx.any_declares("zod")
    # A devDep in apps/api is also visible.
    assert idx.any_declares("drizzle-kit")
    # Root-only devDep is visible.
    assert idx.any_declares("typescript")


def test_workspace_names_are_recorded_as_exempt(tmp_path):
    _bare_monorepo(tmp_path)
    idx = build_dependency_index(tmp_path)
    assert "@bb/api" in idx.workspace_names
    assert idx.is_workspace("@bb/api")
    assert not idx.is_workspace("drizzle-orm")


def test_npm_workspaces_flat_format(tmp_path):
    """npm/yarn flat: ``workspaces`` in root package.json, no pnpm yaml."""
    _write_pkg(tmp_path / "package.json", {
        "name": "flat-root",
        "workspaces": ["apps/*"],
        "devDependencies": {"jest": "^29.0.0"},
    })
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "svc").mkdir()
    _write_pkg(tmp_path / "apps" / "svc" / "package.json", {
        "name": "svc",
        "dependencies": {"express": "^4.0.0"},
    })
    idx = build_dependency_index(tmp_path)
    assert idx.any_declares("jest")
    assert idx.any_declares("express")


def test_malformed_package_json_is_tolerated(tmp_path):
    _write_pkg(tmp_path / "package.json", {"name": "root"})
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "bad").mkdir()
    # Write non-JSON into the child — the index should still build.
    (tmp_path / "apps" / "bad" / "package.json").write_text("not json {")
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'apps/*'\n"
    )
    idx = build_dependency_index(tmp_path)
    # Only the root manifest survives.
    assert {m.name for m in idx.manifests} == {"root"}


# ---------------------------------------------------------------------------
# End-to-end: check_library_symbols
# ---------------------------------------------------------------------------


def test_no_project_dir_is_noop():
    model = parse_spec_model(_SPEC_WITH_IMPORTS)
    assert check_library_symbols(model, project_dir=None) == []


def test_all_declared_imports_produce_zero_findings(tmp_path):
    _bare_monorepo(tmp_path)
    model = parse_spec_model(_SPEC_WITH_IMPORTS)
    assert check_library_symbols(model, project_dir=tmp_path) == []


def test_undeclared_import_produces_finding(tmp_path):
    _bare_monorepo(tmp_path)
    markdown = (
        "# Feature\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "import { drizzle } from 'drizzle-orm';\n"
        "import { ghost } from 'ghost-lib';\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    findings = check_library_symbols(model, project_dir=tmp_path)
    assert len(findings) == 1
    assert findings[0].imported_package == "ghost-lib"
    assert findings[0].layer == "dependency"
    # The finding must surface the line of the opening fence, not 0.
    assert findings[0].line_number > 0
    # The diagnostic must name the package and the project for the human.
    assert "ghost-lib" in findings[0].message


def test_workspace_package_imports_are_exempt(tmp_path):
    _bare_monorepo(tmp_path)
    markdown = (
        "# Feature\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "import { sharedThing } from '@bb/shared';\n"
        "```\n"
    )
    # Register @bb/shared as a workspace but don't declare it as a dep
    # anywhere — the workspace exemption alone must keep it clean.
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "shared").mkdir()
    _write_pkg(tmp_path / "packages" / "shared" / "package.json", {
        "name": "@bb/shared",
    })
    model = parse_spec_model(markdown)
    assert check_library_symbols(model, project_dir=tmp_path) == []


def test_duplicate_undeclared_imports_dedupe_to_one_finding(tmp_path):
    _bare_monorepo(tmp_path)
    markdown = (
        "# Feature\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "import { a } from 'ghost-lib';\n"
        "import { b } from 'ghost-lib/sub';\n"
        "```\n"
        "```typescript\n"
        "import { c } from 'ghost-lib/other';\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    findings = check_library_symbols(model, project_dir=tmp_path)
    assert len(findings) == 1
    assert findings[0].imported_package == "ghost-lib"


def test_typescript_subpath_of_declared_package_is_clean(tmp_path):
    _bare_monorepo(tmp_path)
    markdown = (
        "# Feature\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "import { pgTable } from 'drizzle-orm/pg-core';\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    # Layer 1 only checks the bare package — subpath-existence is Layer 2.
    assert check_library_symbols(model, project_dir=tmp_path) == []


def test_scoped_package_under_declared_namespace_still_needs_declaration(tmp_path):
    """``@fastify/cors`` is not the same package as ``fastify``.

    Declaring ``fastify`` does NOT satisfy an import of ``@fastify/cors``.
    """
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "solo",
        "dependencies": {"fastify": "^5.0.0"},
    }))
    markdown = (
        "# Feature\n\n## 5. Interface Contract\n\n"
        "```typescript\n"
        "import cors from '@fastify/cors';\n"
        "```\n"
    )
    model = parse_spec_model(markdown)
    findings = check_library_symbols(model, project_dir=tmp_path)
    assert len(findings) == 1
    assert findings[0].imported_package == "@fastify/cors"


def test_import_outside_interface_contract_not_flagged(tmp_path):
    """Imports in §2/§11 examples aren't the contract; A3 must ignore them."""
    _bare_monorepo(tmp_path)
    markdown = (
        "# Feature\n\n"
        "## 2. Functional Requirements\n\n"
        "```typescript\nimport { ghost } from 'example-only';\n```\n\n"
        "## 11. Dependencies\n\n"
        "```typescript\nimport { ghost } from 'doc-only';\n```\n"
    )
    model = parse_spec_model(markdown)
    assert check_library_symbols(model, project_dir=tmp_path) == []
