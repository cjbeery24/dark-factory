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


def lint_file(path: Path) -> list[LintIssue]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    issues: list[LintIssue] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _match_migration(line):
            issues.append(LintIssue(
                file=path,
                line=line_no,
                rule="db-migrate-in-scenario",
                excerpt=line.strip()[:160],
                fix_hint=_FIX_HINT_DB_MIGRATE,
            ))
    return issues


_SCENARIO_GLOBS = ("*.test.ts", "*.test.js", "*.spec.ts", "*.spec.js", "test_*.py")


def lint_dir(root: Path) -> list[LintIssue]:
    if not root.is_dir():
        return []
    issues: list[LintIssue] = []
    for pattern in _SCENARIO_GLOBS:
        for p in root.rglob(pattern):
            issues.extend(lint_file(p))
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
    args = ap.parse_args(argv)

    issues = lint_dir(args.scenarios_dir)

    if args.json:
        import json
        payload = [
            {"file": str(i.file), "line": i.line, "rule": i.rule,
             "excerpt": i.excerpt, "fix_hint": i.fix_hint}
            for i in issues
        ]
        print(json.dumps(payload))
    else:
        sys.stdout.write(format_report(issues))

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
