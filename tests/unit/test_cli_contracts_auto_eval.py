"""Tests for the ``dark-factory contracts auto-eval`` CLI subcommand
(ROADMAP.md Phase 3 B9b — third commit, 3c sub-commit).

Drives ``main()`` end-to-end so the argparse wiring is exercised.
Mocks the runner's claude subprocess (via the ``runner`` DI parameter
on ``run_auto_eval``) so no real LLM call happens.

Pins:

1. argparse wiring: ``contracts auto-eval`` parses with positional
   helper, optional ``--model`` (default ``claude-opus-4-7``),
   ``--rounds`` (default 5), ``--out`` (default None), ``--timeout``
   (default 120).
2. Stdout shape: when ``--out`` is unset, ``json.dumps`` of the
   ``EvalResult.asdict`` lands on stdout (sorted keys, indent=2).
3. ``--out PATH`` shape: the file is written, and a one-line summary
   ``Wrote eval-result to <path>`` lands on stdout.
4. ``--out`` with a non-existent parent dir creates the parent.
5. Path B invariant — at no time is anything written to
   ``.dark-factory/audit/`` (regression test for the design decision
   neo argued for in B9b 3 review).
6. Runner errors (``ValueError``, ``RuntimeError``) surface as
   ``exit 1`` with ``auto-eval failed: <msg>`` on stderr.
7. ``contracts`` without a subcommand prints usage and exits 1.

All subprocess calls are mocked at the runner level via a context
manager that swaps ``run_auto_eval``'s default subprocess runner.
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from tests.helpers import TempDirTestCase

from darkfactory.adapters.postgres.retire_after_eval import (
    EVAL_RESULT_SCHEMA_VERSION,
    EvalResult,
)
from darkfactory.cli import main
from darkfactory.utils.subprocess_runner import RunResult


def _make_claude_run_result(body: str) -> RunResult:
    """Build a RunResult mimicking ``claude -p --output-format json``
    output with the model's reply text in the ``result`` field."""
    payload = json.dumps({
        "result": body,
        "usage": {"input_tokens": 50, "output_tokens": 25},
    })
    return RunResult(exit_code=0, stdout=payload)


_VANILLA_TS = """\
import { describe, it, expect } from 'vitest';
describe('something', () => {
  it('does the thing', async () => {
    const result = await someProductionPath();
    expect(result).toBe('ok');
  });
});
"""


class ContractsAutoEvalCLITestCase(TempDirTestCase):
    """Base: ``tmp_dir`` is a project-root with no .dark-factory yet."""

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        """Invoke ``main()`` and capture ``(exit_code, stdout, stderr)``.
        ``main()`` calls ``sys.exit`` — we catch and unwrap.
        """
        full = ["--project-root", str(self.tmp_dir), *args]
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                main(full)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()


class TestContractsAutoEvalStdoutEmit(ContractsAutoEvalCLITestCase):
    """Default ``--out`` unset → JSON to stdout."""

    def test_happy_path_emits_eval_result_json_to_stdout(self) -> None:
        # Patch the subprocess runner used inside run_auto_eval so no
        # real claude call happens. Using subprocess_runner.run as the
        # patch target catches BOTH the runner's _default_subprocess_runner
        # alias AND any future direct call.
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner._default_subprocess_runner",
            return_value=_make_claude_run_result(_VANILLA_TS),
        ):
            code, out, err = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
                "--rounds", "3",
            )
        self.assertEqual(code, 0, msg=f"stderr={err!r}")
        # Stdout is parseable JSON with the expected shape.
        result = json.loads(out)
        self.assertEqual(result["helper_name"], "assertUnsetGucIsNullish")
        self.assertEqual(result["rounds"], 3)
        self.assertEqual(result["schema_version"], EVAL_RESULT_SCHEMA_VERSION)
        self.assertIn(result["verdict"], {"retire", "keep", "inconclusive"})
        self.assertIn("per_round", result)

    def test_stdout_json_uses_sorted_keys_and_indent(self) -> None:
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner._default_subprocess_runner",
            return_value=_make_claude_run_result(_VANILLA_TS),
        ):
            code, out, _ = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
                "--rounds", "1",
            )
        self.assertEqual(code, 0)
        # sort_keys=True: top-level keys appear alphabetically. The
        # first JSON-key after the opening brace must be alphabetically
        # earliest among EvalResult fields. With current schema:
        # ``foot_gun_hits`` (f...) is the first.
        first_key_line = out.splitlines()[1]
        self.assertIn('"foot_gun_hits"', first_key_line)


