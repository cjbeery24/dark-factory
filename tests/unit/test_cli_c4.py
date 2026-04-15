"""Tests for C4 partial re-trigger + loop guard on `remediate --rerun`.

Covers two user-facing guarantees:

1. After a successful `--apply --rerun`, if the re-run emits a new
   ``REMEDIATION_TARGET`` with the same ``rule_or_check`` we just
   applied, the loop guard aborts with exit 1 and a "didn't resolve"
   message on stderr.
2. A re-run that produces a *different* failure (different rule) or
   no remediation target at all passes through its exit code
   unchanged — the loop guard is scoped to the specific rule the
   operator just installed, not any re-run failure.

Additionally spot-checks ``_rerun_produced_same_failure`` directly for
edge cases that are hard to reach through the CLI (malformed event
lines, byte-offset scoping, missing file).

Boundaries:

* ``run_pipeline`` is patched at the module where
  ``_rerun_pipeline_after_remediate`` resolves it
  (``darkfactory.pipeline.orchestrator.run_pipeline``), not at the
  call site — the call site uses a lazy import, which means patching
  after the first import would miss the first call.
* REMEDIATION_TARGET events are written via ``emit_remediation_target``
  (same as the C5 tests) so we exercise the real JSON-line format.
* The applied rule's name ("migrate-vs-push") matches the helper's
  default, so ``append_consistency_rule`` writes a real TOML file
  before the mocked rerun starts.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from darkfactory.cli import (
    _rerun_produced_same_failure,
    main,
)
from darkfactory.errors import DarkFactoryError
from darkfactory.events.remediation import emit_remediation_target
from tests.helpers import TempDirTestCase


class _CLIBase(TempDirTestCase):
    def _run(
        self,
        *args: str,
        isatty: bool = False,
        run_pipeline_mock: mock.Mock | None = None,
    ) -> tuple[int, str, str]:
        full = ["--project-root", str(self.tmp_dir), *args]
        out, err = io.StringIO(), io.StringIO()
        code = 0

        patches: list = [
            redirect_stdout(out),
            redirect_stderr(err),
            mock.patch.object(sys.stdin, "isatty", return_value=isatty),
        ]
        if run_pipeline_mock is not None:
            patches.append(
                mock.patch(
                    "darkfactory.pipeline.orchestrator.run_pipeline",
                    run_pipeline_mock,
                )
            )

        # Nest context managers manually so the list is variable-length.
        with _nested(patches):
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
            suggested_diff=self._rule_toml(rule),
            rationale="Spec says migrate, env emits push.",
        )


def _nested(cms: list) -> "mock._patch":
    """Minimal variable-length context-manager combinator."""

    class _Nested:
        def __enter__(self):
            for cm in cms:
                cm.__enter__()
            return self

        def __exit__(self, *exc):
            result = False
            for cm in reversed(cms):
                if cm.__exit__(*exc):
                    result = True
            return result

    return _Nested()


# ---------- loop guard through the CLI -----------------------------------


class TestLoopGuard(_CLIBase):
    def test_rerun_success_returns_zero_and_no_guard_fires(self) -> None:
        self._emit_target(run_id="run-1")
        rp = mock.Mock(return_value=0)
        code, _out, err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", "--rerun",
            isatty=False, run_pipeline_mock=rp,
        )
        self.assertEqual(code, 0)
        self.assertNotIn("didn't resolve", err)
        rp.assert_called_once()

    def test_rerun_same_rule_again_triggers_loop_guard(self) -> None:
        self._emit_target(run_id="run-1")

        def fake_run_pipeline(*_a, **_kw) -> int:
            # Simulate the pipeline firing the same consistency rule
            # again in a fresh run_id — this is the infinite-loop shape
            # plan §C4 is defending against.
            emit_remediation_target(
                self.events_file,
                spec="demo-spec",
                run_id="run-2",
                failure_class="consistency_violation",
                rule_or_check="migrate-vs-push",
                remediation_type="add_project_hint",
                remediation_target_path=".dark-factory/project-hints.toml",
                auto_applicable=True,
                suggested_diff=self._rule_toml(),
                rationale="Same violation fired again after remediation.",
            )
            return 1

        code, _out, err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", "--rerun",
            isatty=False,
            run_pipeline_mock=mock.Mock(side_effect=fake_run_pipeline),
        )
        self.assertEqual(code, 1)
        self.assertIn("didn't resolve", err)
        self.assertIn("migrate-vs-push", err)
        self.assertIn("Manual intervention", err)

    def test_rerun_different_rule_passes_exit_code_through(self) -> None:
        # A different rule firing on rerun is NOT a loop — it's a
        # different failure the first remediation didn't address but
        # didn't mask. The exit code passes through unchanged; the
        # operator handles the new target on their own.
        self._emit_target(run_id="run-1")

        def fake_run_pipeline(*_a, **_kw) -> int:
            emit_remediation_target(
                self.events_file,
                spec="demo-spec",
                run_id="run-2",
                failure_class="consistency_violation",
                rule_or_check="OTHER-RULE",
                remediation_type="add_project_hint",
                remediation_target_path=".dark-factory/project-hints.toml",
                auto_applicable=True,
                suggested_diff=self._rule_toml("OTHER-RULE"),
                rationale="Different contradiction.",
            )
            return 2  # not-shipped / escalated exit code

        code, _out, err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", "--rerun",
            isatty=False,
            run_pipeline_mock=mock.Mock(side_effect=fake_run_pipeline),
        )
        self.assertEqual(code, 2)
        self.assertNotIn("didn't resolve", err)

    def test_rerun_failure_without_new_target_passes_through(self) -> None:
        # Rerun fails (e.g., infra error) but emits nothing. The loop
        # guard is a rule-specific brake, not a generic "rerun-failed"
        # catch-all; pass through the original exit code.
        self._emit_target(run_id="run-1")
        rp = mock.Mock(return_value=3)
        code, _out, err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", "--rerun",
            isatty=False, run_pipeline_mock=rp,
        )
        self.assertEqual(code, 3)
        self.assertNotIn("didn't resolve", err)

    def test_preexisting_same_rule_target_does_not_trigger_guard(self) -> None:
        # The original failure itself is a REMEDIATION_TARGET for the
        # same rule. The guard must baseline on events-file size at
        # rerun-start and not consider pre-existing targets.
        self._emit_target(run_id="run-1")
        # Seed another target from a prior unrelated run — still the
        # same rule, still pre-existing — to really stress the baseline.
        self._emit_target(run_id="run-older")
        rp = mock.Mock(return_value=0)
        code, _out, err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", "--rerun",
            isatty=False, run_pipeline_mock=rp,
        )
        self.assertEqual(code, 0)
        self.assertNotIn("didn't resolve", err)

    def test_rerun_raises_same_rule_triggers_loop_guard(self) -> None:
        # The real A1 path raises DarkFactoryError; `run_pipeline` does
        # NOT catch it (only HumanHoldRequired/HardAbort/BudgetExceeded
        # are caught at orchestrator.py:702). Without the try/except on
        # the caller side, the loop guard is dead code on the exact
        # scenario plan §C4 targets. This test drives the raise path.
        self._emit_target(run_id="run-1")

        def fake_run_pipeline(*_a, **_kw) -> int:
            # Emit the same-rule target before raising, mirroring the
            # orchestrator's emit-then-raise pattern (orchestrator.py
            # lines ~1675, ~477, etc.).
            emit_remediation_target(
                self.events_file,
                spec="demo-spec",
                run_id="run-1",  # matches orchestrator's
                                 # _handle_resume run_id reuse
                failure_class="consistency_violation",
                rule_or_check="migrate-vs-push",
                remediation_type="add_project_hint",
                remediation_target_path=".dark-factory/project-hints.toml",
                auto_applicable=True,
                suggested_diff=self._rule_toml(),
                rationale="Same rule fired again; raise path.",
            )
            raise DarkFactoryError(
                "Spec↔Env contradiction [migrate-vs-push]: ...",
                run_id="run-1",
            )

        code, _out, err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", "--rerun",
            isatty=False,
            run_pipeline_mock=mock.Mock(side_effect=fake_run_pipeline),
        )
        self.assertEqual(code, 1)
        self.assertIn("didn't resolve", err)
        self.assertIn("migrate-vs-push", err)

    def test_rerun_raises_different_rule_reraises_to_main_handler(
        self,
    ) -> None:
        # A DarkFactoryError on rerun that is NOT the same rule must
        # re-raise so main()'s top-level handler prints the C3 "To fix:"
        # hint. The loop guard should not swallow unrelated errors.
        self._emit_target(run_id="run-1")

        def fake_run_pipeline(*_a, **_kw) -> int:
            emit_remediation_target(
                self.events_file,
                spec="demo-spec",
                run_id="run-1",
                failure_class="consistency_violation",
                rule_or_check="OTHER-RULE",
                remediation_type="add_project_hint",
                remediation_target_path=".dark-factory/project-hints.toml",
                auto_applicable=True,
                suggested_diff=self._rule_toml("OTHER-RULE"),
                rationale="Different contradiction.",
            )
            raise DarkFactoryError(
                "Spec↔Env contradiction [OTHER-RULE]: ...",
                run_id="run-1",
            )

        code, _out, err = self._run(
            "remediate", "run-1", "--apply", "--accept-novel", "--rerun",
            isatty=False,
            run_pipeline_mock=mock.Mock(side_effect=fake_run_pipeline),
        )
        # main()'s top-level DarkFactoryError handler exits 1.
        self.assertEqual(code, 1)
        # Loop-guard message must NOT appear — this was a different rule.
        self.assertNotIn("didn't resolve", err)

    def test_rerun_without_apply_does_not_invoke_pipeline(self) -> None:
        # --rerun without --apply is the "dry inspect then rerun" shape
        # users sometimes try. Without --apply the non-TTY branch errors
        # out before reaching the write, so run_pipeline is never
        # called. Guard test against a regression where the rerun path
        # gets reached with nothing written.
        self._emit_target(run_id="run-1")
        rp = mock.Mock()
        code, _out, err = self._run(
            "remediate", "run-1", "--rerun",
            isatty=False, run_pipeline_mock=rp,
        )
        # No --apply on non-TTY: confirmation-required exit 1.
        self.assertEqual(code, 1)
        self.assertIn("confirmation required", err)
        rp.assert_not_called()


# ---------- _rerun_produced_same_failure direct tests --------------------


class TestRerunProducedSameFailureHelper(unittest.TestCase):
    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_missing_file_returns_false(self) -> None:
        self.assertFalse(
            _rerun_produced_same_failure(
                Path("/definitely/does/not/exist.jsonl"),
                "some-rule",
                0,
            )
        )

    def test_baseline_offset_excludes_older_events(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            # Older REMEDIATION_TARGET for rule-x.
            old = (
                '{"event": "REMEDIATION_TARGET", "run_id": "old", '
                '"data": {"rule_or_check": "rule-x"}}\n'
            )
            self._write(p, old)
            old_size = p.stat().st_size
            # A newer unrelated event (different rule).
            with p.open("a", encoding="utf-8") as fh:
                fh.write(
                    '{"event": "REMEDIATION_TARGET", "run_id": "new", '
                    '"data": {"rule_or_check": "rule-y"}}\n'
                )
            self.assertFalse(
                _rerun_produced_same_failure(p, "rule-x", old_size),
            )

    def test_baseline_offset_catches_newer_matching_event(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            self._write(
                p,
                '{"event": "REMEDIATION_TARGET", "run_id": "old", '
                '"data": {"rule_or_check": "rule-x"}}\n',
            )
            baseline = p.stat().st_size
            with p.open("a", encoding="utf-8") as fh:
                fh.write(
                    '{"event": "REMEDIATION_TARGET", "run_id": "new", '
                    '"data": {"rule_or_check": "rule-x"}}\n'
                )
            self.assertTrue(
                _rerun_produced_same_failure(p, "rule-x", baseline),
            )

    def test_malformed_lines_are_skipped(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            self._write(p, "")
            with p.open("a", encoding="utf-8") as fh:
                fh.write("not-json\n")
                fh.write("\n")  # blank
                fh.write(
                    '{"event": "REMEDIATION_TARGET", "run_id": "new", '
                    '"data": {"rule_or_check": "rule-x"}}\n'
                )
            self.assertTrue(
                _rerun_produced_same_failure(p, "rule-x", 0),
            )

    def test_non_target_events_ignored(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            self._write(
                p,
                '{"event": "REMEDIATION_OUTCOME", "run_id": "new", '
                '"data": {"rule_or_check": "rule-x"}}\n',
            )
            # Outcome, not TARGET — must not trigger the guard.
            self.assertFalse(
                _rerun_produced_same_failure(p, "rule-x", 0),
            )


if __name__ == "__main__":
    unittest.main()
