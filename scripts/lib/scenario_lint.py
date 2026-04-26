#!/usr/bin/env python3
"""Scenario lint — pre-gate check on generated scenario code.

Runs between scenario generation and blind-scenario execution. Catches
shape-level mistakes the Scenario Architect (or a human author) can
make that silently turn into "test crashes with ENOENT / cwd not
found" noise once the gate runs. Cheaper to fail here than to chase
the crash through three iterations of the loop.

Current rules:

- **db-migrate-in-scenario**: scenarios call ``pnpm db:migrate`` /
  ``drizzle-kit push`` / ``alembic upgrade`` / ``rake db:migrate`` / ...
  from a subprocess, usually prefixed by ``cd <dir> && ``. Migrations
  are substrate-setup, not scenario concerns — see
  ``agents/test-engineer.md`` for the rule. The lint error points the
  author at the substrate manifest instead.
- **parameterized-set-local**: scenarios write
  ``tx`SET LOCAL app.x = '${val}'`` ``. Postgres cannot parameterize
  the value position of ``SET LOCAL`` — the placeholder becomes ``$1``
  and the server rejects it with "could not determine data type of
  parameter $1". The correct primitive is
  ``tx`SELECT set_config('app.x', ${val}, true)`` `` (or the
  ``assertTxGucRoundTrips`` helper from the postgres adapter).
- **peer-client-guc-read**: scenarios open a second postgres client
  (``postgres(process.env.…)``) and read ``current_setting('app.*')``
  on it. ``SET LOCAL`` is tx-scoped to the app's own transaction, so
  the peer always sees ``''``; the assertion is a wrong-scope oracle.
  Use ``assertAppSetsTxGuc`` from the postgres adapter instead.

Rules #2 and #3 mirror the classification-hint regexes at
``darkfactory/adapters/postgres/adapter.py:121-138`` — by catching
these patterns pre-gate, we turn what would be a three-iteration
oracle-escalation cycle into a single-shot fix pointer before the
scenario ever runs.

Exit code 0 when no issues; 1 when one or more rules fire. Stdout is
a markdown-ish report suitable for inclusion in the gate report.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# B5 (2026-04-25): foot-gun pattern definitions live in the shared
# library so ``analyze_trial.KNOWN_BUGS`` and
# ``retire_after_fixtures.Fixture`` see the same regexes. Kept the
# scenario_lint-side helper functions and ``LintIssue`` shape
# unchanged so existing call sites and tests are untouched.
from darkfactory.contracts.foot_gun_patterns import BY_NAME_AND_DIALECT

_DB_MIGRATE = BY_NAME_AND_DIALECT[("db-migrate-in-scenario", "ts")]
_PARAMETERIZED_SET_LOCAL = BY_NAME_AND_DIALECT[("parameterized-set-local", "ts")]
_PEER_CLIENT_GUC_READ = BY_NAME_AND_DIALECT[("peer-client-guc-read", "ts")]


# Module-scope re-exports of the built-in patterns. Existing tests
# (``test_b8_lint_row_leak_regression``, ``test_b9b_retire_after_pins``)
# and any external grep over this module's surface continue to find
# the legacy ``_*_RE`` names. The objects ARE the library's compiled
# regexes (identity, not copy). The unified ``_DB_MIGRATE_RE``
# alternation replaces the prior shell+list split (Linus B5 review);
# the legacy aliases ``_MIGRATION_CMD_SHELL_RE`` / ``_MIGRATION_CMD_LIST_RE``
# both point at the unified regex so backwards-compat callers see
# match-coverage of both shell and list shapes.
_MIGRATION_CMD_SHELL_RE = _DB_MIGRATE.pattern
_MIGRATION_CMD_LIST_RE = _DB_MIGRATE.pattern
_PARAMETERIZED_SET_LOCAL_RE = _PARAMETERIZED_SET_LOCAL.pattern
_PEER_CLIENT_GUC_READ_RE = _PEER_CLIENT_GUC_READ.pattern


@dataclass
class LintIssue:
    file: Path
    line: int
    rule: str
    excerpt: str
    fix_hint: str


def _line_number_for(text: str, offset: int) -> int:
    """Map a byte offset inside ``text`` back to a 1-based line number.

    ``str.count`` is O(n) but called at most twice per file per rule,
    so the cost is negligible next to the regex scan itself.
    """
    return text.count("\n", 0, offset) + 1


def _excerpt_at(lines: list[str], line_no: int) -> str:
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1].strip()[:160]
    return ""


def lint_file(
    path: Path,
    *,
    forbidden_test_shapes: "list[_SpecForbiddenShape] | None" = None,
) -> list[LintIssue]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    issues: list[LintIssue] = []
    lines = text.splitlines()

    # Line-level rule — migration invocations inside a scenario.
    # Linus B5 review: ``_match_migration`` helper collapsed into the
    # single ``.search()`` call now that the regex is unified.
    for line_no, line in enumerate(lines, start=1):
        if _DB_MIGRATE.pattern.search(line):
            issues.append(LintIssue(
                file=path,
                line=line_no,
                rule=_DB_MIGRATE.name,
                excerpt=line.strip()[:160],
                fix_hint=_DB_MIGRATE.fix_hint,
            ))

    # File-level rules (patterns can span multiple lines via lazy
    # ``[\s\S]*?`` spans and/or backtick-bounded template bodies, so
    # line-at-a-time matching misses them). ``finditer`` yields every
    # non-overlapping match; the reporter groups by rule.
    for match in _PARAMETERIZED_SET_LOCAL.pattern.finditer(text):
        line_no = _line_number_for(text, match.start())
        issues.append(LintIssue(
            file=path,
            line=line_no,
            rule=_PARAMETERIZED_SET_LOCAL.name,
            excerpt=_excerpt_at(lines, line_no),
            fix_hint=_PARAMETERIZED_SET_LOCAL.fix_hint,
        ))

    for match in _PEER_CLIENT_GUC_READ.pattern.finditer(text):
        line_no = _line_number_for(text, match.start())
        issues.append(LintIssue(
            file=path,
            line=line_no,
            rule=_PEER_CLIENT_GUC_READ.name,
            excerpt=_excerpt_at(lines, line_no),
            fix_hint=_PEER_CLIENT_GUC_READ.fix_hint,
        ))

    # Spec-declared §4a forbidden_test_shapes (Phase 5 Stage 2d). Each
    # entry is a regex authored by the spec, not a built-in rule. The
    # rule name carries the literal pattern so authors can grep for it
    # in their spec when chasing a hit.
    if forbidden_test_shapes:
        for shape in forbidden_test_shapes:
            for match in shape.compiled.finditer(text):
                line_no = _line_number_for(text, match.start())
                issues.append(LintIssue(
                    file=path,
                    line=line_no,
                    rule=f"spec-forbidden-shape:{shape.pattern}",
                    excerpt=_excerpt_at(lines, line_no),
                    fix_hint=(
                        f"Spec §4a forbids this pattern. Reason: "
                        f"{shape.reason or '(no reason provided)'}"
                    ),
                ))

    return issues


# ---------------------------------------------------------------------------
# Spec-driven forbidden_test_shapes loader
# ---------------------------------------------------------------------------

@dataclass
class _SpecForbiddenShape:
    pattern: str
    reason: str
    compiled: "re.Pattern[str]"


_YAML_FENCE = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def _section_4a_body(text: str) -> str:
    """Return §4a body up to the next ``## `` heading."""
    lines = text.splitlines(keepends=False)
    in_section = False
    body: list[str] = []
    pat = re.compile(r"^##\s+4a\.\s+", re.IGNORECASE)
    for line in lines:
        if in_section:
            if line.startswith("## "):
                break
            body.append(line)
            continue
        if pat.match(line):
            in_section = True
    return "\n".join(body)


