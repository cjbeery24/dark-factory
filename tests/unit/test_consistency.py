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
    compile_project_hint_rules,
    compose_rules,
)
from darkfactory.architecture.project_hints import ConsistencyRuleSpec
from darkfactory.errors import ConfigError
from darkfactory.spec.manifest import ArchitectureManifest, RuntimeDecl


def _mk_manifest(amendment_ops: dict) -> ArchitectureManifest:
    m = ArchitectureManifest()
    m.runtime = RuntimeDecl(amendment_ops=dict(amendment_ops))
    return m


# ---------------------------------------------------------------------------
# Migrate-vs-push rule fixture.
#
# Before B5, this rule shipped in ``DEFAULT_RULES`` and tests exercised it
# via the default parameter of ``check_spec_env_consistency``. After B5,
# the rule moved to ``budget-brain-v2/.dark-factory/project-hints.toml``
# — it is a *project-policy* rule, not a framework default. To keep this
# file testing the rule's logic (not its mere shipping location), we
# declare the rule here and pass it explicitly via ``rules=``.
#
# **Drift caveat.** This fixture is a hand-maintained mirror of the
# TOML block in budget-brain-v2. Drift between the two is NOT caught by
# any test in this repo — budget-brain-v2 is a sibling project, and a
# filesystem crossing from a unit test would couple CI to a specific
# sibling layout. Keeping the two in sync is budget-brain-v2's
# responsibility (via its own tests or a future
# ``dark-factory hints verify`` CLI); update both files together. If
# this fixture diverges from the TOML, the framework-side compile /
# compose logic still works — but the real pipeline fires differently
# from what this file asserts.
# ---------------------------------------------------------------------------

_MIGRATE_VS_PUSH_SPEC = ConsistencyRuleSpec(
    name="migrate-mode-vs-push-mode",
    env_paths=("commands.*",),
    spec_patterns=(
        r"`db:migrate`",
        r"\bdb:migrate\b",
        r"\bdrizzle-kit\s+migrate\b",
        r"\bmigrate[- ]only\b",
        r"\bnever\s+auto[\s-]+applied\b",
        r"\bmigration[s]?[^.]{0,80}\b(?:CLI|CI|manual)[- ]only\b",
        r"\b(?:CLI|CI|manual)[- ]only[^.]{0,80}\bmigration",
        r"\b(?:not|never)\s+(?:drizzle-kit\s+)?push\b",
        r"\bno\s+auto[\s-]+migration\s+on\s+startup\b",
    ),
    forbidden_env=(
        r"\bdrizzle-kit\s+push\b",
        r"\bdb:push\b",
    ),
    description=(
        "Spec mandates CLI-driven migrations (db:migrate); env would "
        "run push-mode (drizzle-kit push / db:push) which auto-applies "
        "schema changes and bypasses the migration journal."
    ),
)
_MIGRATE_VS_PUSH_RULES = compile_project_hint_rules((_MIGRATE_VS_PUSH_SPEC,))


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
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
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
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
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
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
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
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
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
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
    )
    assert errors == []


def test_no_error_when_spec_says_nothing_about_migrations():
    manifest = _mk_manifest({
        "commands.setup": "cd apps/api && npx drizzle-kit push",
    })
    bland_spec = "## Overview\n\nA simple API that returns JSON.\n"
    errors = check_spec_env_consistency(
        spec_text=bland_spec,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
    )
    assert errors == []


def test_no_error_when_commands_setup_is_absent():
    manifest = _mk_manifest({"commands.install": "pnpm install"})
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
    )
    assert errors == []


def test_no_error_on_empty_manifest():
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=_mk_manifest({}),
        rules=_MIGRATE_VS_PUSH_RULES,
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
        spec_text=spec_phrase,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
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
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
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
    errors = check_spec_env_consistency(
        spec_text=spec, manifest=manifest, rules=_MIGRATE_VS_PUSH_RULES,
    )
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# Case-insensitivity.
# ---------------------------------------------------------------------------