class TestContractsAutoEvalOutFile(ContractsAutoEvalCLITestCase):
    """``--out PATH`` writes JSON to file + prints summary."""

    def test_out_path_writes_file_and_prints_summary(self) -> None:
        out_path = self.tmp_dir / "eval-out" / "result.json"
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner._default_subprocess_runner",
            return_value=_make_claude_run_result(_VANILLA_TS),
        ):
            code, out, err = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
                "--rounds", "1",
                "--out", str(out_path),
            )
        self.assertEqual(code, 0, msg=f"stderr={err!r}")
        self.assertTrue(out_path.exists())
        # Stdout should NOT contain the JSON payload itself — only the
        # summary line.
        self.assertNotIn('"helper_name"', out)
        self.assertIn(f"Wrote eval-result to {out_path}", out)
        # File contains the JSON.
        file_payload = json.loads(out_path.read_text())
        self.assertEqual(file_payload["helper_name"], "assertUnsetGucIsNullish")

    def test_out_path_creates_missing_parent_dir(self) -> None:
        # Three levels of missing parents — the handler must mkdir -p.
        out_path = self.tmp_dir / "a" / "b" / "c" / "result.json"
        self.assertFalse(out_path.parent.exists())
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner._default_subprocess_runner",
            return_value=_make_claude_run_result(_VANILLA_TS),
        ):
            code, _, _ = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
                "--rounds", "1",
                "--out", str(out_path),
            )
        self.assertEqual(code, 0)
        self.assertTrue(out_path.exists())


class TestContractsAutoEvalNeverWritesToDarkFactoryAudit(
    ContractsAutoEvalCLITestCase,
):
    """Path B regression test: under no condition does the CLI write
    anything under ``<project>/.dark-factory/audit/`` (or anywhere
    under ``.dark-factory/`` for that matter). neo's argument in B9b
    3 review was: auto-eval is a maintainer-only artifact with no
    per-project semantics; the existing ``_AUDIT`` mask is documented
    as Implementer-masked telemetry, not a multi-agent barrier; TE/
    SA/QA Holdout do not have ``_AUDIT`` masked. Writing TS test
    snippets there would promote test-shape information across
    barriers. Path B (stdout/--out, never .dark-factory/) sidesteps
    the leak entirely."""

    def test_no_files_written_under_dark_factory_audit(self) -> None:
        """Targets the actual leak surface: files (not just the dir
        existence) under ``<project>/.dark-factory/audit/``. A
        regression that creates the dir without writing a leak file
        would fail the older "audit_dir.exists() == False" check
        spuriously; this version walks for files directly. Per Linus
        commit-3 final review."""
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner._default_subprocess_runner",
            return_value=_make_claude_run_result(_VANILLA_TS),
        ):
            code, _, _ = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
                "--rounds", "2",
            )
        self.assertEqual(code, 0)
        audit_dir = self.tmp_dir / ".dark-factory" / "audit"
        if audit_dir.exists():
            files = [p for p in audit_dir.rglob("*") if p.is_file()]
            self.assertEqual(
                files, [],
                msg=(
                    f"auto-eval CLI must NEVER write files under "
                    f"<project>/.dark-factory/audit/ — Path B verdict, "
                    f"B9b 3 (neo review). Found leak files: {files!r}"
                ),
            )

    def test_no_files_written_anywhere_under_dark_factory(self) -> None:
        """Even broader: this command should not write any files
        under ``<project>/.dark-factory/`` at all. The CLI is a
        maintainer tool, not a project initializer. File-level (not
        dir-level) check so an unrelated startup mkdir doesn't
        fail-spurious."""
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner._default_subprocess_runner",
            return_value=_make_claude_run_result(_VANILLA_TS),
        ):
            code, _, _ = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
                "--rounds", "1",
            )
        self.assertEqual(code, 0)
        df_dir = self.tmp_dir / ".dark-factory"
        if df_dir.exists():
            files = [p for p in df_dir.rglob("*") if p.is_file()]
            self.assertEqual(
                files, [],
                msg=f"unexpected files in .dark-factory/: {files!r}",
            )


