"""Tests for the CLI top-level DarkFactoryError "To fix:" hint (C3).

When a pipeline emit-then-raise site raises a ``DarkFactoryError`` with
an attached ``run_id``, the CLI's top-level catch looks up the matching
REMEDIATION_TARGET in ``.dark-factory/events.jsonl`` and prints a
next-command hint to stderr.

These tests cover:

* The hint renders with the target's ``next_command`` when one is found.
* The hint is silently skipped when the error has no ``run_id``.
* The hint is silently skipped when the events file is missing, has no
  matching target, or is unreadable — telemetry plumbing must never mask
  the original error the user came to see.
* The error's exit status (1) is unaffected by hint rendering.

The ``_print_next_command_hint`` helper is exercised directly: it is the
unit of logic that can be meaningfully isolated. Going through ``main``
would require standing up a failing pipeline for every case, which is
what the orchestrator-level tests already do — duplicating that here
would test the same emit path twice without improving coverage of the
handler branch.
"""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from darkfactory.cli import _print_next_command_hint, main
from darkfactory.errors import ConfigError, DarkFactoryError
from darkfactory.events.remediation import emit_remediation_target
from tests.helpers import TempDirTestCase


class _HintHelperTestCase(TempDirTestCase):
    """Build an ``argparse.Namespace`` pointing at ``tmp_dir`` and capture
    stderr around calls to ``_print_next_command_hint``."""

    def _make_args(self) -> argparse.Namespace:
        # Mirror the subset of top-level flags the hint helper reads.
        return argparse.Namespace(project_root=self.tmp_dir)

    def _call_hint(self, run_id: str) -> str:
        err = io.StringIO()
        with redirect_stderr(err):
            _print_next_command_hint(self._make_args(), run_id)
        return err.getvalue()

    @property
    def events_file(self) -> Path:
        return self.tmp_dir / ".dark-factory" / "events.jsonl"


# ---------- DarkFactoryError constructor --------------------------------


class TestDarkFactoryErrorRunId(unittest.TestCase):
    """The base class must carry an optional ``run_id`` without breaking
    any of the positional-arg construction patterns already in the tree."""

    def test_no_run_id_defaults_to_empty_string(self) -> None:
        e = DarkFactoryError("boom")
        self.assertEqual(str(e), "boom")
        self.assertEqual(e.run_id, "")

    def test_run_id_is_keyword_only(self) -> None:
        e = DarkFactoryError("boom", run_id="run-abc")
        self.assertEqual(e.run_id, "run-abc")

    def test_subclass_inherits_run_id(self) -> None:
        # ConfigError has no site that sets run_id today, but inheriting
        # the attribute keeps the CLI handler uniform — no isinstance
        # ladder needed to decide whether to call getattr.
        e = ConfigError("bad")
        self.assertEqual(e.run_id, "")


# ---------- happy path: hint renders ------------------------------------


class TestHintRendersWhenTargetExists(_HintHelperTestCase):
    def _emit_default_target(
        self,
        run_id: str = "run-42",
        rule: str = "migrate-vs-push",
        next_command: str = "",
    ) -> None:
        emit_remediation_target(
            self.events_file,
            spec="demo-spec",
            run_id=run_id,
            failure_class="consistency_violation",
            rule_or_check=rule,
            remediation_type="add_project_hint",
            remediation_target_path=".dark-factory/project-hints.toml",
            auto_applicable=False,
            suggested_diff="",
            rationale="Spec says migrate, env emits push.",
            next_command=next_command,
        )

    def test_prints_default_next_command_when_target_has_none(self) -> None:
        self._emit_default_target(run_id="run-42")
        err = self._call_hint("run-42")
        self.assertIn("To fix: dark-factory remediate run-42", err)
        self.assertIn(
            "/df-write-hints from-failure run-42",
            err,
        )
        self.assertIn("(in Claude Code)", err)

    def test_uses_custom_next_command_from_event(self) -> None:
        self._emit_default_target(
            run_id="run-42",
            next_command="dark-factory hints add migrate-vs-push",
        )
        err = self._call_hint("run-42")
        # Per-target override wins over the default remediate command.
        self.assertIn("To fix: dark-factory hints add migrate-vs-push", err)
        # Claude Code fallback still uses run_id.
        self.assertIn("/df-write-hints from-failure run-42", err)

    def test_picks_most_recent_target_when_run_emitted_many(self) -> None:
        self._emit_default_target(run_id="run-42", rule="rule-one")
        self._emit_default_target(
            run_id="run-42",
            rule="rule-two",
            next_command="dark-factory hints add rule-two",
        )
        err = self._call_hint("run-42")
        # find_targets_by_run_id returns most-recent-first; index 0 wins.
        self.assertIn("dark-factory hints add rule-two", err)
        self.assertNotIn("rule-one", err)


