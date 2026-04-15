"""B1 — project-hints.toml loader tests.

Covers:

* Happy-path parse for both [detected] and [policy] sections.
* Empty-file and empty-section correctness (loader returns functional
  defaults, not ``None``).
* Strict structural validation: every field's type is enforced; malformed
  consistency rules raise ``ConfigError`` with enough context to locate
  the offending entry.
* Forward-compat: unknown keys at any level log a WARN but do not raise.
* Backward-compat synthesis from ``environment.toml`` + ``hints.json``
  when ``project-hints.toml`` is absent.
* Duplicate consistency-rule names are rejected.
* ``schema_version`` is int-typed (``bool`` does not leak through as int).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from darkfactory.architecture.project_hints import (
    CURRENT_SCHEMA_VERSION,
    ConsistencyRuleSpec,
    DetectedFacts,
    MigrationsPolicy,
    PolicySection,
    ProjectHints,
    WorkspacePolicy,
    load_project_hints,
)
from darkfactory.errors import ConfigError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _hints_path(project_dir: Path) -> Path:
    return project_dir / ".dark-factory" / "project-hints.toml"


def _write_hints(project_dir: Path, body: str) -> Path:
    p = _hints_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _write_legacy(
    project_dir: Path, *, hints_json: dict | None = None,
    env_toml_body: str | None = None,
) -> None:
    arch = project_dir / ".dark-factory" / "architecture"
    arch.mkdir(parents=True, exist_ok=True)
    if hints_json is not None:
        (arch / "hints.json").write_text(
            json.dumps(hints_json), encoding="utf-8",
        )
    if env_toml_body is not None:
        (arch / "environment.toml").write_text(env_toml_body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Missing file → empty hints
# ---------------------------------------------------------------------------


def test_missing_file_returns_empty_hints(tmp_path):
    hints = load_project_hints(tmp_path)
    assert hints == ProjectHints.empty()
    assert hints.detected == DetectedFacts()
    assert hints.policy == PolicySection()
    assert hints.policy.is_empty is True
    assert hints.source_path is None
    assert hints.synthesized_from_legacy is False


def test_empty_file_returns_empty_hints(tmp_path):
    path = _write_hints(tmp_path, "")
    hints = load_project_hints(tmp_path)
    assert hints.detected == DetectedFacts()
    assert hints.policy == PolicySection()
    assert hints.policy.is_empty is True
    assert hints.source_path == path
    assert hints.synthesized_from_legacy is False


# ---------------------------------------------------------------------------
# [detected] parsing
# ---------------------------------------------------------------------------


def test_detected_full_parse(tmp_path):
    _write_hints(tmp_path, """
[detected]
schema_version = 1
language = "node"
package_manager = "pnpm"
workspace_layout = "monorepo"
typescript = true
test_runner = "vitest"
active_adapters = ["typescript-node", "postgres"]
lockfiles = ["pnpm-lock.yaml"]
""")
    h = load_project_hints(tmp_path).detected
    assert h.schema_version == 1
    assert h.language == "node"
    assert h.package_manager == "pnpm"
    assert h.workspace_layout == "monorepo"
    assert h.typescript is True
    assert h.test_runner == "vitest"
    assert h.active_adapters == ("typescript-node", "postgres")
    assert h.lockfiles == ("pnpm-lock.yaml",)


def test_detected_schema_version_defaults_to_current_when_omitted(tmp_path):
    _write_hints(tmp_path, '[detected]\nlanguage = "node"\n')
    h = load_project_hints(tmp_path).detected
    assert h.schema_version == CURRENT_SCHEMA_VERSION


def test_detected_bool_must_be_bool_not_string(tmp_path):
    """Common authoring error: TOML boolean written as the string "true"."""
    _write_hints(tmp_path, '[detected]\ntypescript = "true"\n')
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "detected.typescript" in str(exc.value)
    assert "bool" in str(exc.value)


def test_detected_int_must_not_be_bool(tmp_path):
    """bool is a subclass of int in Python; TOML schema_version must refuse it."""
    _write_hints(tmp_path, "[detected]\nschema_version = true\n")
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "schema_version" in str(exc.value)
    assert "bool" in str(exc.value)


def test_detected_array_of_non_strings_rejected(tmp_path):
    _write_hints(tmp_path, "[detected]\nlockfiles = [1, 2]\n")
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "detected.lockfiles[0]" in str(exc.value)


def test_detected_empty_string_in_array_rejected(tmp_path):
    _write_hints(tmp_path, '[detected]\nlockfiles = [""]\n')
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "detected.lockfiles[0]" in str(exc.value)
    assert "non-empty" in str(exc.value)


def test_detected_newer_schema_logs_warning_but_loads(tmp_path, caplog):
    body = f"[detected]\nschema_version = {CURRENT_SCHEMA_VERSION + 1}\n"
    _write_hints(tmp_path, body)
    with caplog.at_level(logging.WARNING):
        hints = load_project_hints(tmp_path)
    assert hints.detected.schema_version == CURRENT_SCHEMA_VERSION + 1
    assert any("newer than supported" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# [policy] parsing
# ---------------------------------------------------------------------------


def test_policy_full_parse(tmp_path):
    _write_hints(tmp_path, """
