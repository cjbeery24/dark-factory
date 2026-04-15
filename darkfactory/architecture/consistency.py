"""Post-architect spec↔environment consistency checks.

When the architect emits an environment delta that contradicts the spec's
declared constraints, the pipeline should fail *before* the implementer is
invoked — not burn an iteration watching the implementer trust the env and
violate the spec.

This is a deterministic keyword-pair check against a curated table. Each
row is motivated by a post-mortem where spec/env drift cost us a pipeline
iteration. The matching is literal regex; no LLM, no semantic extraction.

**Rule sourcing (B5):** the pipeline composes rules from three sources at
call time:

1. ``DEFAULT_RULES`` — framework-universal rules that apply to every project
   regardless of ecosystem or policy. After B5, this tuple is intentionally
   empty: every rule we ship today is either ecosystem-specific (adapter-
   owned) or project-policy (project-hints-owned). New entries here require
   cross-ecosystem justification.
2. Adapter baselines — ``adapter.consistency_rules()`` for each active
   ecosystem adapter. Ecosystem-universal rules live here.
3. Project-hints — ``[policy.consistency_rules]`` in
   ``.dark-factory/project-hints.toml``. Project-specific policy lives here.
   Compiled at composition time via ``compile_project_hint_rules`` so
   regex errors surface with the rule name in the error message (not here
   during TOML load).

Composition is "concat then dedupe by name, last writer wins." Order is
``DEFAULT_RULES → adapter baselines → project-hints`` so project-hints wins
ties, matching the plan's layer rubric (spec > policy > adapter).

If a contradiction slips through, add a row in the right layer and a
regression test.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from darkfactory.errors import ConfigError
from darkfactory.spec.manifest import ArchitectureManifest

if TYPE_CHECKING:
    from darkfactory.architecture.project_hints import ConsistencyRuleSpec

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConsistencyRule:
    """A single spec↔env contradiction fingerprint.

    Fires when *any* ``spec_patterns`` entry matches ``spec_text`` AND
    *any* ``forbidden_env_patterns`` entry matches any string-valued op
    whose dotted path is covered by ``env_paths``.

    ``env_paths`` entries are either exact paths (``"commands.setup"``)
    or prefix globs ending in ``.*`` (``"commands.*"``). The glob form is
    how we avoid the "architect moves the command to a different key"
    evasion that a single-key match would miss.
    """

    name: str
    env_paths: tuple[str, ...]
    spec_patterns: tuple[re.Pattern[str], ...]
    forbidden_env_patterns: tuple[re.Pattern[str], ...]
    description: str


@dataclass(frozen=True)
class ConsistencyError:
    rule_name: str
    env_path: str
    env_value: str
    spec_match: str
    env_match: str
    description: str

    def format(self) -> str:
        return (
            f"spec↔env contradiction [{self.rule_name}]: "
            f"spec contains {self.spec_match!r} but manifest sets "
            f"{self.env_path}={self.env_value!r} which matches forbidden "
            f"pattern {self.env_match!r}. {self.description}"
        )


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    # DOTALL so `.{0,40}` and similar bounded gap-matchers tolerate newlines
    # in prose that crosses bullet boundaries.
    return tuple(re.compile(p, re.IGNORECASE | re.DOTALL) for p in patterns)


def _path_covered_by(path: str, rule_paths: tuple[str, ...]) -> bool:
    """Does any rule pattern cover ``path``?

    Three pattern forms are supported:

    * Exact — ``"commands.setup"`` matches only that literal path.
    * Trailing ``.*`` — ``"commands.*"`` is a prefix-glob: matches any
      path that starts with ``"commands."``. This is the original form
      and is how rules defeat the "architect moves the command to a
      different key" evasion for flat key families.
    * Segment wildcard — ``"services.*.inject_env"`` uses ``*`` in a
      non-terminal segment. Matches only paths with the *same* number
      of dot-segments, where each ``*`` matches exactly one segment.
      Introduced for ``services.<name>.<field>`` rules where ``<name>``
      varies per manifest but the wrapping segments are fixed. Multiple
      wildcards are allowed; each matches independently.

    Prefix-glob and segment-wildcard don't compose: a pattern like
    ``services.*.inject_env.*`` would be interpreted as prefix-glob
    ``services.*.inject_env.`` (literal ``*``) and never match. If a
    future rule needs that, extend this matcher; for now the
    service-ops path is always the 3-segment ``services.<name>.<field>``
    shape because ``amend._build_ops`` groups inject_env keys into a
    single dict-valued op per service.
    """
    for pattern in rule_paths:
        if pattern.endswith(".*"):
            if path.startswith(pattern[:-1]):
                return True
        elif "*" in pattern:
            pat_parts = pattern.split(".")
            path_parts = path.split(".")
            if len(pat_parts) == len(path_parts) and all(
                pp == "*" or pp == xp
                for pp, xp in zip(pat_parts, path_parts)
            ):
                return True
        elif path == pattern:
            return True
    return False


# Framework-universal seed rules.
#
# The bar for an entry here is framework-plumbing universality: a rule
# that expresses a contract of the pipeline orchestrator itself — not a
# contract of any ecosystem or any project. Ecosystem-flavored rules
# (e.g. Drizzle migrate-vs-push) go in the matching adapter's
# ``consistency_rules()``. Project-specific policy goes in
# ``[[policy.consistency_rules]]`` in
# ``.dark-factory/project-hints.toml``.
#
# Historical note: the ``migrate-mode-vs-push-mode`` rule used to live
# here. It was migrated to ``budget-brain-v2/.dark-factory/project-hints.toml``
# during B5 because it targets Drizzle specifically and its spec-language
# phrasing ("`db:migrate`", "drizzle-kit push") is project-idiomatic,
# not framework-universal. A rule with broader ecosystem applicability
# (e.g. covering Prisma, Alembic, ActiveRecord migrations in one
# fingerprint) could live here, but should only land with evidence from
# at least two ecosystems.
#
# The ``service-sidecar-localhost-forbidden`` entry below is NOT
# ecosystem-flavored: it expresses the orchestrator's service-sidecar
# networking contract. Service sidecars run on the ``df-<run>-execution``
# docker network and are addressable by service name; ``localhost`` or
# ``127.0.0.1`` inside a consumer container resolves to the consumer's
# own loopback, which has no sidecar. The rule is axiomatic for every
# ecosystem the orchestrator will ever support, which is why it lives
# here despite a single-ecosystem post-mortem.
DEFAULT_RULES: tuple[ConsistencyRule, ...] = (
    ConsistencyRule(
        name="service-sidecar-localhost-forbidden",
        # Matches the 3-segment ``services.<name>.inject_env`` key that
        # amend._build_ops emits per service (value is the inject_env
        # dict, which stringifies to contain all its values).
        env_paths=("services.*.inject_env",),
        # Unconditional fire on the env side — the service-sidecar
        # networking contract doesn't care what the spec says. ``.*``
        # always matches (even empty spec_text) so the spec-match leg
        # of ``check_spec_env_consistency`` always succeeds for this
        # rule, leaving the forbidden-env leg as the sole discriminator.
        spec_patterns=_compile(r".*"),
        forbidden_env_patterns=_compile(
            r"\blocalhost\b",
            r"\b127\.0\.0\.1\b",
            # IPv6 loopback — cannot use \b because `:` is a non-word
            # character, so \b anchors fail around colons. Require that
            # `::1` is NOT surrounded by further hex/colon characters,
            # which would mean we're inside a different IPv6 address
            # like 2001:db8::1:5 rather than the loopback itself.
            r"(?<![0-9a-fA-F:])::1(?![0-9a-fA-F:])",
        ),
        description=(
            "Service sidecars run on the pipeline's execution docker "
            "network, addressed by service name (e.g. "
            "postgresql://...@postgres:5432/...). `localhost` / "
            "`127.0.0.1` / `::1` in a consumer container's inject_env "
            "resolves to the consumer's own loopback — no sidecar lives "
            "there. Use the service name as the host in inject_env URLs."
        ),
    ),
)


def compile_project_hint_rules(
    specs: Sequence["ConsistencyRuleSpec"],
) -> tuple[ConsistencyRule, ...]:
    """Compile TOML-shaped ``ConsistencyRuleSpec`` into runtime
    ``ConsistencyRule`` objects.

    B1 deliberately keeps project-hint rules as raw strings at load time
    so that a bad regex surfaces here, with the rule's ``name`` in the
    error message, rather than as a stack trace inside the TOML loader.

    Rejects rules that would be silently inert:

    * empty ``env_paths`` — ``_path_covered_by`` would return False for
      every op, so the rule could never fire; the TOML block would look
      right but have no effect. Fail at compile so authoring mistakes
      don't ship as dead policy.

    Raises:
        ConfigError: if any pattern in any rule fails ``re.compile``, or
            if any rule has empty ``env_paths``. The error cites the
            rule ``name`` and the offending field.
    """
    out: list[ConsistencyRule] = []
    for spec in specs:
        if not spec.env_paths:
            raise ConfigError(
                f"project-hints rule {spec.name!r}: env_paths must be "
                f"non-empty — a rule with no env_paths can never fire "
                f"(silent no-op). Declare at least one path or prefix "
                f"glob (e.g. 'commands.*')."
            )
        try:
            spec_patterns = _compile(*spec.spec_patterns)
        except re.error as exc:
            raise ConfigError(
                f"project-hints rule {spec.name!r}: invalid regex in "
                f"spec_patterns: {exc}"
            ) from exc
        try:
            forbidden_env_patterns = _compile(*spec.forbidden_env)
        except re.error as exc:
            raise ConfigError(
                f"project-hints rule {spec.name!r}: invalid regex in "
                f"forbidden_env: {exc}"
            ) from exc
        out.append(ConsistencyRule(
            name=spec.name,
            env_paths=spec.env_paths,
            spec_patterns=spec_patterns,
            forbidden_env_patterns=forbidden_env_patterns,
            description=spec.description,
        ))
    return tuple(out)


def compose_rules(
    *,
    defaults: Sequence[ConsistencyRule] = (),
    adapter_rules: Sequence[ConsistencyRule] = (),
    project_hint_rules: Sequence[ConsistencyRule] = (),
) -> tuple[ConsistencyRule, ...]:
    """Concatenate rule sources with last-writer-wins on ``name``.

    Order: ``defaults`` → ``adapter_rules`` → ``project_hint_rules``.
    A rule declared in a later source overrides an earlier one with the
    same ``name``. This matches the plan's layer rubric (spec > policy >
    adapter) and lets a project-hint override a framework default or
    adapter baseline of the same name.

    All three parameters default to ``()``. ``DEFAULT_RULES`` is
    intentionally *not* a default here — after B5 the framework ships
    no default rules, and a silently-empty fallback would let a caller
    that forgets to pass a source validate against nothing. The one
    authoritative composition site is the orchestrator, which always
    passes all three sources explicitly.

    Dedup is by ``name`` only; the payload of the later rule wins. Rules
    with distinct names are preserved in declaration order (defaults
    first, then adapters, then project-hints). Within each source,
    original order is preserved — only cross-source duplicates are
    collapsed.

    Shadowed rules (where a later source overrides a same-named rule
    from an earlier source) are logged at INFO. The log names both the
    shadowing and shadowed source, so an operator debugging "why does
    this rule behave differently from the adapter's documentation?" can
    find the project-hint that redefined it. Silent override would
    hide accidental name collisions.
    """
    # Track which source contributed each name so the shadow log can
    # name both sides. Keyed by rule.name → source label.
    source_of: dict[str, str] = {}
    by_name: dict[str, ConsistencyRule] = {}

    def _add(source: str, rules: Sequence[ConsistencyRule]) -> None:
        for rule in rules:
            if rule.name in by_name:
                log.info(
                    "event=consistency_rule_shadow rule=%s shadowed_by=%s "
                    "previous_source=%s",
                    rule.name, source, source_of[rule.name],
                )
            by_name[rule.name] = rule
            source_of[rule.name] = source

    # Insertion order (dict preserves insertion) governs the emitted
    # sequence. Later assignment to the same key overwrites the payload
    # but keeps the original insertion slot, so a project-hints override
    # lands in the position of the first ``defaults`` occurrence. Callers
    # that rely on iteration order can reason about this: once a name is
    # seen, its position in the output is fixed.
    _add("defaults", defaults)
    _add("adapter", adapter_rules)
    _add("project-hints", project_hint_rules)
    return tuple(by_name.values())


def render_consistency_rules_for_prompt(
    rules: Sequence[ConsistencyRule],
) -> str:
    """Serialize a composed rule set into LLM-readable architect-prompt text.

    A1 consistency rules are silent trip-wires unless the architect is
    told about them up-front. The 2026-04-15 budget-brain-v2 wet-run
    surfaced the bug: project-hints' ``migrate-mode-vs-push-mode`` rule
    fired against a ``drizzle-kit push`` manifest the architect had just
    written — because the architect's prompt had no mention of the rule.
    The rule was only a post-hoc gate, never prompt guidance.

    The orchestrator calls this helper with the exact same composed rule
    tuple the post-architect A1 check will run against. Drift between
    "what the architect is told" and "what the architect is judged
    against" is the one thing this function must prevent.

    Empty input → empty string (caller-friendly: no block is injected).
    """
    if not rules:
        return ""

    lines: list[str] = [
        "PROJECT POLICY CONSTRAINTS — hard violations abort the pipeline.",
        "",
        "The following consistency rules will be enforced against your "
        "manifest after you write it. Each rule fires when *any* "
        "spec-pattern matches the spec text AND *any* forbidden-env "
        "pattern matches a string-valued op under the listed env_paths. "
        "Do NOT emit any command or value that matches the forbidden "
        "patterns below — the pipeline will abort and emit a "
        "REMEDIATION_TARGET event against your manifest.",
        "",
    ]
    for rule in rules:
        lines.append(f"- Rule: {rule.name}")
        lines.append(f"  Why: {rule.description}")
        if rule.env_paths:
            paths_str = ", ".join(rule.env_paths)
            lines.append(f"  Applies to manifest env_paths: {paths_str}")
        if rule.forbidden_env_patterns:
            lines.append("  Forbidden patterns (Python regex, case-insensitive):")
            for pat in rule.forbidden_env_patterns:
                lines.append(f"    - {pat.pattern}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def check_spec_env_consistency(
    *,
    spec_text: str,
    manifest: ArchitectureManifest,
    rules: Sequence[ConsistencyRule] = (),
) -> list[ConsistencyError]:
    """Run consistency rules against the architect's proposed amendment.

    Reads ``manifest.runtime.amendment_ops`` — the architect's *proposed*
    env operations, not the folded contract. Fires before the delta is
    persisted, so a failure here means we reject the manifest outright.

    Also synthesizes per-service ops from ``manifest.runtime.services``
    and evaluates them alongside ``amendment_ops``. Services are held
    on ``RuntimeDecl`` as a list (not in the flat dotted-path
    ``amendment_ops``) because ``amend._build_ops`` is what flattens
    them to ``services.<name>.<field>`` keys for the environment delta.
    Synthesizing the same shape here closes the ``services.*`` coverage
    gap without requiring consistency.py to depend on amend.py. The
    synthesis only covers fields that current rules care about
    (``inject_env`` today); extend alongside new rules, not ahead of
    them.

    ``rules`` defaults to ``()`` — an empty rule set means "check
    nothing" and is only useful in tests that want to isolate parse /
    validate behavior from rule firing. Real callers (the orchestrator)
    always pass the full composed rule set from ``compose_rules``. The
    default is intentionally *not* ``DEFAULT_RULES`` because that would
    silently validate-against-nothing whenever a caller forgot the
    parameter.

    Emits a structured log record per violation so operators can grep
    post-mortem logs for rule firings (``event=consistency_violation``).

    Returns an empty list when no contradictions are detected.
    """
    errors: list[ConsistencyError] = []
    # Start with the architect's flat amendment_ops, then overlay
    # synthesized service ops. Later wins on key collision, but in
    # practice these two sources address disjoint path spaces
    # (``services.*`` is never in ``amendment_ops`` — services are
    # held on ``RuntimeDecl.services``, not parsed into
    # ``amendment_ops`` by ``_parse_runtime``).
    ops: dict[str, object] = dict(manifest.runtime.amendment_ops)
    for svc in manifest.runtime.services:
        if svc.inject_env:
            ops[f"services.{svc.name}.inject_env"] = dict(svc.inject_env)

    for rule in rules:
        for op_path, op_value in ops.items():
            if not _path_covered_by(op_path, rule.env_paths):
                continue

            if isinstance(op_value, list):
                # Join with newlines (not spaces) so regexes can't match
                # adjacent list elements as if they were space-separated
                # shell tokens. Authors who want cross-element matching
                # opt into it explicitly.
                env_str = "\n".join(str(v) for v in op_value)
            else:
                env_str = str(op_value)

            env_match: str | None = None
            for p in rule.forbidden_env_patterns:
                m = p.search(env_str)
                if m:
                    env_match = m.group(0)
                    break
            if env_match is None:
                continue

            spec_match: str | None = None
            for p in rule.spec_patterns:
                m = p.search(spec_text)
                if m:
                    spec_match = m.group(0)
                    break
            if spec_match is None:
                continue

            err = ConsistencyError(
                rule_name=rule.name,
                env_path=op_path,
                env_value=env_str,
                spec_match=spec_match,
                env_match=env_match,
                description=rule.description,
            )
            errors.append(err)
            # Structured log only; events.jsonl plumbing deferred — both
            # callers of _apply_manifest_to_environment lack an events_file
            # handle. Grep these with event=consistency_violation.
            log.warning(
                "event=consistency_violation rule=%s env_path=%s env_match=%r spec_match=%r",
                err.rule_name, err.env_path, err.env_match, err.spec_match,
            )

    return errors
