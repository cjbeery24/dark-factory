"""Tests for C5 auto-remediation policy wiring.

Covers three user-facing surfaces introduced by C5:

1. ``dark-factory remediate --apply --accept-novel`` bypasses the novel-
   rule guard in non-TTY contexts and emits a
   ``REMEDIATION_ACCEPTED_NOVEL`` audit event.
2. ``dark-factory remediate --accept-novel`` without ``--apply`` errors
   at the arg layer (silently-inert combination).
3. ``dark-factory hints stats`` aggregates ``REMEDIATION_OUTCOME``
   events per ``rule_or_check`` and prints a per-rule table sorted by
   total-desc.

Boundaries:

* REMEDIATION_TARGET and REMEDIATION_OUTCOME events are written
  directly via the emit helpers, not by driving a failing pipeline —
  the CLI surfaces are the unit under test.
* ``sys.stdin.isatty`` is patched false throughout to exercise the
  CI-shaped code path (the novel-rule guard's primary use case).
* ``_prompt_baseline_policy`` is never invoked because the new
  ``stats`` handler doesn't touch it.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from darkfactory.cli import main
from darkfactory.events.remediation import (
    emit_remediation_outcome,
    emit_remediation_target,
)
from tests.helpers import TempDirTestCase


class _CLIBase(TempDirTestCase):
    def _run(
        self,
        *args: str,
        stdin_responses: list[str] | None = None,
        isatty: bool = False,
    ) -> tuple[int, str, str]:
        full = ["--project-root", str(self.tmp_dir), *args]
        out, err = io.StringIO(), io.StringIO()
        code = 0
        responses = iter(stdin_responses or [])

        def _fake_input(prompt: str = "") -> str:
            try:
                return next(responses)
            except StopIteration:
                raise EOFError()

        with (
            redirect_stdout(out),
            redirect_stderr(err),
            mock.patch("builtins.input", _fake_input),
            mock.patch.object(sys.stdin, "isatty", return_value=isatty),
        ):
            try:
                main(full)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    @property
    def events_file(self) -> Path:
        return self.tmp_dir / ".dark-factory" / "events.jsonl"

    @property
    def hints_path(self) -> Path:
        return self.tmp_dir / ".dark-factory" / "project-hints.toml"

    @staticmethod
    def _rule_toml(name: str = "migrate-vs-push") -> str:
        return (
            "[[policy.consistency_rules]]\n"
            f'name = "{name}"\n'
            'description = "migrate discipline"\n'
            "spec_patterns = ['db:migrate']\n"
            "forbidden_env = ['drizzle-kit\\s+push']\n"
            "env_paths = ['commands.setup']\n"
        )

    def _emit_target(
        self,
        run_id: str = "run-1",
        spec: str = "demo-spec",
        rule: str = "migrate-vs-push",
        suggested_diff: str | None = None,
    ) -> None:
        emit_remediation_target(
            self.events_file,
            spec=spec,
            run_id=run_id,
            failure_class="consistency_violation",
            rule_or_check=rule,
            remediation_type="add_project_hint",
            remediation_target_path=".dark-factory/project-hints.toml",
            auto_applicable=True,
            suggested_diff=(
                self._rule_toml(rule)
                if suggested_diff is None
                else suggested_diff
            ),
            rationale="Spec says migrate, env emits push.",
        )

    def _read_events(self) -> list[dict]:
        if not self.events_file.exists():
            return []
        out: list[dict] = []
        for line in self.events_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


# ---------- --accept-novel on remediate --------------------------------


class TestAcceptNovelFlag(_CLIBase):
    def test_novel_plus_apply_without_accept_novel_errors_on_non_tty(self) -> None:
        self._emit_target(run_id="run-1")
        code, _out, err = self._run("remediate", "run-1", "--apply", isatty=False)
        self.assertEqual(code, 1)
        self.assertIn("NOVEL RULE", err)
        # And the file was not written.
        self.assertFalse(self.hints_path.exists())

    def test_novel_plus_apply_plus_accept_novel_proceeds(self) -> None:
        self._emit_target(run_id="run-1")
        code, _out, err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", isatty=False,
        )
        self.assertEqual(code, 0)
        # Degraded-run warning surfaced on stderr (operator visibility).
        self.assertIn("DEGRADED RUN", err)
        # Rule was written.
        self.assertTrue(self.hints_path.exists())
        self.assertIn(
            "migrate-vs-push",
            self.hints_path.read_text(encoding="utf-8"),
        )

    def test_novel_plus_apply_plus_accept_novel_emits_audit_event(self) -> None:
        self._emit_target(run_id="run-1")
        code, _out, _err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", isatty=False,
        )
        self.assertEqual(code, 0)

        events = self._read_events()
        audit = [
            e for e in events
            if e.get("event") == "REMEDIATION_ACCEPTED_NOVEL"
        ]
        self.assertEqual(len(audit), 1, f"expected 1 audit event, got {events}")
        rec = audit[0]
        self.assertEqual(rec["run_id"], "run-1")
        self.assertEqual(rec["data"]["rule_or_check"], "migrate-vs-push")
        # Non-TTY invocations record applied_by="ci" — picked up by
        # downstream auditing to tell interactive ops from CI bypasses.
        self.assertEqual(rec["data"]["applied_by"], "ci")
        self.assertIn("override", rec["data"]["reason"])

    def test_accept_novel_without_apply_errors_at_arg_layer(self) -> None:
        self._emit_target(run_id="run-1")
        code, _out, err = self._run(
            "remediate", "run-1", "--accept-novel", isatty=False,
        )
        # _arg_error exits 2 (argparse convention for bad cmdline).
        self.assertEqual(code, 2)
        self.assertIn("--accept-novel", err)
        self.assertIn("--apply", err)

    def test_accept_novel_without_apply_errors_on_spec_quality_target(self) -> None:
        # The arg-shape check must cover every failure class, not just
        # consistency_violation. Earlier this check lived in the
        # consistency branch — a Guided (spec_quality) target silently
        # ignored `--accept-novel` without `--apply`. Regression guard.
        from darkfactory.events.remediation import emit_remediation_target
        emit_remediation_target(
            self.events_file,
            spec="demo-spec",
            run_id="run-sq",
            failure_class="spec_quality",
            rule_or_check="spec_validator",
            remediation_type="edit_spec",
            remediation_target_path=".dark-factory/specs/demo-spec.md",
            auto_applicable=False,
            suggested_diff="",
            rationale="validator said no",
        )
        code, _out, err = self._run(
            "remediate", "run-sq", "--accept-novel", isatty=False,
        )
        self.assertEqual(code, 2)
        self.assertIn("--accept-novel", err)
        self.assertIn("--apply", err)

    def test_accept_novel_on_non_novel_target_warns_but_proceeds(self) -> None:
        # Seed an older target so the rule is no longer novel.
        self._emit_target(run_id="run-older")
        # Second target (same rule) on a *different* run.
        self._emit_target(run_id="run-1")
        code, _out, _err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", isatty=False,
        )
        # Succeeds; --accept-novel is belt-and-braces here.
        self.assertEqual(code, 0)
        # No audit event (rule wasn't novel).
        events = self._read_events()
        audit = [
            e for e in events
            if e.get("event") == "REMEDIATION_ACCEPTED_NOVEL"
        ]
        self.assertEqual(len(audit), 0)


# ---------- hints stats --------------------------------------------------


class TestHintsStats(_CLIBase):
    def _emit_outcome(
        self,
        rule: str,
        outcome: str,
        spec: str = "demo-spec",
        run_id: str | None = None,
    ) -> None:
        emit_remediation_outcome(
            self.events_file,
            spec=spec,
            run_id=run_id or f"run-{rule}-{outcome}",
            remediation_run_id="run-origin",
            rule_or_check=rule,
            remediation_type="add_project_hint",
            outcome=outcome,  # type: ignore[arg-type]
        )

    def test_empty_events_file_prints_no_data_message(self) -> None:
        # Events file absent entirely.
        code, out, _err = self._run("hints", "stats")
        self.assertEqual(code, 0)
        self.assertIn("no remediation outcomes", out)

    def test_aggregates_per_rule_and_sorts_by_total_desc(self) -> None:
        # rule-a: 3 fixed, 1 same_failure_again (total 4)
        # rule-b: 2 fixed (total 2)
        # rule-c: 1 different_failure (total 1)
        for _ in range(3):
            self._emit_outcome("rule-a", "fixed")
        self._emit_outcome("rule-a", "same_failure_again")
        for _ in range(2):
            self._emit_outcome("rule-b", "fixed")
        self._emit_outcome("rule-c", "different_failure")

        code, out, _err = self._run("hints", "stats")
        self.assertEqual(code, 0)
        # Header present.
        self.assertIn("rule", out)
        self.assertIn("total", out)
        self.assertIn("fixed", out)
        self.assertIn("%fixed", out)
        # Sorted by total desc: rule-a (4) before rule-b (2) before rule-c (1).
        idx_a = out.index("rule-a")
        idx_b = out.index("rule-b")
        idx_c = out.index("rule-c")
        self.assertLess(idx_a, idx_b)
        self.assertLess(idx_b, idx_c)

    def test_percent_fixed_calculation(self) -> None:
        # 3 fixed / 4 total = 75% — exercise the float-format path so a
        # future switch back to integer truncation is caught.
        for _ in range(3):
            self._emit_outcome("rule-a", "fixed")
        self._emit_outcome("rule-a", "same_failure_again")
        code, out, _err = self._run("hints", "stats")
        self.assertEqual(code, 0)
        self.assertIn("75.0%", out)

    def test_ignores_non_outcome_events(self) -> None:
        # Emit a TARGET + one OUTCOME; only the OUTCOME should show up.
        self._emit_target(run_id="run-foo", rule="rule-a")
        self._emit_outcome("rule-a", "fixed")
        code, out, _err = self._run("hints", "stats")
        self.assertEqual(code, 0)
        # Total=1 (only the one OUTCOME event counted).
        # The target doesn't inflate totals.
        # Find the line containing rule-a and verify total column == 1.
        rule_line = next(
            line for line in out.splitlines() if line.startswith("rule-a")
        )
        # Line format: rule  total  fixed  %fixed  (...). Split on WS.
        parts = rule_line.split()
        self.assertEqual(parts[1], "1")  # total
        self.assertEqual(parts[2], "1")  # fixed

    def test_unknown_hints_subcommand_still_errors(self) -> None:
        # Regression: adding `stats` must not break the existing unknown-
        # subcommand guard. argparse itself rejects unknown values with
        # exit 2 when parsing the subcommand.
        code, _out, _err = self._run("hints", "bogus")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