def load_forbidden_test_shapes(
    spec_path: Path,
) -> list[_SpecForbiddenShape]:
    """Parse §4a forbidden_test_shapes from a spec markdown file.

    Returns empty list when:
    - The spec file doesn't exist.
    - There is no §4a section.
    - YAML parsing fails (malformed pattern blocks log to stderr but
      do not crash the lint).
    - The pattern itself fails to compile (logged, skipped).
    """
    if not spec_path.is_file():
        return []
    try:
        text = spec_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    body = _section_4a_body(text)
    if not body:
        return []

    # PyYAML is available in the gate container (py3-yaml in
    # docker/Dockerfile.base). Lazy-import so a missing yaml doesn't
    # break the lint when the spec has no §4a anyway.
    try:
        import yaml  # type: ignore
    except ImportError:
        sys.stderr.write(
            "scenario_lint: pyyaml not available; skipping spec "
            "forbidden_test_shapes\n"
        )
        return []

    out: list[_SpecForbiddenShape] = []
    for raw in _YAML_FENCE.findall(body):
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            sys.stderr.write(
                f"scenario_lint: §4a YAML parse error: {exc}\n"
            )
            continue
        if not isinstance(doc, dict):
            continue
        shapes = doc.get("forbidden_test_shapes", []) or []
        if not isinstance(shapes, list):
            continue
        for entry in shapes:
            if not isinstance(entry, dict):
                continue
            pattern = entry.get("pattern", "")
            reason = entry.get("reason", "")
            if not isinstance(pattern, str) or not pattern:
                continue
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                sys.stderr.write(
                    f"scenario_lint: §4a forbidden_test_shapes pattern "
                    f"{pattern!r} is not valid regex ({exc}); skipping\n"
                )
                continue
            out.append(_SpecForbiddenShape(
                pattern=pattern,
                reason=str(reason) if reason else "",
                compiled=compiled,
            ))
    return out