[policy.workspace]
scope = "apps/api"
banned_recursive = ["pnpm -r", "pnpm --recursive"]

[policy.migrations]
mode = "cli-only"
command = "pnpm db:migrate"

[[policy.consistency_rules]]
name = "migrate-mode-vs-push-mode"
spec_patterns = ["db:migrate", "never auto-applied"]
forbidden_env = ["drizzle-kit push", "db:push"]
env_paths = ["commands.*"]
description = "Spec mandates CLI-only migrations; push-mode bypasses journal"

[[policy.consistency_rules]]
name = "no-recursive-pnpm-on-monorepo"
spec_patterns = ["scoped to apps/api"]
forbidden_env = ["pnpm -r", "pnpm --recursive"]
env_paths = ["commands.*"]
description = "Monorepo workspace requires per-package commands"
""")
    p = load_project_hints(tmp_path).policy
    assert p.workspace == WorkspacePolicy(
        scope="apps/api",
        banned_recursive=("pnpm -r", "pnpm --recursive"),
    )
    assert p.migrations == MigrationsPolicy(
        mode="cli-only", command="pnpm db:migrate",
    )
    assert len(p.consistency_rules) == 2
    assert p.consistency_rules[0] == ConsistencyRuleSpec(
        name="migrate-mode-vs-push-mode",
        spec_patterns=("db:migrate", "never auto-applied"),
        forbidden_env=("drizzle-kit push", "db:push"),
        env_paths=("commands.*",),
        description="Spec mandates CLI-only migrations; push-mode bypasses journal",
    )
    assert p.consistency_rules[1].name == "no-recursive-pnpm-on-monorepo"
    assert p.is_empty is False


def test_empty_policy_section_yields_empty_policy(tmp_path):
    _write_hints(tmp_path, '[detected]\nlanguage = "node"\n')
    p = load_project_hints(tmp_path).policy
    assert p == PolicySection()
    assert p.is_empty is True


def test_consistency_rule_missing_name_rejected(tmp_path):
    _write_hints(tmp_path, """
[[policy.consistency_rules]]
description = "no name"
""")
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "policy.consistency_rules[0]" in str(exc.value)
    assert "'name'" in str(exc.value)


def test_consistency_rule_empty_name_rejected(tmp_path):
    _write_hints(tmp_path, """
[[policy.consistency_rules]]
name = ""
""")
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "name" in str(exc.value)
    assert "non-empty" in str(exc.value)


def test_duplicate_consistency_rule_name_rejected(tmp_path):
    _write_hints(tmp_path, """
[[policy.consistency_rules]]
name = "dup"
description = "first"

