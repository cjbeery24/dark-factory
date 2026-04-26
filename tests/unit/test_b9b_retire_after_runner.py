"""ROADMAP.md Phase 3 B9b — third-commit tests for the LLM-invocation
runner.

Pins:

1. ``run_auto_eval`` happy path — N successful rounds → EvalResult
   with the harness's verdict semantics flowing through unchanged.
2. Argument validation: ``rounds < 1`` and unknown ``helper_name``.
3. Subprocess command shape: claude -p <prompt> --model <model>
   --output-format json --max-turns 1.
4. Prompt rendering: preamble + trigger body, both present in the
   subprocess invocation.
5. Failure modes (each → RuntimeError): non-zero exit, timeout, bad
   JSON, missing/non-string ``result`` field.
6. Failure messages include the round index (so a maintainer doing
   N=20 rounds knows which call failed).
7. Per-round timeout is forwarded to the subprocess runner.
8. Subprocess-runner dependency injection works (the test stub
   replaces the production runner).

Each subprocess test mocks at the runner-parameter boundary. No real
``claude`` subprocess is invoked; no network call.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from darkfactory.adapters.postgres.retire_after_eval import EvalResult
from darkfactory.adapters.postgres.retire_after_fixtures import FIXTURES
from darkfactory.adapters.postgres.retire_after_runner import (
    _PROMPT_PREAMBLE,
    run_auto_eval,
)
from darkfactory.utils.subprocess_runner import RunResult


def _make_run_result(
    body: str,
    *,
    exit_code: int = 0,
    timed_out: bool = False,
    stderr: str = "",
) -> RunResult:
    """Construct a RunResult with the model's reply embedded in the
    JSON output's ``result`` field — mirrors the claude-CLI JSON
    output format the runner parses."""
    if timed_out:
        return RunResult(
            exit_code=124, stdout="", stderr=stderr, timed_out=True,
        )
    if exit_code != 0:
        return RunResult(exit_code=exit_code, stdout="", stderr=stderr)
    payload = json.dumps({
        "result": body,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    return RunResult(exit_code=0, stdout=payload)


# Synthetic-vanilla TS snippets used in happy-path runs. Match the
# foot-gun-free / helper-import-free shape that the harness's verdict
# logic should classify as ``retire`` (no foot-gun pattern hits, no
# helper imports). Identical semantics across rounds — not testing
# variability, testing the contract.
_VANILLA_TS = """\
import { describe, it, expect } from 'vitest';
describe('something', () => {
  it('does the thing', async () => {
    const result = await someProductionPath();
    expect(result).toBe('ok');
  });
});
"""


class TestArgumentValidation(unittest.TestCase):
    """rounds < 1 and unknown helper names raise ValueError."""

    def test_rounds_zero_raises(self) -> None:
        runner = MagicMock()
        with self.assertRaisesRegex(ValueError, "rounds must be >= 1"):
            run_auto_eval(
                "assertUnsetGucIsNullish",
                "claude-opus-4-7",
                0,
                runner=runner,
            )
        runner.assert_not_called()

    def test_negative_rounds_raises(self) -> None:
        runner = MagicMock()
        with self.assertRaisesRegex(ValueError, "rounds must be >= 1"):
            run_auto_eval(
                "assertUnsetGucIsNullish",
                "claude-opus-4-7",
                -3,
                runner=runner,
            )
        runner.assert_not_called()

    def test_unknown_helper_raises(self) -> None:
        runner = MagicMock()
        with self.assertRaisesRegex(ValueError, "unknown helper"):
            run_auto_eval(
                "assertSomethingThatDoesNotExist",
                "claude-opus-4-7",
                1,
                runner=runner,
            )
        runner.assert_not_called()

    def test_unknown_helper_lists_known_helpers(self) -> None:
        """Error message MUST list the known helpers — a maintainer
        seeing the message should not have to grep the framework to
        find the typo."""
        runner = MagicMock()
        try:
            run_auto_eval(
                "assertNonExistent",
                "claude-opus-4-7",
                1,
                runner=runner,
            )
        except ValueError as exc:
            msg = str(exc)
            for fixture in FIXTURES:
                self.assertIn(fixture.helper_name, msg)
        else:
            self.fail("expected ValueError")


class TestHappyPathFlowsThroughHarness(unittest.TestCase):
    """N successful rounds with synthetic-vanilla output produce a
    ``retire`` verdict for pattern-bearing helpers and ``inconclusive``
    for the RLS fixture."""

    def test_5_round_synthetic_vanilla_yields_retire_for_unset_guc(self) -> None:
        runner = MagicMock(return_value=_make_run_result(_VANILLA_TS))
        result = run_auto_eval(
            "assertUnsetGucIsNullish",
            "claude-opus-4-7",
            5,
            runner=runner,
        )
        self.assertIsInstance(result, EvalResult)
        self.assertEqual(result.verdict, "retire")
        self.assertEqual(result.rounds, 5)
        self.assertEqual(result.foot_gun_hits, 0)
        self.assertEqual(result.helper_import_count, 0)
        self.assertEqual(runner.call_count, 5)

    def test_5_round_for_rls_yields_inconclusive(self) -> None:
        # RLS fixture has foot_gun_pattern=None → harness verdict
        # is structurally inconclusive regardless of round content.
        # The runner must surface this honestly, not paper over it.
        runner = MagicMock(return_value=_make_run_result(_VANILLA_TS))
        result = run_auto_eval(
            "assertRlsEnabled",
            "claude-opus-4-7",
            5,
            runner=runner,
        )
        self.assertEqual(result.verdict, "inconclusive")
        self.assertEqual(result.rounds, 5)
        self.assertEqual(runner.call_count, 5)

    def test_helper_imported_yields_keep(self) -> None:
        # If the model finds the helper through some out-of-band
        # path and imports it, the verdict must be ``keep`` — the
        # helper is still load-bearing in the model's working set.
        importing_ts = """\
