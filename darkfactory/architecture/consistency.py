"""Post-architect spec↔environment consistency checks.

When the architect emits an environment delta that contradicts the spec's
declared constraints, the pipeline should fail *before* the implementer is
invoked — not burn an iteration watching the implementer trust the env and
violate the spec.

This is a deterministic keyword-pair check against a curated table. Each
row is motivated by a post-mortem where spec/env drift cost us a pipeline
iteration. The matching is literal regex; no LLM, no semantic extraction.

If a contradiction slips through, add a row and a regression test.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Sequence

from darkfactory.spec.manifest import ArchitectureManifest

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
    for pattern in rule_paths:
        if pattern.endswith(".*"):
            if path.startswith(pattern[:-1]):
                return True
        elif path == pattern:
            return True
    return False


# Seed rules. Grow via post-mortem evidence, not speculation.
# Each row cites the incident that motivated it.
DEFAULT_RULES: tuple[ConsistencyRule, ...] = (
    # Motivated by 2026-04-14 database-foundation escalation:
    # spec §Constraints said "Migrations are CLI-only (`db:migrate`) — never
    # auto-applied on startup"; architect emitted
    # commands.setup="cd apps/api && npx drizzle-kit push". The implementer
    # trusted the env.toml, moved RLS GUC setup into runtime code, and
    # broke the RLS-via-migration invariant.
    #
    # env_paths is a prefix over every commands.* key because
    # `drizzle-kit push` at install / setup / server_start is equally
    # contrary to "migrations are CLI-only."
    ConsistencyRule(
        name="migrate-mode-vs-push-mode",
        env_paths=("commands.*",),
        spec_patterns=_compile(
            # Direct CLI-command reference with backticks or without.
            r"`db:migrate`",
            r"\bdb:migrate\b",
            r"\bdrizzle-kit\s+migrate\b",
            # Generic CLI-discipline phrases.
            r"\bmigrate[- ]only\b",
            r"\bnever\s+auto[\s-]+applied\b",
            # "CLI-only" / "CI-only" / "manual" near "migration" word.
            # [^.]{0,80} tolerates newlines (cross-bullet) but stops at
            # the next sentence terminator.
            r"\bmigration[s]?[^.]{0,80}\b(?:CLI|CI|manual)[- ]only\b",
            r"\b(?:CLI|CI|manual)[- ]only[^.]{0,80}\bmigration",
            # Active forbiddance of push mode.
            r"\b(?:not|never)\s+(?:drizzle-kit\s+)?push\b",
            r"\bno\s+auto[\s-]+migration\s+on\s+startup\b",
        ),
        forbidden_env_patterns=_compile(
            r"\bdrizzle-kit\s+push\b",
            r"\bdb:push\b",
        ),
        description=(
            "Spec mandates CLI-driven migrations (db:migrate); env would "
            "run push-mode (drizzle-kit push / db:push) which auto-applies "
            "schema changes and bypasses the migration journal."
        ),
    ),
)


def check_spec_env_consistency(
    *,
    spec_text: str,
    manifest: ArchitectureManifest,
    rules: Sequence[ConsistencyRule] = DEFAULT_RULES,
) -> list[ConsistencyError]:
    """Run consistency rules against the architect's proposed amendment.

    Reads ``manifest.runtime.amendment_ops`` — the architect's *proposed*
    env operations, not the folded contract. Fires before the delta is
    persisted, so a failure here means we reject the manifest outright.

    Coverage gap: only ``amendment_ops`` is checked.
    ``runtime.services[*].command`` goes through a separate amendment
    path (amend._build_ops) and is not inspected here. Expand when a
    post-mortem shows a service-command carried a contradictory
    invariant.

    Emits a structured log record per violation so operators can grep
    post-mortem logs for rule firings (``event=consistency_violation``).

    Returns an empty list when no contradictions are detected.
    """
    errors: list[ConsistencyError] = []
    ops = manifest.runtime.amendment_ops

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