_SCENARIO_GLOBS = ("*.test.ts", "*.test.js", "*.spec.ts", "*.spec.js", "test_*.py")


def lint_dir(
    root: Path,
    *,
    forbidden_test_shapes: "list[_SpecForbiddenShape] | None" = None,
) -> list[LintIssue]:
    if not root.is_dir():
        return []
    issues: list[LintIssue] = []
    for pattern in _SCENARIO_GLOBS:
        for p in root.rglob(pattern):
            issues.extend(lint_file(
                p, forbidden_test_shapes=forbidden_test_shapes,
            ))
    return issues


def format_report(issues: list[LintIssue]) -> str:
    if not issues:
        return "scenario-lint: no issues\n"
    lines: list[str] = [f"scenario-lint: {len(issues)} issue(s)\n"]
    # Group by rule for readability.
    by_rule: dict[str, list[LintIssue]] = {}
    for i in issues:
        by_rule.setdefault(i.rule, []).append(i)
    for rule, group in by_rule.items():
        lines.append(f"\n## {rule} ({len(group)})\n")
        # Fix hint is per-rule, not per-issue.
        lines.append(f"{group[0].fix_hint}\n")
        for issue in group:
            lines.append(f"- {issue.file}:{issue.line}: `{issue.excerpt}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenarios_dir", type=Path,
                    help="Directory containing generated scenario files.")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON instead of the markdown-ish report.")
    ap.add_argument("--spec-path", type=Path, default=None,
                    help=(
                        "Path to the spec markdown. When provided, §4a "
                        "forbidden_test_shapes patterns are loaded and "
                        "applied to every test file in scenarios_dir. "
                        "Optional — omit to run only the built-in rules."
                    ))
    ap.add_argument("--detail-out", type=Path, default=None,
                    help=(
                        "Path to write the verbose lint report. When set, "
                        "stdout becomes the issue count only (a single "
                        "integer); the verbose body — rule names, file:line "
                        "anchors, source excerpts — goes to this path. "
                        "ROADMAP.md Phase 2 B2: keeps test-shape information "
                        "off the gate report (the validator-bridge surface) "
                        "while preserving full diagnostics for "
                        "Reviewer/Oracle/human consumers via the audit dir."
                    ))
    args = ap.parse_args(argv)

    forbidden_shapes: list[_SpecForbiddenShape] = []
    if args.spec_path is not None:
        forbidden_shapes = load_forbidden_test_shapes(args.spec_path)

    issues = lint_dir(
        args.scenarios_dir,
        forbidden_test_shapes=forbidden_shapes,
    )

    if args.json:
        import json
        payload = [
            {"file": str(i.file), "line": i.line, "rule": i.rule,
             "excerpt": i.excerpt, "fix_hint": i.fix_hint}
            for i in issues
        ]
        if args.detail_out is not None:
            args.detail_out.parent.mkdir(parents=True, exist_ok=True)
            args.detail_out.write_text(json.dumps(payload))
            print(len(issues))
        else:
            print(json.dumps(payload))
    else:
        if args.detail_out is not None:
            args.detail_out.parent.mkdir(parents=True, exist_ok=True)
            args.detail_out.write_text(format_report(issues))
            print(len(issues))
        else:
            sys.stdout.write(format_report(issues))

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
