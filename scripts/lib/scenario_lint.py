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
- **unbounded-exec-in-scenario**: scenarios call ``execSync``/
  ``spawnSync`` (synchronous child_process exec) with no ``timeout:``
  option. A sync exec blocks the vitest worker's event loop, so
  ``testTimeout`` cannot interrupt it — a hung tool (e.g. ``pnpm`` in a
  not-yet-wired repo) runs to the gate/agent wall (900s SIGKILL). The
  fix is a bounded ``timeout:`` on the call. Matched code-aware (string
  and comment contents are masked first) so an ``execSync(`` token
  inside a source-assertion string or a comment is not flagged.

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


# ``unbounded-exec-in-scenario`` (2026-06-16, subprocess-stall sprint).
# A SYNCHRONOUS child_process exec (``execSync``/``spawnSync``) with no
# ``timeout:`` option blocks the libuv event loop — so vitest's
# ``testTimeout`` (a timer on that same loop) CANNOT interrupt it. A command
# that hangs (e.g. ``pnpm install`` / ``pnpm lint`` in a not-yet-wired
# monorepo) then runs to the 900s agent/container wall and SIGKILLs the agent
# with no observability. Verified root cause of run 1caa89f9 (three 900s
# wall-timeouts, one aborted campaign spec).
#
# Detection (Neo design, 2026-06-16, corrected against the failing corpus):
# the tool name is decoupled from the exec call via DRY ``run()`` wrappers —
# 9/12 files in the failing spec do ``execSync(cmd, {...})`` with the tool
# literal on a SEPARATE ``run('pnpm …')`` line — so a tool-allowlist regex has
# a FALSE NEGATIVE on the actual bug. The structural foot-gun is the
# sync-exec-without-timeout itself, tool-independent. Async ``exec(cmd, cb)``
# does NOT block the loop (vitest can interrupt it) → NOT flagged. So: match
# the call site, paren-scan its argument span, and flag iff that BOUNDED span
# has no ``timeout:`` key. The span scan (not a lookahead) is what prevents a
# distant/unrelated ``timeout:`` — e.g. the ``timeout-minutes:`` YAML string a
# CI-config assertion greps for — from suppressing the flag.
#
# This rule needs procedural paren-balancing, which the pure-regex
# ``FootGunPattern`` library (``foot_gun_patterns.py``: "exactly ONE compiled
# regex") cannot express, so it lives here as a bespoke scenario-lint check
# rather than a ``FootGunPattern`` record.
#
# Out of scope (accepted, backstopped by the gate hard-wall in
# ``quality-gate.sh`` which bounds any lint miss at the gate timeout rather
# than the 900s container wall): an ALIASED import (``import { execSync as
# runSync }``) is not matched — alias resolution would need import-graph
# parsing, and the idiom is uncommon for these tools; a sync exec inside a
# regex literal (``/\\)/g``) may mis-balance toward a benign extra flag.
# Accepted residual false-NEGATIVES (adversarial/nonsensical shapes in
# generated test code, all bounded by the hard-wall): a key string containing a
# literal ``{``/``}`` (e.g. a key named ``"}"``) can skew the brace count; a
# labeled ``timeout:`` statement inside a trailing callback arg; a ``timeout:``
# key on a NON-options top-level object argument. ``_top_level_timeout_present``
# counts braces span-wide rather than isolating the options argument, so these
# can suppress — they do not occur in real scenario code and a miss only costs
# the gate-timeout backstop, not a silent stall.
_UNBOUNDED_EXEC_CALL_RE = re.compile(r"\b(?:execSync|spawnSync)\s*\(")
# Bare options key (``timeout:``) — searched on the MASKED span so a
# ``timeout:`` appearing inside a command STRING (``'echo timeout: 5'``) is
# already blanked and cannot falsely suppress the flag.
_UNBOUNDED_EXEC_BARE_TIMEOUT_RE = re.compile(r"\btimeout\s*:")
# Quoted options key (``'timeout':`` / ``"timeout":``, incl. computed
# ``["timeout"]:``). ``_mask_noncode`` PRESERVES object-key strings (blanking
# only string VALUES), so this is searched on the MASKED span just like the
# bare key — a ``'timeout':`` inside a string value is already blanked and
# cannot false-suppress.
_UNBOUNDED_EXEC_QUOTED_TIMEOUT_RE = re.compile(r"""['"]timeout['"]\s*\]?\s*:""")
_UNBOUNDED_EXEC_RULE = "unbounded-exec-in-scenario"
# Fail-toward-detection bound: if a call's parens don't balance within this
# many chars (malformed/truncated source), flag rather than silently skip.
_UNBOUNDED_EXEC_SCAN_LIMIT = 2000
_UNBOUNDED_EXEC_FIX_HINT = (
    "Synchronous child_process exec (execSync/spawnSync) blocks the event "
    "loop, so vitest's testTimeout cannot interrupt it — a hung command runs "
    "to the gate/agent wall (900s SIGKILL). Add a `timeout:` (ms) to this "
    "call's options object, e.g. `execSync(cmd, { ...opts, timeout: 120000 })`"
    ". If this is a wrapper helper (e.g. a `run()` function), fixing it here "
    "bounds EVERY caller at once — do NOT add timeouts at the call sites."
)


def _balanced_paren_span(
    text: str, open_idx: int, limit: int,
) -> "tuple[int, int] | None":
    """Return ``(start, end)`` of a call's argument region, where ``open_idx``
    is the index of its opening ``(`` (``text[open_idx] == '('``), found by
    paren-depth scanning. ``start`` is just after the ``(``; ``end`` is the
    index of the matching ``)``. Returns ``None`` when the parens do not
    balance within ``limit`` chars (the caller treats that as
    fail-toward-flag).

    String literals (``'…'``, ``"…"``, `` `…` ``) are skipped so a ``)`` inside
    a command-string argument cannot mis-balance the scan; backslash escapes
    are honored. Template-literal ``${…}`` interpolation is treated as opaque
    string content (a rare edge whose only miss-mode is a benign extra flag,
    backstopped by ``limit``). A full JS tokenizer is overkill for a lint
    heuristic."""
    depth = 0
    i = open_idx
    end_cap = min(len(text), open_idx + limit)
    while i < end_cap:
        ch = text[i]
        if ch in "'\"`":
            quote = ch
            i += 1
            while i < end_cap:
                c = text[i]
                if c == "\\":
                    i += 2
                    continue
                if c == quote:
                    break
                i += 1
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return open_idx + 1, i
        i += 1
    return None


def _mask_noncode(text: str) -> str:
    """Return ``text`` with string-literal and comment CONTENTS replaced by
    spaces, preserving length, byte offsets, and newlines. Lets a downstream
    scanner see only real code: an ``execSync(`` token inside a
    source-assertion string (``expect(s).toContain('execSync(x)')``) or a
    comment (``// don't execSync here``) is masked, so it is neither matched as
    a call site nor able to mis-balance the paren scan, and a ``timeout:``
    inside a string/comment cannot spuriously satisfy the timeout check.

    Handles ``'``/``"``/`` ` `` strings (with backslash escapes) and ``//`` /
    ``/* */`` comments. Single/double-quoted strings stop at a newline (JS
    forbids raw newlines in them), so an UNTERMINATED ``'``/``"`` cannot run to
    EOF and swallow real calls on later lines. Template-literal ``${…}``
    interpolations are NOT masked — they are CODE, so a real ``execSync`` inside
    one (`` `${execSync(cmd)}` ``) is still seen. Does NOT mask regex literals —
    the ``/`` vs division ambiguity makes them unreliable to detect
    heuristically; a regex literal with an unbalanced ``)`` (e.g. ``/\\)/g``) is
    a known, accepted gap that fails toward a (benign) extra flag, not a miss.

    Known residual: a string literal NESTED inside a ``${…}`` interpolation is
    left as code (not re-masked), so a ``)`` inside such a nested string could
    mis-balance — an edge-of-an-edge that fails toward a benign extra flag."""
    out = list(text)
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"":
            # Find the string extent FIRST, then decide whether to blank it. An
            # OBJECT KEY (closing quote followed by ``:``, or ``]:`` for a
            # computed key) is left as CODE so a quoted ``'timeout':`` key
            # survives the mask; a VALUE is blanked. This stops a string VALUE
            # that merely CONTAINS the text ``'timeout':`` (e.g. a shell command
            # ``shell: "echo 'timeout': 5"``) from masquerading as a bounding
            # timeout key. Single/double-quoted strings have no raw newlines, so
            # the scan stops at a newline — an unterminated literal cannot run
            # to EOF and swallow real calls on later lines.
            quote = ch
            j = i + 1
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    break
                j += 1
            closed = j < n and text[j] == quote
            is_key = False
            if closed:
                # Followed by ``:`` (or ``]:`` for a computed key)?
                k = j + 1
                while k < n and text[k] in " \t":
                    k += 1
                if k < n and text[k] == "]":  # computed key: ["timeout"]:
                    k += 1
                    while k < n and text[k] in " \t":
                        k += 1
                followed_by_colon = k < n and text[k] == ":"
                # AND in key POSITION — preceded by ``{``, ``,`` or ``[`` (a
                # computed key ``["timeout"]:``), or the start of the span. This
                # rejects a string VALUE that merely precedes a colon for
                # another reason, e.g. a ternary ``cond ? "x" : y``, which would
                # otherwise be wrongly preserved and could false-suppress the
                # timeout check. (An array element is preceded by ``[`` too but
                # is never followed by ``:``, so allowing ``[`` is safe.)
                b = i - 1
                while b >= 0 and text[b] in " \t\r\n":
                    b -= 1
                in_key_position = b < 0 or text[b] in "{,["
                is_key = followed_by_colon and in_key_position
            if not is_key:
                blank_end = (j + 1) if closed else j
                for m in range(i, min(blank_end, n)):
                    if text[m] != "\n":
                        out[m] = " "
            i = (j + 1) if closed else j
            continue
        if ch == "`":
            out[i] = " "
            i += 1
            while i < n:
                c = text[i]
                if c == "\\":
                    out[i] = " "
                    if i + 1 < n:
                        out[i + 1] = " "
                    i += 2
                    continue
                if c == "`":
                    out[i] = " "
                    i += 1
                    break
                if c == "$" and i + 1 < n and text[i + 1] == "{":
                    # ${…} interpolation is CODE — leave it unmasked. Track
                    # brace depth to find the matching ``}`` and resume the
                    # template-string scan after it.
                    depth = 0
                    while i < n:
                        cc = text[i]
                        if cc == "{":
                            depth += 1
                        elif cc == "}":
                            depth -= 1
                            if depth == 0:
                                i += 1
                                break
                        i += 1
                    continue
                if c != "\n":
                    out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            out[i] = out[i + 1] = " "
            i += 2
            while i < n:
                if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    out[i] = out[i + 1] = " "
                    i += 2
                    break
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            continue
        i += 1
    return "".join(out)


def _top_level_timeout_present(masked_span: str) -> bool:
    """True iff a ``timeout:`` key is a DIRECT key of this call's top-level
    options object — not a ``timeout:`` belonging to a nested call
    (``execSync(buildCmd({ timeout: 5 }), { cwd })``) NOR a nested object
    (``execSync(cmd, { env: { timeout: 1 } })`` — that's ``env.timeout``, not
    ``options.timeout``). The discriminator is depth at the key position:
    ``paren_depth == 0`` (not inside a nested call) AND ``brace_depth == 1``
    (a direct key of the single top-level options object, not nested one level
    deeper). A balanced inner ``{}`` before the key (e.g. an arrow-fn arg) nets
    to 0, so the real options key still reads at brace depth 1.

    Both bare and quoted keys are searched on the MASKED span: masking blanks
    string VALUES but PRESERVES object-key strings, so a ``'timeout':`` inside a
    string value is already gone and only a genuine key can match."""
    starts = [m.start() for m in _UNBOUNDED_EXEC_BARE_TIMEOUT_RE.finditer(masked_span)]
    starts += [m.start() for m in _UNBOUNDED_EXEC_QUOTED_TIMEOUT_RE.finditer(masked_span)]
    for pos in starts:
        pre = masked_span[:pos]
        paren_depth = pre.count("(") - pre.count(")")
        brace_depth = pre.count("{") - pre.count("}")
        if paren_depth == 0 and brace_depth == 1:
            return True
    return False


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

    # File-level rule — synchronous exec with no timeout (event-loop hang).
    # Matched CODE-AWARE: string-literal and comment contents are masked first
    # (length/offset-preserving) so an ``execSync(`` token inside a
    # source-assertion string or a comment is NOT a call site (a false positive
    # there would clear+retry the TE and could HardAbort the run — the exact
    # failure this sprint exists to prevent). For each real call site,
    # paren-scan its OWN argument span (on the masked text, so parens inside
    # strings can't mis-balance it) and flag iff that span carries no
    # top-level ``timeout:`` key. One issue per call site (a shared ``run()``
    # wrapper yields a single issue at the wrapper, not one per caller).
    masked = _mask_noncode(text)
    for match in _UNBOUNDED_EXEC_CALL_RE.finditer(masked):
        open_idx = match.end() - 1  # index of the matched '('
        span = _balanced_paren_span(
            masked, open_idx, _UNBOUNDED_EXEC_SCAN_LIMIT,
        )
        if span is None:
            # Unbalanced/runaway within the scan limit → fail-toward-flag.
            flagged = True
        else:
            flagged = not _top_level_timeout_present(masked[span[0]:span[1]])
        if flagged:
            line_no = _line_number_for(text, match.start())
            issues.append(LintIssue(
                file=path,
                line=line_no,
                rule=_UNBOUNDED_EXEC_RULE,
                excerpt=_excerpt_at(lines, line_no),
                fix_hint=_UNBOUNDED_EXEC_FIX_HINT,
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