def test_case_insensitive_spec_match():
    manifest = _mk_manifest({"commands.setup": "DRIZZLE-KIT PUSH"})
    spec = "Migrations are CLI-ONLY (`db:migrate`)."
    errors = check_spec_env_consistency(
        spec_text=spec, manifest=manifest, rules=_MIGRATE_VS_PUSH_RULES,
    )
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
            spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
            manifest=manifest,
            rules=_MIGRATE_VS_PUSH_RULES,
        )
    matching = [r for r in caplog.records if "event=consistency_violation" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "rule=migrate-mode-vs-push-mode" in msg
    assert "env_path=commands.setup" in msg


# ---------------------------------------------------------------------------
# Frozenness guarantee.
# ---------------------------------------------------------------------------


def test_consistency_rule_is_frozen_dataclass():
    """``ConsistencyRule`` must remain frozen — its identity is the tuple
    of compiled regex, and mutation after composition would let a caller
    reshape the framework's live rules under another caller's feet."""
    rule = _MIGRATE_VS_PUSH_RULES[0]
    assert isinstance(rule, ConsistencyRule)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rule.name = "mutated"  # type: ignore[misc]


def test_default_rules_are_empty_post_b5():
    """Contract: ``DEFAULT_RULES`` is empty.

    Every framework-shipped rule has turned out to be either
    ecosystem-specific (adapter-owned) or project-policy
    (project-hints-owned).

    **If this test fails because you are adding a new ``DEFAULT_RULES``
    entry: stop.** Read ``docs/ADAPTER-GOVERNANCE.md`` on where a rule
    belongs:

    * Cross-ecosystem post-mortem evidence from at least two ecosystems
      → goes here (delete this test, cite evidence in the commit).
    * Single-ecosystem rule → goes in the matching adapter's
      ``consistency_rules()``.
    * Single-project rule → goes in that project's
      ``.dark-factory/project-hints.toml`` under
      ``[[policy.consistency_rules]]``.

    The ratchet is here in code (not just in governance docs) so a
    reviewer can't merge a new default without consciously deleting
    this test.
    """
    assert DEFAULT_RULES == ()


# ---------------------------------------------------------------------------
# B5 — compile_project_hint_rules: turn TOML-shaped specs into compiled
# ConsistencyRule objects. Bad regex must surface at compile time with the
# rule name in the error, not at match time as a generic re.error trace.
# ---------------------------------------------------------------------------


def test_compile_project_hint_rules_basic_roundtrip():
    spec = ConsistencyRuleSpec(
        name="no-watch-in-ci",
        spec_patterns=(r"\bCI mode\b",),
        forbidden_env=(r"--watch\b",),
        env_paths=("commands.test",),
        description="Spec says CI mode; --watch keeps the process running.",
    )
    rules = compile_project_hint_rules((spec,))
    assert len(rules) == 1
    rule = rules[0]
    assert rule.name == "no-watch-in-ci"
    assert rule.env_paths == ("commands.test",)
    assert rule.description.startswith("Spec says CI mode")
    # Patterns must be compiled with IGNORECASE + DOTALL (same flags as
    # DEFAULT_RULES) so TOML-authored patterns match the same way the
    # framework-shipped ones do.
    assert rule.spec_patterns[0].flags & re.IGNORECASE
    assert rule.spec_patterns[0].flags & re.DOTALL
    assert rule.forbidden_env_patterns[0].flags & re.IGNORECASE


def test_compile_project_hint_rules_fires_against_manifest():
    spec = ConsistencyRuleSpec(
        name="no-watch-in-ci",
        spec_patterns=(r"\bCI mode\b",),
        forbidden_env=(r"--watch\b",),
        env_paths=("commands.test",),
        description="Spec says CI mode; --watch keeps the process running.",
    )
    rules = compile_project_hint_rules((spec,))
    manifest = _mk_manifest({"commands.test": "pnpm test --watch"})
    errors = check_spec_env_consistency(
        spec_text="The pipeline runs in CI mode.",
        manifest=manifest,
        rules=rules,
    )
    assert len(errors) == 1
    assert errors[0].rule_name == "no-watch-in-ci"


def test_compile_project_hint_rules_bad_spec_regex_raises_with_name():
    bad = ConsistencyRuleSpec(
        name="bad-spec-regex",
        spec_patterns=(r"unbalanced[",),  # truncated character class
        forbidden_env=("anything",),
        env_paths=("commands.test",),
    )
    with pytest.raises(ConfigError) as exc_info:
        compile_project_hint_rules((bad,))
    msg = str(exc_info.value)
    assert "bad-spec-regex" in msg
    assert "spec_patterns" in msg


def test_compile_project_hint_rules_bad_forbidden_env_regex_raises_with_name():
    bad = ConsistencyRuleSpec(
        name="bad-env-regex",
        spec_patterns=(r"anything",),
        forbidden_env=(r"(unclosed-group",),
        env_paths=("commands.test",),
    )
    with pytest.raises(ConfigError) as exc_info:
        compile_project_hint_rules((bad,))
    msg = str(exc_info.value)
    assert "bad-env-regex" in msg
    assert "forbidden_env" in msg


def test_compile_project_hint_rules_empty_input_empty_output():
    assert compile_project_hint_rules(()) == ()


def test_compile_project_hint_rules_rejects_empty_env_paths():
    """A rule with no ``env_paths`` can never fire — ``_path_covered_by``
    returns False for every op path. That's a silent no-op footgun: the
    TOML block looks plausible, the rule compiles clean, and it never
    fires. Reject at compile time with the rule name so the authoring
    mistake surfaces instead of shipping as dead policy."""
    inert = ConsistencyRuleSpec(
        name="silently-inert",
        spec_patterns=(r"anything",),
        forbidden_env=(r"anything",),
        env_paths=(),  # the footgun
    )
    with pytest.raises(ConfigError) as exc_info:
        compile_project_hint_rules((inert,))
    msg = str(exc_info.value)
    assert "silently-inert" in msg
    assert "env_paths" in msg


# ---------------------------------------------------------------------------
# B5 — compose_rules: framework + adapter + project-hints composition with
# last-writer-wins dedup on ``name``. The regression this protects is:
# during the transitional window where DEFAULT_RULES still holds a rule
# that a project-hint has also declared, the rule must fire once (not
# twice) with the project-hint's payload — project-policy overrides
# framework default.
# ---------------------------------------------------------------------------


def _mk_rule(name: str, tag: str) -> ConsistencyRule:
    """Tiny factory for compose-test fixtures. Each rule fires on its
    own spec-token / env-token pair, so we can tell which source's
    payload actually ran by reading the match back off the error."""
    return ConsistencyRule(
        name=name,
        env_paths=("commands.setup",),
        spec_patterns=(re.compile(f"spec-{tag}"),),
        forbidden_env_patterns=(re.compile(f"env-{tag}"),),
        description=f"tag={tag}",
    )


def test_compose_rules_empty_defaults_no_adapters_no_hints():
    assert compose_rules(defaults=(), adapter_rules=(), project_hint_rules=()) == ()


def test_compose_rules_concat_when_names_disjoint():
    d = _mk_rule("default-rule", "d")
    a = _mk_rule("adapter-rule", "a")
    p = _mk_rule("hint-rule", "p")
    composed = compose_rules(
        defaults=(d,),
        adapter_rules=(a,),
        project_hint_rules=(p,),
    )
    assert [r.name for r in composed] == ["default-rule", "adapter-rule", "hint-rule"]


def test_compose_rules_project_hints_override_defaults_by_name():
    d = _mk_rule("same-name", "default")
    p = _mk_rule("same-name", "hint")
    composed = compose_rules(defaults=(d,), project_hint_rules=(p,))
    assert len(composed) == 1
    assert composed[0].description == "tag=hint"


def test_compose_rules_project_hints_override_adapter_by_name():
    a = _mk_rule("same-name", "adapter")
    p = _mk_rule("same-name", "hint")
    composed = compose_rules(adapter_rules=(a,), project_hint_rules=(p,))
    assert len(composed) == 1
    assert composed[0].description == "tag=hint"


def test_compose_rules_adapter_overrides_default_by_name():
    d = _mk_rule("same-name", "default")
    a = _mk_rule("same-name", "adapter")
    composed = compose_rules(defaults=(d,), adapter_rules=(a,))
    assert len(composed) == 1
    assert composed[0].description == "tag=adapter"


def test_compose_rules_preserves_first_position_on_override():
    """When a later source overrides by name, the rule keeps its
    original position in the output. Downstream iteration order is
    stable as long as the name set is stable."""
    d1 = _mk_rule("rule-one", "d1")
    d2 = _mk_rule("rule-two", "d2")
    p = _mk_rule("rule-one", "hint")  # same name as d1
    composed = compose_rules(defaults=(d1, d2), project_hint_rules=(p,))
    assert [r.name for r in composed] == ["rule-one", "rule-two"]
    # The project-hint payload wins but stays in rule-one's slot.
    assert composed[0].description == "tag=hint"
    assert composed[1].description == "tag=d2"


# ---------------------------------------------------------------------------
# B5 — compose_rules shadow logging. A later source overriding an earlier
# source's rule by name is the *designed* behavior (spec > policy > adapter),
# but it is also the failure mode for accidental name collisions where the
# project author didn't realize an adapter already ships a rule of the same
# name. The INFO log names both sides so post-mortem debugging can trace
# "which source's rule actually fired."
# ---------------------------------------------------------------------------


def test_compose_rules_logs_shadow_with_both_sides(caplog):
    """Project-hints overriding an adapter rule logs both the shadowing
    source and the previous source so an operator can find the culprit."""
    a = _mk_rule("shared-name", "adapter")
    p = _mk_rule("shared-name", "hint")
    with caplog.at_level(logging.INFO, logger="darkfactory.architecture.consistency"):
        compose_rules(adapter_rules=(a,), project_hint_rules=(p,))
    matches = [r for r in caplog.records if "event=consistency_rule_shadow" in r.getMessage()]
    assert len(matches) == 1
    msg = matches[0].getMessage()
    assert "rule=shared-name" in msg
    assert "shadowed_by=project-hints" in msg
    assert "previous_source=adapter" in msg


def test_compose_rules_no_shadow_log_when_names_disjoint(caplog):
    d = _mk_rule("rule-a", "d")
    a = _mk_rule("rule-b", "a")
    p = _mk_rule("rule-c", "p")
    with caplog.at_level(logging.INFO, logger="darkfactory.architecture.consistency"):
        compose_rules(defaults=(d,), adapter_rules=(a,), project_hint_rules=(p,))
    matches = [r for r in caplog.records if "event=consistency_rule_shadow" in r.getMessage()]
    assert matches == []


def test_compose_rules_shadow_chain_logs_each_override(caplog):
    """Default → adapter → project-hints, all same name, logs twice
    (adapter shadows default, project-hints shadows adapter)."""
    d = _mk_rule("same", "default")
    a = _mk_rule("same", "adapter")
    p = _mk_rule("same", "hint")
    with caplog.at_level(logging.INFO, logger="darkfactory.architecture.consistency"):
        compose_rules(defaults=(d,), adapter_rules=(a,), project_hint_rules=(p,))
    matches = [r for r in caplog.records if "event=consistency_rule_shadow" in r.getMessage()]
    assert len(matches) == 2
    # Order reflects the composition order: adapter-over-default, then
    # project-hints-over-adapter.
    first, second = matches
    assert "shadowed_by=adapter" in first.getMessage()
    assert "previous_source=defaults" in first.getMessage()
    assert "shadowed_by=project-hints" in second.getMessage()
    assert "previous_source=adapter" in second.getMessage()


# ---------------------------------------------------------------------------
# B5 — regression: the 2026-04-14 migrate-vs-push contradiction must still
# fire after the rule migrates from DEFAULT_RULES to a project-hints entry.
# This is the load-bearing regression test for Sprint 3 — if budget-brain-
# v2's project-hints.toml has the rule, the same failure must still abort
# the pipeline.
# ---------------------------------------------------------------------------


def test_project_hint_rule_reproduces_migrate_vs_push_contradiction():
    """The full migrate-vs-push rule, expressed as a project-hints
    entry, catches the same contradiction that DEFAULT_RULES caught
    before B5 shrank. ``_MIGRATE_VS_PUSH_RULES`` at the top of this file
    is the compiled form of the TOML entry in
    ``budget-brain-v2/.dark-factory/project-hints.toml``.
    """
    manifest = _mk_manifest({
        "commands.setup": "cd apps/api && npx drizzle-kit push",
    })
    errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
    )
    assert len(errors) == 1
    assert errors[0].rule_name == "migrate-mode-vs-push-mode"


def test_empty_rules_means_no_false_positives():
    """A project with no adapter rules, no project-hints, and an empty
    DEFAULT_RULES produces zero errors. Protects against the "B5 made
    A1 accidentally quiet-permissive" reviewer concern.

    The assertion is paired with a positive control in the same test —
    the same spec/manifest fed through ``_MIGRATE_VS_PUSH_RULES`` must
    still fire. Without the positive control, ``errors == []`` would
    pass trivially if the check function became a no-op for any reason.
    """
    manifest = _mk_manifest({
        "commands.setup": "cd apps/api && npx drizzle-kit push",
    })
    # Empty rule set → nothing fires.
    empty_errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=(),
    )
    assert empty_errors == []
    # Positive control: same inputs, project-hints-equivalent rules →
    # the historical contradiction still fires. Proves the empty result
    # above is rule-driven, not input-driven or function-broken.
    firing_errors = check_spec_env_consistency(
        spec_text=_DATABASE_FOUNDATION_SPEC_EXCERPT,
        manifest=manifest,
        rules=_MIGRATE_VS_PUSH_RULES,
    )
    assert len(firing_errors) == 1
    assert firing_errors[0].rule_name == "migrate-mode-vs-push-mode"
