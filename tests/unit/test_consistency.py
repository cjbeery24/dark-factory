"""A1 — spec↔environment consistency checks.

Motivated by 2026-04-14 database-foundation escalation. See
``docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md``.
"""
from __future__ import annotations

import dataclasses
import logging
import re

import pytest

from darkfactory.architecture.consistency import (
    ConsistencyRule,
    DEFAULT_RULES,
    check_spec_env_consistency,
)
from darkfactory.spec.manifest import ArchitectureManifest, RuntimeDecl


def _mk_manifest(amendment_ops: dict) -> ArchitectureManifest:
    m = ArchitectureManifest()
    m.runtime = RuntimeDecl(amendment_ops=dict(amendment_ops))
    return m


# ---------------------------------------------------------------------------
# The reproducer: the actual payload that shipped to the implementer on
# 2026-04-14. Both arguments come from real run artifacts.
# ---------------------------------------------------------------------------

_DATABASE_FOUNDATION_SPEC_EXCERPT = """
## Constraints & Technical Decisions

- **Migration tool:** drizzle-kit
- Drizzle schema defined in `apps/api/src/db/schema/` with one file per entity
- Migrations are CLI-only (`db:migrate`) — never auto-applied on startup
"""


def test_reproduces_2026_04_14_database_foundation_contradiction():
    manifest = _mk_manifest({
        "commands.setup": "cd apps/api && npx drizzle-kit push",
    })
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT, manifest=manifest,
    )
    assert len(errors) == 1
    err = errors[0]
    assert err.rule_name == "migrate-mode-vs-push-mode"
    assert err.env_path == "commands.setup"
    assert "drizzle-kit push" in err.env_match
    assert re.search(
        re.escape(err.spec_match),
        _DATABASE_FOUNDATION_SPEC_EXCERPT,
        re.IGNORECASE,
    )


def test_format_includes_citations():
    manifest = _mk_manifest({
        "commands.setup": "cd apps/api && npx drizzle-kit push",
    })
    [err] = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT, manifest=manifest,
    )
    formatted = err.format()
    assert "migrate-mode-vs-push-mode" in formatted
    assert "commands.setup" in formatted
    assert "drizzle-kit push" in formatted


# ---------------------------------------------------------------------------
# Coverage across env_paths glob: the architect can't evade A1 by moving
# the command to a different `commands.*` key.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "commands.install",
    "commands.setup",
    "commands.build",
    "commands.server_start",
    "commands.test_server",
])
def test_forbidden_pattern_fires_across_all_commands_paths(path: str):
    manifest = _mk_manifest({path: "drizzle-kit push"})
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT, manifest=manifest,
    )
    assert len(errors) == 1, (
        f"migrate-vs-push must fire regardless of which commands.* key "
        f"the architect chose; missed on {path!r}"
    )
    assert errors[0].env_path == path


def test_unmatched_path_is_ignored():
    # server.health_path is a legitimate env path but should never be
    # the carrier for a setup-time migration command, so the migrate
    # rule's prefix glob excludes it.
    manifest = _mk_manifest({"server.health_path": "drizzle-kit push"})
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT, manifest=manifest,
    )
    assert errors == []


# ---------------------------------------------------------------------------
# Precision tests: both halves must fire.
# ---------------------------------------------------------------------------


def test_no_error_when_env_has_migrate_mode():
    manifest = _mk_manifest({
        "commands.setup": "cd apps/api && pnpm db:migrate",
    })
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT, manifest=manifest,
    )
    assert errors == []


def test_no_error_when_spec_says_nothing_about_migrations():
    manifest = _mk_manifest({
        "commands.setup": "cd apps/api && npx drizzle-kit push",
    })
    bland_spec = "## Overview\n\nA simple API that returns JSON.\n"
    errors = check_spec_env_consistency(spec_text=bland_spec, manifest=manifest)
    assert errors == []


def test_no_error_when_commands_setup_is_absent():
    manifest = _mk_manifest({"commands.install": "pnpm install"})
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT, manifest=manifest,
    )
    assert errors == []


def test_no_error_on_empty_manifest():
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=_mk_manifest({}),
    )
    assert errors == []


# ---------------------------------------------------------------------------
# Spec-side pattern breadth. Each of these phrasings should fire the rule
# against a drizzle-kit push env. Sourced from realistic prose variants,
# not just the original post-mortem wording.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec_phrase", [
    "Migrations are CLI-only (`db:migrate`) — never auto-applied on startup",
    "Migrations are CLI-only (`db:migrate`) - never auto-applied on startup",  # ASCII hyphen
    "All migrations are migrate-only.",
    "Run `db:migrate` to apply.",
    "Use db:migrate for schema changes.",
    "Schema changes are never auto-applied.",
    "Schema changes are never auto applied.",  # space not hyphen
    "No auto-migration on startup.",
    "Migrations must be CLI-only.",
    "Use drizzle-kit migrate to ship schema.",
    "Never drizzle-kit push in this repo; we use migrations.",
    "The CI-only migration flow runs via db:migrate.",
])
def test_spec_pattern_variants_trigger(spec_phrase: str):
    manifest = _mk_manifest({
        "commands.setup": "drizzle-kit push --force",
    })
    errors = check_spec_env_consistency(
        spec_text=spec_phrase, manifest=manifest,
    )
    assert len(errors) == 1, f"spec phrase did not fire: {spec_phrase!r}"