class TestContractsAutoEvalErrorPaths(ContractsAutoEvalCLITestCase):
    """Runner-level errors surface as exit-1 with stderr diagnostics."""

    def test_unknown_helper_exits_1_with_stderr(self) -> None:
        # No subprocess patch needed — runner aborts on validation
        # before any subprocess call.
        code, out, err = self.run_cli(
            "contracts", "auto-eval", "assertSomethingThatDoesNotExist",
            "--rounds", "1",
        )
        self.assertEqual(code, 1)
        self.assertIn("auto-eval failed", err)
        self.assertIn("unknown helper", err)
        # Stdout should be silent on error.
        self.assertEqual(out, "")

    def test_invalid_rounds_exits_1(self) -> None:
        code, _, err = self.run_cli(
            "contracts", "auto-eval", "assertUnsetGucIsNullish",
            "--rounds", "0",
        )
        self.assertEqual(code, 1)
        self.assertIn("auto-eval failed", err)
        self.assertIn("rounds must be >= 1", err)

    def test_subprocess_failure_exits_1(self) -> None:
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner._default_subprocess_runner",
            return_value=RunResult(exit_code=1, stdout="", stderr="claude: auth failed"),
        ):
            code, out, err = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
                "--rounds", "1",
            )
        self.assertEqual(code, 1)
        self.assertIn("auto-eval failed", err)
        # The round-index-bearing diagnostic from the runner surfaces.
        self.assertIn("round 1/1", err)


class TestContractsAutoEvalArgparseDefaults(ContractsAutoEvalCLITestCase):
    """Pin the documented defaults so a maintainer changing them in
    cli.py without thinking sees a test failure."""

    def test_default_rounds_is_5(self) -> None:
        # Capture the runner call to inspect what got passed.
        from unittest.mock import MagicMock
        mock_run_auto_eval = MagicMock(
            return_value=EvalResult(
                schema_version=1,
                helper_name="assertUnsetGucIsNullish",
                rounds=5,
                foot_gun_hits=0,
                helper_import_count=0,
                verdict="retire",
            ),
        )
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner.run_auto_eval",
            mock_run_auto_eval,
        ):
            code, _, _ = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
            )
        self.assertEqual(code, 0)
        self.assertEqual(mock_run_auto_eval.call_args.kwargs["rounds"], 5)

    def test_default_model_is_current_pin(self) -> None:
        from unittest.mock import MagicMock
        mock_run_auto_eval = MagicMock(
            return_value=EvalResult(
                schema_version=1,
                helper_name="assertUnsetGucIsNullish",
                rounds=1,
                foot_gun_hits=0,
                helper_import_count=0,
                verdict="retire",
            ),
        )
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner.run_auto_eval",
            mock_run_auto_eval,
        ):
            code, _, _ = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
                "--rounds", "1",
            )
        self.assertEqual(code, 0)
        # The default must match the current retire_after pin in
        # retire-after.toml. If a release bump updates the TOML
        # without this CLI default, this test fails — exactly the
        # drift signal we want.
        self.assertEqual(
            mock_run_auto_eval.call_args.kwargs["model"],
            "claude-opus-4-7",
        )

    def test_default_timeout_is_120(self) -> None:
        from unittest.mock import MagicMock
        mock_run_auto_eval = MagicMock(
            return_value=EvalResult(
                schema_version=1,
                helper_name="assertUnsetGucIsNullish",
                rounds=1,
                foot_gun_hits=0,
                helper_import_count=0,
                verdict="retire",
            ),
        )
        with patch(
            "darkfactory.adapters.postgres.retire_after_runner.run_auto_eval",
            mock_run_auto_eval,
        ):
            code, _, _ = self.run_cli(
                "contracts", "auto-eval", "assertUnsetGucIsNullish",
                "--rounds", "1",
            )
        self.assertEqual(code, 0)
        self.assertEqual(mock_run_auto_eval.call_args.kwargs["timeout"], 120)


class TestContractsParentSubcommand(ContractsAutoEvalCLITestCase):
    """``dark-factory contracts`` without a subcommand prints usage."""

    def test_contracts_alone_prints_usage_and_exits_1(self) -> None:
        code, _, err = self.run_cli("contracts")
        self.assertEqual(code, 1)
        self.assertIn("Usage", err)


if __name__ == "__main__":
    unittest.main()