[[policy.consistency_rules]]
name = "dup"
description = "second"
""")
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "duplicate" in str(exc.value).lower()
    assert "'dup'" in str(exc.value)


def test_consistency_rules_not_array_rejected(tmp_path):
    _write_hints(tmp_path, '[policy]\nconsistency_rules = "nope"\n')
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "consistency_rules" in str(exc.value)


def test_policy_workspace_scope_wrong_type_rejected(tmp_path):
    _write_hints(tmp_path, "[policy.workspace]\nscope = 42\n")
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "policy.workspace.scope" in str(exc.value)


# ---------------------------------------------------------------------------
# Forward-compat (unknown keys warn, don't raise)
# ---------------------------------------------------------------------------


def test_unknown_top_level_section_warns(tmp_path, caplog):
    _write_hints(tmp_path, "[future_section]\nfoo = 1\n")
    with caplog.at_level(logging.WARNING):
        hints = load_project_hints(tmp_path)
    assert hints == ProjectHints(source_path=_hints_path(tmp_path))
    assert any("future_section" in m for m in caplog.messages)


def test_unknown_detected_key_warns(tmp_path, caplog):
    _write_hints(tmp_path, '[detected]\nlanguage = "node"\nfuture_field = true\n')
    with caplog.at_level(logging.WARNING):
        hints = load_project_hints(tmp_path)
    assert hints.detected.language == "node"
    assert any("future_field" in m for m in caplog.messages)


def test_unknown_policy_key_warns(tmp_path, caplog):
    _write_hints(tmp_path, """
[policy]
future_policy_key = "soon"

[policy.workspace]
scope = "apps/api"
""")
    with caplog.at_level(logging.WARNING):
        hints = load_project_hints(tmp_path)
    assert hints.policy.workspace.scope == "apps/api"
    assert any("future_policy_key" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# TOML parse errors
# ---------------------------------------------------------------------------


def test_invalid_toml_raises_with_path(tmp_path):
    _write_hints(tmp_path, "[detected\nlanguage = node\n")
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "invalid TOML" in str(exc.value)
    assert str(tmp_path) in str(exc.value)


# ---------------------------------------------------------------------------
# Legacy synthesis
# ---------------------------------------------------------------------------


def test_legacy_synthesis_from_hints_json(tmp_path, caplog):
    _write_legacy(
        tmp_path,
        hints_json={
            "language": "node",
            "package_manager": "pnpm",
            "workspace_layout": "monorepo",
            "lockfiles": ["pnpm-lock.yaml"],
        },
        env_toml_body="""
[commands]
typecheck = "pnpm -w exec tsc --noEmit"
test = "pnpm -w exec vitest run"
""",
    )
    with caplog.at_level(logging.WARNING):
        hints = load_project_hints(tmp_path)
    assert hints.synthesized_from_legacy is True
    assert hints.source_path is None
    assert hints.detected.language == "node"
    assert hints.detected.package_manager == "pnpm"
    assert hints.detected.workspace_layout == "monorepo"
    assert hints.detected.lockfiles == ("pnpm-lock.yaml",)
    assert hints.detected.active_adapters == ()
    # typescript / test_runner deliberately NOT heuristically inferred —
    # migration CLI owns re-detection. Flag stays at default.
    assert hints.detected.typescript is False
    assert hints.detected.test_runner == ""
    assert hints.policy.is_empty is True
    # Warning points users at the migration command.
    assert any("--migrate-hints" in m for m in caplog.messages)


def test_legacy_synthesis_with_only_hints_json(tmp_path):
    _write_legacy(tmp_path, hints_json={"language": "python"})
    hints = load_project_hints(tmp_path)
    assert hints.synthesized_from_legacy is True
    assert hints.detected.language == "python"
    assert hints.detected.typescript is False
    assert hints.detected.test_runner == ""


def test_legacy_synthesis_with_only_environment_toml(tmp_path):
    """environment.toml alone triggers synthesis — but yields an empty
    DetectedFacts because no heuristic sniffing is done; hints.json is
    the only structured source."""
    _write_legacy(tmp_path, env_toml_body="""