import { describe, it } from 'vitest';
import { assertTxGucRoundTrips } from '../_contracts/postgres';
describe('x', () => { it('y', async () => { await assertTxGucRoundTrips(c, 'app.x', 'v'); }); });
"""
        runner = MagicMock(return_value=_make_run_result(importing_ts))
        result = run_auto_eval(
            "assertTxGucRoundTrips",
            "claude-opus-4-7",
            3,
            runner=runner,
        )
        self.assertEqual(result.verdict, "keep")
        self.assertEqual(result.helper_import_count, 3)


class TestSubprocessCommandShape(unittest.TestCase):
    """The runner invokes claude with the documented argv shape."""

    def test_command_argv(self) -> None:
        runner = MagicMock(return_value=_make_run_result(_VANILLA_TS))
        run_auto_eval(
            "assertUnsetGucIsNullish",
            "claude-opus-4-7",
            1,
            runner=runner,
        )
        call_args = runner.call_args
        argv = call_args.args[0]
        self.assertEqual(argv[0], "claude")
        self.assertEqual(argv[1], "-p")
        # argv[2] is the prompt itself (variable; tested below).
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-4-7")
        self.assertIn("--output-format", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertIn("--max-turns", argv)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")

    def test_prompt_includes_preamble_and_trigger_body(self) -> None:
        runner = MagicMock(return_value=_make_run_result(_VANILLA_TS))
        run_auto_eval(
            "assertTxGucRoundTrips",
            "claude-opus-4-7",
            1,
            runner=runner,
        )
        prompt = runner.call_args.args[0][2]
        # Preamble must appear at the start.
        self.assertTrue(prompt.startswith(_PROMPT_PREAMBLE))
        # Trigger body marker — pick something distinctive from the
        # tx_guc trigger that won't collide with the preamble.
        self.assertIn("round-trips through a transaction", prompt)
        # Helper name must NOT appear (scrubbed-mention property of
        # the trigger registry; runner must not bypass it).
        self.assertNotIn("assertTxGucRoundTrips", prompt)

    def test_timeout_forwarded(self) -> None:
        runner = MagicMock(return_value=_make_run_result(_VANILLA_TS))
        run_auto_eval(
            "assertUnsetGucIsNullish",
            "claude-opus-4-7",
            1,
            runner=runner,
            timeout=300,
        )
        self.assertEqual(runner.call_args.kwargs["timeout"], 300)

    def test_default_timeout_is_120s(self) -> None:
        # Pin the default so a maintainer who lowers it (or raises
        # it without thinking about cost) sees a test failure.
        runner = MagicMock(return_value=_make_run_result(_VANILLA_TS))
        run_auto_eval(
            "assertUnsetGucIsNullish",
            "claude-opus-4-7",
            1,
            runner=runner,
        )
        self.assertEqual(runner.call_args.kwargs["timeout"], 120)


class TestSubprocessFailureModes(unittest.TestCase):
    """Each non-zero / timeout / bad-JSON / missing-field failure
    raises ``RuntimeError``. The error message includes the round
    index and a snippet of the diagnostic payload."""

    def test_non_zero_exit_raises_with_round_index(self) -> None:
        runner = MagicMock(
            return_value=_make_run_result("", exit_code=1, stderr="auth failed"),
        )
        with self.assertRaisesRegex(RuntimeError, r"round 1/3"):
            run_auto_eval(
                "assertUnsetGucIsNullish",
                "claude-opus-4-7",
                3,
                runner=runner,
            )

    def test_non_zero_exit_message_includes_stderr_snippet(self) -> None:
        runner = MagicMock(
            return_value=_make_run_result("", exit_code=1, stderr="boom"),
        )
        with self.assertRaisesRegex(RuntimeError, "boom"):
            run_auto_eval(
                "assertUnsetGucIsNullish",
                "claude-opus-4-7",
                1,
                runner=runner,
            )

    def test_timeout_raises_with_timeout_message(self) -> None:
        runner = MagicMock(return_value=_make_run_result("", timed_out=True))
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            run_auto_eval(
                "assertUnsetGucIsNullish",
                "claude-opus-4-7",
                1,
                runner=runner,
            )

    def test_unparseable_json_raises(self) -> None:
        runner = MagicMock(
            return_value=RunResult(exit_code=0, stdout="not a json string"),
        )
        with self.assertRaisesRegex(RuntimeError, "JSON-parse failed"):
            run_auto_eval(
                "assertUnsetGucIsNullish",
                "claude-opus-4-7",
                1,
                runner=runner,
            )

    def test_missing_result_field_raises(self) -> None:
        runner = MagicMock(
            return_value=RunResult(
                exit_code=0,
                stdout=json.dumps({"usage": {"input_tokens": 10}}),
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "missing 'result'"):
            run_auto_eval(
                "assertUnsetGucIsNullish",
                "claude-opus-4-7",
                1,
                runner=runner,
            )

    def test_non_string_result_field_raises(self) -> None:
        runner = MagicMock(
            return_value=RunResult(
                exit_code=0,
                stdout=json.dumps({"result": 42}),
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "missing 'result'"):
            run_auto_eval(
                "assertUnsetGucIsNullish",
                "claude-opus-4-7",
                1,
                runner=runner,
            )

    def test_failure_on_round_3_of_5_reports_round_3(self) -> None:
        # Mid-loop failure: 2 successful rounds, then exit_code=1.
        # Verdict aborts — partial results are not silently
        # downgraded to a verdict on N=2 rounds.
        responses = [
            _make_run_result(_VANILLA_TS),
            _make_run_result(_VANILLA_TS),
            _make_run_result("", exit_code=2, stderr="model unavailable"),
        ]
        runner = MagicMock(side_effect=responses)
        with self.assertRaisesRegex(RuntimeError, r"round 3/5"):
            run_auto_eval(
                "assertUnsetGucIsNullish",
                "claude-opus-4-7",
                5,
                runner=runner,
            )
        # Subprocess called 3 times (2 success + 1 fail), then aborted.
        self.assertEqual(runner.call_count, 3)


class TestPerHelperWiring(unittest.TestCase):
    """Each universal helper can be driven through the runner; the
    bidirectional invariant tests pin the data, this test pins the
    runner-side wiring across the full helper set."""

    def test_every_universal_helper_is_runnable(self) -> None:
        runner = MagicMock(return_value=_make_run_result(_VANILLA_TS))
        for fixture in FIXTURES:
            with self.subTest(helper=fixture.helper_name):
                runner.reset_mock()
                result = run_auto_eval(
                    fixture.helper_name,
                    "claude-opus-4-7",
                    2,
                    runner=runner,
                )
                self.assertIsInstance(result, EvalResult)
                self.assertEqual(result.helper_name, fixture.helper_name)
                self.assertEqual(runner.call_count, 2)


if __name__ == "__main__":
    unittest.main()