@pytest.mark.parametrize("env_value", [
    "drizzle-kit push",
    "npx drizzle-kit push",
    "pnpm db:push",
    "cd apps/api && npx drizzle-kit push --force",
])
def test_env_pattern_variants_trigger(env_value: str):
    manifest = _mk_manifest({"commands.setup": env_value})
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT, manifest=manifest,
    )
    assert len(errors) == 1, f"env value did not fire: {env_value!r}"


# ---------------------------------------------------------------------------
# Cross-bullet prose. The CLI-only-near-migration patterns use .{0,80} gap
# matchers, so they have to tolerate newlines.
# ---------------------------------------------------------------------------


def test_spec_pattern_tolerates_newline_between_words():
    spec = (
        "## Constraints\n\n"
        "- CLI-only\n"
        "- Migrations are generated by drizzle-kit\n"
    )
    manifest = _mk_manifest({"commands.setup": "drizzle-kit push"})
    errors = check_spec_env_consistency(spec_text=spec, manifest=manifest)
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# Case-insensitivity.
# ---------------------------------------------------------------------------


def test_case_insensitive_spec_match():
    manifest = _mk_manifest({"commands.setup": "DRIZZLE-KIT PUSH"})
    spec = "Migrations are CLI-ONLY (`db:migrate`)."
    errors = check_spec_env_consistency(spec_text=spec, manifest=manifest)
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# List-valued env values: joined with newlines, not spaces, so a regex
# can't accidentally match across independent list elements as if they
# were adjacent shell tokens.
# ---------------------------------------------------------------------------


def test_list_env_values_joined_with_newline_not_space():
    # A rule that would false-match under space-join but not under newline-join.
    rule = ConsistencyRule(
        name="adjacency-guard",
        env_paths=("layout.source_dirs",),
        spec_patterns=(re.compile(r"always"),),
        # Literal dot would match a space under space-join; requires
        # newline-join for the test to fail.
        forbidden_env_patterns=(re.compile(r"src.bar"),),
        description="smoke",
    )
    manifest = _mk_manifest({"layout.source_dirs": ["src", "bar"]})
    errors = check_spec_env_consistency(
        spec_text="always", manifest=manifest, rules=(rule,),
    )
    assert errors == [], (
        "list-elements must be joined with a separator that regex . cannot "
        "match (newline), so forbidden patterns don't accidentally fire "
        "across independent elements"
    )


def test_list_env_values_join_preserves_within_element_matching():
    rule = ConsistencyRule(
        name="within-element",
        env_paths=("layout.source_dirs",),
        spec_patterns=(re.compile(r"always"),),
        forbidden_env_patterns=(re.compile(r"bar"),),
        description="smoke",
    )
    manifest = _mk_manifest({"layout.source_dirs": ["src", "bar", "baz"]})
    errors = check_spec_env_consistency(
        spec_text="always", manifest=manifest, rules=(rule,),
    )
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# Multiple rules firing on the same manifest.
# ---------------------------------------------------------------------------


def test_multiple_rules_fire_independently():
    rule_a = ConsistencyRule(
        name="rule-a",
        env_paths=("commands.*",),
        spec_patterns=(re.compile(r"alpha"),),
        forbidden_env_patterns=(re.compile(r"bad-a"),),
        description="a",
    )
    rule_b = ConsistencyRule(
        name="rule-b",
        env_paths=("commands.*",),
        spec_patterns=(re.compile(r"beta"),),
        forbidden_env_patterns=(re.compile(r"bad-b"),),
        description="b",
    )
    manifest = _mk_manifest({
        "commands.setup": "run bad-a then bad-b",
    })
    errors = check_spec_env_consistency(
        spec_text="alpha beta", manifest=manifest, rules=(rule_a, rule_b),
    )
    assert {e.rule_name for e in errors} == {"rule-a", "rule-b"}


# ---------------------------------------------------------------------------
# Structured log emission.
# ---------------------------------------------------------------------------


def test_violation_emits_structured_log(caplog):
    manifest = _mk_manifest({
        "commands.setup": "cd apps/api && npx drizzle-kit push",
    })
    with caplog.at_level(logging.WARNING, logger="darkfactory.architecture.consistency"):
        check_spec_env_consistency(
            spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT, manifest=manifest,
        )
    matching = [r for r in caplog.records if "event=consistency_violation" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "rule=migrate-mode-vs-push-mode" in msg
    assert "env_path=commands.setup" in msg


# ---------------------------------------------------------------------------
# Frozenness guarantee.
# ---------------------------------------------------------------------------


def test_default_rules_are_frozen_dataclass_instances():
    for rule in DEFAULT_RULES:
        assert isinstance(rule, ConsistencyRule)
        with pytest.raises(dataclasses.FrozenInstanceError):
            rule.name = "mutated"  # type: ignore[misc]
