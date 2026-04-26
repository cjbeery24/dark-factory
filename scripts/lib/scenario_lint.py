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

# Patterns that indicate a migration invocation inside a scenario.
# Conservative — only flag lines that look like a subprocess call that
# runs a schema-migration command. Don't flag code that merely mentions
# the string "migrate" (e.g., a test description).
_MIGRATION_CMD_SHELL_RE = re.compile(
    r"""(?x)
    (
        execSync      |       # node child_process.execSync("...")
        spawnSync     |       # node child_process.spawnSync
        exec          |       # child_process.exec (callback form)
        subprocess\.run  |    # python subprocess.run("...", shell=True)
        subprocess\.check_(?:output|call)  |
        subprocess\.Popen     # python subprocess.Popen(...)
    )
    [^;\n]{0,200}?            # command args (bounded, no newline)
    (
        (?:cd\s+\S+\s*&&\s*)?  # optional leading cd
        (?:
            pnpm\s+db:migrate                |
            npm\s+run\s+db:migrate           |
            yarn\s+db:migrate                |
            (?:npx\s+|pnpm\s+exec\s+)?drizzle-kit\s+(?:push|migrate)  |
            (?:python\s+-m\s+)?alembic\s+upgrade\b                     |
            rake\s+db:migrate                |
            (?:python\s+manage\.py|django-admin)\s+migrate\b           |
            prisma\s+migrate\s+(?:dev|deploy)                          |
            goose\s+up                       |
            flyway\s+migrate
        )
    )
    """,
)

# Python subprocess list-form: subprocess.run(["alembic", "upgrade", ...]).
# List-form arg parsing is simpler: any known migration tool as the
# first array element counts.
_MIGRATION_CMD_LIST_RE = re.compile(
    r"""(?x)
    subprocess\.(?:run|Popen|check_(?:output|call))
    \s*\(\s*\[\s*
    ['"](
        alembic |
        drizzle-kit |
        goose |
        flyway |
        prisma
    )['"]
    """,
)


def _match_migration(line: str) -> bool:
    return bool(
        _MIGRATION_CMD_SHELL_RE.search(line)
        or _MIGRATION_CMD_LIST_RE.search(line)
    )


# A ``tx`SET LOCAL <name> = '${value}'`` `` tagged-template. postgres.js
# converts ``${value}`` to a bind parameter ``$1``; Postgres rejects the
# statement because ``SET LOCAL`` cannot accept a bind. The correct form
# is ``tx`SELECT set_config(${name}, ${value}, true)`` ``, which runs
# the value through the bindable ``set_config`` function.
#
# The regex is bounded: ``tx`` → backtick → non-backtick body (lazy) →
# ``SET`` → ``LOCAL`` → non-backtick body (lazy) → ``${``. The bounded
# ``[^`]`` segments prevent the lazy quantifier from roaming across
# unrelated tagged templates elsewhere in the file.
_PARAMETERIZED_SET_LOCAL_RE = re.compile(
    r"""(?x)
    \btx\s*`                # tagged-template call on ``tx``
    [^`]*?                  # SQL body before SET LOCAL (lazy, no backtick)
    \bSET\s+LOCAL\b         # the foot-gun statement
    [^`]*?                  # SQL body between SET LOCAL and placeholder
    \$\{                    # postgres.js bind-parameter placeholder
    """,
    re.IGNORECASE,
)


# A second ``postgres(process.env.…)`` client followed by a
# ``current_setting('app.*')`` read on that client. ``SET LOCAL`` is
# tx-scoped to the app connection; a peer connection's ``current_setting``
# always returns ``''`` regardless of impl correctness, so the
# assertion carries zero RLS signal.
#
# Regex ported verbatim from
# ``darkfactory/adapters/postgres/adapter.py:121-138`` so the lint rule
# and the runtime classification hint are in exact lockstep — if one
# fires for a scenario, the other fires too. ``[\s\S]*?`` allows the
# two pattern anchors to be arbitrarily far apart, since
# ``lint_file`` runs this against the full file body rather than
# per-describe like the classification hint does.
_PEER_CLIENT_GUC_READ_RE = re.compile(
    r"postgres\s*\(\s*process\.env\."
    r"[A-Z_][A-Z0-9_]*"
    r"[\s\S]*?current_setting\s*\(\s*['\"`]app\."
)


@dataclass
class LintIssue:
    file: Path
    line: int
    rule: str
    excerpt: str
    fix_hint: str


_FIX_HINT_DB_MIGRATE = (
    "Remove the migration invocation from this scenario. Migrations are "
    "substrate setup and must run before the gate, not from inside a "
    "blind scenario. If a scenario requires the DB to be migrated, add "
    "the migration to the substrate manifest (see "
    ".dark-factory/architecture/substrate-manifests/<spec>.toml) so the "
    "pipeline runs it once during Substrate Verification."
)

_FIX_HINT_SET_LOCAL_PARAM = (
    "Replace ``tx`SET LOCAL app.x = '${val}'`` with "
    "``tx`SELECT set_config('app.x', ${val}, true)`` —  SET LOCAL does "
    "not accept bind parameters, but set_config does and has identical "
    "semantics (transaction-local when the third argument is ``true``). "
    "If you are asserting that the app correctly writes the GUC, use "
    "``assertAppSetsTxGuc`` from the postgres adapter; if you are "
    "round-tripping a known value, use ``assertTxGucRoundTrips``."
)

_FIX_HINT_PEER_CLIENT_GUC = (
    "Peer-connection current_setting('app.*') always returns '' — SET "
    "LOCAL is transaction-scoped to the app's own transaction, so a "
    "second client can never observe it. Move the assertion onto the "
    "same tx as the SET (use ``client.begin(async tx => …)`` and read "
    "the GUC via ``tx``), or use ``assertAppSetsTxGuc`` from the "
    "postgres adapter, which opens the tx, invokes the app callback, "
    "and reads back the GUC within the same tx."
)


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
    for line_no, line in enumerate(lines, start=1):
        if _match_migration(line):
            issues.append(LintIssue(
                file=path,
                line=line_no,
                rule="db-migrate-in-scenario",
                excerpt=line.strip()[:160],
                fix_hint=_FIX_HINT_DB_MIGRATE,
            ))

    # File-level rules (patterns can span multiple lines via lazy
    # ``[\s\S]*?`` spans and/or backtick-bounded template bodies, so
    # line-at-a-time matching misses them). ``finditer`` yields every
    # non-overlapping match; the reporter groups by rule.
    for match in _PARAMETERIZED_SET_LOCAL_RE.finditer(text):
        line_no = _line_number_for(text, match.start())
        issues.append(LintIssue(
            file=path,
            line=line_no,
            rule="parameterized-set-local",
            excerpt=_excerpt_at(lines, line_no),
            fix_hint=_FIX_HINT_SET_LOCAL_PARAM,
        ))

    for match in _PEER_CLIENT_GUC_READ_RE.finditer(text):
        line_no = _line_number_for(text, match.start())
        issues.append(LintIssue(
            file=path,
            line=line_no,
            rule="peer-client-guc-read",
            excerpt=_excerpt_at(lines, line_no),
            fix_hint=_FIX_HINT_PEER_CLIENT_GUC,
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
