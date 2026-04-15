"""Tests for the ``dark-factory remediate`` CLI subcommand (C2, Sprint 5).

Covers the dispatch machinery end-to-end through
``darkfactory.cli.main`` so the argparse wiring, the events.jsonl
resolution helpers, and the per-failure-class branches are all
exercised together.

Boundaries chosen deliberately:

* We write REMEDIATION_TARGET events directly via
  :func:`emit_remediation_target` rather than driving a failing
  pipeline — we're testing the CLI handler, not the emitters.
* ``input()`` is monkey-patched when the handler needs confirmation;
  ``sys.stdin.isatty`` is patched at module-level in the CLI so the
  novel-rule guard's TTY check is deterministic.
* ``--rerun`` exercises a stub ``run_pipeline`` patched into
  ``darkfactory.pipeline.orchestrator`` — a live run would require
  Docker.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from darkfactory.cli import main
from darkfactory.events.remediation import emit_remediation_target
from tests.helpers import TempDirTestCase


class RemediateCLITestCase(TempDirTestCase):
    """Base: ``tmp_dir`` is a project root; ``.dark-factory/`` may be empty."""

    def run_cli(
        self,
        *args: str,
        stdin_responses: list[str] | None = None,
        isatty: bool = False,
    ) -> tuple[int, str, str]:
        """Invoke ``main()`` and capture ``(exit_code, stdout, stderr)``.

        ``stdin_responses`` feeds successive ``input()`` calls when the
        handler prompts; any extra prompt raises ``EOFError`` which
        matches the handler's "no response" branch.

        ``isatty`` patches ``sys.stdin.isatty`` globally for the call —
        the novel-rule guard needs this to route between the interactive
        path and the "stdin is not a TTY" error.
        """
        full = ["--project-root", str(self.tmp_dir), "remediate", *args]

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

    def emit_consistency_target(
        self,
        run_id: str = "run-1",
        spec: str = "demo-spec",
        rule: str = "migrate-vs-push",
        suggested_diff: str = "",
        auto_applicable: bool = False,
        rationale: str = "Spec says migrate, env emits push.",
    ) -> None:
        """Emit a single consistency_violation REMEDIATION_TARGET."""
        emit_remediation_target(
            self.events_file,
            spec=spec,
            run_id=run_id,
            failure_class="consistency_violation",
            rule_or_check=rule,
            remediation_type="add_project_hint",
            remediation_target_path=".dark-factory/project-hints.toml",
            auto_applicable=auto_applicable,
            suggested_diff=suggested_diff,
            rationale=rationale,
        )

    @staticmethod
    def full_suggested_diff(
        name: str = "migrate-vs-push",
        env_paths: tuple[str, ...] = ("commands.setup",),
        spec_patterns: tuple[str, ...] = (r"db:migrate",),
        forbidden_env: tuple[str, ...] = (r"drizzle-kit\s+push",),
        description: str = "migrate discipline",
    ) -> str:
        """Render a COMPLETE rule TOML that will round-trip through the writer.

        Regex patterns are emitted as TOML **literal** strings
        (single-quoted) — basic strings would interpret ``\\s`` as an
        invalid escape, and we want the payload to round-trip through
        ``tomllib`` and the writer's re-validation without surprises.
        """
        parts = [
            "[[policy.consistency_rules]]",
            f'name = "{name}"',
            f'description = "{description}"',
            "spec_patterns = [" + ", ".join(f"'{p}'" for p in spec_patterns) + "]",
            "forbidden_env = [" + ", ".join(f"'{p}'" for p in forbidden_env) + "]",
            "env_paths = [" + ", ".join(f"'{p}'" for p in env_paths) + "]",
        ]
        return "\n".join(parts) + "\n"


# ---------- arg validation --------------------------------------------------


class TestRemediateArgValidation(RemediateCLITestCase):
    def test_no_args_errors(self) -> None:
        code, _out, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("provide a run_id", err)

    def test_run_id_and_latest_are_mutex(self) -> None:
        code, _out, err = self.run_cli("run-1", "--latest")
        self.assertEqual(code, 2)
        self.assertIn("not both", err)

    def test_nonexistent_run_id_exits_1(self) -> None:
        self.emit_consistency_target(run_id="run-A")
        code, _out, err = self.run_cli("run-MISSING")
        self.assertEqual(code, 1)
        self.assertIn("no REMEDIATION_TARGET event", err)
        self.assertIn("run-MISSING", err)

    def test_latest_with_empty_events_file_exits_1(self) -> None:
        # No events.jsonl at all → graceful error, not a crash.
        code, _out, err = self.run_cli("--latest")
        self.assertEqual(code, 1)
        self.assertIn("nothing to remediate", err)

    def test_dry_run_and_rerun_are_mutex(self) -> None:
        # Added per Linus review: silent --rerun no-op after --dry-run
        # is too surprising; reject at the arg layer.
        self.emit_consistency_target(run_id="r")
        code, _out, err = self.run_cli("r", "--dry-run", "--rerun")
        self.assertEqual(code, 2)
        self.assertIn("cannot be combined", err)

    def test_unreadable_events_file_surfaces_distinct_error(self) -> None:
        # Per Linus review: "can't read events.jsonl" must NOT be
        # collapsed into "no targets for this run_id" — make the
        # finders raise and the CLI print a distinct message.
        #
        # Simulate OSError by making events.jsonl a directory (which is
        # cross-platform and doesn't require permission manipulation
        # that might no-op under root-in-Docker).
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self.events_file.mkdir()
        code, _out, err = self.run_cli("r")
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)
        # And not the "no targets" message — that's the whole point.
        self.assertNotIn("no REMEDIATION_TARGET event for run_id", err)


# ---------- --latest resolution --------------------------------------------


class TestRemediateLatest(RemediateCLITestCase):
    def test_latest_picks_most_recent_target(self) -> None:
        # Two different targets; --latest should pick the second.
        self.emit_consistency_target(run_id="old-run", rule="old-rule")
        self.emit_consistency_target(run_id="new-run", rule="new-rule")

        code, out, _err = self.run_cli("--latest")
        self.assertEqual(code, 0)
        # The shape-only branch fires (no suggested_diff); the summary
        # header names the latest run_id.
        self.assertIn("new-run", out)
        self.assertIn("new-rule", out)
        self.assertNotIn("old-rule", out)

    def test_run_id_with_multiple_targets_picks_newest_and_lists(self) -> None:
        # Same run_id emits two rules; handler processes [0] (most recent)
        # and surfaces the full list so the user knows more exist.
        self.emit_consistency_target(run_id="r", rule="rule-A")
        self.emit_consistency_target(run_id="r", rule="rule-B")

        code, out, _err = self.run_cli("r")
        self.assertEqual(code, 0)
        self.assertIn("2 REMEDIATION_TARGET events", out)
        # Both names appear in the listing, but the processed one is B.
        self.assertIn("rule-A", out)
        self.assertIn("rule-B", out)
        # Selected marker lands on [0] (which is rule-B, most-recent-first).
        self.assertIn("rule-B", out)
        self.assertIn("<-- selected", out)

    def test_target_index_picks_nth_target(self) -> None:
        # Per Linus review: --target-index lets the user work through
        # a multi-target run. Emits [A, B]; most-recent-first → [0]=B, [1]=A.
        self.emit_consistency_target(run_id="r", rule="rule-A")
        self.emit_consistency_target(run_id="r", rule="rule-B")
        code, out, _err = self.run_cli("r", "--target-index", "1")
        self.assertEqual(code, 0)
        # Summary line names the *processed* rule; [1] is rule-A.
        self.assertIn("rule_or_check:   rule-A", out)

    def test_target_index_out_of_range_exits_2(self) -> None:
        self.emit_consistency_target(run_id="r", rule="only-one")
        code, _out, err = self.run_cli("r", "--target-index", "5")
        self.assertEqual(code, 2)
        self.assertIn("out of range", err)


# ---------- manual/guided failure classes ----------------------------------


class TestRemediateManualGuidance(RemediateCLITestCase):
    def _emit(self, failure_class: str, **extra) -> None:
        emit_remediation_target(
            self.events_file,
            spec="my-spec",
            run_id="r",
            failure_class=failure_class,  # type: ignore[arg-type]
            rule_or_check="somecheck",
            remediation_type=extra.get("remediation_type", "manual"),
            remediation_target_path=extra.get("path", "src/foo.ts"),
            auto_applicable=False,
            rationale="because reasons",
            suggested_diff=extra.get("suggested_diff", ""),
        )

    def test_spec_quality_routes_to_df_write_feedback(self) -> None:
        self._emit("spec_quality", remediation_type="edit_spec",
                   path=".dark-factory/specs/my-spec.md")
        code, out, _err = self.run_cli("r")
        self.assertEqual(code, 0)
        self.assertIn("MANUAL / GUIDED", out)
        self.assertIn("/df-write-feedback my-spec", out)
        # No file writes — hints.toml must still be absent.
        self.assertFalse(self.hints_path.exists())

    def test_precheck_finding_routes_to_manual_rerun(self) -> None:
        self._emit("precheck_finding")
        code, out, _err = self.run_cli("r")
        self.assertEqual(code, 0)
        self.assertIn("dark-factory run my-spec", out)
        self.assertFalse(self.hints_path.exists())

    def test_stub_compile_error_routes_to_fix_at_path(self) -> None:
        self._emit("stub_compile_error", path="src/stub.ts")
        code, out, _err = self.run_cli("r")
        self.assertEqual(code, 0)
        self.assertIn("src/stub.ts", out)
        self.assertFalse(self.hints_path.exists())


# ---------- consistency_violation: shape-only payload (today's state) ------


class TestRemediateConsistencyShapeOnly(RemediateCLITestCase):
    def test_shape_only_suggested_diff_routes_to_hints_skill(self) -> None:
        # The orchestrator's current _consistency_suggested_diff emits
        # name + description only — no env_paths. The handler detects
        # this and routes to /df-write-hints from-failure.
        shape_only = (
            "[[policy.consistency_rules]]\n"
            'name = "migrate-vs-push"\n'
            'description = "..."\n'
        )
        self.emit_consistency_target(
            run_id="r", suggested_diff=shape_only, auto_applicable=False,
        )
        code, out, _err = self.run_cli("r")
        self.assertEqual(code, 0)
        self.assertIn("SHAPE-ONLY", out)
        self.assertIn("/df-write-hints from-failure r", out)
        self.assertFalse(self.hints_path.exists())

    def test_empty_suggested_diff_also_routes_to_hints_skill(self) -> None:
        # No suggested_diff at all is the minimum case — also shape-only.
        self.emit_consistency_target(run_id="r")
        code, out, _err = self.run_cli("r")
        self.assertEqual(code, 0)
        self.assertIn("SHAPE-ONLY", out)
        self.assertFalse(self.hints_path.exists())

    def test_malformed_suggested_diff_exits_1_with_emitter_bug_hint(self) -> None:
        # Per Linus review: a malformed diff is an emitter bug, not a
        # shape-only payload. The user needs to see that distinction so
        # they don't waste time in the shape-only walkthrough when the
        # real fix is upstream.
        self.emit_consistency_target(
            run_id="r",
            suggested_diff="[[policy.consistency_rules]]\nname = unterminated",
        )
        code, _out, err = self.run_cli("r")
        self.assertEqual(code, 1)
        self.assertIn("malformed", err)
        self.assertIn("emitter bug", err)
        # And specifically NOT the shape-only message.
        self.assertNotIn("SHAPE-ONLY", err)


# ---------- consistency_violation: complete payload (future state) ---------


class TestRemediateConsistencyFullPayload(RemediateCLITestCase):
    def test_dry_run_prints_proposal_and_does_not_write(self) -> None:
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        code, out, _err = self.run_cli("r", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("--dry-run:", out)
        self.assertIn("without side effects", out)
        self.assertFalse(self.hints_path.exists())

    def test_apply_non_novel_writes_rule(self) -> None:
        # Two emissions so the second is non-novel. Apply the second.
        self.emit_consistency_target(run_id="r1")           # novel (no diff)
        self.emit_consistency_target(                       # non-novel
            run_id="r2",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        code, out, _err = self.run_cli("r2", "--apply")
        self.assertEqual(code, 0, msg=f"stderr={_err!r}")
        self.assertIn("wrote rule", out)
        self.assertTrue(self.hints_path.exists())
        content = self.hints_path.read_text(encoding="utf-8")
        self.assertIn("migrate-vs-push", content)
        # Complete round-trip: env_paths carried through.
        self.assertIn("commands.setup", content)

    def test_apply_requires_auto_applicable(self) -> None:
        # Pre-seed history so the rule is NOT novel (so the refusal we
        # see is the auto_applicable gate, not the novelty gate).
        self.emit_consistency_target(run_id="r0")
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=False,
        )
        code, out, _err = self.run_cli("r", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("auto_applicable=false", out)
        self.assertFalse(self.hints_path.exists())


# ---------- novel-rule guard (C5 forward-port) -----------------------------


class TestRemediateNovelRuleGuard(RemediateCLITestCase):
    def test_novel_rule_forces_interactive_even_with_apply(self) -> None:
        # First emission → novel. --apply alone is NOT enough; the
        # handler must name NOVELTY specifically (not the generic
        # "confirmation required" — the user passed --apply, so they
        # deserve to know WHY --apply wasn't honored).
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        code, _out, err = self.run_cli("r", "--apply", isatty=False)
        self.assertEqual(code, 1)
        self.assertIn("NOVEL RULE", err)
        self.assertIn("migrate-vs-push", err)
        self.assertFalse(self.hints_path.exists())

    def test_non_novel_non_apply_on_non_tty_errors_generically(self) -> None:
        # The other branch of the split error: non-novel, no --apply,
        # no TTY → generic "confirmation required" (NOT the novelty
        # message). Prove the split is correct.
        self.emit_consistency_target(run_id="r0")  # primes novelty
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        code, _out, err = self.run_cli("r", isatty=False)
        self.assertEqual(code, 1)
        self.assertIn("confirmation required", err)
        self.assertNotIn("NOVEL RULE", err)

    def test_novel_rule_interactive_y_applies(self) -> None:
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        code, out, _err = self.run_cli(
            "r", "--apply",
            isatty=True,
            stdin_responses=["y"],
        )
        self.assertEqual(code, 0)
        self.assertIn("NOVEL RULE", out)
        self.assertIn("wrote rule", out)
        self.assertTrue(self.hints_path.exists())

    def test_novel_rule_interactive_n_cancels(self) -> None:
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        code, out, _err = self.run_cli(
            "r", "--apply",
            isatty=True,
            stdin_responses=["n"],
        )
        self.assertEqual(code, 0)
        self.assertIn("cancelled", out)
        self.assertFalse(self.hints_path.exists())

    def test_no_apply_flag_also_prompts_for_non_novel(self) -> None:
        # Without --apply, the user must confirm regardless of novelty.
        self.emit_consistency_target(run_id="r0")  # primes novelty
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        code, out, _err = self.run_cli(
            "r",
            isatty=True,
            stdin_responses=["y"],
        )
        self.assertEqual(code, 0)
        # Non-novel: no NOVEL RULE banner.
        self.assertNotIn("NOVEL RULE", out)
        self.assertIn("wrote rule", out)


# ---------- unknown failure_class (forward compatibility) ------------------


class TestRemediateUnknownFailureClass(RemediateCLITestCase):
    def test_unknown_class_surfaces_schema_hint(self) -> None:
        # Hand-write a record with a forward-incompatible failure_class —
        # we bypass emit_remediation_target because it's Literal-typed.
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        import json
        rec = {
            "ts": "2026-04-14T00:00:00Z",
            "event": "REMEDIATION_TARGET",
            "spec": "s",
            "run_id": "r",
            "data": {
                "schema_version": 999,
                "failure_class": "some_future_class",
                "rule_or_check": "future",
                "remediation_type": "manual",
                "remediation_target_path": "",
                "auto_applicable": False,
                "suggested_diff": "",
                "rationale": "future",
                "next_command": "",
                "novel_rule_name": True,
            },
        }
        self.events_file.write_text(json.dumps(rec) + "\n", encoding="utf-8")

        code, _out, err = self.run_cli("r")
        self.assertEqual(code, 1)
        self.assertIn("unrecognized failure_class", err)
        self.assertIn("SCHEMA_VERSION", err)


# ---------- --rerun chaining -----------------------------------------------


class TestRemediateRerun(RemediateCLITestCase):
    def test_rerun_invokes_run_pipeline_after_apply(self) -> None:
        # Pre-seed history so the rule is non-novel, then apply + rerun.
        self.emit_consistency_target(run_id="r0")
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        # Seed a config file so _load_config succeeds.
        (self.tmp_dir / ".dark-factory").mkdir(exist_ok=True)
        (self.tmp_dir / ".dark-factory" / "config.toml").write_text(
            "# minimal\n", encoding="utf-8",
        )

        with mock.patch(
            "darkfactory.pipeline.orchestrator.run_pipeline",
            return_value=0,
        ) as mock_run:
            code, out, _err = self.run_cli("r", "--apply", "--rerun")

        self.assertEqual(code, 0)
        self.assertIn("wrote rule", out)
        self.assertIn("re-running pipeline", out)
        mock_run.assert_called_once()
        # run_pipeline(spec, config, project, ...); check the positional spec arg
        args_passed, kwargs_passed = mock_run.call_args
        self.assertEqual(args_passed[0], "demo-spec")

    def test_rerun_surfaces_pipeline_exit_code(self) -> None:
        self.emit_consistency_target(run_id="r0")
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        (self.tmp_dir / ".dark-factory").mkdir(exist_ok=True)
        (self.tmp_dir / ".dark-factory" / "config.toml").write_text(
            "# minimal\n", encoding="utf-8",
        )

        with mock.patch(
            "darkfactory.pipeline.orchestrator.run_pipeline",
            return_value=7,
        ):
            code, _out, _err = self.run_cli("r", "--apply", "--rerun")
        self.assertEqual(code, 7)


# ---------- legacy-unmigrated refusal propagates ----------------------------


class TestRemediateLegacyRefusal(RemediateCLITestCase):
    def test_apply_fails_if_legacy_hints_block_write(self) -> None:
        # Writer refuses if legacy hints.json is present without project-hints.toml.
        arch_dir = self.tmp_dir / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True)
        (arch_dir / "hints.json").write_text("{}", encoding="utf-8")

        # Non-novel so we go straight to apply.
        self.emit_consistency_target(run_id="r0")
        self.emit_consistency_target(
            run_id="r",
            suggested_diff=self.full_suggested_diff(),
            auto_applicable=True,
        )
        code, _out, err = self.run_cli("r", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("legacy", err.lower())
        # Writer refused → no project-hints.toml was created.
        self.assertFalse(self.hints_path.exists())


if __name__ == "__main__":
    unittest.main()