# ---------- silent skips ------------------------------------------------


class TestHintSkippedWhenNoActionableTarget(_HintHelperTestCase):
    def test_empty_run_id_prints_nothing(self) -> None:
        # The top-level catch passes getattr(e, "run_id", "") — base
        # DarkFactoryError raises without a run_id must not print a hint.
        err = self._call_hint("")
        self.assertEqual(err, "")

    def test_missing_events_file_prints_nothing(self) -> None:
        self.assertFalse(self.events_file.exists())
        err = self._call_hint("run-42")
        self.assertEqual(err, "")

    def test_no_matching_target_prints_nothing(self) -> None:
        # Events file exists, but this run_id didn't emit any target —
        # either emission failed and was swallowed by the wrapper, or the
        # user's in a fresh session. Either way, no noise.
        emit_remediation_target(
            self.events_file,
            spec="demo-spec",
            run_id="other-run",
            failure_class="consistency_violation",
            rule_or_check="some-rule",
            remediation_type="add_project_hint",
            remediation_target_path=".dark-factory/project-hints.toml",
            auto_applicable=False,
            suggested_diff="",
            rationale="unrelated",
        )
        err = self._call_hint("run-42")
        self.assertEqual(err, "")

    def test_missing_project_root_attr_prints_nothing(self) -> None:
        # Belt-and-braces: the top-level parser always adds project_root,
        # but the helper uses getattr with a "." default to avoid a hard
        # crash if called from a future code path that hasn't parsed args
        # through the top-level parser (e.g., a test harness or library
        # use). Regression test pins the default — if someone later
        # drops the getattr for a direct attribute access, this fails.
        err = io.StringIO()
        with redirect_stderr(err):
            _print_next_command_hint(argparse.Namespace(), "run-42")
        self.assertEqual(err.getvalue(), "")

    def test_unreadable_events_file_prints_nothing(self) -> None:
        # If find_targets_by_run_id raises OSError (permission denied,
        # path-is-directory), the hint is suppressed — the primary error
        # message is what the user needs to see, not a plumbing trace.
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.write_text("", encoding="utf-8")
        with mock.patch(
            "darkfactory.events.remediation.find_targets_by_run_id",
            side_effect=OSError("permission denied"),
        ):
            err = self._call_hint("run-42")
        self.assertEqual(err, "")


# ---------- integration with main() catch ------------------------------


class TestMainHandlerCatchesAndExitsOne(_HintHelperTestCase):
    """End-to-end: a stubbed ``_dispatch`` raises ``DarkFactoryError``; we
    verify the catch renders the hint and exits with code 1.

    We stub dispatch rather than driving a failing pipeline because the
    hint formatter is the code under test — wiring more of the pipeline
    in would only dilute what these tests prove.
    """

    def _run_main_with_error(
        self,
        error: DarkFactoryError,
    ) -> tuple[int, str]:
        err = io.StringIO()
        out = io.StringIO()
        code = 0
        with (
            redirect_stdout(out),
            redirect_stderr(err),
            mock.patch("darkfactory.cli._dispatch", side_effect=error),
        ):
            try:
                main(["--project-root", str(self.tmp_dir), "dashboard"])
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, err.getvalue()

    def test_error_without_run_id_exits_1_and_no_hint(self) -> None:
        # The error message itself is emitted via log.error (existing
        # behavior not owned by C3); we only assert the new hint branch
        # here — exit code unchanged, no hint printed.
        code, err = self._run_main_with_error(DarkFactoryError("bare"))
        self.assertEqual(code, 1)
        self.assertNotIn("To fix:", err)

    def test_error_with_run_id_and_matching_target_prints_hint(self) -> None:
        emit_remediation_target(
            self.events_file,
            spec="demo-spec",
            run_id="run-xyz",
            failure_class="consistency_violation",
            rule_or_check="rule-a",
            remediation_type="add_project_hint",
            remediation_target_path=".dark-factory/project-hints.toml",
            auto_applicable=False,
            suggested_diff="",
            rationale="—",
        )
        code, err = self._run_main_with_error(
            DarkFactoryError("pipeline failure", run_id="run-xyz"),
        )
        self.assertEqual(code, 1)
        self.assertIn("To fix: dark-factory remediate run-xyz", err)

    def test_error_with_run_id_but_no_events_file_exits_cleanly(self) -> None:
        # events file absent — user still gets the error but no hint noise.
        self.assertFalse(self.events_file.exists())
        code, err = self._run_main_with_error(
            DarkFactoryError("pipeline failure", run_id="run-xyz"),
        )
        self.assertEqual(code, 1)
        self.assertNotIn("To fix:", err)


if __name__ == "__main__":
    unittest.main()