[commands]
test = "pytest"
""")
    hints = load_project_hints(tmp_path)
    assert hints.synthesized_from_legacy is True
    assert hints.detected.language == ""
    assert hints.detected.test_runner == ""
    assert hints.detected.typescript is False


def test_legacy_synthesis_coerce_debug_log(tmp_path, caplog):
    """Non-string values in hints.json are dropped silently at WARN but
    logged at DEBUG for 2-AM debugging."""
    _write_legacy(tmp_path, hints_json={"language": 123, "package_manager": "pip"})
    with caplog.at_level(logging.DEBUG):
        hints = load_project_hints(tmp_path)
    assert hints.detected.language == ""
    assert hints.detected.package_manager == "pip"
    assert any(
        "dropped non-string" in m and "language" in m for m in caplog.messages
    )


def test_legacy_synthesis_tolerates_malformed_hints_json(tmp_path, caplog):
    arch = tmp_path / ".dark-factory" / "architecture"
    arch.mkdir(parents=True)
    (arch / "hints.json").write_text("{not json}", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        hints = load_project_hints(tmp_path)
    assert hints.synthesized_from_legacy is True
    assert hints.detected.language == ""
    # Two warnings: one for the json failure, one for the "run migrate" hint.
    assert any("hints.json unreadable" in m for m in caplog.messages)


def test_project_hints_takes_precedence_over_legacy(tmp_path):
    """If project-hints.toml exists, legacy files are ignored entirely."""
    _write_hints(tmp_path, '[detected]\nlanguage = "go"\n')
    _write_legacy(tmp_path, hints_json={"language": "node"})
    hints = load_project_hints(tmp_path)
    assert hints.detected.language == "go"
    assert hints.synthesized_from_legacy is False


# ---------------------------------------------------------------------------
# Dataclass immutability / identity
# ---------------------------------------------------------------------------


def test_dataclasses_are_frozen(tmp_path):
    hints = load_project_hints(tmp_path)
    with pytest.raises(Exception):
        hints.detected.language = "rust"  # type: ignore[misc]
    with pytest.raises(Exception):
        hints.policy.workspace.scope = "x"  # type: ignore[misc]


def test_empty_detected_facts_uses_current_schema_version():
    assert DetectedFacts().schema_version == CURRENT_SCHEMA_VERSION


def test_current_schema_version_is_pinned_to_one():
    """Bumping this constant is a schema migration — the pin is here so
    that bump shows up in code review, not silently."""
    assert CURRENT_SCHEMA_VERSION == 1


def test_policy_is_empty_is_specific(tmp_path):
    """is_empty flips false as soon as ANY policy field is populated."""
    _write_hints(tmp_path, '[policy.migrations]\nmode = "cli-only"\n')
    hints = load_project_hints(tmp_path)
    assert hints.policy.is_empty is False


# ---------------------------------------------------------------------------
# Structural type errors at section boundaries
# ---------------------------------------------------------------------------


def test_detected_not_a_table_rejected(tmp_path):
    _write_hints(tmp_path, 'detected = "a string, not a table"\n')
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "[detected]" in str(exc.value)
    assert "str" in str(exc.value)


def test_policy_not_a_table_rejected(tmp_path):
    _write_hints(tmp_path, 'policy = "bad"\n')
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "[policy]" in str(exc.value)


def test_policy_workspace_not_a_table_rejected(tmp_path):
    _write_hints(tmp_path, '[policy]\nworkspace = "bad"\n')
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "policy.workspace" in str(exc.value)


def test_policy_migrations_not_a_table_rejected(tmp_path):
    _write_hints(tmp_path, '[policy]\nmigrations = "bad"\n')
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "policy.migrations" in str(exc.value)


def test_policy_workspace_as_array_rejected(tmp_path):
    """Regression guard for Linus's item 3: ``if not raw:`` used to
    silently accept ``workspace = []``. Structural wrong-type always raises."""
    _write_hints(tmp_path, "[policy]\nworkspace = []\n")
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "policy.workspace" in str(exc.value)
    assert "list" in str(exc.value)


def test_policy_migrations_as_array_rejected(tmp_path):
    _write_hints(tmp_path, "[policy]\nmigrations = []\n")
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "policy.migrations" in str(exc.value)


def test_policy_as_scalar_rejected(tmp_path):
    """``policy = "bad"`` at the top level — a single-value typo where a
    table was expected. TOML rules out ``policy = []`` when subtables
    exist, so a scalar is the common failure mode we must refuse."""
    _write_hints(tmp_path, 'policy = "bad"\n')
    with pytest.raises(ConfigError) as exc:
        load_project_hints(tmp_path)
    assert "[policy]" in str(exc.value)
